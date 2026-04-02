# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Robust, locally-executed AI extraction pipeline that reads document text (OCR) 
# and suggests structured database additions into the AI Inbox. 
# Handles JSON formatting fallbacks (like regex and ast.literal_eval) to gracefully 
# process conversational outputs from small LLMs (like Phi-3).
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
Return ONLY a valid JSON array. Each item must have:
  - "field_key": exactly one of the known keys below
  - "value": extracted value as a string OR a nested JSON object (for lists)
  - "confidence": float 0.0-1.0

Known keys:
patient.name, patient.phone, patient.email, patient.address (include street, apt/unit if present, city, state, zip), allergyintolerance.list, medicationstatement.current_list, insurance.list, procedures.list, conditions.list

EXAMPLE OUTPUT FORMAT:
[
  {{"field_key": "patient.address", "value": "1210 Cullen Dr, Apt 4B, Forks, WA 98331", "confidence": 0.9}},
  {{"field_key": "allergyintolerance.list", "value": {{"substance": "Penicillin", "reaction": "Hives", "severity": "High"}}, "confidence": 0.9}},
  {{"field_key": "medicationstatement.current_list", "value": {{"name": "Lisinopril", "dose": "10mg", "frequency": "Daily"}}, "confidence": 0.95}}
]

Document:
\"\"\"
{text}
\"\"\"

Return ONLY valid JSON. Your output must strictly match the format of the brackets and quotes in the example above.
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
        raw = str(raw).strip()
        
        # Phi-3 likes to be conversational. Isolate the JSON array strictly using regex.
        import re
        match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
        if match:
            raw = match.group(0)
        else:
            raw = "[]"  # Graceful fallback if the LLM utterly failed to provide JSON.
            
        try:
            candidates = json.loads(raw)
        except json.JSONDecodeError:
            # Phi-3 often mistakenly uses single quotes in its Python/JSON outputs. Json.loads crashes.
            import ast
            try:
                # ast.literal_eval crashes on JSON literals like null, true, false. 
                # Replace them with Python equivalents safely before evaluating.
                safe_raw = raw.replace("null", "None").replace("true", "True").replace("false", "False")
                candidates = ast.literal_eval(safe_raw)
            except Exception:
                candidates = []
        if not isinstance(candidates, list):
            candidates = []
            
        # Natively serialize nested dictionaries/lists if the AI used complex objects for list structures
        for item in candidates:
            if isinstance(item.get("value"), (dict, list)):
                item["value"] = json.dumps(item["value"])

        # Explode multi-item list values into individual suggestions so each
        # item goes through conflict detection independently.
        # (e.g. LLM returns 3 allergies as one array → 3 separate suggestions)
        _exploded = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            fk = str(item.get("field_key", ""))
            val_str = str(item.get("value", "")).strip()
            if ".list" in fk or "current_list" in fk:
                try:
                    parsed = json.loads(val_str)
                    if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
                        for sub in parsed:
                            _exploded.append({
                                "field_key": fk,
                                "value": json.dumps(sub),
                                "confidence": item.get("confidence", 0.5),
                            })
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            _exploded.append(item)
        candidates = _exploded
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

        # Skip list items where all meaningful fields are empty/none
        # (LLM sometimes outputs placeholder objects with no real data)
        if ".list" in field_key or "_list" in field_key:
            try:
                val_obj = json.loads(value)
                if isinstance(val_obj, dict):
                    meaningful = {k: v for k, v in val_obj.items()
                                  if v and str(v).strip() and str(v).lower() != "none"
                                  and not k.startswith("_")}
                    if not meaningful:
                        continue
            except (json.JSONDecodeError, TypeError):
                pass

        conflict = False
        existing_value = None

        if field_key in existing:
            ex_val, ex_source = existing[field_key]
            
            # Silently drop suggestions that exactly match existing verified data
            if ex_val and str(ex_val).strip().lower() == value.lower():
                continue

            # Default flag for generic fields
            if ex_source == "user" and ex_val and ex_val != value:
                conflict = True
                existing_value = ex_val
                
                # Boost confidence if the document is a clinical record
                if clinical:
                    confidence = min(confidence + 0.2, 1.0)

            # SMART List Handling: Don't conflict if we are just appending a NEW item
            if (".list" in field_key or "_list" in field_key) and ex_val:
                try:
                    ex_list = json.loads(ex_val)
                    new_obj = json.loads(value)
                    
                    if isinstance(ex_list, list) and isinstance(new_obj, dict):
                        # Find the defining key depending on the list type
                        pk = "name"
                        if "allergy" in field_key: pk = "substance"
                        if "insurance" in field_key: pk = "provider"
                        
                        new_item_name = str(new_obj.get(pk, "")).strip().lower()
                        
                        if new_item_name:
                            # Search the existing list for this precise item
                            matched = None
                            for existing_item in ex_list:
                                if isinstance(existing_item, dict) and str(existing_item.get(pk, "")).strip().lower() == new_item_name:
                                    matched = existing_item
                                    break
                            
                            if matched:
                                # Allergies: same substance with new reaction/severity
                                # info is an enrichment, not a conflict — let it merge.
                                if "allergy" in field_key:
                                    conflict = False
                                    existing_value = json.dumps(matched)
                                else:
                                    # Other lists (meds, etc.): same PK is a real conflict
                                    conflict = True
                                    existing_value = json.dumps(matched)
                            else:
                                # It's a completely NEW item! It's just an append, no conflict.
                                conflict = False
                                existing_value = None
                except Exception:
                    pass

        suggestions.append({
            "field_key": field_key,
            "value": value,
            "confidence": confidence,
            "source_file_name": source_file_name,
            "conflict": conflict,
            "existing_value": existing_value,
        })

    return suggestions
