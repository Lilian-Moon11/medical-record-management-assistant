# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Deterministic post-processing pipeline for AI-extracted medical data.
# Handles deduplication, reclassification, noise filtering, and cross-reference
# validation. Separated from extraction.py to keep the codebase maintainable.
#
# Public API:
#   explode_and_deduplicate(candidates) -> list[dict]
#   post_process(candidates, chunk_substance_counts=None, total_chunks=0) -> list[dict]
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Only accept these field_key values — reject anything the LLM invents
VALID_FIELD_KEYS = frozenset([
    "core.name", "patient.phone", "patient.email", "patient.address",
    "allergyintolerance.list", "medicationstatement.current_list",
    "insurance.list", "procedures.list", "conditions.list",
    "vitals.list", "lab_results.list", "providers.list",
    "immunization.list", "family_history.list", "social_history.list",
])

# Values that indicate "no data" — exact-match only.
# These are checked with == so 'normal' won't accidentally filter
# 'Normal pressure hydrocephalus' or 'less' won't filter 'Restless leg'.
_EMPTY_EXACT = frozenset([
    "na", "n/a", "none", "null", "unknown", "normal", "unremarkable",
    "pending", "less", "less than", "see above", "see below",
])

# Longer phrases safe for substring matching — unambiguous in any context.
_EMPTY_SUBSTRING = (
    "not specified", "not provided", "not available",
    "no data", "no results",
    "email not provided", "phone number not provided",
    "full address not provided", "payer not provided",
)

# Vitals whitelist — only these measurement names are valid vitals
_VITALS_NAMES = frozenset([
    "blood pressure", "systolic", "diastolic",
    "heart rate", "pulse", "hr",
    "respiratory rate", "rr", "respirations",
    "temperature", "temp",
    "weight", "height", "bmi", "body mass index",
    "o2 saturation", "oxygen saturation", "spo2", "o2 sat",
    "phq-9", "phq9", "phq 9",
    "gad-7", "gad7", "gad 7",
    "audit-c", "audit c", "auditc",
    "bsa", "body surface area",
])

# Vaccine keywords for immunization validation
_VACCINE_KEYWORDS = frozenset([
    "vaccine", "vaccination", "immunization", "shot", "booster",
    "flu", "influenza", "tdap", "tetanus", "diphtheria", "pertussis",
    "hpv", "gardasil", "mmr", "measles", "mumps", "rubella",
    "hepatitis", "hep a", "hep b", "polio", "ipv", "opv",
    "varicella", "chickenpox", "shingles", "zoster",
    "covid", "pfizer", "moderna", "janssen", "pneumococcal",
    "meningococcal", "rotavirus", "prevnar", "pneumovax",
    "dtap", "td", "bcg", "typhoid", "rabies", "anthrax",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_empty_phrase(text: str) -> bool:
    """Check if text is an empty/placeholder phrase.

    Uses exact matching for short ambiguous words ('normal', 'less',
    'pending') so they don't accidentally filter real clinical terms
    like 'Normal pressure hydrocephalus' or 'Restless leg syndrome'.
    Longer unambiguous phrases use substring matching.
    """
    t = text.strip().lower()
    if not t:
        return True
    if t in _EMPTY_EXACT:
        return True
    return any(phrase in t for phrase in _EMPTY_SUBSTRING)


def _normalize_dedup_key(key: str) -> str:
    """Normalize a dedup key by stripping parentheticals, hyphens, qualifiers."""
    key = re.sub(r'\s*\(.*?\)', '', key)
    key = re.sub(r'[-_]', ' ', key)
    _QUALIFIERS = (
        ", unspecified type", ", unspecified", ", type 2", ", type 1",
        "without hematuria", "with hematuria",
        "without complication", "with complication",
    )
    key_lower = key.lower()
    for q in _QUALIFIERS:
        key_lower = key_lower.replace(q, "")
    key = key_lower
    key = re.sub(r'\s+', ' ', key)
    return key.strip().lower()


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

# Primary key mapping for list-type fields
_PK_MAP = {
    "allergy": "substance",
    "intolerance": "substance",
    "medication": "name",
    "condition": "name",
    "procedure": "name",
    "vital": "_name_value",
    "lab_result": "_name_value",
    "provider": "clinic",
    "immunization": "immunization",
    "insurance": "payer",
    "family_history": "_relation_condition",
}


def _get_pk(field_key: str, val_dict: dict) -> str | None:
    """Return the primary-key value for dedup, or None if not a list item."""
    fk_lower = field_key.lower()
    for pattern, pk_field in _PK_MAP.items():
        if pattern in fk_lower:
            if pk_field == "_name_value":
                name = str(val_dict.get("name", "")).strip().lower()
                v = str(val_dict.get("value", val_dict.get("value_text", ""))).strip().lower()
                return _normalize_dedup_key(f"{name}|{v}") if name else None
            if pk_field == "_relation_condition":
                relation = str(val_dict.get("relation", "")).strip().lower()
                condition = str(val_dict.get("condition", "")).strip().lower()
                return _normalize_dedup_key(f"{relation}|{condition}") if relation else None
            pk_val = str(val_dict.get(pk_field, "")).strip().lower()
            if not pk_val and pattern == "provider":
                pk_val = str(val_dict.get("name", "")).strip().lower()
            return _normalize_dedup_key(pk_val) if pk_val else None
    name = str(val_dict.get("name", "")).strip().lower()
    return _normalize_dedup_key(name) if name else None


def _detail_score(val_dict: dict, fk: str = "") -> int:
    """Count non-empty, meaningful fields as a richness score.
    For vitals, precise values (with decimals) get a slight tiebreaker.
    """
    score = 0
    for k, v in val_dict.items():
        v_str = str(v).strip()
        if v_str and v_str.lower() not in ("none", "null", "n/a", "unknown", ""):
            score += 10
            # Tiebreaker for precise vital values
            if fk == "vitals.list" and k == "value" and "." in v_str:
                score += 2
    return score


def explode_and_deduplicate(candidates: list[dict]) -> list[dict]:
    """Explode multi-item list values into individual suggestions and deduplicate."""
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

    _exploded_unique = []
    _pk_index: dict[tuple[str, str], int] = {}
    _seen_scalars: set[tuple[str, str]] = set()

    for item in _exploded:
        fk = str(item.get("field_key", ""))
        val = str(item.get("value", "")).strip()

        if ".list" in fk or "_list" in fk:
            try:
                parsed_val = json.loads(val.lower()) if val.startswith("{") else None
            except Exception:
                parsed_val = None

            if isinstance(parsed_val, dict):
                pk_val = _get_pk(fk, parsed_val)
                if pk_val:
                    dedup_key = (fk, pk_val)
                    if dedup_key in _pk_index:
                        existing_idx = _pk_index[dedup_key]
                        existing_item = _exploded_unique[existing_idx]
                        try:
                            existing_parsed = json.loads(str(existing_item.get("value", "")).lower())
                        except Exception:
                            existing_parsed = {}
                        if _detail_score(parsed_val, fk) > _detail_score(existing_parsed, fk):
                            _exploded_unique[existing_idx] = item
                        continue
                    else:
                        _pk_index[dedup_key] = len(_exploded_unique)
                        _exploded_unique.append(item)
                        continue

        scalar_key = (fk, val.lower())
        if scalar_key not in _seen_scalars:
            _seen_scalars.add(scalar_key)
            _exploded_unique.append(item)

    # --- Secondary dedup pass: substring consolidation ---
    _to_remove: set[int] = set()
    _fk_groups: dict[str, list[tuple[int, str]]] = {}
    for idx, item in enumerate(_exploded_unique):
        fk = str(item.get("field_key", ""))
        if not (".list" in fk or "_list" in fk):
            continue
        val = str(item.get("value", "")).strip()
        try:
            pv = json.loads(val.lower()) if val.startswith("{") else None
        except Exception:
            pv = None
        if isinstance(pv, dict):
            pk = _get_pk(fk, pv)
            if pk and len(pk) > 2:
                _fk_groups.setdefault(fk, []).append((idx, pk))

    for fk, entries in _fk_groups.items():
        if "vital" in fk:
            continue
        for i, (idx_a, pk_a) in enumerate(entries):
            if idx_a in _to_remove:
                continue
            for j, (idx_b, pk_b) in enumerate(entries):
                if i == j or idx_b in _to_remove:
                    continue
                if pk_a in pk_b or pk_b in pk_a:
                    item_a = _exploded_unique[idx_a]
                    item_b = _exploded_unique[idx_b]
                    try:
                        pa = json.loads(str(item_a.get("value", "")).lower())
                        pb = json.loads(str(item_b.get("value", "")).lower())
                    except Exception:
                        continue
                    if _detail_score(pa) >= _detail_score(pb):
                        _to_remove.add(idx_b)
                    else:
                        _to_remove.add(idx_a)
                        break

    if _to_remove:
        _exploded_unique = [
            item for idx, item in enumerate(_exploded_unique)
            if idx not in _to_remove
        ]

    return _exploded_unique


# ---------------------------------------------------------------------------
# Post-processing filters
# ---------------------------------------------------------------------------

def post_process(
    candidates: list[dict],
    chunk_item_counts: dict[str, int] | None = None,
    total_chunks: int = 0,
) -> list[dict]:
    """Clean up LLM misclassifications and filter noise.

    Args:
        candidates: Deduplicated extraction candidates.
        chunk_substance_counts: Maps lowercased allergy substance -> number of
            chunks that produced it.  Used for consensus filtering.
        total_chunks: Total number of chunks the document was split into.
    """

    # --- Pre-scan: collect cross-reference data for later filters ---
    _patient_names_lower = set()
    _medication_names_lower = set()
    _patient_conditions_lower = set()
    for _pre_item in candidates:
        _pre_fk = str(_pre_item.get("field_key", "")).strip()
        _pre_val = str(_pre_item.get("value", "")).strip()
        if _pre_fk == "core.name" and _pre_val:
            _patient_names_lower.add(_pre_val.lower())
            for part in re.split(r'[,\s]+', _pre_val):
                part = part.strip().lower()
                if len(part) > 2:
                    _patient_names_lower.add(part)
        elif _pre_fk == "medicationstatement.current_list" and _pre_val.startswith("{"):
            try:
                _pre_med = json.loads(_pre_val)
                _med_name = str(_pre_med.get("name", "")).strip().lower()
                if _med_name:
                    _medication_names_lower.add(_med_name)
                    _norm = re.sub(r'\s*\(.*?\)', '', _med_name).strip()
                    if _norm:
                        _medication_names_lower.add(_norm)
            except Exception:
                pass
        elif _pre_fk == "conditions.list" and _pre_val.startswith("{"):
            try:
                _pre_cond = json.loads(_pre_val)
                _cond_name = str(_pre_cond.get("name", "")).strip().lower()
                if _cond_name:
                    _patient_conditions_lower.add(_cond_name)
            except Exception:
                pass

    _seen_patient_name = False
    _seen_patient_address = False
    _seen_patient_phone = False
    _seen_patient_email = False
    cleaned = []

    for item in candidates:
        if not isinstance(item, dict):
            continue
        fk = str(item.get("field_key", "")).strip()
        val = str(item.get("value", "")).strip()

        # 1. Reject unknown field keys
        if fk not in VALID_FIELD_KEYS:
            continue

        # 2. Parse the value for list items to inspect fields
        parsed = None
        if ".list" in fk or "_list" in fk:
            try:
                parsed = json.loads(val) if val.startswith("{") else None
            except Exception:
                parsed = None

        # --- Reclassification rules ---

        # R1. Conditions -> Procedures: surgical terms misclassified as conditions
        if fk == "conditions.list" and parsed:
            cond_name = str(parsed.get("name", "")).strip().lower()
            _SURGICAL_TERMS = (
                "tubal ligation", "appendectomy", "cholecystectomy",
                "hysterectomy", "cesarean", "c-section", "tonsillectomy",
                "vasectomy", "biopsy", "excision", "lumpectomy",
                "mastectomy", "arthroscopy", "laparoscopy",
            )
            if any(st in cond_name for st in _SURGICAL_TERMS):
                item["field_key"] = "procedures.list"
                fk = "procedures.list"
                parsed["date"] = parsed.pop("onset_date", "")
                parsed.pop("symptoms", None)
                item["value"] = json.dumps(parsed)
                val = item["value"]

        # R2. Procedures -> Labs: imaging/diagnostic results belong in lab_results
        if fk == "procedures.list" and parsed:
            proc_name = str(parsed.get("name", "")).strip().lower()
            _IMAGING_TERMS = (
                "x-ray", "xray", "x ray", "mri", "ct scan", "ct ",
                "ultrasound", "sonogram", "mammogram", "dexa",
                "bone density", "echocardiogram", "ekg", "eeg",
            )
            if any(it in proc_name for it in _IMAGING_TERMS):
                item["field_key"] = "lab_results.list"
                fk = "lab_results.list"
                if "name" in parsed and "test_name" not in parsed:
                    parsed["test_name"] = parsed.pop("name")
                parsed.pop("surgeon", None)
                parsed.pop("facility", None)
                item["value"] = json.dumps(parsed)
                val = item["value"]

        # R3. Procedures -> Labs: cultures and blood tests belong in labs
        if fk == "procedures.list" and parsed:
            proc_name = str(parsed.get("name", "")).strip().lower()
            _LAB_PROC_TERMS = ("culture", "urine", "blood test", "panel", "swab", "screen")
            if any(lt in proc_name for lt in _LAB_PROC_TERMS):
                item["field_key"] = "lab_results.list"
                fk = "lab_results.list"
                if "name" in parsed and "test_name" not in parsed:
                    parsed["test_name"] = parsed.pop("name")
                parsed.pop("surgeon", None)
                parsed.pop("facility", None)
                item["value"] = json.dumps(parsed)
                val = item["value"]

        # R4. Medications -> Route: move route terms from frequency to route
        if fk == "medicationstatement.current_list" and parsed:
            freq = str(parsed.get("frequency", "")).strip()
            freq_lower = freq.lower()
            if freq_lower:
                _ROUTE_TERMS = (
                    "oral", "subcutaneous", "intramuscular", "intravenous", "iv ",
                    "topical", "sublingual", "rectal", "ophthalmic", "otic", "nasal",
                    "inhaled", "inhalation", "transdermal", "by mouth", "po"
                )
                if freq_lower in _ROUTE_TERMS:
                    parsed["route"] = freq
                    parsed["frequency"] = ""
                    item["value"] = json.dumps(parsed)
                    val = item["value"]
                else:
                    for rt in _ROUTE_TERMS:
                        if rt in freq_lower:
                            if not parsed.get("route"):
                                parsed["route"] = rt.capitalize() if len(rt) > 3 else rt.upper()
                            parsed["frequency"] = re.sub(rf'\b{re.escape(rt)}\b', '', freq, flags=re.IGNORECASE).strip()
                            item["value"] = json.dumps(parsed)
                            val = item["value"]
                            break

        # R4b. Medications -> Route: also rescue route terms from notes field
        if fk == "medicationstatement.current_list" and parsed:
            notes = str(parsed.get("notes", "")).strip()
            notes_lower = notes.lower()
            if notes_lower and not parsed.get("route"):
                _ROUTE_TERMS_NOTES = (
                    "oral", "subcutaneous", "intramuscular", "intravenous",
                    "topical", "sublingual", "rectal", "ophthalmic", "otic", "nasal",
                    "inhaled", "inhalation", "transdermal", "by mouth",
                )
                for rt in _ROUTE_TERMS_NOTES:
                    if rt in notes_lower:
                        parsed["route"] = rt.capitalize()
                        # Remove the route term from notes
                        parsed["notes"] = re.sub(
                            rf'\b{re.escape(rt)}\b', '', notes, flags=re.IGNORECASE
                        ).strip().strip(",").strip()
                        item["value"] = json.dumps(parsed)
                        val = item["value"]
                        break

        # R5. Medications -> Shorthand normalization: expand abbreviations
        # to plain English (e.g. "bid" → "Twice daily", "PO" → "By mouth")
        if fk == "medicationstatement.current_list" and parsed:
            from utils.medical_abbreviations import normalize_medical_shorthand
            _changed = False
            for _mf in ("frequency", "route", "dose"):
                _mval = str(parsed.get(_mf, "")).strip()
                if _mval:
                    _normalized = normalize_medical_shorthand(_mval)
                    if _normalized != _mval:
                        parsed[_mf] = _normalized
                        _changed = True
            if _changed:
                item["value"] = json.dumps(parsed)
                val = item["value"]

        # R6. Medications -> Name cleanup: strip dose/strength from the name
        # when the LLM puts "Lisinopril 10mg" in name instead of just "Lisinopril"
        if fk == "medicationstatement.current_list" and parsed:
            med_name = str(parsed.get("name", "")).strip()
            if med_name:
                # Match trailing dose patterns:
                # "Lisinopril 10mg", "Metformin 500 mg", "Amoxicillin 250mg/5ml",
                # "Ibuprofen 200 mg tablet", "Aspirin 81mg"
                _dose_pattern = re.compile(
                    r'\s+(\d+\.?\d*\s*(?:mg|mcg|ml|mL|meq|iu|g|%|units?)'
                    r'(?:\s*/\s*\d+\.?\d*\s*(?:mg|mcg|ml|mL|meq|iu|g|%|units?))?'
                    r'(?:\s+(?:tablet|tablets|tab|capsule|capsules|cap|oral|solution|suspension))?)$',
                    re.IGNORECASE,
                )
                _dose_match = _dose_pattern.search(med_name)
                if _dose_match:
                    extracted_dose = _dose_match.group(1).strip()
                    cleaned_name = med_name[:_dose_match.start()].strip()
                    if cleaned_name:  # safety: don't blank the name
                        parsed["name"] = cleaned_name
                        # If dose field is empty, move the extracted dose there
                        if not str(parsed.get("dose", "")).strip():
                            parsed["dose"] = extracted_dose
                        item["value"] = json.dumps(parsed)
                        val = item["value"]

        # --- Standard filters ---

        # 3. Reject if the primary value is an empty phrase
        if parsed and isinstance(parsed, dict):
            primary_val = (
                parsed.get("value") or parsed.get("value_text") or
                parsed.get("name") or parsed.get("substance") or
                parsed.get("immunization") or parsed.get("payer") or
                parsed.get("relation") or ""
            )
            
            # Reject exact hallucinated placeholders from prompt examples
            if str(primary_val).strip().lower() in ("john doe", "jane doe", "yyyy-mm-dd"):
                continue
            if str(parsed.get("date", "")).strip() in ("2023-01-01", "YYYY-MM-DD", "19xx", "20xx"):
                continue

            if _is_empty_phrase(str(primary_val).strip()):
                continue
            meaningful_count = 0
            for k, v in parsed.items():
                v_str = str(v).strip()
                if v_str and not _is_empty_phrase(v_str):
                    if not set(v_str) <= {"_", "-"}:
                        meaningful_count += 1
            if meaningful_count == 0:
                continue
        elif not parsed:
            if _is_empty_phrase(val):
                continue

        # 4. Vitals: only allow recognized vital sign names
        if fk == "vitals.list" and parsed:
            vital_name = str(parsed.get("name", "")).strip().lower()
            if not any(vn in vital_name for vn in _VITALS_NAMES):
                continue
            vital_value = str(parsed.get("value", parsed.get("value_text", ""))).strip()
            if _is_empty_phrase(vital_value):
                continue
            # Blood pressure must have systolic/diastolic (e.g. "120/80")
            if "blood pressure" in vital_name or "bp" == vital_name:
                if "/" not in vital_value:
                    continue  # reject systolic-only readings
            # Screening scores (PHQ-9, GAD-7, AUDIT-C): validate that the
            # value is a valid integer within the known scoring range.
            # Also require high confidence (>=0.90) since screening scores
            # are the most commonly hallucinated category — an explicitly
            # documented score like "PHQ-9: 14" yields high confidence,
            # while fabricated defaults (e.g. 0) tend to be lower.
            _SCREENING_RANGES = {
                "phq": (0, 27), "gad": (0, 21), "audit": (0, 40),
            }
            _is_screening = False
            for prefix, (lo, hi) in _SCREENING_RANGES.items():
                if prefix in vital_name:
                    _is_screening = True
                    try:
                        score_val = int(vital_value)
                    except (ValueError, TypeError):
                        score_val = -1  # force rejection
                    if score_val < lo or score_val > hi:
                        break  # reject
                    break  # valid — stop checking prefixes
            else:
                _is_screening = False  # no prefix matched
            if _is_screening:
                if score_val < lo or score_val > hi:
                    continue
                # Require high confidence to prevent hallucinated scores
                try:
                    item_conf = float(item.get("confidence", 0))
                except (ValueError, TypeError):
                    item_conf = 0.0
                if item_conf < 0.90:
                    continue
            # Clear noise flags (e.g. BMI "Normal") — keep "abnormal" since
            # it carries real clinical meaning (e.g. bradycardia, tachycardia)
            vflag = str(parsed.get("abnormal_flag", "")).strip().lower()
            if vflag in ("normal", "n"):
                parsed["abnormal_flag"] = ""
                item["value"] = json.dumps(parsed)
                val = item["value"]

            # 4b. Normalize weight/height/temperature to canonical metric units
            # so dedup works correctly across documents using different systems.
            vital_name = str(parsed.get("name", "")).strip().lower()
            if vital_name in ("weight", "height", "temperature"):
                from utils.unit_conversion import normalize_vital_to_metric
                parsed = normalize_vital_to_metric(parsed)
                item["value"] = json.dumps(parsed)
                val = item["value"]

        # 5. Lab results: must have actual result values
        if fk == "lab_results.list" and parsed:
            result_val = str(parsed.get("value_text", "")).strip()
            if not result_val or _is_empty_phrase(result_val):
                continue
            result_lower = result_val.lower()
            _ORDER_ONLY_TERMS = ("ordered", "pending", "in process",
                                 "collected", "received")
            if any(result_lower.startswith(ot) for ot in _ORDER_ONLY_TERMS):
                continue
            
            # Clear noise flags when no reference range is provided
            flag = str(parsed.get("abnormal_flag", "")).strip().lower()
            ref = str(parsed.get("ref_range_text", parsed.get("reference_range", ""))).strip()
            if flag in ("normal", "n") and not ref:
                parsed["abnormal_flag"] = ""
                item["value"] = json.dumps(parsed)
                val = item["value"]

        # 6. Immunizations: must contain a vaccine-related term
        if fk == "immunization.list" and parsed:
            # LLM sometimes outputs "name" instead of "immunization"
            imm_name = str(parsed.get("immunization", "") or parsed.get("name", "")).strip().lower()
            if parsed.get("name") and not parsed.get("immunization"):
                parsed["immunization"] = parsed.pop("name")
                item["value"] = json.dumps(parsed)
                val = item["value"]
            if not imm_name or not any(vk in imm_name for vk in _VACCINE_KEYWORDS):
                continue

        # 7. Insurance: reject MRNs, hospital names, placeholders, and provider misclassifications
        if fk == "insurance.list" and parsed:
            payer = str(parsed.get("payer", "")).strip().lower()
            member_id = str(parsed.get("member_id", "")).strip()
            group_no = str(parsed.get("group_no", "")).strip()
            ins_phone = str(parsed.get("phone", "")).strip()
            ins_notes = str(parsed.get("notes", "")).strip().lower()
            if _is_empty_phrase(payer):
                continue
            # Reject hospital/provider names misclassified as insurance payers
            _PROVIDER_TERMS = (
                "hospital", "medical center", "medical record", "mrn",
                "clinic", "health system", "health center", "primary",
                "physician", "associates", "specialists",
            )
            if any(term in payer for term in _PROVIDER_TERMS):
                continue
            # Reject common placeholder / hallucinated values
            _PLACEHOLDER_IDS = ("123456789", "000000000", "999999999", "12345", "00000")
            _PLACEHOLDER_PHONES = ("555-555-5555", "5555555555", "000-000-0000")
            if member_id in _PLACEHOLDER_IDS or group_no in _PLACEHOLDER_IDS:
                continue
            if ins_phone in _PLACEHOLDER_PHONES:
                parsed["phone"] = ""
                item["value"] = json.dumps(parsed)
                val = item["value"]
            # Reject if notes contain clinical diagnoses (not insurance info)
            _CLINICAL_NOTE_TERMS = (
                "diagnosis", "dx:", "icd", "presenting",
                "dysuria", "hypertension", "diabetes", "anxiety",
            )
            if any(ct in ins_notes for ct in _CLINICAL_NOTE_TERMS):
                continue

        # 8. Patient address: must contain a digit
        if fk == "patient.address":
            if not any(c.isdigit() for c in val):
                continue

        # 9. Allergies: filter nonsensical substances and misclassifications
        if fk == "allergyintolerance.list" and parsed:
            substance = str(parsed.get("substance", "")).strip().lower()
            reaction = str(parsed.get("reaction", "")).strip().lower()
            notes = str(parsed.get("notes", "")).strip().lower()
            _NON_ALLERGY_TERMS = frozenset([
                "condom", "condoms", "alcohol", "recreational drugs",
                "recreational drug", "sex", "unsafe sex", "unprotected sex",
                "smokeless tobacco", "tobacco", "smoking",
            ])
            if substance in _NON_ALLERGY_TERMS:
                continue

            _MED_NOTES_INDICATORS = (
                "birth control", "method of birth control",
                "patient reports using", "contraception",
                "patient-reported medication", "past medical history of",
            )
            if any(mi in notes for mi in _MED_NOTES_INDICATORS):
                continue

            _NON_REACTION_PHRASES = (
                "no known", "contraception", "monitoring",
                "birth control", "protection",
            )
            if any(nr in reaction for nr in _NON_REACTION_PHRASES):
                continue

            _CONDITION_INDICATORS = (
                "syndrome", "disease", "disorder", "itis",
                "osis", "emia", "pathy", "algia", "opia",
                "hypertension", "hypothyroid", "diabetes",
                "anxiety", "depression", "asthma", "obesity",
                "vaginosis", "candidiasis", "hemorrhoid",
            )
            if any(ci in substance for ci in _CONDITION_INDICATORS):
                continue
            _MED_ROUTE_INDICATORS = (
                "tablet", "capsule", "spray", "inhaler", "injection",
                "mg", "mcg", "ml", "daily", "twice", "oral",
                "nasal", "topical", "cream", "ointment",
            )
            if any(mi in reaction for mi in _MED_ROUTE_INDICATORS):
                continue

            notes_stripped = notes.strip()
            if notes_stripped and notes_stripped.replace(".", "").isdigit():
                continue

        # 10. Allergy cross-reference + valid reaction check
        if fk == "allergyintolerance.list" and parsed:
            substance = str(parsed.get("substance", "")).strip().lower()
            substance_norm = re.sub(r'\s*\(.*?\)', '', substance).strip()
            if substance_norm in _medication_names_lower or substance in _medication_names_lower:
                continue
            reaction = str(parsed.get("reaction", "")).strip().lower()
            _VALID_REACTIONS = (
                "rash", "hives", "anaphylaxis", "swelling", "itching", "itchy",
                "urticaria", "angioedema", "shortness of breath", "wheezing",
                "nausea", "vomiting", "diarrhea", "stomach", "gi upset",
                "allergic reaction", "allergy", "hypersensitivity", "sensitivity",
                "skin reaction", "dermatitis", "eczema", "blistering", "peeling",
                "throat swelling", "tongue swelling", "difficulty breathing",
            )
            if reaction and not any(vr in reaction for vr in _VALID_REACTIONS):
                continue

        # 10c. Chunk consensus: require allergy, medication, or vital to appear in 2+ chunks for
        #      documents with 10+ chunks. Eliminates single-source hallucinations
        if parsed and chunk_item_counts:
            if total_chunks >= 10:
                key_to_check = None
                if fk == "allergyintolerance.list":
                    key_to_check = str(parsed.get("substance", "")).strip().lower()
                elif fk == "medicationstatement.current_list":
                    key_to_check = str(parsed.get("name", "")).strip().lower()
                elif fk == "vitals.list":
                    key_to_check = str(parsed.get("name", "")).strip().lower()
                
                if key_to_check:
                    chunk_count = chunk_item_counts.get(key_to_check, 0)
                    if chunk_count < 2:
                        continue

        # 11. Conditions: reject vague symptom phrases and instructions
        if fk == "conditions.list" and parsed:
            cond_name = str(parsed.get("name", "")).strip().lower()
            if len(cond_name) > 60:
                continue
            _SYMPTOM_PHRASES = (
                "symptoms go away", "get help right away", "cannot keep down",
                "call your doctor", "go to the emergency", "seek medical",
                "return to", "follow up with", "come back if",
                "watch for", "look for signs", "contact your",
            )
            if any(sp in cond_name for sp in _SYMPTOM_PHRASES):
                continue

        # 12. Conditions: reject medication names misclassified as conditions
        if fk == "conditions.list" and parsed:
            cond_name = str(parsed.get("name", "")).strip().lower()
            cond_norm = re.sub(r'\s*\(.*?\)', '', cond_name).strip()
            if cond_norm in _medication_names_lower:
                continue
            _DOSAGE_TERMS = ("mg", "mcg", "ml", "tablet", "capsule",
                             "inhaler", "cream", "ointment", "patch")
            if any(f" {dt}" in cond_name or cond_name.endswith(dt) for dt in _DOSAGE_TERMS):
                continue

        # 13. Medications: reject non-drug entries
        if fk == "medicationstatement.current_list":
            if parsed:
                med_name = str(parsed.get("name", "")).strip().lower()
            else:
                med_name = val.strip().lower()
            _GENERIC_MED_NAMES = (
                "antibiotic medicines", "antifungal medicines",
                "antiviral medicines", "pain medicines", "medicine",
                "medicines", "medication", "medications",
            )
            if med_name in _GENERIC_MED_NAMES:
                continue
            _ADVICE_PHRASES = (
                "drinking enough", "drink plenty", "drink more",
                "over-the-counter", "over the counter", "otc medicines",
                "immune disorder", "health maintenance",
            )
            if any(ap in med_name for ap in _ADVICE_PHRASES):
                continue
            if len(med_name) > 50:
                continue
            if parsed:
                med_notes = str(parsed.get("notes", "")).strip()
                if len(med_notes) > 200:
                    continue
                # Strip discharge-instruction boilerplate from notes
                med_notes_lower = med_notes.lower()
                _DISCHARGE_PHRASES = (
                    "do not give", "do not take", "call your doctor",
                    "children under", "seek medical attention",
                    "if you experience", "go to the emergency",
                    "call 911", "contact your doctor", "stop taking",
                    "tell your doctor", "ask your doctor",
                    "if symptoms persist", "return to the emergency",
                    "do not use", "keep out of reach",
                )
                if any(dp in med_notes_lower for dp in _DISCHARGE_PHRASES):
                    parsed["notes"] = ""
                    item["value"] = json.dumps(parsed)
                    val = item["value"]

        # 14. Medications: reject screening/lab orders
        if fk == "medicationstatement.current_list":
            if parsed:
                med_name = str(parsed.get("name", "")).strip().lower()
                med_dose = str(parsed.get("dose", "")).strip().lower()
            else:
                med_name = val.strip().lower()
                med_dose = ""
            _LAB_INDICATORS = ("screening", "culture", "panel", "antigen",
                               "antibodies", "test ", "dipstick", "poct",
                               "specimen")
            if any(li in med_name for li in _LAB_INDICATORS):
                continue
            if med_dose in ("screening", "test", "lab"):
                continue

        # 15. Procedures: reject referrals and office visits
        if fk == "procedures.list" and parsed:
            proc_name = str(parsed.get("name", "")).strip().lower()
            _REFERRAL_TERMS = ("referral", "consult", "consultation",
                               "follow-up", "follow up", "office visit",
                               "appointment", "amb referral", "physical exam",
                               "well woman", "checkup", "check up")
            if any(rt in proc_name for rt in _REFERRAL_TERMS):
                continue
            _TITLE_PATTERNS = ("dr.", "dr ", ", md", ", do", ", np",
                               ", pa-c", ", rn", ", phd")
            if any(tp in proc_name for tp in _TITLE_PATTERNS):
                continue

        # 16. Providers: reject if name matches the patient
        if fk == "providers.list" and parsed:
            prov_name = str(parsed.get("name", "")).strip().lower()
            if prov_name and prov_name in _patient_names_lower:
                continue
            prov_parts = set(
                p.strip().lower() for p in re.split(r'[,\s]+', prov_name) if len(p.strip()) > 2
            )
            if prov_parts and prov_parts.issubset(_patient_names_lower):
                continue

        # 17. Family history: reject self-references and "no known" entries
        if fk == "family_history.list" and parsed:
            fh_notes = str(parsed.get("notes", "")).strip().lower()
            fh_condition = str(parsed.get("condition", "")).strip().lower()
            if "patient has" in fh_notes or "patient's" in fh_notes:
                continue
            if "patient family" in fh_notes:
                continue
            if _is_empty_phrase(fh_condition):
                continue
            if "no known" in fh_condition or "no known" in fh_notes:
                continue
            if fh_condition in _patient_conditions_lower:
                if any(w in fh_notes for w in ("patient", "past medical", "pmh")):
                    continue

        # 18. Demographics: singletons (address excluded — may have multiples)
        if fk == "core.name":
            if _seen_patient_name:
                continue
            _seen_patient_name = True

        if fk == "patient.phone":
            if _seen_patient_phone:
                continue
            _seen_patient_phone = True

        if fk == "patient.email":
            if _seen_patient_email:
                continue
            _seen_patient_email = True

        cleaned.append(item)

    return cleaned
