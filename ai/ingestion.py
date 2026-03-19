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
# (PDF-native → scanned-PDF OCR → image OCR), chunks the text, and inserts
# chunks into document_chunks.
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

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 512    # approximate tokens (chars / 4)
_CHUNK_OVERLAP = 64  # overlap to preserve context across boundaries


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _extract_text(file_bytes: bytes, file_name: str) -> list[tuple[int, str]]:
    """
    Extract text from decrypted file bytes.
    Returns list of (page_number, text) tuples (1-indexed pages).

    Fallback chain:
      1. PDF with embedded text (pypdf)
      2. Scanned PDF (pdf2image + rapidocr)
      3. Image file (rapidocr directly)
    """
    name_lower = file_name.lower()

    if name_lower.endswith(".pdf"):
        # Try embedded text first (fast, lossless)
        try:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            pages = []
            for i, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                pages.append((i, text))
            # If we got meaningful text, use it
            if any(t.strip() for _, t in pages):
                return pages
        except Exception as exc:
            logger.debug("pypdf extraction failed: %s", exc)

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
                pages.append((i, text))
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
            return [(1, text)]
        except Exception as exc:
            logger.warning("Image OCR failed: %s", exc)
            return []

    logger.info("Unsupported file type for text extraction: %s", file_name)
    return []


def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE,
                overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into fixed-size character chunks with overlap."""
    step = max(chunk_size - overlap, 1)
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i : i + chunk_size])
        i += step
    return chunks


def _get_unindexed_docs(conn, patient_id: int) -> list[dict]:
    """Return documents that have no rows in document_chunks yet."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.id, d.file_name, d.file_path
        FROM documents d
        WHERE d.patient_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM document_chunks c WHERE c.doc_id = d.id
          )
        """,
        (patient_id,),
    )
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _insert_chunks(conn, doc_id: int, patient_id: int,
                   page_number: int, source_file_name: str,
                   chunks: list[str]) -> None:
    cur = conn.cursor()
    now = _now_ts()
    cur.executemany(
        """
        INSERT INTO document_chunks
            (doc_id, patient_id, page_number, source_file_name,
             chunk_text, chunk_index, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (doc_id, patient_id, page_number, source_file_name, chunk, idx, now)
            for idx, chunk in enumerate(chunks)
        ],
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
    docs = _get_unindexed_docs(conn, patient_id)
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

        full_path = (
            file_path if os.path.isabs(file_path)
            else os.path.join(data_dir, file_path)
        )

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

        for page_num, page_text in pages:
            if stop_event and stop_event.is_set():
                break
            chunks = _chunk_text(page_text)
            if chunks:
                _insert_chunks(conn, doc["id"], patient_id,
                               page_num, file_name, chunks)

        if progress_cb:
            progress_cb(i + 1, total)
        logger.info("Ingested doc %d (%s)", doc["id"], file_name)
