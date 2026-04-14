# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Background document ingestion pipeline.
#
# Fetches unindexed documents for a patient, decrypts them, extracts text
# (PDF-native → scanned-PDF OCR → image OCR), runs AI field extraction,
# and inserts suggestions into ai_extraction_inbox.
#
# Designed to run in a threading.Thread. A threading.Event is used as a
# cooperative stop signal so the UI can cancel mid-run.
#
# Public API:
#   run_ingestion(conn, dmk_raw, patient_id, progress_cb=None, stop_event=None)
# -----------------------------------------------------------------------------

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from crypto.file_crypto import decrypt_bytes, get_or_create_file_master_key
from ai.extraction import extract_fields

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _table_to_markdown(table: list[list]) -> str:
    """Convert a pdfplumber table (list of rows, each a list of cell strings) to Markdown."""
    if not table or not table[0]:
        return ""
    # Clean cells: replace None with empty string
    clean = [[str(cell).strip() if cell else "" for cell in row] for row in table]

    header = clean[0]
    md = "| " + " | ".join(header) + " |\n"
    md += "| " + " | ".join("---" for _ in header) + " |\n"
    for row in clean[1:]:
        # Pad short rows to match header length
        padded = row + [""] * (len(header) - len(row))
        md += "| " + " | ".join(padded[:len(header)]) + " |\n"
    return md


def _detect_quality_flags(words: list[dict], page_width: float, text: str) -> list[str]:
    """
    Analyze page layout and return quality warning strings.
    - Multi-column detection (text clusters in distinct horizontal bands)
    - Low-text detection (likely a scanned page inside a digital PDF)
    - Empty page detection
    """
    flags = []

    if not text.strip():
        flags.append("empty_page")
        return flags

    if len(text.strip()) < 50:
        flags.append("low_text")
        return flags

    # Multi-column detection: if words span wide with a gap in the middle
    if words and page_width > 0:
        x_positions = sorted(set(int(w.get("x0", 0)) for w in words))
        if len(x_positions) > 10:
            # Check for a horizontal gap in the middle third of the page
            mid_start = page_width * 0.35
            mid_end = page_width * 0.65
            words_in_mid = [x for x in x_positions if mid_start <= x <= mid_end]
            words_left = [x for x in x_positions if x < mid_start]
            words_right = [x for x in x_positions if x > mid_end]
            # If significant text lives on both sides but not the middle, likely multi-column
            if words_left and words_right and len(words_in_mid) < len(x_positions) * 0.15:
                flags.append("multi_column")

    return flags


def _extract_text(file_bytes: bytes, file_name: str) -> list[tuple[int, str, list[str]]]:
    """
    Extract text from decrypted file bytes.
    Returns list of (page_number, text, quality_flags) tuples (1-indexed pages).

    quality_flags is a list of strings:
      - "empty_page": page had no extractable text
      - "low_text": very little text found (likely a scanned page in a digital PDF)
      - "multi_column": potential multi-column layout detected
      - "tables_found": structured tables were extracted as Markdown

    Fallback chain:
      0. Plain text file (.txt) — returned directly
      1. Digital PDF with tables/text (pdfplumber)
      2. Scanned PDF (pdf2image + RapidOCR)
      3. Image file (RapidOCR directly)
    """
    name_lower = file_name.lower()

    # Plain text passthrough (no PDF/OCR needed)
    if name_lower.endswith(".txt"):
        try:
            return [(1, file_bytes.decode("utf-8", errors="replace"), [])]
        except Exception:
            return []

    if name_lower.endswith(".pdf"):
        # Try pdfplumber first (structured extraction with table support)
        try:
            import io
            import pdfplumber

            pdf = pdfplumber.open(io.BytesIO(file_bytes))
            pages = []
            has_meaningful_text = False

            for i, pg in enumerate(pdf.pages, start=1):
                parts = []
                flags = []

                # Extract tables as Markdown
                tables = pg.extract_tables()
                if tables:
                    flags.append("tables_found")
                    for table in tables:
                        md = _table_to_markdown(table)
                        if md.strip():
                            parts.append(md)

                # Extract non-table text
                text = pg.extract_text() or ""
                if text.strip():
                    parts.append(text)

                page_text = "\n\n".join(parts)

                # Quality flagging
                try:
                    words = pg.extract_words() or []
                except Exception:
                    words = []
                quality_flags = _detect_quality_flags(words, pg.width, page_text)
                flags.extend(quality_flags)

                if page_text.strip():
                    has_meaningful_text = True

                pages.append((i, page_text, flags))

            pdf.close()

            if has_meaningful_text:
                return pages
        except Exception as exc:
            logger.debug("pdfplumber extraction failed: %s", exc)

        # Scanned PDF: rasterise then OCR
        try:
            import io
            from pdf2image import convert_from_bytes
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            images = convert_from_bytes(file_bytes, dpi=150)
            pages = []
            for i, img in enumerate(images, start=1):
                result, _ = ocr(img)
                text = "\n".join(r[1] for r in result) if result else ""
                pages.append((i, text, []))
            return pages
        except Exception as exc:
            logger.warning("Scanned PDF OCR failed: %s", exc)
            return []

    # Image file
    if any(name_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif")):
        try:
            import io
            from PIL import Image
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            img = Image.open(io.BytesIO(file_bytes))
            result, _ = ocr(img)
            text = "\n".join(r[1] for r in result) if result else ""
            return [(1, text, [])]
        except Exception as exc:
            logger.warning("Image OCR failed: %s", exc)
            return []

    logger.info("Unsupported file type for text extraction: %s", file_name)
    return []



def _get_unprocessed_docs(conn, patient_id: int) -> list[dict]:
    """Return documents that have not yet fully completed AI extraction."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.id, d.file_name, d.file_path
        FROM documents d
        WHERE d.patient_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM ai_extraction_inbox c 
              WHERE c.doc_id = d.id AND c.field_key = 'system.processed'
          )
        """,
        (patient_id,),
    )
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]



def _insert_suggestions(conn, patient_id: int, doc_id: int, suggestions: list[dict]) -> None:
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT OR IGNORE INTO ai_extraction_inbox
            (patient_id, doc_id, field_key, suggested_value, confidence, source_file_name, conflict, existing_value, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        [
            (
                patient_id,
                doc_id,
                s["field_key"],
                s["value"],
                s["confidence"],
                s["source_file_name"],
                1 if s.get("conflict") else 0,
                s.get("existing_value")
            )
            for s in suggestions
        ]
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_ingestion(
    conn,
    dmk_raw: bytes,
    patient_id: int,
    data_dir: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """
    Run the ingestion pipeline synchronously.
    Call from a threading.Thread for non-blocking operation.

    Parameters
    ----------
    conn        : open database connection (SQLCipher)
    dmk_raw     : 32-byte Database Master Key
    patient_id  : patient to process
    data_dir    : root encrypted-file directory (paths.data_dir)
    progress_cb : optional callback(completed_docs, total_docs)
    stop_event  : set() this to interrupt processing mid-run
    """
    import os

    fmk = get_or_create_file_master_key(conn, dmk_raw=dmk_raw)

    # Purge orphaned chunks/suggestions from deleted documents
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM ai_extraction_inbox
        WHERE doc_id IS NOT NULL
          AND doc_id NOT IN (SELECT id FROM documents)
    """)
    orphaned = cur.rowcount
    conn.commit()
    if orphaned:
        logger.info("Cleaned up orphaned AI data from deleted documents")

    docs = _get_unprocessed_docs(conn, patient_id)
    total = len(docs)

    if not docs:
        logger.info("No unindexed documents for patient %d", patient_id)
        if progress_cb:
            progress_cb(0, 0)
        return

    for i, doc in enumerate(docs):
        if stop_event and stop_event.is_set():
            logger.info("Ingestion stopped by stop_event after %d/%d docs", i, total)
            break

        file_path = doc.get("file_path", "")
        if not file_path:
            continue

        from core.paths import resolve_doc_path
        full_path = str(resolve_doc_path(file_path))

        if not os.path.isfile(full_path):
            logger.warning("Encrypted file not found, skipping: %s", full_path)
            continue

        try:
            with open(full_path, "rb") as f:
                ciphertext = f.read()
            plaintext = decrypt_bytes(fmk, ciphertext)
        except Exception as exc:
            logger.error("Decryption failed for doc %d: %s", doc["id"], exc)
            continue

        file_name = doc.get("file_name", "unknown")
        pages = _extract_text(plaintext, file_name)

        full_text_parts = []
        doc_quality_flags = set()
        for page_num, page_text, quality_flags in pages:
            if stop_event and stop_event.is_set():
                break
            full_text_parts.append(page_text)
            doc_quality_flags.update(quality_flags)


        if not (stop_event and stop_event.is_set()):
            full_text = "\n".join(full_text_parts)

            # Persist extracted text so check_upload_for_matches can use it
            try:
                conn.execute(
                    "UPDATE documents SET parsed_text = ? WHERE id = ?",
                    (full_text if full_text.strip() else None, doc["id"]),
                )
                conn.commit()
            except Exception as pt_ex:
                logger.warning("Failed to write parsed_text for doc %d: %s", doc["id"], pt_ex)

            # Insert quality warnings as special inbox entries so users see them
            _quality_warnings = []
            if "multi_column" in doc_quality_flags:
                _quality_warnings.append({
                    "field_key": "system.quality_warning",
                    "value": "This document may have a multi-column layout. "
                             "Some data might be out of order. Please verify the extracted information below.",
                    "confidence": 0.0,
                    "source_file_name": file_name,
                    "conflict": False,
                    "existing_value": None,
                })
            if "low_text" in doc_quality_flags:
                _quality_warnings.append({
                    "field_key": "system.quality_warning",
                    "value": "Some pages in this document appear to be scanned. "
                             "The AI extracted what it could — please verify nothing was missed.",
                    "confidence": 0.0,
                    "source_file_name": file_name,
                    "conflict": False,
                    "existing_value": None,
                })
            if "empty_page" in doc_quality_flags and not full_text.strip():
                _quality_warnings.append({
                    "field_key": "system.quality_warning",
                    "value": "This document couldn't be read automatically. "
                             "You may need to add this information manually from the Documents tab.",
                    "confidence": 0.0,
                    "source_file_name": file_name,
                    "conflict": False,
                    "existing_value": None,
                })
            if _quality_warnings:
                _insert_suggestions(conn, patient_id, doc["id"], _quality_warnings)

            if full_text.strip():
                # Extract Document Metadata (Visit Date & Specialty) using AI
                try:
                    doc_meta_prompt = f"""
From the following document text, what is the single primary visit date (the FIRST listed visit date) and the primary medical specialty/clinic name?
All dates MUST be formatted using the ISO 8601 international standard (YYYY-MM-DD).
Return ONLY a valid JSON object exactly like this:
{{"visit_date": "YYYY-MM-DD", "specialty": "Specialty Name"}}
If not found, use null.

Document:
{full_text[:3000]}
"""
                    from ai.backend import get_llm
                    import json
                    import re
                    llm = get_llm()
                    raw = llm.complete(doc_meta_prompt).text
                    match = re.search(r"\{.*\}", str(raw).strip(), flags=re.DOTALL)
                    if match:
                        meta = json.loads(match.group(0))
                        vd = str(meta.get("visit_date", "")).strip()
                        sp = str(meta.get("specialty", "")).strip()
                        if vd.lower() in ("unknown", "none", "null", ""): vd = None
                        if sp.lower() in ("unknown", "none", "null", ""): sp = None
                        
                        c = conn.cursor()
                        c.execute("UPDATE documents SET visit_date = ?, specialty = ? WHERE id = ?", (vd, sp, doc["id"]))
                        conn.commit()
                except Exception as meta_ex:
                    logger.warning("Document metadata extraction failed for doc %d: %s", doc["id"], meta_ex)

                try:
                    suggestions = extract_fields(
                        conn, 
                        patient_id, 
                        full_text, 
                        file_name
                    )
                    if suggestions:
                        _insert_suggestions(conn, patient_id, doc["id"], suggestions)
                except Exception as ex:
                    logger.error("Extraction failed for doc %d: %s", doc["id"], ex)

        if not (stop_event and stop_event.is_set()):
            # Always push a dummy processed record so _get_unprocessed_docs stops fetching it
            conn.execute(
                "INSERT OR IGNORE INTO ai_extraction_inbox (patient_id, doc_id, field_key, suggested_value, confidence, source_file_name, status) VALUES (?, ?, 'system.processed', ?, 1.0, ?, 'system')",
                (patient_id, doc["id"], str(doc["id"]), file_name)
            )
            conn.commit()

        if progress_cb:
            progress_cb(i + 1, total)
        logger.info("Ingested doc %d (%s)", doc["id"], file_name)
