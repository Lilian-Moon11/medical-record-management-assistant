# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

from __future__ import annotations
import json
import logging
import os
import zipfile
from datetime import datetime

from crypto.file_crypto import get_or_create_file_master_key, decrypt_bytes
from utils.airlock import _fetch_all_as_dicts

logger = logging.getLogger(__name__)

def export_unencrypted_profile(
    conn,
    dmk_raw: bytes,
    data_dir: str,
    dest_path: str,
    tabs: dict,
) -> str:
    """
    Export selected sections of the patient profile to an UNENCRYPTED ZIP file.
    tabs is a dict: {"overview": bool, "health_record": bool, "providers": bool, "labs": bool, "documents": bool}
    """
    fmk = get_or_create_file_master_key(conn, dmk_raw=dmk_raw)
    cur = conn.cursor()

    def _now_ts():
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    manifest = {
        "exported_at": _now_ts(),
        "unencrypted_export": True,
        "patients": _fetch_all_as_dicts(cur, "SELECT id, name, dob, notes FROM patients"),
    }

    if tabs.get("overview", False):
        manifest["records_requests"] = _fetch_all_as_dicts(
            cur, "SELECT * FROM records_requests")

    if tabs.get("health_record", False) or tabs.get("immunizations", False) or tabs.get("family_history", False):
        manifest["field_definitions"] = _fetch_all_as_dicts(
            cur, "SELECT * FROM field_definitions")
        
        # Determine which fields to export based on granular selections
        keys_to_exclude = []
        if not tabs.get("health_record", False):
            # Exclude all generic health record fields if health record isn't selected
            keys_to_exclude.append("field_key NOT LIKE 'allergyintolerance.%'")
            keys_to_exclude.append("field_key NOT LIKE 'medicationstatement.%'")
            keys_to_exclude.append("field_key NOT LIKE 'conditions.%'")
            keys_to_exclude.append("field_key NOT LIKE 'procedures.%'")
            keys_to_exclude.append("field_key NOT LIKE 'insurance.%'")
        if not tabs.get("immunizations", False):
            keys_to_exclude.append("field_key != 'immunization.list'")
        if not tabs.get("family_history", False):
            keys_to_exclude.append("field_key != 'family_history.list'")
            
        where_clause = " WHERE " + " AND ".join(keys_to_exclude) if keys_to_exclude else ""
        manifest["patient_field_values"] = _fetch_all_as_dicts(
            cur, f"SELECT * FROM patient_field_values{where_clause}")

    if tabs.get("providers", False):
        manifest["providers"] = _fetch_all_as_dicts(
            cur, "SELECT * FROM providers")

    if tabs.get("labs", False):
        manifest["lab_reports"] = _fetch_all_as_dicts(
            cur, "SELECT * FROM lab_reports")
        manifest["lab_results"] = _fetch_all_as_dicts(
            cur, "SELECT * FROM lab_results")

    docs = []
    if tabs.get("documents", False):
        docs = _fetch_all_as_dicts(cur, "SELECT * FROM documents")
        manifest["documents"] = docs
        manifest["ai_extraction_inbox"] = _fetch_all_as_dicts(cur, "SELECT * FROM ai_extraction_inbox")


    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── README for non-technical users ────────────────────────────────
        patient_name = ""
        if manifest["patients"]:
            patient_name = manifest["patients"][0].get("name", "")

        readme_lines = [
            "MEDICAL RECORDS EXPORT",
            "=" * 40,
            "",
            f"Patient: {patient_name}",
            f"Exported: {manifest['exported_at']}",
            "",
            "WHAT'S IN THIS FILE",
            "-" * 40,
            "",
            "Medical_Summary.pdf",
            "    Your complete medical summary in an easy-to-read",
            "    PDF format. You can print this or share it with",
            "    your doctor. Open it with any PDF viewer.",
            "",
        ]

        if tabs.get("documents", False) and docs:
            readme_lines += [
                "documents/",
                "    Your uploaded medical documents (PDFs, images,",
                "    etc.) organized by patient. These are the original",
                "    files you uploaded into the app.",
                "",
            ]

        readme_lines += [
            "technical_data/",
            "    Contains a machine-readable copy of your data in",
            "    JSON format. You can ignore this folder unless you",
            "    need to import your data into another system.",
            "",
            "-" * 40,
            "IMPORTANT: This file is NOT encrypted.",
            "Anyone who has this file can read your medical records.",
            "Store it in a safe place and delete it when you no",
            "longer need it.",
            "",
        ]

        zf.writestr("README.txt", "\r\n".join(readme_lines))

        # ── JSON manifest (tucked into technical_data/) ───────────────────
        zf.writestr(
            "technical_data/medical_data.json",
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )

        # ── Decrypted documents ───────────────────────────────────────────
        if tabs.get("documents", False):
            from core.paths import resolve_doc_path
            for doc in docs:
                enc_path = doc.get("file_path", "")
                if not enc_path:
                    continue

                full_enc = str(resolve_doc_path(enc_path))

                if not os.path.isfile(full_enc):
                    continue

                try:
                    with open(full_enc, "rb") as f:
                        ciphertext = f.read()
                    plaintext = decrypt_bytes(fmk, ciphertext)
                    zip_name = f"documents/{doc['file_name']}"
                    zf.writestr(zip_name, plaintext)
                except Exception as ex:
                    logger.warning("Failed to export doc '%s': %s", doc.get('file_name', '?'), ex)

        # ── Readable PDF summary (primary artifact) ───────────────────────
        try:
            from utils.pdf_gen import generate_summary_pdf
            patient_id = None
            if manifest["patients"]:
                patient_id = manifest["patients"][0]["id"]
            if patient_id:
                pdf_opts = {
                    "insurance":  tabs.get("health_record", False),
                    "allergies":  tabs.get("health_record", False),
                    "labs":       tabs.get("labs", False),
                    "meds":       tabs.get("health_record", False),
                    "conditions": tabs.get("health_record", False),
                    "notes":      tabs.get("health_record", False),
                    "providers":  tabs.get("providers", False),
                    "immunizations":   tabs.get("immunizations", False),
                    "family_history": tabs.get("family_history", False),
                }
                pdf_path = generate_summary_pdf(conn, patient_id, options=pdf_opts)
                if os.path.exists(pdf_path):
                    with open(pdf_path, 'rb') as f:
                        zf.writestr("Medical_Summary.pdf", f.read())
                    try: os.remove(pdf_path)
                    except OSError: pass
        except Exception as e:
            logger.error("Failed to append readable PDF to zip: %s", e)

    return dest_path
