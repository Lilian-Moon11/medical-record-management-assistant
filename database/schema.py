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
# Phase 5.0 additions (idempotent):
# - document_chunks table for AI ingestion pipeline
# - patient_field_values.source_doc_id / ai_confidence columns for provenance
# -----------------------------------------------------------------------------

from datetime import datetime
from .patient import ensure_field_definition


def _ensure_schema(conn):
    cur = conn.cursor()

    # ── Core tables ──────────────────────────────────────────────────────────
    cur.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS patients (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, dob TEXT, notes TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER, file_name TEXT, file_path TEXT, parsed_text TEXT, upload_date TEXT, FOREIGN KEY(patient_id) REFERENCES patients(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS field_definitions (id INTEGER PRIMARY KEY AUTOINCREMENT, field_key TEXT UNIQUE NOT NULL, label TEXT NOT NULL, data_type TEXT NOT NULL DEFAULT 'text', category TEXT NOT NULL DEFAULT 'General', is_sensitive INTEGER NOT NULL DEFAULT 0, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS patient_field_values (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, field_key TEXT NOT NULL, value_text TEXT, source TEXT NOT NULL DEFAULT 'user', updated_at TEXT, UNIQUE(patient_id, field_key), FOREIGN KEY(patient_id) REFERENCES patients(id))")

    # ── Domain tables ─────────────────────────────────────────────────────────
    cur.execute("CREATE TABLE IF NOT EXISTS providers (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, name TEXT NOT NULL, specialty TEXT, clinic TEXT, phone TEXT, fax TEXT, email TEXT, address TEXT, notes TEXT, created_at TEXT, updated_at TEXT, FOREIGN KEY(patient_id) REFERENCES patients(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS lab_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, source_document_id INTEGER, collected_date TEXT, reported_date TEXT, ordering_provider TEXT, facility TEXT, notes TEXT, created_at TEXT, updated_at TEXT, FOREIGN KEY(patient_id) REFERENCES patients(id), FOREIGN KEY(source_document_id) REFERENCES documents(id))")
    cur.execute("CREATE TABLE IF NOT EXISTS lab_results (id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, report_id INTEGER NOT NULL, test_name TEXT NOT NULL, value_text TEXT NOT NULL, value_num REAL, unit TEXT, ref_range_text TEXT, ref_low REAL, ref_high REAL, ref_unit TEXT, abnormal_flag TEXT, result_date TEXT, notes TEXT, created_at TEXT, updated_at TEXT, FOREIGN KEY(patient_id) REFERENCES patients(id), FOREIGN KEY(report_id) REFERENCES lab_reports(id))")

    # ── Phase 5.0: AI document chunks ─────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id           INTEGER NOT NULL,
            patient_id       INTEGER NOT NULL,
            page_number      INTEGER,
            source_file_name TEXT,
            chunk_text       TEXT NOT NULL,
            chunk_index      INTEGER NOT NULL,
            created_at       TEXT,
            FOREIGN KEY(doc_id)     REFERENCES documents(id),
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)

    # ── Phase 5.1: AI suggestion review inbox ─────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_extraction_inbox (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id       INTEGER NOT NULL,
            doc_id           INTEGER,
            field_key        TEXT,
            suggested_value  TEXT,
            confidence       REAL,
            source_file_name TEXT,
            conflict         INTEGER DEFAULT 0,
            existing_value   TEXT,
            status           TEXT DEFAULT 'pending',
            UNIQUE(patient_id, field_key, suggested_value),
            FOREIGN KEY(patient_id) REFERENCES patients(id),
            FOREIGN KEY(doc_id)     REFERENCES documents(id)
        )
    """)

    # ── Phase 5.0: Provenance columns on patient_field_values (additive) ──────
    # Wrapped in try/except for SQLite < 3.37 compatibility (no IF NOT EXISTS
    # support for ALTER TABLE ADD COLUMN until 3.37).
    for col_sql in (
        "ALTER TABLE patient_field_values ADD COLUMN source_doc_id INTEGER",
        "ALTER TABLE patient_field_values ADD COLUMN ai_confidence REAL",
        "ALTER TABLE documents ADD COLUMN visit_date TEXT",
        "ALTER TABLE documents ADD COLUMN specialty TEXT",
        "ALTER TABLE lab_results ADD COLUMN category TEXT DEFAULT 'Lab'",
    ):
        try:
            cur.execute(col_sql)
        except Exception:
            pass  # column already exists

    # ── Seed default field definitions ────────────────────────────────────────
    defaults = [
        ("patient.phone",                   "Phone",                      "phone",  "Demographics", 0),
        ("patient.email",                   "Email",                      "email",  "Demographics", 0),
        ("patient.address",                 "Address",                    "text",   "Demographics", 0),
        ("allergyintolerance.list",         "Allergies (JSON)",           "json",   "Allergies",    0),
        ("medicationstatement.current_list","Current Medications (JSON)", "json",   "Medications",  0),
        ("insurance.list",                  "Insurance Plans (JSON)",     "json",   "Insurance",    0),
        ("immunization.list",               "Immunizations (JSON)",       "json",   "Immunizations",0),
        ("family_history.list",             "Family History (JSON)",      "json",   "Family History",0),
    ]
    for k, label, dt, cat, sens in defaults:
        ensure_field_definition(conn, k, label, dt, cat, sens, commit=False)

    conn.commit()
