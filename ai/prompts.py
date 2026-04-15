# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

_EXTRACTION_PROMPT_TEMPLATE = """\
Extract structured medical fields from the following document excerpt.
Return ONLY a valid JSON array. Each item must have:
  - "field_key": exactly one of the known keys below
  - "value": extracted value as a string OR a nested JSON object (for lists)
  - "confidence": float 0.0-1.0

Known keys:
patient.name, patient.phone, patient.email, patient.address, allergyintolerance.list, medicationstatement.current_list, insurance.list, procedures.list, conditions.list, vitals.list, lab_results.list, providers.list, immunization.list, family_history.list

Patient vs. Provider identification rule:
- Medical documents frequently contain BOTH a provider header (clinic name, clinic address, clinic phone, physician name) AND a patient section (usually labeled "Patient:", "Name:", "DOB:", etc.).
- patient.name, patient.phone, patient.email, and patient.address MUST come from the patient-labeled section only.
- Information found in the document header, letterhead, or footer belongs to providers.list — NEVER to patient.* fields.
- When in doubt about whether contact details belong to the patient or provider, assign them to providers.list.

Normalization Rules:
- For vitals, use normalized names (e.g., use "Blood Pressure" instead of "BP", "Heart Rate" instead of "HR").
- Be thorough and comprehensive: Capture all distinctly recorded measurements from charts, tables, or itemized lists. Ensure exhaustive extraction so the patient's record is complete, including all secondary readings or duplicate types.
- Confidently ignore boilerplate document text, legal disclaimers, copyright dates, page numbers, and publisher markings.

Expected JSON Structure for List Items:
- vitals.list: {{"name": "...", "value": "...", "unit": "...", "date": "YYYY-MM-DD"}}
- lab_results.list: {{"name": "...", "value_text": "...", "unit": "...", "abnormal_flag": "...", "date": "YYYY-MM-DD"}}
- providers.list: {{"name": "...", "specialty": "...", "clinic": "...", "phone": "...", "fax": "...", "address": "..."}}
- immunization.list: {{"immunization": "...", "date": "YYYY-MM-DD", "lot": "...", "administered_by": "...", "notes": "..."}}
- family_history.list: {{"relation": "...", "condition": "...", "notes": "..."}}
- allergyintolerance.list: {{"substance": "...", "reaction": "...", "notes": "..."}}
- medicationstatement.current_list: {{"name": "...", "dose": "...", "frequency": "...", "notes": "..."}}
- procedures.list: {{"name": "...", "date": "YYYY-MM-DD", "surgeon": "...", "facility": "...", "notes": "..."}}
- conditions.list: {{"name": "...", "onset_date": "YYYY-MM-DD", "diagnosis_date": "YYYY-MM-DD", "symptoms": "...", "notes": "..."}}
- insurance.list: {{"payer": "...", "member_id": "...", "group_no": "...", "bin": "...", "pcn": "...", "phone": "...", "notes": "..."}}}

EXAMPLE OUTPUT FORMAT:
[
  {{"field_key": "patient.address", "value": "1210 Cullen Dr, Apt 4B, Forks, WA 98331", "confidence": 0.9}},
  {{"field_key": "allergyintolerance.list", "value": {{"substance": "Penicillin", "reaction": "Hives", "notes": ""}}, "confidence": 0.9}},
  {{"field_key": "medicationstatement.current_list", "value": {{"name": "Metformin", "dose": "500mg", "frequency": "Twice daily", "notes": ""}}, "confidence": 0.95}},
  {{"field_key": "procedures.list", "value": {{"name": "Appendectomy", "date": "2019-03-12", "surgeon": "Dr. Jane Smith", "facility": "Memorial Hospital", "notes": ""}}, "confidence": 0.9}},
  {{"field_key": "vitals.list", "value": {{"name": "Blood Pressure", "value": "120/80", "unit": "mmHg", "date": "2023-10-15"}}, "confidence": 0.9}}
]

Document:
\"\"\"
{text}
\"\"\"

Return ONLY valid JSON. Your output must strictly match the format of the brackets and quotes in the example above.
"""
