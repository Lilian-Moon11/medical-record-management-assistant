# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Secure archiving of completed paperwork and the records-request tracker hook.
#
# Provides:
# - archive_to_records(): encrypts a filled PDF, saves it to the patient's
#   record store, registers it in the documents table, and marks it as
#   already-processed so AI extraction skips it.
# - create_roi_records_request(): parses a due date from the original template
#   text and inserts a tracking row into records_requests for ROI forms.
#
# Both helpers are used by both the AcroForm fill path and the static-PDF
# placement-review callback, eliminating the duplication that previously
# existed inside the wizard.
# -----------------------------------------------------------------------------

import logging
import os
from datetime import datetime

from core import paths
from crypto.file_crypto import get_or_create_file_master_key, encrypt_bytes
from database import add_document
from database.clinical import list_providers
from database.records_requests import create_request as create_records_request_db
from utils.roi_parser import parse_due_date_from_text
from utils.ui_helpers import show_snack

logger = logging.getLogger(__name__)


def archive_to_records(
    page,
    patient_id: int,
    archive_bytes: bytes,
    form_prefix: str,
    timestamp: str,
) -> int | None:
    """Encrypt *archive_bytes* and save to the patient's document store.

    Returns the new ``doc_id`` on success, or ``None`` on failure.
    """
    try:
        dest_dir = os.path.join(paths.data_dir, str(patient_id))
        os.makedirs(dest_dir, exist_ok=True)

        display_name = f"{form_prefix}_Signed_{timestamp}.pdf"
        enc_path = os.path.join(dest_dir, display_name + ".enc")

        fmk = get_or_create_file_master_key(page.db_connection, dmk_raw=page.db_key_raw)
        ciphertext = encrypt_bytes(fmk, archive_bytes)

        with open(enc_path, "wb") as f:
            f.write(ciphertext)

        doc_id = add_document(
            page.db_connection,
            patient_id,
            display_name,
            enc_path,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        # Flag as processed so background AI extraction ignores it
        page.db_connection.execute(
            "INSERT OR IGNORE INTO ai_extraction_inbox "
            "(patient_id, doc_id, field_key, suggested_value, confidence, source_file_name, status) "
            "VALUES (?, ?, 'system.processed', ?, 1.0, ?, 'system')",
            (patient_id, doc_id, str(doc_id), display_name),
        )
        page.db_connection.commit()
        show_snack(page, "Form securely archived.", "blue")
        return doc_id
    except Exception as db_ex:
        logger.error("Archive error: %s", db_ex)
        show_snack(page, "Archive failed, check data folder.", "orange")
        return None


def create_roi_records_request(
    page,
    patient_id: int,
    prov_from_value: str | None,
    template_bytes: bytes,
    source_doc_id: int | None = None,
) -> None:
    """Create a pending records request after a successful ROI completion.

    Extracts the provider name from the 'Records From' dropdown selection,
    parses a due date from the template text (or falls back to 30 days),
    and inserts a row into records_requests.
    """
    try:
        # Resolve provider name from the wizard dropdown
        provider_name = ""
        department: str | None = None
        if prov_from_value:
            provs = list_providers(page.db_connection, patient_id)
            prov = next((p for p in provs if str(p[0]) == prov_from_value), None)
            if prov:
                # provider tuple: (id, name, specialty, clinic, phone, fax, email, address, ...)
                provider_name = (prov[1] or "").strip()
                department = (prov[3] or "").strip() or None  # clinic as department

        if not provider_name:
            provider_name = "Unknown Provider"

        # Parse due date from template text (lightweight regex, no LLM)
        try:
            import pdfplumber
            text = ""
            import io as _io
            with pdfplumber.open(_io.BytesIO(template_bytes)) as pdf:
                for pg in pdf.pages:
                    text += (pg.extract_text() or "")
        except Exception:
            text = ""

        today = datetime.today()
        due_date, due_source = parse_due_date_from_text(text, request_date=today)
        date_requested = today.strftime("%Y-%m-%d")

        create_records_request_db(
            page.db_connection,
            patient_id,
            provider_name,
            department,
            date_requested,
            due_date,
            due_source,
            notes=None,
            source_doc_id=source_doc_id,
        )

        # Refresh the Overview panel if it is currently visible
        if hasattr(page, "_refresh_requests_panel"):
            try:
                page.mrma._refresh_requests_panel()
            except Exception:
                pass
    except Exception as ex:
        # Non-critical: log but don't surface to the user
        logger.debug("Records request hook error: %s", ex)
