# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Structured field extraction from document text via prompt → LLM.
#
# Returns a list of extraction suggestions. NEVER persists anything —
# the caller (UI layer) handles the approve / edit / persist flow.
#
# Provenance conflict detection:
#   If an existing user-entered field value exists and the AI extracts the
#   same field from a document that contains clinical/surgical language,
#   the suggestion is flagged as a potential conflict for the caller to surface
#   as a dialog. Self-reported data is NEVER silently overwritten.
#
# Public API:
#   extract_fields(conn, patient_id, text, source_file_name, llm=None)
#       -> list[Suggestion]
#
# Suggestion is a TypedDict:
#   {
#     "field_key":         str,
#     "value":             str,
#     "confidence":        float,   # 0.0–1.0
#     "source_file_name":  str,
#     "conflict":          bool,
#     "existing_value":    str | None,
#   }
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Keywords that indicate a clinical/procedural source, raising confidence for
# date fields when a user-entered value already exists.
_CLINICAL_KEYWORDS = frozenset([
    "operative", "surgery", "procedure", "incision", "anesthesia",
    "post-op", "preoperative", "surgical", "operation report",
])

_EXTRACTION_PROMPT_TEMPLATE = """\
Extract structured medical fields from the following document excerpt.
Return a JSON array. Each item must have:
  - "field_key": one of the known keys below
  - "value": extracted value as a string
  - "confidence": float 0.0-1.0

Known field keys:
  patient.phone, patient.email, patient.address,
  allergyintolerance.list, medicationstatement.current_list,
  insurance.list, surgery.date, surgery.name, condition.name,
  diagnosis.date, medication.name, medication.dose

Document:
\"\"\"
{text}
\"\"\"

Return ONLY valid JSON. No prose before or after.
"""


def _is_clinical_source(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in _CLINICAL_KEYWORDS)


def extract_fields(
    conn,
    patient_id: int,
    text: str,
    source_file_name: str,
    llm=None,
) -> list[dict]:
    """
    Run structured extraction on a text excerpt.

    Returns a list of suggestion dicts (see module docstring).
    Does not write to the database.
    """
    if llm is None:
        from ai.backend import get_llm
        llm = get_llm()

    prompt = _EXTRACTION_PROMPT_TEMPLATE.format(text=text[:4000])

    try:
        raw = llm.complete(prompt).text
        # Strip any markdown fences the model may add
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        candidates = json.loads(raw)
        if not isinstance(candidates, list):
            candidates = []
    except Exception as exc:
        logger.warning("Extraction parse failed (%s): %s", source_file_name, exc)
        return []

    # Fetch existing user-entered values for conflict detection
    cur = conn.cursor()
    cur.execute(
        "SELECT field_key, value_text, source FROM patient_field_values WHERE patient_id=?",
        (patient_id,),
    )
    existing = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    clinical = _is_clinical_source(text)
    suggestions = []

    for item in candidates:
        if not isinstance(item, dict):
            continue
        field_key = item.get("field_key", "").strip()
        value = str(item.get("value", "")).strip()
        confidence = float(item.get("confidence", 0.5))
        if not field_key or not value:
            continue

        conflict = False
        existing_value = None

        if field_key in existing:
            ex_val, ex_source = existing[field_key]
            if ex_source == "user" and ex_val and ex_val != value:
                # Boost confidence if the document is a clinical record
                if clinical:
                    confidence = min(confidence + 0.2, 1.0)
                conflict = True
                existing_value = ex_val

        suggestions.append({
            "field_key": field_key,
            "value": value,
            "confidence": confidence,
            "source_file_name": source_file_name,
            "conflict": conflict,
            "existing_value": existing_value,
        })

    return suggestions
