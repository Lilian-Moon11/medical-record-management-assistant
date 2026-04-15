# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Pure-logic helpers for PDF form field mapping and AcroForm filling.
#
# These functions extract the "how to fill a PDF" concerns from the Paperwork
# Wizard so they can be independently tested and reused.
#
# Includes:
# - _find_key(): two-pass fuzzy field-name matcher (exact then substring)
# - build_ui_mapping(): maps wizard-collected inputs to PDF field names
# - fill_acroform_pdf(): drives PyPDFForm to produce accessible / flattened copies
# -----------------------------------------------------------------------------

import logging
import os
from datetime import datetime

from PyPDFForm import PdfWrapper

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field-name matching
# ---------------------------------------------------------------------------

def _find_key(pdf_fields: list[str], possible_matches: list[str], exclude: str | None = None) -> str | None:
    """Two-pass fuzzy match for a PDF field name.

    Pass 1: exact case-insensitive match.
    Pass 2: substring match.
    """
    for p in possible_matches:
        for f in pdf_fields:
            if exclude and f == exclude:
                continue
            if p.lower() == f.lower():
                return f  # exact match first
    for p in possible_matches:
        for f in pdf_fields:
            if exclude and f == exclude:
                continue
            if p.lower() in f.lower():
                return f  # substring match
    return None


# ---------------------------------------------------------------------------
# UI-sourced field mapping
# ---------------------------------------------------------------------------

def build_ui_mapping(
    pdf_fields: list[str],
    patient_name: str,
    patient_dob: str,
    sign_date: str,
    sig_path: str | None,
    form_type: str,
    roi_details: dict | None = None,
) -> dict[str, str]:
    """Build the mapping from wizard-collected inputs to PDF field names.

    ``roi_details``, when provided, should contain:
        - recipient: dict with keys name, address, phone, email
        - purpose: str
        - expiry: str

    Returns a dict of {pdf_field_name: value}.
    """
    mapping: dict[str, str] = {}

    # Fields present in BOTH intake and ROI forms
    name_key = _find_key(pdf_fields, ["patient name", "Patient Name", "name", "patient"])
    if name_key:
        mapping[name_key] = patient_name

    dob_key = _find_key(pdf_fields, ["birth date", "Birth Date", "dob", "date of birth", "DOB"])
    if dob_key:
        mapping[dob_key] = patient_dob

    date_key = _find_key(pdf_fields, ["Date", "date", "Sign Date", "today"], exclude=dob_key)
    if date_key:
        mapping[date_key] = sign_date

    sig_key = _find_key(pdf_fields, ["signature", "Signature", "sign"])
    if sig_path and sig_key:
        mapping[sig_key] = sig_path

    # ROI-specific: recipient provider fields selected in the Wizard UI
    if form_type == "roi" and roi_details:
        recip = roi_details.get("recipient", {})

        rn_key = _find_key(pdf_fields, ["Recipient Name", "recipient", "send to"])
        if rn_key and recip.get("name"):
            mapping[rn_key] = recip["name"]

        addr_key = _find_key(pdf_fields, ["Address", "address", "street"])
        if addr_key and recip.get("address"):
            mapping[addr_key] = recip["address"]

        ph_key = _find_key(pdf_fields, ["Phone", "phone", "telephone", "tel"])
        if ph_key and recip.get("phone"):
            mapping[ph_key] = recip["phone"]

        # Exact match for Email_2 to avoid colliding with patient Email field
        em_key = _find_key(pdf_fields, ["Email_2"])
        if em_key and recip.get("email"):
            mapping[em_key] = recip["email"]

        # ROI purpose and expiry
        purpose_key = _find_key(pdf_fields, ["purpose", "Purpose", "reason", "Reason"])
        if purpose_key and roi_details.get("purpose"):
            mapping[purpose_key] = roi_details["purpose"]

        expiry_key = _find_key(pdf_fields, ["expir", "Expir", "expiration", "Expiration", "expires"])
        if expiry_key and roi_details.get("expiry"):
            mapping[expiry_key] = roi_details["expiry"]

    logger.debug("UI mapping applied (keys: %s)", list(mapping.keys()))
    return mapping


# ---------------------------------------------------------------------------
# AcroForm PDF generation
# ---------------------------------------------------------------------------

def fill_acroform_pdf(
    template_path: str,
    mapping: dict[str, str],
    form_prefix: str,
    timestamp: str,
    download_dir: str,
    want_accessible: bool = True,
    want_flattened: bool = False,
) -> tuple[str | None, bytes | None, bytes | None]:
    """Fill a PDF template and write output files to *download_dir*.

    Returns ``(output_path, acc_bytes, flat_bytes)`` where *output_path* is the
    first file written (used for auto-open), and the byte buffers are kept for
    optional archiving.
    """
    output_path: str | None = None
    acc_bytes: bytes | None = None
    flat_bytes: bytes | None = None

    # --- Accessible Copy ---
    if want_accessible:
        filled_acc = PdfWrapper(template_path, generate_appearance_streams=True).fill(mapping)
        acc_bytes = filled_acc.read()
        acc_file = os.path.join(download_dir, f"{form_prefix}_Accessible_{timestamp}.pdf")
        with open(acc_file, "wb") as f:
            f.write(acc_bytes)
        output_path = acc_file

    # --- Flattened Copy ---
    if want_flattened:
        filled_flat = PdfWrapper(template_path, generate_appearance_streams=True).fill(mapping, flatten=True)
        flat_bytes = filled_flat.read()
        flat_file = os.path.join(download_dir, f"{form_prefix}_Flattened_{timestamp}.pdf")
        with open(flat_file, "wb") as f:
            f.write(flat_bytes)
        if not output_path:
            output_path = flat_file

    return output_path, acc_bytes, flat_bytes
