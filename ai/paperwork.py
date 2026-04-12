# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# AI-assisted PDF form field mapping for the Paperwork Wizard.
#
# Uses the offline Qwen2.5 LLM (via ai.backend.get_llm) to intelligently
# match patient health record data to the blank fields discovered in a PDF
# template. Designed to complement — not replace — the hardcoded UI-sourced
# mappings (signature, sign date, recipient) that the Wizard collects directly.
#
# Supports both use cases:
#   - Patient Intake Forms: demographics, DOB, address, insurance, allergies,
#     medications, conditions, emergency contact, etc.
#   - Release of Information (ROI) Forms: purpose, expiry, provider info,
#     referred fields that aren't captured from the UI dropdown.
#
# Public API:
#   map_pdf_fields(db_conn, patient_id, pdf_fields, field_limits=None, llm=None) -> dict
#
# Returns a dict where:
#   - Keys are a strict subset of the supplied `pdf_fields` list (hallucination
#     safeguard applied internally — no phantom keys ever reach PdfWrapper).
#   - Values are patient data strings, truncated to each field's own character
#     limit derived from the PDF schema's `maxLength` property (the /MaxLen PDF
#     attribute). Falls back to PDF_FIELD_DEFAULT_LIMIT for unlimited fields.
#   - Missing / unknown fields receive an empty string value.
# -----------------------------------------------------------------------------

from __future__ import annotations

import ast
import json
import logging
import re

logger = logging.getLogger(__name__)

# Fallback character limit for fields with no /MaxLen set in the PDF.
# When a form author sets no limit, they usually intend multi-line or
# large text areas — so we use a generous default rather than clipping.
PDF_FIELD_DEFAULT_LIMIT = 500

# ---------------------------------------------------------------------------
# Medical shorthand normalisation
# ---------------------------------------------------------------------------
# Common prescription abbreviations → plain-English equivalents.
# Applied when building the patient digest so forms expecting "Twice daily"
# don't receive the raw abbreviation "bid".
_MED_SHORTHAND = {
    "bid": "Twice daily", "b.i.d.": "Twice daily",
    "tid": "Three times daily", "t.i.d.": "Three times daily",
    "qd": "Once daily", "q.d.": "Once daily",
    "qid": "Four times daily", "q.i.d.": "Four times daily",
    "qhs": "At bedtime", "q.h.s.": "At bedtime",
    "prn": "As needed", "p.r.n.": "As needed",
    "qod": "Every other day", "q.o.d.": "Every other day",
    "ac": "Before meals", "a.c.": "Before meals",
    "pc": "After meals", "p.c.": "After meals",
    "q4h": "Every 4 hours", "q6h": "Every 6 hours",
    "q8h": "Every 8 hours", "q12h": "Every 12 hours",
    "po": "By mouth", "p.o.": "By mouth",
    "sl": "Sublingual", "s.l.": "Sublingual",
    "im": "Intramuscular", "i.m.": "Intramuscular",
    "iv": "Intravenous", "i.v.": "Intravenous",
    "sq": "Subcutaneous", "subq": "Subcutaneous",
    "od": "Right eye", "os": "Left eye", "ou": "Both eyes",
    "gtts": "Drops", "tab": "Tablet", "tabs": "Tablets",
    "cap": "Capsule", "caps": "Capsules",
    "mg": "mg", "mcg": "mcg", "ml": "mL",
}


def _normalize_frequency(text: str) -> str:
    """Replace medical shorthand anywhere in *text* with plain English."""
    if not text:
        return text
    # Whole-token replacement (case-insensitive, word-boundary aware)
    for abbr, full in _MED_SHORTHAND.items():
        pattern = re.compile(r'\b' + re.escape(abbr) + r'\b', re.IGNORECASE)
        text = pattern.sub(full, text)
    return text

_MAP_PROMPT_TEMPLATE = """\
You are a medical form assistant. A patient's structured health record is provided
below as a JSON object. Your task is to match the patient's information to the
exact PDF form fields listed.

Patient Record (JSON):
{digest}

PDF Form Fields to fill (use THESE EXACT strings as dictionary keys):
{fields_list}

Instructions:
- Output ONLY a valid JSON dictionary.
- Every key must be EXACTLY one of the PDF field names listed above.
- The value must be the patient's matching data as a plain string.
- If no patient data matches a field, set its value to an empty string "".
- Do NOT invent new keys. Do NOT add explanations outside the JSON.
- For text list fields (e.g. medications, allergies, conditions), format as a
  comma-separated plain text string.
- Keep each value concise — the form may have strict character limits.
- For fields marked [CHOOSE ONE OF: ...], your value MUST be exactly one of the
  listed options — no other text.
- For fields marked [CHECK: true/false], output true if the patient has this
  condition/property, false otherwise.
- For medication table columns ("Medication Name", "Dosage", "Frequency",
  "Reason for Taking", "Medication Name 2", etc.): fill the first row with
  the first listed medication's details, "Medication Name 2" with the second, etc.
  Leave numbered rows empty if there are fewer medications than rows.
- Recognise common medical form abbreviations when matching labels to data:
  DOB = Date of Birth, Pt = Patient, Ins = Insurance, Tel/Ph = Phone,
  Addr = Address, Emerg = Emergency, Rx = Prescription/Medication, Hx = History,
  Dx = Diagnosis, Sx = Symptoms, Sig = Signature, No/# = Number, Grp = Group.
  Match these to the closest patient data key.

Example output format:
{{"PatientName": "Jane Doe", "HasDiabetes": true, "Smoker": false, "Gender": "Female", "Allergies": "Penicillin, Latex"}}

Output ONLY the JSON dictionary. No preamble. No trailing text.
"""


# ---------------------------------------------------------------------------
# Address parsing
# ---------------------------------------------------------------------------

# Two-letter US state/territory abbreviations
_US_STATES = frozenset([
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
])

# Full state name → abbreviation map
_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

_PARENS_RE = re.compile(r'\(([A-Za-z]{2})\)')  # "Indiana(IN)" → group(1)="IN"
_ZIP_RE = re.compile(r'\b(\d{5}(?:-\d{4})?)\b')
_APT_RE = re.compile(
    r'(?:^|,\s*)((?:apt|apartment|unit|suite|ste|#)\s*[A-Za-z0-9\-]+)',
    re.IGNORECASE,
)


def _normalize_state(text: str) -> str:
    """Return 2-letter state abbreviation if *text* looks like a US state."""
    cleaned = text.strip().rstrip(".,")
    # Direct abbreviation: "WA", "IN"
    if cleaned.upper() in _US_STATES:
        return cleaned.upper()
    # Parenthetical: "Indiana(IN)"
    pm = _PARENS_RE.search(cleaned)
    if pm and pm.group(1).upper() in _US_STATES:
        return pm.group(1).upper()
    # Full name: "Indiana", "New York"
    lower = re.sub(r'\(.*?\)', '', cleaned).strip().lower()
    if lower in _STATE_NAMES:
        return _STATE_NAMES[lower]
    return ""


def _parse_address(raw: str) -> dict:
    """
    Best-effort parse of a US address string into components.

    Returns dict with keys: street, apartment, city, state, zip.
    Missing components are empty strings.  The street value always
    includes the apartment/unit if present (never truncated).
    """
    result = {"street": raw.strip(), "apartment": "", "city": "", "state": "", "zip": ""}

    if not raw or not raw.strip():
        return result

    # Extract zip code
    zm = _ZIP_RE.search(raw)
    if zm:
        result["zip"] = zm.group(1)

    # Extract apartment/unit
    am = _APT_RE.search(raw)
    if am:
        result["apartment"] = am.group(1).strip()

    # Split on commas for multi-part addresses
    parts = [p.strip() for p in raw.split(",") if p.strip()]

    if len(parts) >= 4:
        # "123 Main St, Apt 4B, Indianapolis, Indiana(IN), 46204"
        # Scan backwards for state part — check both the whole part and tokens
        state_idx = -1
        for i in range(len(parts) - 1, 0, -1):
            # Try whole part first ("Indiana(IN)", "OR")
            st = _normalize_state(parts[i])
            if st:
                result["state"] = st
                state_idx = i
                break
            # Try individual tokens ("WA 98331" → "WA")
            for tok in parts[i].split():
                st = _normalize_state(tok)
                if st:
                    result["state"] = st
                    state_idx = i
                    break
            if state_idx >= 0:
                break
        if state_idx > 1:
            result["city"] = parts[state_idx - 1].strip()
            result["street"] = ", ".join(parts[:state_idx - 1]).strip()
        elif state_idx == -1:
            # No state found — assume positional: street, city, state?, zip
            result["street"] = parts[0]
            result["city"] = parts[1]

    elif len(parts) == 3:
        # "123 Main St, Forks, WA 98331" or "123 Main, City, State"
        result["street"] = parts[0]
        # Check last part for state (+ optional zip)
        st = _normalize_state(parts[2].split()[0] if parts[2].split() else "")
        if st:
            result["state"] = st
            result["city"] = parts[1].strip()
        else:
            # Maybe middle part is state
            st2 = _normalize_state(parts[1])
            if st2:
                result["state"] = st2
            else:
                result["city"] = parts[1].strip()

    elif len(parts) == 2:
        # "123 Main St, Forks WA 98331"
        result["street"] = parts[0]
        tokens = parts[1].split()
        for tok in tokens:
            st = _normalize_state(tok)
            if st:
                result["state"] = st
                idx = parts[1].find(tok)
                if idx > 0:
                    result["city"] = parts[1][:idx].strip().rstrip(",")
                break

    # If street ended up empty, fall back to full raw
    if not result["street"]:
        result["street"] = raw.strip()

    return result


def _build_patient_json(db_conn, patient_id: int) -> dict:
    """
    Build a structured dict of the patient's record.

    This is the single source of truth for patient context fed to all
    paperwork LLM prompts.  Medical shorthand in medication frequencies
    is normalised to plain English (e.g. "bid" → "Twice daily").

    Returns a dict with typed keys — callers can json.dumps() it for
    prompt injection or iterate directly.
    """
    from database.patient import get_profile, get_patient_field_map

    profile = get_profile(db_conn)
    field_map = get_patient_field_map(db_conn, patient_id)

    data: dict = {}

    # --- Core demographics from profiles table ---
    if profile:
        data["name"] = profile[1] or ""
        data["date_of_birth"] = profile[2] or ""
        # Alias so LLM matches both "Date of Birth" and "DOB" labels
        if profile[2]:
            data["dob"] = profile[2]
        if profile[3]:  # notes
            data["notes"] = profile[3]

    # Helper to pull a scalar field value
    def _val(key: str) -> str:
        entry = field_map.get(key)
        if not entry:
            return ""
        return str(entry.get("value", "") or "").strip()

    # Demographics
    phone = _val("patient.phone")
    if phone:
        data["phone"] = phone

    email = _val("patient.email")
    if email:
        data["email"] = email

    address = _val("patient.address")
    if address:
        # Parse address into components so forms with separate City/State/ZIP
        # fields get correct values.  Only the street portion goes into
        # "street_address" — no full address key to avoid the LLM dumping
        # everything into a single "Address" field.
        addr_parts = _parse_address(address)
        data["street_address"] = addr_parts["street"]
        if addr_parts.get("apartment"):
            data["apartment"] = addr_parts["apartment"]
        if addr_parts.get("city"):
            data["city"] = addr_parts["city"]
        if addr_parts.get("state"):
            data["state"] = addr_parts["state"]
        if addr_parts.get("zip"):
            data["zip_code"] = addr_parts["zip"]

    # Insurance — flattened to top-level keys so the LLM can easily match
    # form labels like "Insurance Provider", "Policy Number", etc.
    # DB stores: payer, member_id, group_no, bin, pcn, phone, notes
    insurance_raw = _val("insurance.list")
    if insurance_raw:
        try:
            ins_list = json.loads(insurance_raw)
            if isinstance(ins_list, list) and ins_list:
                ins = ins_list[0]  # primary insurance
                if isinstance(ins, dict):
                    if ins.get("payer"):
                        data["insurance_provider"] = ins["payer"]
                    if ins.get("member_id"):
                        data["policy_number"] = ins["member_id"]
                    if ins.get("group_no"):
                        data["group_number"] = ins["group_no"]
        except (json.JSONDecodeError, TypeError):
            data["insurance_provider"] = insurance_raw

    # Allergies
    allergies_raw = _val("allergyintolerance.list")
    if allergies_raw:
        try:
            allergy_list = json.loads(allergies_raw)
            if isinstance(allergy_list, list):
                cleaned = []
                for a in allergy_list:
                    if isinstance(a, dict) and a.get("substance"):
                        entry = {"substance": a["substance"]}
                        if a.get("reaction"):
                            entry["reaction"] = a["reaction"]
                        if a.get("severity"):
                            entry["severity"] = a["severity"]
                        cleaned.append(entry)
                if cleaned:
                    data["allergies"] = cleaned
        except (json.JSONDecodeError, TypeError):
            data["allergies"] = allergies_raw

    # Medications (with shorthand normalisation)
    meds_raw = _val("medicationstatement.current_list")
    if meds_raw:
        try:
            med_list = json.loads(meds_raw)
            if isinstance(med_list, list):
                cleaned = []
                for m in med_list:
                    if not isinstance(m, dict):
                        continue
                    entry = {}
                    if m.get("name"):
                        entry["name"] = m["name"]
                    if m.get("dose"):
                        entry["dose"] = _normalize_frequency(m["dose"])
                    if m.get("frequency"):
                        entry["frequency"] = _normalize_frequency(m["frequency"])
                    if entry:
                        cleaned.append(entry)
                if cleaned:
                    data["current_medications"] = cleaned
        except (json.JSONDecodeError, TypeError):
            data["current_medications"] = meds_raw

    # Conditions / Diagnoses
    conditions_raw = _val("conditions.list")
    if conditions_raw:
        try:
            cond_list = json.loads(conditions_raw)
            if isinstance(cond_list, list):
                cond_strs = [
                    c.get("name", "") for c in cond_list
                    if isinstance(c, dict) and c.get("name")
                ]
                if cond_strs:
                    data["medical_conditions"] = cond_strs
        except (json.JSONDecodeError, TypeError):
            data["medical_conditions"] = conditions_raw

    # Surgical History
    procs_raw = _val("procedures.list")
    if procs_raw:
        try:
            proc_list = json.loads(procs_raw)
            if isinstance(proc_list, list):
                proc_strs = [
                    p.get("name", "") for p in proc_list
                    if isinstance(p, dict) and p.get("name")
                ]
                if proc_strs:
                    data["surgical_history"] = proc_strs
        except (json.JSONDecodeError, TypeError):
            data["surgical_history"] = procs_raw

    return data


def _build_patient_digest(db_conn, patient_id: int) -> str:
    """
    Construct a JSON-formatted string of the patient's record for LLM context.

    Wraps _build_patient_json() and serialises to indented JSON.  Kept as the
    primary public helper so existing callers (paperwork_overlay.py, etc.)
    continue to work without changes.
    """
    data = _build_patient_json(db_conn, patient_id)
    if not data:
        return '{"_note": "No patient data available."}'
    return json.dumps(data, indent=2, ensure_ascii=False)


def _safe_parse_dict(raw: str) -> dict:
    """
    Robustly extract a JSON/Python dictionary from raw LLM output.
    Mimics the resilience of ai/extraction.py:
      1. Regex-isolate the first {...} block (handles chattiness from small LLMs)
      2. json.loads (strict JSON)
      3. ast.literal_eval with null/true/false translation (handles Python-style output)
      4. Empty dict fallback
    """
    raw = str(raw).strip()

    # Isolate the first {...} block
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if match:
        raw = match.group(0)
    else:
        return {}

    # Attempt 1: standard JSON
    try:
        result = json.loads(raw)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Attempt 2: Python literal (handles single quotes, Python None/True/False)
    try:
        safe = (
            raw.replace("null", "None")
               .replace("true", "True")
               .replace("false", "False")
        )
        result = ast.literal_eval(safe)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    return {}


def _resolve_boolean_field(field_name: str, patient_digest: str) -> bool:
    """
    Attempt to resolve a checkbox field using keyword matching against the patient digest.
    Returns True if a probable match is found, otherwise False.
    """
    # Extract significant alphanumeric words from the field name
    words = re.findall(r'[a-zA-Z0-9]+', field_name)
    sig_words = [
        w.lower() for w in words 
        if len(w) > 3 and w.lower() not in {"check", "box", "true", "false", "yes", "no", "is", "has", "have", "do", "does"}
    ]
    
    if not sig_words:
        return False
        
    digest_lower = patient_digest.lower()
    for w in sig_words:
        if w in digest_lower:
            return True
            
    return False


def map_pdf_fields(
    db_conn,
    patient_id: int,
    pdf_fields: list[str],
    field_schema: dict[str, dict] | None = None,
    field_limits: dict[str, int] | None = None,
    llm=None,
) -> dict:
    """
    Use the offline LLM to map patient health record data to blank PDF form fields.
    Handles text fields, checkboxes (boolean), and radio/enum fields.

    Args:
        db_conn:        Active (decrypted) database connection.
        patient_id:     ID of the current patient.
        pdf_fields:     List of PDF field key strings that need to be mapped.
                        Should already have UI-sourced fields removed by the caller.
        field_schema:   Optional dict mapping field name -> schema properties dict
                        (e.g. {"type": "boolean"} or {"type": "string", "enum": ["Yes", "No"]}).
                        Used to route checkboxes and radio groups correctly.
                        If None, all fields are treated as plain text.
        field_limits:   Optional dict mapping field name -> max character count,
                        derived from the PDF schema's `maxLength` property.
        llm:            Optional pre-loaded LLM object. If None, backend.get_llm() is called.

    Returns:
        dict: Keys are a strict subset of pdf_fields.
              Checkbox fields → Python bool (True/False).
              Radio/enum fields → exact allowed string.
              Text fields → truncated string.
    """
    if not pdf_fields:
        return {}

    if llm is None:
        from ai.backend import get_llm
        llm = get_llm()

    # Build human-readable patient digest for LLM context
    try:
        digest = _build_patient_digest(db_conn, patient_id)
    except Exception as exc:
        logger.warning("paperwork: failed to build patient digest: %s", exc)
        digest = "No patient data available."

    schema = field_schema or {}
    final_mapping: dict = {}

    # --- Phase 1: Resolve checkbox (boolean) fields without the LLM ---
    # Small LLMs are unreliable at simple yes/no reasoning; keyword matching
    # against the patient digest is faster and more accurate.
    boolean_fields = [f for f in pdf_fields if schema.get(f, {}).get("type") == "boolean"]
    text_and_enum_fields = [f for f in pdf_fields if f not in boolean_fields]

    for field in boolean_fields:
        result = _resolve_boolean_field(field, digest)
        final_mapping[field] = result
        logger.debug("paperwork: checkbox '%s' resolved to %s", field, result)

    if boolean_fields:
        logger.info("paperwork: resolved %d checkbox fields without LLM", len(boolean_fields))

    # --- Phase 2: Build annotated field list for the LLM ---
    # Text fields are listed plainly.
    # Enum/radio fields include their allowed options so the LLM can pick correctly.
    if not text_and_enum_fields:
        ai_raw = {}
    else:
        annotated_fields = []
        for f in text_and_enum_fields:
            props = schema.get(f, {})
            enum_values = props.get("enum") if isinstance(props, dict) else None
            if enum_values:
                opts = ", ".join(f'"{v}"' for v in enum_values)
                annotated_fields.append(f'  "{f}" [CHOOSE ONE OF: {opts}]')
            else:
                annotated_fields.append(f'  "{f}"')

        fields_list = "\n".join(annotated_fields)
        prompt = _MAP_PROMPT_TEMPLATE.format(digest=digest, fields_list=fields_list)
        logger.debug("paperwork: prompt digest preview:\n%s", digest[:400])

        try:
            raw_output = llm.complete(prompt).text
            logger.debug("paperwork: raw LLM output: %s", raw_output[:500])
        except Exception as exc:
            logger.error("paperwork: LLM call failed: %s", exc)
            raw_output = "{}"

        ai_raw = _safe_parse_dict(raw_output)

    # --- Hallucination safeguard ---
    # Only keep keys that were in the original pdf_fields list.
    valid_keys = set(pdf_fields)
    ai_mapping = {k: v for k, v in ai_raw.items() if k in valid_keys}

    # --- Type coercion for enum/radio fields ---
    # Ensure the LLM's chosen value is one of the allowed options.
    # If the LLM produced something invalid, fall back to empty string
    # (better to leave the field blank than to inject wrong data).
    for field, value in list(ai_mapping.items()):
        props = schema.get(field, {})
        if not isinstance(props, dict):
            continue
        enum_values = props.get("enum")
        if enum_values:
            str_val = str(value)
            # Accept exact match; tolerate case differences and extra whitespace
            match = next(
                (opt for opt in enum_values if opt.strip().lower() == str_val.strip().lower()),
                None,
            )
            ai_mapping[field] = match if match else ""
            if not match:
                logger.debug("paperwork: enum field '%s' had unrecognised value '%s'", field, str_val)

    # --- Per-field character limit enforcement (text fields only) ---
    limits = field_limits or {}
    for key in list(ai_mapping.keys()):
        val = ai_mapping[key]
        # Booleans and None values skip truncation
        if not isinstance(val, str):
            continue
        limit = limits.get(key, PDF_FIELD_DEFAULT_LIMIT)
        if limit and len(val) > limit:
            ai_mapping[key] = val[:limit]
            logger.debug(
                "paperwork: truncated field '%s' to %d chars (limit=%d, pdf_maxlen=%s)",
                key, limit, limit, "yes" if key in limits else "default",
            )

    # Merge boolean results (phase 1) with LLM results (phase 2)
    final_mapping.update(ai_mapping)

    logger.info(
        "paperwork: mapped %d/%d remaining fields via AI (%d checkbox, %d text/enum)",
        len(final_mapping), len(pdf_fields), len(boolean_fields), len(ai_mapping),
    )
    print(f"AI PDF MAPPING: {final_mapping}")
    return final_mapping
