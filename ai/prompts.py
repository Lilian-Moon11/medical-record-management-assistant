# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

_EXTRACTION_PROMPT_TEMPLATE = """\
Extract medical facts from the document excerpt below.
Return ONLY a valid JSON array of objects. Example format:
[
  {{"field_key": "core.name", "value": "JANE DOE", "confidence": 1.0}},
  {{"field_key": "vitals.list", "value": {{"name": "Weight", "value": "150", "unit": "lbs", "date": "YYYY-MM-DD"}}, "confidence": 0.9}}
]

Each item in the array MUST have exactly three keys:
  - "field_key": exactly one of the known keys listed below
  - "value": extracted value (string or nested JSON object)
  - "confidence": float 0.0-1.0

CATEGORY DEFINITIONS — use ONLY these field_key values:

core.name — The patient's full name. Found in "Patient:" or "Member:" sections only.
patient.phone — The patient's phone number. Found in patient sections only.
patient.email — The patient's email. Found in patient sections only.
patient.address — The patient's home address (street, city, state, zip). Found in patient sections only. Referral addresses and provider addresses belong in providers.list.

allergyintolerance.list — Confirmed allergies and adverse drug reactions ONLY. A medication the patient takes is a medication, and goes in medicationstatement.current_list. Allergies are substances that cause allergic reactions (rash, anaphylaxis, hives, swelling).
  Format: {{"substance": "...", "reaction": "...", "notes": "..."}}

medicationstatement.current_list — ALL medications (active and discontinued). Set "is_current" to true for active, false for discontinued. Includes: prescriptions, OTC drugs, supplements, injections like Depo-Provera. Put ONLY the drug name in "name" (e.g. "Lisinopril", NOT "Lisinopril 10mg"). Dose/strength (e.g. "10mg", "500 mg") goes ONLY in the "dose" field.
  Format: {{"is_current": true, "name": "...", "dose": "...", "route": "...", "frequency": "...", "notes": "..."}}

conditions.list — Diagnoses, symptoms, and medical conditions from Assessment, Plan, HPI, and Problem List sections. Includes narrative diagnoses like "back pain" or "anxiety".
  Format: {{"name": "...", "onset_date": "YYYY-MM-DD", "diagnosis_date": "YYYY-MM-DD", "symptoms": "...", "notes": "..."}}

vitals.list — ONLY these measurements: blood pressure, heart rate/pulse, respiratory rate, temperature, weight, height, BMI, O2 saturation, and clinical screening scores (PHQ-9, GAD-7, AUDIT-C). Each entry must have an EXACT numeric value as it appears in the text (do not round or use typical default values like 25). For blood pressure, ALWAYS preserve the FULL systolic/diastolic format exactly as written (e.g. "120/80"), never extract only the systolic number alone. For screening scores (PHQ-9, GAD-7, AUDIT-C): ONLY extract if a specific numeric score is explicitly written in the document (e.g. "GAD-7: 12"). Do NOT infer or fabricate a score from mentions of anxiety, depression, or alcohol use — those belong in conditions.list or social_history.list instead.
  Format: {{"name": "...", "value": "...", "unit": "...", "date": "YYYY-MM-DD"}}

lab_results.list — Lab test results WITH actual result values, imaging findings (X-ray, MRI, CT, ultrasound results), and pathology reports. Only include labs that have completed results.
  Format: {{"name": "...", "value_text": "...", "unit": "...", "abnormal_flag": "...", "date": "YYYY-MM-DD"}}

procedures.list — Surgical procedures ONLY: surgeries, tubal ligation, colonoscopies, endoscopies, biopsies, steroid spinal injections, cryosurgery, and similar interventional procedures. Medications go in medicationstatement.current_list. Lab orders go in lab_results.list.
  Format: {{"name": "...", "date": "YYYY-MM-DD", "surgeon": "...", "facility": "...", "notes": "..."}}

providers.list — Doctors, clinics, and medical facilities. Include the provider even when only a clinic name is available. Referral destinations belong here.
  Format: {{"name": "...", "specialty": "...", "clinic": "...", "phone": "...", "address": "..."}}

immunization.list — Vaccines and immunizations ONLY (flu shot, Tdap, HPV, COVID, etc.). Lab tests are lab_results.list.
  Format: {{"immunization": "...", "date": "YYYY-MM-DD", "lot": "...", "notes": "..."}}

insurance.list — Insurance payer name, member ID, group number. ONLY actual insurance companies (e.g. Aetna, UnitedHealthcare, Cigna, Humana). Hospital systems and health networks (e.g. Baylor Scott, Kaiser) are providers, NOT insurance — put those in providers.list. Do NOT fabricate member IDs, group numbers, or phone numbers.
  Format: {{"payer": "...", "member_id": "...", "group_no": "...", "phone": "...", "notes": "..."}}

family_history.list — Medical conditions of family members (mother, father, siblings). Only include conditions explicitly stated for that specific family member in the text.
  Format: {{"relation": "...", "condition": "...", "notes": "..."}}

social_history.list — Social history: smoking status, alcohol use, drug use, occupation, exercise, sexual history, living situation, and other social factors documented in the Social History section.
  Format: {{"topic": "...", "details": "...", "notes": "..."}}

Rules:
1. Every value must come directly from the document text. Do not invent dates; if a vital or lab is listed, use the date of the visit from the section headers, NOT the date the document was printed. When dates use 2-digit years (e.g. "09/20/22"), interpret as 20xx (2022), not 19xx.
2. Combine duplicate references into one comprehensive item.
3. Only include data that is actually present with real values.
4. Focus on clinical data. Skip page numbers, legal text, and patient demographics (name, phone, email, address) if the values are empty or already captured elsewhere in the document.
5. Omit any field entirely if the value is empty, unknown, or absent from the text.
6. NEVER return the placeholder values from the example format (like "JANE DOE", "150", or "YYYY-MM-DD").
7. For vitals (weight, height, temperature), ALWAYS include the exact unit from the document text in the "unit" field (e.g. "lbs", "kg", "cm", "in", "°F", "°C"). Never assume or omit units.

Document:
\"\"\"
{text}
\"\"\"

Return ONLY a valid JSON array.
"""
