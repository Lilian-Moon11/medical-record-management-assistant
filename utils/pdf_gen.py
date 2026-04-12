# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Medical Summary PDF generator.
#
# This module compiles key patient information into a clinician-friendly,
# printable PDF summary built from the app’s Phase 1 (FHIR-lite) data model.
#
# Responsibilities include:
# - Reading patient demographics and free-text notes from the patient profile
# - Pulling dynamic patient fields (e.g., phone, email, address) from the
#   flexible patient_field_values map
# - Rendering high-signal sections conditionally (only when data exists), such as:
#   - Insurance coverage
#   - Critical allergy alerts (visually emphasized)
#   - Recent abnormal lab findings (filtered from lab results)
#   - Tabular summaries for medications and conditions
# - Producing a locally saved PDF file path for export/sharing workflows
#
# Design goals:
# - Keep output readable for clinical contexts (clear headers, sections, paging)
# - Avoid clutter by omitting empty sections
# - Support offline/local-first use: no network calls; reads from local DB only
# -----------------------------------------------------------------------------

import os
import json
from datetime import datetime
from fpdf import FPDF
from database.patient import get_patient_field_map, get_profile
from database.clinical import list_lab_reports, list_lab_results_for_report

class MedicalSummaryPDF(FPDF):
    def header(self):
        # Professional Header for clinical documents
        self.set_font("helvetica", "B", 16)
        self.cell(0, 10, "Personal Medical Summary", ln=True, align="C")
        self.set_font("helvetica", "I", 8)
        self.cell(0, 5, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True, align="R")
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

def generate_summary_pdf(db_conn, patient_id, options=None):
    options = options or {}
    # 1. Fetch data from your Phase 1 model
    profile = get_profile(db_conn) # (id, name, dob, notes)
    field_map = get_patient_field_map(db_conn, patient_id) #
    
    pdf = MedicalSummaryPDF()
    pdf.add_page()

    # 2. Expanded Patient Header (Demographics)
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, f"NAME: {str(profile[1]).upper()}", ln=True)
    pdf.set_font("helvetica", "", 10)
    
    # Retrieve dynamic fields
    phone = (field_map.get("patient.phone", {}) or {}).get("value")
    email = (field_map.get("patient.email", {}) or {}).get("value")
    addr = (field_map.get("patient.address", {}) or {}).get("value")
    
    pdf.cell(0, 6, f"DOB: {profile[2] or 'Not Set'}", ln=True)
    if phone: pdf.cell(0, 6, f"PHONE: {phone}", ln=True)
    if email: pdf.cell(0, 6, f"EMAIL: {email}", ln=True)
    if addr: 
        pdf.multi_cell(0, 6, f"ADDRESS: {addr}")
    pdf.ln(5)

    # 3. Insurance (Conditional)
    raw_ins = (field_map.get("insurance.list", {}) or {}).get("value")
    insurance = json.loads(raw_ins or "[]")
    if options.get('insurance', True) and any(i.get("payer") for i in insurance): # Only show if data is typed
        pdf.set_font("helvetica", "B", 12)
        pdf.set_fill_color(230, 240, 255) # Light Blue header
        pdf.cell(0, 8, " Insurance Coverage", ln=True, fill=True)
        pdf.set_font("helvetica", "", 10)
        for i in insurance:
            if i.get("payer"):
                pdf.cell(0, 7, f"- {i.get('payer')} (ID: {i.get('member_id')}, Group: {i.get('group_no')})", ln=True)
        pdf.ln(5)

    # 4. Critical Alerts: Allergies (Red Box)
    raw_allergies = (field_map.get("allergyintolerance.list", {}) or {}).get("value")
    allergies = json.loads(raw_allergies or "[]")
    if options.get('allergies', True) and allergies:
        pdf.set_fill_color(255, 200, 200) # Safety Red
        pdf.set_text_color(150, 0, 0)
        pdf.set_font("helvetica", "B", 12)
        pdf.cell(0, 8, " CRITICAL ALERTS: ALLERGIES", ln=True, fill=True)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("helvetica", "", 10)
        for a in allergies:
            pdf.cell(0, 6, f"- {a.get('substance')}: {a.get('reaction')} ({a.get('severity')})", ln=True)
        pdf.ln(5)

    # 5. Abnormal Labs (Filtering)
    reports = list_lab_reports(db_conn, patient_id, limit=10) if options.get('labs', True) else [] #
    abnormal_results = []
    for r in reports:
        res_list = list_lab_results_for_report(db_conn, patient_id, r[0]) #
        # Check column index 9 for abnormal_flag
        abnormals = [res for res in res_list if res[9] and str(res[9]).strip() != ""]
        if abnormals:
            abnormal_results.extend([(r[2], res) for res in abnormals]) # (date, result_row)

    if abnormal_results:
        pdf.set_font("helvetica", "B", 12)
        pdf.cell(0, 8, " Recent Abnormal Lab Findings", ln=True, border="B")
        pdf.set_font("helvetica", "", 9)
        for date, res in abnormal_results:
            pdf.cell(0, 6, f"[{date}] {res[1]}: {res[2]} {res[4]} (Flag: {res[9]}) Ref: {res[5]}", ln=True)
        pdf.ln(5)

    # 6. Tables Helper (Meds/Conditions)
    def draw_section_table(title, key, columns, filter_current=False):
        raw = (field_map.get(key, {}) or {}).get("value")
        items = json.loads(raw or "[]")
        if filter_current:
            items = [i for i in items if bool(i.get("is_current", False))]
        
        if not items: return

        pdf.set_font("helvetica", "B", 12)
        pdf.cell(0, 8, f" {title}", ln=True, border="B")
        
        col_width = 190 / len(columns)
        pdf.set_font("helvetica", "B", 10)
        for _, label in columns:
            pdf.cell(col_width, 8, label, border=1, align="C")
        pdf.ln()

        pdf.set_font("helvetica", "", 9)
        for item in items:
            for col_key, _ in columns:
                # Support multiline for symptoms/notes
                val = str(item.get(col_key, ""))
                pdf.cell(col_width, 7, val, border=1)
            pdf.ln()
        pdf.ln(5)

    if options.get('meds', True):
        draw_section_table("Current Medications", "medicationstatement.current_list", 
                       [("name", "Name"), ("dose", "Dose"), ("frequency", "Frequency")], filter_current=True)
    
    if options.get('conditions', True):
        draw_section_table("Active Conditions", "conditions.list", 
                       [("name", "Condition"), ("onset_date", "Onset Date"), ("symptoms", "Symptoms")])

    # 7. Final Output
    pdf.set_font("helvetica", "I", 10)
    if options.get('notes', True):
        pdf.multi_cell(0, 8, f"General Notes: {profile[3] or ''}")
    
    import tempfile
    filename = os.path.join(tempfile.gettempdir(), f"Medical_Summary_{patient_id}.pdf")
    pdf.output(filename)
    return os.path.abspath(filename)