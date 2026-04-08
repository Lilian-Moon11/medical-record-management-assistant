# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Coordinate-based text overlay for static (non-AcroForm) PDFs.
#
# When a user uploads a static PDF (visual underlines, no embedded fields),
# this module attempts to:
#   1. Extract visible form labels and their approximate page coordinates
#      using pypdf's visitor_text callback.
#   2. Ask the LLM to match patient data to the discovered labels.
#   3. Build a transparent text overlay using fpdf2.
#   4. Merge the overlay onto the original PDF using pypdf.
#
# Results are approximate — positioning is estimated from where labels and
# underscores appear on the page, not from authoritative field geometry.
# Callers should inform users that the output needs review before submitting.
#
# Public API:
#   fill_static_pdf(template_path, db_conn, patient_id, llm=None) -> bytes
# -----------------------------------------------------------------------------

from __future__ import annotations

import ast
import io
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

_PT_TO_MM = 25.4 / 72  # 1 point = 0.352778 mm

_STATIC_MAP_PROMPT = """\
You are filling out a paper form for a patient.
The following labels were found on the form. Match each one to the patient's information.

Patient Record (JSON):
{digest}

Form Labels found on the page:
{labels_list}

Instructions:
- Output ONLY a valid JSON dictionary.
- Keys must be the EXACT label strings from the list above.
- Values must be the patient's matching data as plain strings.
- If no data matches a label, use an empty string "".
- Keep each value concise — aim for under 60 characters per field.
- Use comma-separated values ONLY for labels that explicitly ask for a list
  (e.g. "Current Medications", "Allergies", "Medical Conditions").
- For labels like "Other", "Other conditions", "Additional notes", or any
  vague/generic label: leave it empty ("") unless a single specific value
  from the record is an obvious, complete match. Do NOT dump leftover items here.
- Each label should receive at most ONE specific piece of information.
  Do not concatenate multiple medications, conditions, or other records
  into a field that is not explicitly asking for a complete list.
- For medication table columns ("Medication Name", "Dosage", "Frequency",
  "Reason for Taking", "Medication Name 2", etc.): fill the first row with
  the first listed medication's details, "Medication Name 2" with the second, etc.
  Leave numbered rows empty if there are fewer medications than rows.
- Recognise common medical form abbreviations when matching labels to data:
  DOB = Date of Birth, Pt = Patient, Ins = Insurance, Tel/Ph = Phone,
  Addr = Address, Emerg = Emergency, Rx = Prescription/Medication, Hx = History,
  Dx = Diagnosis, Sx = Symptoms, Sig = Signature, No/# = Number, Grp = Group.
  Match these to the closest patient data key.

Output ONLY the JSON dictionary. No preamble. No trailing text.
"""


# Words that appear before a colon in non-field contexts (headings, sentences)
_SKIP_LABELS = frozenset([
    "please", "note", "for", "if", "and", "the", "or", "in", "of", "to",
    "a", "an", "at", "by", "not", "be", "as", "is", "we", "you", "your",
    "that", "this", "which", "when", "where", "how", "what", "who",
])

# Lines that are section headings, not form fields
_HEADING_PATTERNS = re.compile(
    r'^(personal information|insurance information|medical history|'
    r'surgical history|emergency contact|'
    r'review of systems|social history|family history|'
    r'please (check|list|describe|indicate)|'
    r'authorization|consent|acknowledgement)$',
    re.IGNORECASE,
)

# Column header tokens that identify a medication table row
_MED_TABLE_HEADERS = re.compile(
    r'\bmedication\s+name\b',
    re.IGNORECASE,
)

# Underline pattern: 3+ consecutive underscores, optionally with spaces
_UNDERLINE_RE = re.compile(r'_{3,}[\s_]*')
# Labels that map to medication-table column positions (left→right order)
_MED_COL_LABELS = ["Medication Name", "Dosage", "Frequency", "Reason for Taking"]


# Labels whose text strongly implies a signature field
_SIG_LABEL_RE = re.compile(r'\bsignature\b|\bsign\b', re.IGNORECASE)

# Checkbox pattern: [ ] or [x] or [X] followed by label text
_CHECKBOX_RE = re.compile(
    r'^\s*\[\s*[xX]?\s*\]\s*(.+)',
)


def _extract_field_positions(pdf_path: str) -> list[dict]:
    """
    Extract candidate form field positions from a static PDF.

    Uses pypdf's layout extraction mode, which correctly reconstructs the
    visual line order from any well-formed PDF (including Word exports).
    Positions are estimated from line number and character offset.

    Returns a list of dicts:
        {
            "label": str,         # e.g. "Full Name"
            "page": int,          # 0-indexed page number
            "x_pt": float,        # x position (PDF points) to write value
            "y_pt": float,        # y position (PDF points, from page bottom)
            "page_height": float,
            "page_width": float,
        }
    """
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    all_fields: list[dict] = []
    seen_labels: set[str] = set()

    for page_idx, page in enumerate(reader.pages):
        page_height = float(page.mediabox.height)
        page_width = float(page.mediabox.width)

        # layout mode preserves spatial arrangement as spaces/newlines
        try:
            raw_text = page.extract_text(extraction_mode="layout")
        except TypeError:
            raw_text = page.extract_text()  # fallback for older pypdf

        if not raw_text:
            continue

        lines = raw_text.split("\n")

        # Estimate geometry for this page.
        total_lines = max(len(lines), 1)
        top_margin_pt = 72.0
        bottom_margin_pt = 50.0
        usable_height = page_height - top_margin_pt - bottom_margin_pt
        line_height_pt = max(8.0, min(18.0, usable_height / total_lines))

        left_margin_pt = 54.0
        usable_width = page_width - left_margin_pt - 36.0
        char_width_pt = usable_width / max(
            max((len(l) for l in lines), default=85), 85
        )

        for line_idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            # Strip leading bullet/checkbox/list markers.
            clean = re.sub(r'^[^A-Za-z0-9]+', '', stripped).strip()

            # ----- Pass A: Colon-delimited labels ("Full Name:") -----
            # Also handles multi-label lines like "Signature: ____ Date: ____"
            # by scanning for additional labels after underscores.
            _remaining = clean
            _x_offset = 0  # character offset into the raw line
            _found_colon = False
            while _remaining:
                colon_pos = _remaining.find(":")
                if colon_pos < 1 or colon_pos > 60:
                    break

                label_candidate = _remaining[:colon_pos].strip()
                label_candidate = label_candidate.rstrip("_").strip()

                if _is_valid_label(label_candidate, seen_labels):
                    seen_labels.add(label_candidate)
                    _found_colon = True

                    y_pt = page_height - top_margin_pt - (line_idx + 0.65) * line_height_pt
                    # Find the colon position in the original line for x estimation
                    raw_pos = line.find(label_candidate + ":", _x_offset)
                    if raw_pos >= 0:
                        x_char = raw_pos + len(label_candidate) + 2
                    else:
                        x_char = _x_offset + colon_pos + 2
                    x_pt = min(left_margin_pt + x_char * char_width_pt, page_width - 80.0)

                    if bottom_margin_pt <= y_pt <= page_height - top_margin_pt:
                        all_fields.append({
                            "label": label_candidate,
                            "page": page_idx,
                            "x_pt": x_pt,
                            "y_pt": y_pt,
                            "page_height": page_height,
                            "page_width": page_width,
                        })

                # Advance past this label's value area to check for more labels
                after_colon = _remaining[colon_pos + 1:]
                # Skip underscores/whitespace to find next potential label
                skip_match = re.match(r'^[\s_]*', after_colon)
                if skip_match and skip_match.end() < len(after_colon):
                    _x_offset += colon_pos + 1 + skip_match.end()
                    _remaining = after_colon[skip_match.end():]
                else:
                    break

            if _found_colon:
                continue  # colon match(es) found; don't also try underline

            # ----- Pass B: Underline-delimited labels ("Full Name ___________") -----
            ul_match = _UNDERLINE_RE.search(clean)
            if ul_match and ul_match.start() >= 3:
                label_candidate = clean[:ul_match.start()].strip()
                # Also strip a trailing colon if present ("Name: ___")
                label_candidate = label_candidate.rstrip(":").strip()

                if _is_valid_label(label_candidate, seen_labels):
                    seen_labels.add(label_candidate)

                    y_pt = page_height - top_margin_pt - (line_idx + 0.65) * line_height_pt
                    # Place text where the underline starts
                    raw_ul_pos = line.find("_")
                    x_char = (raw_ul_pos + 1) if raw_ul_pos >= 0 else (len(label_candidate) + 2)
                    x_pt = min(left_margin_pt + x_char * char_width_pt, page_width - 80.0)

                    if bottom_margin_pt <= y_pt <= page_height - top_margin_pt:
                        all_fields.append({
                            "label": label_candidate,
                            "page": page_idx,
                            "x_pt": x_pt,
                            "y_pt": y_pt,
                            "page_height": page_height,
                            "page_width": page_width,
                        })

            # ----- Pass C: Checkbox items ("[ ] Diabetes") -----
            cb_match = _CHECKBOX_RE.match(clean)
            if cb_match:
                label_candidate = cb_match.group(1).strip()
                # Strip trailing colon + underscores ("Allergies (Specify): ___")
                label_candidate = _UNDERLINE_RE.sub('', label_candidate).rstrip(':').strip()

                if _is_valid_label(label_candidate, seen_labels):
                    seen_labels.add(label_candidate)

                    y_pt = page_height - top_margin_pt - (line_idx + 0.65) * line_height_pt
                    # Place value right after the checkbox marker
                    cb_end = line.find(']')
                    x_char = (cb_end + 2) if cb_end >= 0 else 4
                    x_pt = min(left_margin_pt + x_char * char_width_pt, page_width - 80.0)

                    if bottom_margin_pt <= y_pt <= page_height - top_margin_pt:
                        all_fields.append({
                            "label": label_candidate,
                            "page": page_idx,
                            "x_pt": x_pt,
                            "y_pt": y_pt,
                            "page_height": page_height,
                            "page_width": page_width,
                            "is_checkbox": True,
                        })

        # ----------------------------------------------------------------- #
        # Medication TABLE detection on same page
        # ----------------------------------------------------------------- #
        for line_idx2, line2 in enumerate(lines):
            if not _MED_TABLE_HEADERS.search(line2):
                continue
            col_w = usable_width / 4.0
            header_y_pt = (page_height - top_margin_pt
                           - (line_idx2 + 0.65) * line_height_pt)
            row_gap = line_height_pt * 2.5
            for row in range(3):
                row_y_pt = header_y_pt - (row + 1) * row_gap
                if row_y_pt < 36.0:
                    break
                for col, col_label in enumerate(_MED_COL_LABELS):
                    synth_label = f"{col_label} {row + 1}" if row > 0 else col_label
                    if synth_label in seen_labels:
                        continue
                    seen_labels.add(synth_label)
                    all_fields.append({
                        "label": synth_label,
                        "page": page_idx,
                        "x_pt": left_margin_pt + col * col_w + 4.0,
                        "y_pt": row_y_pt,
                        "page_height": page_height,
                        "page_width": page_width,
                    })
            break  # only one medication table per page

    return all_fields


def _is_valid_label(label: str, seen: set[str]) -> bool:
    """Shared validation for colon-delimited and underline-delimited label candidates."""
    if not label or len(label) < 3 or len(label) > 55:
        return False
    if label.lower() in _SKIP_LABELS:
        return False
    if _HEADING_PATTERNS.match(label):
        return False
    if label.isupper() and len(label) < 3:
        return False
    if sum(c.isdigit() for c in label) > len(label) // 2:
        return False
    if label in seen:
        return False
    return True


# ---------------------------------------------------------------------------
# LLM label→value mapping
# ---------------------------------------------------------------------------

def _safe_parse_dict(raw: str) -> dict:
    """Robust dict extraction from LLM output.

    Uses json.JSONDecoder.raw_decode() which reads exactly one JSON value
    starting from the first '{' and ignores any trailing text the LLM may
    have appended.
    """
    raw = str(raw).strip()
    start = raw.find("{")
    if start < 0:
        return {}

    # Primary: raw_decode reads one complete JSON object and stops
    decoder = json.JSONDecoder()
    try:
        result, _end = decoder.raw_decode(raw, start)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Fallback: try non-greedy regex + ast.literal_eval
    match = re.search(r"\{[^{}]+\}", raw)
    if match:
        try:
            safe = match.group(0).replace("null", "None").replace("true", "True").replace("false", "False")
            result = ast.literal_eval(safe)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    return {}


def _map_labels_to_values(label_fields: list[dict], digest: str, llm=None) -> dict:
    """Ask the LLM to match patient data to discovered form labels.

    Signature fields (is_signature=True) are excluded — the wizard handles them
    as image overlays; asking the LLM about them yields empty strings anyway.
    """
    non_sig_fields = [f for f in label_fields if not f.get("is_signature")]
    if not non_sig_fields:
        return {}
    if llm is None:
        from ai.backend import get_llm
        llm = get_llm()

    def _fmt_label(f):
        lbl = f["label"]
        if f.get("is_checkbox"):
            return f'  "{lbl}" [CHECK: write "X" if the patient has this, otherwise ""]'
        return f'  "{lbl}"'

    labels_list = "\n".join(_fmt_label(f) for f in non_sig_fields)
    prompt = _STATIC_MAP_PROMPT.format(digest=digest, labels_list=labels_list)

    try:
        raw = llm.complete(prompt).text
        # print(f"OVERLAY LLM RAW ({len(raw)} chars): {raw[:500]}")
        logger.debug("overlay: LLM raw output: %s", str(raw)[:400])
    except Exception as exc:
        logger.error("overlay: LLM call failed: %s", exc)
        return {}

    return _safe_parse_dict(raw)


# ---------------------------------------------------------------------------
# Overlay PDF construction
# ---------------------------------------------------------------------------

# Signature image size in PDF points (width × height)
_SIG_IMG_W_PT = 120.0
_SIG_IMG_H_PT = 36.0


def _build_overlay_bytes(page_count: int, fill_items: list[dict]) -> bytes:
    """
    Build a multi-page overlay PDF using fpdf2.
    Each page matches the original's dimensions and contains only the
    positioned text (and optional signature images) — no background — so
    pypdf merge_page is transparent.

    Items with ``sig_path`` are rendered as an embedded PNG image.
    Items with ``value`` are rendered as text.
    """
    from fpdf import FPDF

    by_page: dict[int, list[dict]] = {}
    for item in fill_items:
        by_page.setdefault(item["page"], []).append(item)

    # Use points as the native unit to match pypdf coordinate space
    pdf = FPDF(unit="pt")

    for page_idx in range(page_count):
        page_items = by_page.get(page_idx, [])
        if page_items:
            ph = page_items[0]["page_height"]
            pw = page_items[0]["page_width"]
        else:
            ph, pw = 792.0, 612.0  # US Letter fallback

        pdf.add_page(format=(pw, ph))
        pdf.set_font("Helvetica", size=10)
        pdf.set_text_color(0, 0, 0)

        for item in page_items:
            ph = item["page_height"]
            pw = item["page_width"]
            x_pt = item["x_pt"]
            # PDF y is from bottom; fpdf2 y is from top.
            y_pt_fpdf = ph - item["y_pt"] - 9

            # Clamp to visible page area
            x_pt = max(10.0, min(x_pt, pw - 60.0))

            sig_path = item.get("sig_path")
            if sig_path and os.path.exists(sig_path):
                # Render signature PNG as an embedded image
                # Align top of image with the top of the UI placement block
                sig_top = max(10.0, min(ph - item["y_pt"] - 20, ph - _SIG_IMG_H_PT - 5))
                try:
                    pdf.image(sig_path, x=x_pt, y=sig_top, w=_SIG_IMG_W_PT, h=_SIG_IMG_H_PT)
                except Exception as sig_ex:
                    logger.warning("overlay: signature image error: %s", sig_ex)
                continue

            value = str(item.get("value", "")).strip()
            if not value:
                continue

            y_pt_fpdf = max(10.0, min(y_pt_fpdf, ph - 15.0))

            # Truncate value to fit within remaining page width (~6pt per char)
            available_chars = max(5, int((pw - x_pt - 10) / 6))
            if len(value) > available_chars:
                value = value[:available_chars]

            pdf.set_xy(x_pt, y_pt_fpdf)
            pdf.cell(text=value)

    return bytes(pdf.output())


def _merge_overlay(template_path: str, overlay_bytes: bytes) -> bytes:
    """Merge the text overlay onto the original PDF pages using pypdf."""
    from pypdf import PdfReader, PdfWriter

    original = PdfReader(template_path)
    overlay_reader = PdfReader(io.BytesIO(overlay_bytes))

    writer = PdfWriter()
    for i, orig_page in enumerate(original.pages):
        if i < len(overlay_reader.pages):
            orig_page.merge_page(overlay_reader.pages[i])
        writer.add_page(orig_page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fill_static_pdf(
    template_path: str, db_conn, patient_id: int, llm=None, sig_path: str | None = None
) -> tuple[bytes, list[dict]]:
    """
    Attempt to fill a static (non-AcroForm) PDF via coordinate-based text overlay.

    Pipeline:
        1. Extract visible label positions using pypdf layout extraction.
        1b. Tag "Signature" labels; exclude from LLM, add as image items.
        2. Build patient digest from the health record.
        3. LLM maps patient data to each non-signature label.
        4. fpdf2 builds a transparent overlay (text + optional sig image).
        5. pypdf merges overlay onto the original template.

    Parameters
    ----------
    sig_path    Path to the captured signature PNG (from SignaturePad).
                If provided and a Signature label is found, the image is
                placed at that label's estimated position.

    Returns:
        (merged_pdf_bytes, fill_items) — fill_items contains each placed field
        with label, value or sig_path, and PDF coords for the placement review.
        On any failure returns (original_template_bytes, []).
    """
    from ai.paperwork import _build_patient_digest

    logger.info("overlay: starting static fill for '%s'", template_path)

    def _fallback():
        with open(template_path, "rb") as f:
            return f.read(), []

    # Step 1: extract label positions
    try:
        label_fields = _extract_field_positions(template_path)
        # print(f"OVERLAY LABELS DETECTED: {[f['label'] for f in label_fields]}")
        logger.info("overlay: found %d candidate label fields", len(label_fields))
    except Exception as exc:
        logger.error("overlay: field extraction failed: %s", exc)
        return _fallback()

    if not label_fields:
        logger.warning("overlay: no labels found, returning template unchanged")
        return _fallback()

    # Step 1b: tag signature labels so the LLM skips them
    for field in label_fields:
        if _SIG_LABEL_RE.search(field["label"]):
            field["is_signature"] = True
    sig_label_count = sum(1 for f in label_fields if f.get("is_signature"))
    if sig_label_count:
        logger.info("overlay: detected %d signature label(s)", sig_label_count)

    # Step 2: patient digest
    try:
        digest = _build_patient_digest(db_conn, patient_id)
    except Exception as exc:
        logger.warning("overlay: digest failed: %s", exc)
        digest = "No patient data available."

    # Step 3: LLM mapping (signature fields excluded inside helper)
    label_values = _map_labels_to_values(label_fields, digest, llm=llm)
    # print(f"OVERLAY LABEL→VALUE MAP: {label_values}")
    # Step 3b: Inject today's date for date-like labels the LLM missed.
    # Catches "Date", "Signature Date", "Date Signed" etc., but NOT "Date of Birth".
    from datetime import datetime as _dt
    _today = _dt.now().strftime("%Y-%m-%d")
    _DOB_RE = re.compile(r'\b(birth|dob|born)\b', re.IGNORECASE)
    for field in label_fields:
        lbl = field["label"]
        if field.get("is_signature"):
            continue
        if "date" in lbl.lower() and not _DOB_RE.search(lbl):
            if not label_values.get(lbl):
                label_values[lbl] = _today
                logger.debug("overlay: injected today's date for label '%s'", lbl)

    # print(f"OVERLAY LABEL→VALUE MAP (after date inject): {label_values}")

    # Step 4: build fill_items
    # - text fields: from LLM mapping
    # - signature fields: inject sig_path image item (if sig captured)
    fill_items: list[dict] = []
    for field in label_fields:
        base = {
            "label": field["label"],
            "page": field["page"],
            "x_pt": field["x_pt"],
            "y_pt": field["y_pt"],
            "page_height": field["page_height"],
            "page_width": field["page_width"],
        }
        if field.get("is_signature"):
            if sig_path:
                fill_items.append({**base, "sig_path": sig_path, "value": ""})
        else:
            val = str(label_values.get(field["label"], ""))
            if val:
                fill_items.append({**base, "value": val})

    logger.info("overlay: placing %d field items", len(fill_items))

    if not fill_items:
        logger.warning("overlay: nothing to place; returning template unchanged")
        return _fallback()

    # Step 5: build overlay and merge
    try:
        from pypdf import PdfReader
        page_count = len(PdfReader(template_path).pages)
        overlay_bytes = _build_overlay_bytes(page_count, fill_items)
        result = _merge_overlay(template_path, overlay_bytes)
        logger.info("overlay: complete, output %d bytes", len(result))
        return result, fill_items

    except Exception as exc:
        logger.error("overlay: merge failed: %s", exc)
        return _fallback()


def rebuild_overlay(template_path: str, fill_items: list[dict]) -> bytes:
    """
    Re-render the overlay with (possibly adjusted) fill_items coordinates.
    Called by the placement review on Save Final.
    """
    try:
        from pypdf import PdfReader
        page_count = len(PdfReader(template_path).pages)
        overlay_bytes = _build_overlay_bytes(page_count, fill_items)
        return _merge_overlay(template_path, overlay_bytes)
    except Exception as exc:
        logger.error("overlay: rebuild failed: %s", exc)
        with open(template_path, "rb") as f:
            return f.read()


def render_page_images(pdf_bytes: bytes, dpi: int = 120) -> list[str]:
    """
    Render each page of a PDF to a temporary PNG file.
    Returns a list of temp file paths (caller is responsible for cleanup).
    Returns [] if pypdfium2 is not available or rendering fails.
    """
    import tempfile
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("render_page_images: pypdfium2 not installed")
        return []

    try:
        doc = pdfium.PdfDocument(pdf_bytes)
        scale = dpi / 72.0
        paths: list[str] = []
        for i in range(len(doc)):
            pdf_page = doc[i]
            bitmap = pdf_page.render(scale=scale)
            pil_img = bitmap.to_pil()
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            pil_img.save(tmp.name, "PNG")
            tmp.close()
            paths.append(tmp.name)
        return paths
    except Exception as exc:
        logger.error("render_page_images: failed: %s", exc)
        return []
