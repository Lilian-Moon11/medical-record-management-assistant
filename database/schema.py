# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Database schema initialization and default field seeding.
#
# This module ensures all required SQLite tables exist for the application,
# including:
# - Core system tables (app_settings, patients, documents)
# - Dynamic field system (field_definitions, patient_field_values)
# - Domain tables (providers, lab_reports, lab_results)
#
# Responsibilities:
# - Create tables idempotently via `CREATE TABLE IF NOT EXISTS`
# - Define foreign-key relationships between patients, documents, providers,
#   lab reports, and lab results
# - Seed default FHIR-lite-style field definitions (e.g., phone, email,
#   address, JSON list fields for allergies/medications/insurance)
# - Use `ensure_field_definition()` to avoid duplicate seeds and allow safe
#   re-execution during startup or migrations
#
# Design Goals:
# - Safe to call at application startup
# - Deterministic schema creation without destructive migrations
# - Establish a pragmatic domain backbone for structured patient data
# -----------------------------------------------------------------------------

from datetime import datetime
from .patient import ensure_field_definition

def _ensure_schema(conn):
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS patients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, dob TEXT, notes TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER, file_name TEXT, file_path TEXT, parsed_text TEXT, upload_date TEXT, FOREIGN KEY(patient_id) REFERENCES patients(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS field_definitions (id INTEGER PRIMARY KEY AUTOINCREMENT, field_key TEXT UNIQUE NOT NULL, label TEXT NOT NULL, data_type TEXT NOT NULL DEFAULT 'text', category TEXT NOT NULL DEFAULT 'General', is_sensitive INTEGER NOT NULL DEFAULT 0, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS patient_field_values (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, field_key TEXT NOT NULL, value_text TEXT, source TEXT NOT NULL DEFAULT 'user', updated_at TEXT, UNIQUE(patient_id, field_key), FOREIGN KEY(patient_id) REFERENCES patients(id))")
    
    # Domain Tables
    cur.execute("CREATE TABLE IF NOT EXISTS providers (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, name TEXT NOT NULL, specialty TEXT, clinic TEXT, phone TEXT, fax TEXT, email TEXT, address TEXT, notes TEXT, created_at TEXT, updated_at TEXT, FOREIGN KEY(patient_id) REFERENCES patients(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS lab_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, source_document_id INTEGER, collected_date TEXT, reported_date TEXT, ordering_provider TEXT, facility TEXT, notes TEXT, created_at TEXT, updated_at TEXT, FOREIGN KEY(patient_id) REFERENCES patients(id), FOREIGN KEY(source_document_id) REFERENCES documents(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS lab_results (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, report_id INTEGER NOT NULL, test_name TEXT NOT NULL, value_text TEXT NOT NULL, value_num REAL, unit TEXT, ref_range_text TEXT, ref_low REAL, ref_high REAL, ref_unit TEXT, abnormal_flag TEXT, result_date TEXT, notes TEXT, created_at TEXT, updated_at TEXT, FOREIGN KEY(patient_id) REFERENCES patients(id), FOREIGN KEY(report_id) REFERENCES lab_reports(id))")

    # Seed Defaults
    defaults = [
        ("patient.phone", "Phone", "phone", "Demographics", 0),
        ("patient.email", "Email", "email", "Demographics", 0),
        ("patient.address", "Address", "text", "Demographics", 0),
        ("allergyintolerance.list", "Allergies (JSON)", "json", "Allergies", 0),
        ("medicationstatement.current_list", "Current Medications (JSON)", "json", "Medications", 0),
        ("insurance.list", "Insurance Plans (JSON)", "json", "Insurance", 0),
    ]
    for k, label, dt, cat, sens in defaults:
        ensure_field_definition(conn, k, label, dt, cat, sens, commit=False)
    conn.commit()