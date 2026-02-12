# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# SQLCipher database access layer and vault lifecycle management.
#
# This module owns opening/creating the encrypted SQLite vault, applying the
# SQLCipher key, ensuring required schema exists, and providing the app’s core
# persistence helpers for profiles, documents, settings, and dynamic patient
# fields.
#
# Responsibilities include:
# - Resolving the on-disk DB path for both development and packaged (.exe) runs
# - Creating or unlocking the vault via keybag-stored encryption keys
#   (password unlock or recovery-key unlock)
# - Initializing SQLCipher using the raw Database Master Key (DMK) and
#   verifying key correctness before any schema work
# - Ensuring tables exist and seeding default field definitions safely
# - CRUD helpers for:
#   - patient profiles
#   - encrypted document metadata (paths, names, timestamps)
#   - app settings (key/value)
#   - field definitions + per-patient field values (upsert + mapping utilities)
#
# Design goals:
# - Keep crypto concerns separated: this module uses the keybag API to obtain
#   the DMK, then uses SQLCipher PRAGMA key with the raw bytes to unlock
# - Fail closed on incorrect keys / corrupted DB (no partial initialization)
# - Keep schema creation idempotent so upgrades and first-run paths are safe
# -----------------------------------------------------------------------------

from sqlcipher3 import dbapi2 as sqlite3
import os
import sys
from datetime import datetime

from crypto.keybag import (
    load_keybag,
    create_new_keybag,
    unlock_db_key_with_password,
    unlock_db_key_with_recovery,
)

def resource_path(relative_path):
    """ Get absolute path to resource for dev and .exe """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def ensure_field_definition(conn, field_key, label, data_type="text", category="General", is_sensitive=0, commit=True):
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO field_definitions
            (field_key, label, data_type, category, is_sensitive, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (field_key, label, data_type, category, is_sensitive, datetime.now().strftime("%Y-%m-%d %H:%M")))

    if commit:
        conn.commit()


def list_field_definitions(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT field_key, label, data_type, category, is_sensitive
        FROM field_definitions
        ORDER BY category, label
    """)
    return cur.fetchall()


def get_patient_field_map(conn, patient_id):
    cur = conn.cursor()
    cur.execute("""
        SELECT field_key, value_text, source, updated_at
        FROM patient_field_values
        WHERE patient_id = ?
    """, (patient_id,))
    rows = cur.fetchall()
    return {k: {"value": v, "source": s, "updated_at": u} for (k, v, s, u) in rows}


def upsert_patient_field_value(conn, patient_id, field_key, value_text, source="user"):
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("""
        INSERT INTO patient_field_values (patient_id, field_key, value_text, source, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(patient_id, field_key)
        DO UPDATE SET
            value_text=excluded.value_text,
            source=excluded.source,
            updated_at=excluded.updated_at
    """, (patient_id, field_key, value_text, source, now))
    conn.commit()


def _sqlcipher_set_key(cursor, db_key_raw: bytes):
    """Set SQLCipher key using raw bytes (hex format)."""
    hexkey = db_key_raw.hex()
    cursor.execute(f'PRAGMA key = "x\'{hexkey}\'";')


def _ensure_schema(conn):
    """
    This is your old CREATE TABLE section, moved here so it always runs
    after we successfully unlock the DB with the raw key.
    """
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Optional legacy table you had
    cur.execute("""
        CREATE TABLE IF NOT EXISTS security (
            id INTEGER PRIMARY KEY,
            password_hash TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            dob TEXT,
            notes TEXT
        )
    """)

    # Keep parsed_text since your last pushed DB had it
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            file_name TEXT,
            file_path TEXT,
            parsed_text TEXT,
            upload_date TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)

    # Match your working field_definitions shape (id + unique field_key)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS field_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_key TEXT UNIQUE NOT NULL,
            label TEXT NOT NULL,
            data_type TEXT NOT NULL DEFAULT 'text',
            category TEXT NOT NULL DEFAULT 'General',
            is_sensitive INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS patient_field_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            field_key TEXT NOT NULL,
            value_text TEXT,
            source TEXT NOT NULL DEFAULT 'user',
            updated_at TEXT,
            UNIQUE(patient_id, field_key),
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)

    # -----------------------------------------------------------------
    # Schema versioning (minimal, additive)
    # -----------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)

    # If this is the first time we see schema_version, initialize to 1.
    cur.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(?, ?)",
            (1, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )

    # -----------------------------------------------------------------
    # Phase 1 domain tables (FHIR-lite foundation)
    # Keep patient_id on all tables for future multi-profile readiness.
    # -----------------------------------------------------------------

    # Provider directory
    cur.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            specialty TEXT,
            clinic TEXT,
            phone TEXT,
            fax TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_providers_patient_name
                   ON providers(patient_id, name)""")

    # Medical history (structured)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            diagnosed_date TEXT,
            status TEXT,
            severity TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_conditions_patient_date
                   ON conditions(patient_id, diagnosed_date)""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            dosage TEXT,
            frequency TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT,
            prescribing_provider TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_medications_patient_start
                   ON medications(patient_id, start_date)""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS procedures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            procedure_date TEXT,
            outcome TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_procedures_patient_date
                   ON procedures(patient_id, procedure_date)""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            dosage TEXT,
            start_date TEXT,
            end_date TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_supplements_patient_start
                   ON supplements(patient_id, start_date)""")

    # Labs: report (event) + results (analytes)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lab_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            source_document_id INTEGER,
            collected_date TEXT,
            reported_date TEXT,
            ordering_provider TEXT,
            facility TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id),
            FOREIGN KEY(source_document_id) REFERENCES documents(id)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_lab_reports_patient_collected
                   ON lab_reports(patient_id, collected_date)""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS lab_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            report_id INTEGER NOT NULL,

            test_name TEXT NOT NULL,

            value_text TEXT NOT NULL,
            value_num REAL,
            unit TEXT,

            ref_range_text TEXT,
            ref_low REAL,
            ref_high REAL,
            ref_unit TEXT,

            abnormal_flag TEXT,   -- 'H', 'L', 'A', 'N' (or NULL)

            result_date TEXT,
            notes TEXT,

            created_at TEXT,
            updated_at TEXT,

            FOREIGN KEY(patient_id) REFERENCES patients(id),
            FOREIGN KEY(report_id) REFERENCES lab_reports(id)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_lab_results_patient_test
                   ON lab_results(patient_id, test_name)""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_lab_results_patient_report
                   ON lab_results(patient_id, report_id)""")


    # Your seeded defaults
    defaults = [
        ("patient.phone", "Phone", "phone", "Demographics", 0),
        ("patient.email", "Email", "email", "Demographics", 0),
        ("patient.address", "Address", "text", "Demographics", 0),
        ("insurance.member_id", "Insurance Member ID", "text", "Insurance", 1),
        ("insurance.group_id", "Insurance Group ID", "text", "Insurance", 1),
    ]
    for k, label, dt, cat, sens in defaults:
        ensure_field_definition(conn, k, label, dt, cat, sens, commit=False)

    conn.commit()


def init_db_with_db_key(db_key_raw: bytes):
    """Open/create SQLCipher DB using raw DB key (NOT user password)."""
    db_path = resource_path("medical_records_v1.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn.cursor()

    _sqlcipher_set_key(cursor, db_key_raw)

    # Verify key is correct
    try:
        cursor.execute("SELECT count(*) FROM sqlite_master;")
    except Exception:
        conn.close()
        raise ValueError("Invalid DB key or corrupted database.")

    _ensure_schema(conn)

    return conn


def open_or_create_vault(password: str):
    """
    Return (conn, db_key_raw, db_path, recovery_key_if_created).

    recovery_key_if_created is None on normal unlock, or a string the first time.
    """
    db_path = resource_path("medical_records_v1.db")

    kb = load_keybag(db_path)
    recovery_key = None

    if kb is None:
        db_key_raw, recovery_key = create_new_keybag(db_path, password)
    else:
        db_key_raw = unlock_db_key_with_password(db_path, password)

    conn = init_db_with_db_key(db_key_raw)
    return conn, db_key_raw, db_path, recovery_key


# --- The rest of your CRUD functions can stay the same ---

def create_profile(conn, name, dob, notes):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO patients (name, dob, notes) VALUES (?, ?, ?)", (name, dob, notes))
    conn.commit()

def get_profile(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, dob, notes FROM patients LIMIT 1")
    return cursor.fetchone()

def update_profile(conn, profile_id, name, dob, notes):
    cursor = conn.cursor()
    cursor.execute("UPDATE patients SET name=?, dob=?, notes=? WHERE id=?", (name, dob, notes, profile_id))
    conn.commit()

def add_document(conn, patient_id, file_name, file_path, upload_date):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO documents (patient_id, file_name, file_path, upload_date)
        VALUES (?, ?, ?, ?)
    """, (patient_id, file_name, file_path, upload_date))
    conn.commit()

def delete_document(conn, document_id):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()

def get_document_path(conn, document_id):
    cursor = conn.cursor()
    cursor.execute("SELECT file_path FROM documents WHERE id = ?", (document_id,))
    row = cursor.fetchone()
    return row[0] if row else None

def get_patient_documents(conn, patient_id):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, file_name, upload_date, file_path
        FROM documents
        WHERE patient_id = ?
        ORDER BY id DESC
    """, (patient_id,))
    return cursor.fetchall()

def get_setting(conn, key, default=None):
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default

def set_setting(conn, key, value):
    cur = conn.cursor()
    if value is None:
        cur.execute("DELETE FROM app_settings WHERE key=?", (key,))
    else:
        cur.execute("""
            INSERT INTO app_settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))
    conn.commit()

# -----------------------------------------------------------------------------
# Phase 1 helpers: Providers + Labs
# -----------------------------------------------------------------------------

def _now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _like(s: str) -> str:
    # Safe LIKE pattern wrapper
    return f"%{s.strip()}%"


# ----------------------------
# Providers
# ----------------------------

def list_providers(conn, patient_id: int, search: str | None = None, limit: int = 200):
    """
    Return providers for a patient. Optional search matches name/clinic/specialty/phone/fax/email/address/notes.
    """
    cur = conn.cursor()
    params = [patient_id]

    sql = """
        SELECT id, name, specialty, clinic, phone, fax, email, address, notes, created_at, updated_at
        FROM providers
        WHERE patient_id = ?
    """

    if search and search.strip():
        q = _like(search)
        sql += """
            AND (
                name LIKE ? OR
                clinic LIKE ? OR
                specialty LIKE ? OR
                phone LIKE ? OR
                fax LIKE ? OR
                email LIKE ? OR
                address LIKE ? OR
                notes LIKE ?
            )
        """
        params.extend([q, q, q, q, q, q, q, q])

    sql += " ORDER BY name COLLATE NOCASE ASC LIMIT ?"
    params.append(int(limit))

    cur.execute(sql, tuple(params))
    return cur.fetchall()


def create_provider(
    conn,
    patient_id: int,
    name: str,
    specialty: str | None = None,
    clinic: str | None = None,
    phone: str | None = None,
    fax: str | None = None,
    email: str | None = None,
    address: str | None = None,
    notes: str | None = None,
):
    """
    Insert a provider row. Returns new provider id.
    """
    cur = conn.cursor()
    now = _now_ts()

    cur.execute(
        """
        INSERT INTO providers
            (patient_id, name, specialty, clinic, phone, fax, email, address, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (patient_id, name, specialty, clinic, phone, fax, email, address, notes, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_provider(
    conn,
    patient_id: int,
    provider_id: int,
    name: str,
    specialty: str | None = None,
    clinic: str | None = None,
    phone: str | None = None,
    fax: str | None = None,
    email: str | None = None,
    address: str | None = None,
    notes: str | None = None,
):
    """
    Update a provider row scoped to patient_id.
    """
    cur = conn.cursor()
    now = _now_ts()

    cur.execute(
        """
        UPDATE providers
        SET
            name = ?,
            specialty = ?,
            clinic = ?,
            phone = ?,
            fax = ?,
            email = ?,
            address = ?,
            notes = ?,
            updated_at = ?
        WHERE id = ? AND patient_id = ?
        """,
        (name, specialty, clinic, phone, fax, email, address, notes, now, provider_id, patient_id),
    )
    conn.commit()
    return cur.rowcount  # 1 if updated, 0 if not found


def delete_provider(conn, patient_id: int, provider_id: int):
    """
    Delete a provider row scoped to patient_id.
    """
    cur = conn.cursor()
    cur.execute("DELETE FROM providers WHERE id = ? AND patient_id = ?", (provider_id, patient_id))
    conn.commit()
    return cur.rowcount


# ----------------------------
# Labs: Reports
# ----------------------------

def list_lab_reports(conn, patient_id: int, search: str | None = None, limit: int = 200):
    """
    List lab reports sorted by collected_date (desc), then id (desc).
    Optional search matches facility/order/provider/notes.
    """
    cur = conn.cursor()
    params = [patient_id]

    sql = """
        SELECT
            id,
            source_document_id,
            collected_date,
            reported_date,
            ordering_provider,
            facility,
            notes,
            created_at,
            updated_at
        FROM lab_reports
        WHERE patient_id = ?
    """

    if search and search.strip():
        q = _like(search)
        sql += """
            AND (
                facility LIKE ? OR
                ordering_provider LIKE ? OR
                notes LIKE ?
            )
        """
        params.extend([q, q, q])

    sql += """
        ORDER BY
            CASE WHEN collected_date IS NULL OR collected_date = '' THEN 1 ELSE 0 END ASC,
            collected_date DESC,
            id DESC
        LIMIT ?
    """
    params.append(int(limit))

    cur.execute(sql, tuple(params))
    return cur.fetchall()


def create_lab_report(
    conn,
    patient_id: int,
    source_document_id: int | None = None,
    collected_date: str | None = None,
    reported_date: str | None = None,
    ordering_provider: str | None = None,
    facility: str | None = None,
    notes: str | None = None,
):
    """
    Insert a lab report. Returns new report id.
    """
    cur = conn.cursor()
    now = _now_ts()

    cur.execute(
        """
        INSERT INTO lab_reports
            (patient_id, source_document_id, collected_date, reported_date, ordering_provider, facility, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (patient_id, source_document_id, collected_date, reported_date, ordering_provider, facility, notes, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_lab_report(
    conn,
    patient_id: int,
    report_id: int,
    source_document_id: int | None = None,
    collected_date: str | None = None,
    reported_date: str | None = None,
    ordering_provider: str | None = None,
    facility: str | None = None,
    notes: str | None = None,
):
    """
    Update a lab report scoped to patient_id.
    """
    cur = conn.cursor()
    now = _now_ts()

    cur.execute(
        """
        UPDATE lab_reports
        SET
            source_document_id = ?,
            collected_date = ?,
            reported_date = ?,
            ordering_provider = ?,
            facility = ?,
            notes = ?,
            updated_at = ?
        WHERE id = ? AND patient_id = ?
        """,
        (source_document_id, collected_date, reported_date, ordering_provider, facility, notes, now, report_id, patient_id),
    )
    conn.commit()
    return cur.rowcount


def delete_lab_report(conn, patient_id: int, report_id: int):
    """
    Delete a lab report and all its results (scoped to patient_id).
    We do this explicitly (no FK cascade assumption).
    """
    cur = conn.cursor()

    # Delete results first
    cur.execute(
        "DELETE FROM lab_results WHERE report_id = ? AND patient_id = ?",
        (report_id, patient_id),
    )
    # Delete report
    cur.execute(
        "DELETE FROM lab_reports WHERE id = ? AND patient_id = ?",
        (report_id, patient_id),
    )

    conn.commit()
    return cur.rowcount


# ----------------------------
# Labs: Results
# ----------------------------

def list_lab_results_for_report(conn, patient_id: int, report_id: int, search_test: str | None = None, limit: int = 500):
    """
    List results for a report. Optional filter by test name.
    """
    cur = conn.cursor()
    params = [patient_id, report_id]

    sql = """
        SELECT
            id,
            test_name,
            value_text,
            value_num,
            unit,
            ref_range_text,
            ref_low,
            ref_high,
            ref_unit,
            abnormal_flag,
            result_date,
            notes,
            created_at,
            updated_at
        FROM lab_results
        WHERE patient_id = ? AND report_id = ?
    """

    if search_test and search_test.strip():
        q = _like(search_test)
        sql += " AND test_name LIKE ?"
        params.append(q)

    sql += """
        ORDER BY
            CASE WHEN result_date IS NULL OR result_date = '' THEN 1 ELSE 0 END ASC,
            result_date DESC,
            test_name COLLATE NOCASE ASC,
            id ASC
        LIMIT ?
    """
    params.append(int(limit))

    cur.execute(sql, tuple(params))
    return cur.fetchall()


def add_lab_result(
    conn,
    patient_id: int,
    report_id: int,
    test_name: str,
    value_text: str,
    value_num: float | None = None,
    unit: str | None = None,
    ref_range_text: str | None = None,
    ref_low: float | None = None,
    ref_high: float | None = None,
    ref_unit: str | None = None,
    abnormal_flag: str | None = None,
    result_date: str | None = None,
    notes: str | None = None,
):
    """
    Insert a lab result row. Returns new result id.
    Also validates that report_id belongs to this patient_id.
    """
    cur = conn.cursor()

    # Defensive: ensure report belongs to patient (prevents mixed-patient corruption)
    cur.execute("SELECT 1 FROM lab_reports WHERE id=? AND patient_id=?", (report_id, patient_id))
    if cur.fetchone() is None:
        raise ValueError("Invalid report_id for this patient.")

    now = _now_ts()

    cur.execute(
        """
        INSERT INTO lab_results
            (patient_id, report_id, test_name, value_text, value_num, unit,
             ref_range_text, ref_low, ref_high, ref_unit, abnormal_flag,
             result_date, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            patient_id,
            report_id,
            test_name,
            value_text,
            value_num,
            unit,
            ref_range_text,
            ref_low,
            ref_high,
            ref_unit,
            abnormal_flag,
            result_date,
            notes,
            now,
            now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_lab_result(
    conn,
    patient_id: int,
    report_id: int,
    result_id: int,
    test_name: str,
    value_text: str,
    value_num: float | None = None,
    unit: str | None = None,
    ref_range_text: str | None = None,
    ref_low: float | None = None,
    ref_high: float | None = None,
    ref_unit: str | None = None,
    abnormal_flag: str | None = None,
    result_date: str | None = None,
    notes: str | None = None,
):
    """
    Update a lab result row scoped to patient_id and report_id.
    """
    cur = conn.cursor()
    now = _now_ts()

    cur.execute(
        """
        UPDATE lab_results
        SET
            test_name = ?,
            value_text = ?,
            value_num = ?,
            unit = ?,
            ref_range_text = ?,
            ref_low = ?,
            ref_high = ?,
            ref_unit = ?,
            abnormal_flag = ?,
            result_date = ?,
            notes = ?,
            updated_at = ?
        WHERE id = ? AND patient_id = ? AND report_id = ?
        """,
        (
            test_name,
            value_text,
            value_num,
            unit,
            ref_range_text,
            ref_low,
            ref_high,
            ref_unit,
            abnormal_flag,
            result_date,
            notes,
            now,
            result_id,
            patient_id,
            report_id,
        ),
    )
    conn.commit()
    return cur.rowcount


def delete_lab_result(conn, patient_id: int, result_id: int):
    """
    Delete a single result row scoped to patient_id.
    """
    cur = conn.cursor()
    cur.execute("DELETE FROM lab_results WHERE id = ? AND patient_id = ?", (result_id, patient_id))
    conn.commit()
    return cur.rowcount


def search_lab_results(conn, patient_id: int, query: str, limit: int = 200):
    """
    Search across lab results by test name, facility, ordering provider, or notes.
    Returns rows joined with report fields for easy display.
    Uses LIKE only (no FTS).
    """
    q = query.strip() if query else ""
    if not q:
        return []

    like = _like(q)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            r.id AS report_id,
            r.collected_date,
            r.reported_date,
            r.facility,
            r.ordering_provider,
            r.source_document_id,

            x.id AS result_id,
            x.test_name,
            x.value_text,
            x.value_num,
            x.unit,
            x.ref_range_text,
            x.ref_low,
            x.ref_high,
            x.ref_unit,
            x.abnormal_flag,
            x.result_date,
            x.notes
        FROM lab_results x
        JOIN lab_reports r ON r.id = x.report_id
        WHERE
            x.patient_id = ?
            AND r.patient_id = ?
            AND (
                x.test_name LIKE ? OR
                x.notes LIKE ? OR
                r.facility LIKE ? OR
                r.ordering_provider LIKE ?
            )
        ORDER BY
            CASE WHEN r.collected_date IS NULL OR r.collected_date = '' THEN 1 ELSE 0 END ASC,
            r.collected_date DESC,
            r.id DESC,
            x.test_name COLLATE NOCASE ASC
        LIMIT ?
        """,
        (patient_id, patient_id, like, like, like, like, int(limit)),
    )
    return cur.fetchall()

def get_document_metadata(conn, document_id):
    """
    Returns (file_name, file_path, upload_date) or None.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT file_name, file_path, upload_date
        FROM documents
        WHERE id = ?
    """, (document_id,))
    return cur.fetchone()

def open_vault_with_recovery(recovery_key_b64: str):
    """
    Return (conn, db_key_raw, db_path).
    Uses recovery key instead of password.
    """
    db_path = resource_path("medical_records_v1.db")
    db_key_raw = unlock_db_key_with_recovery(db_path, recovery_key_b64)
    conn = init_db_with_db_key(db_key_raw)
    return conn, db_key_raw, db_path
