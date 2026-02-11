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


def ensure_field_definition(conn, field_key, label, data_type="text", category="General", is_sensitive=0):
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO field_definitions
            (field_key, label, data_type, category, is_sensitive, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (field_key, label, data_type, category, is_sensitive, datetime.now().strftime("%Y-%m-%d %H:%M")))
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

    # Your seeded defaults
    defaults = [
        ("patient.phone", "Phone", "phone", "Demographics", 0),
        ("patient.email", "Email", "email", "Demographics", 0),
        ("patient.address", "Address", "text", "Demographics", 0),
        ("insurance.member_id", "Insurance Member ID", "text", "Insurance", 1),
        ("insurance.group_id", "Insurance Group ID", "text", "Insurance", 1),
    ]
    for k, label, dt, cat, sens in defaults:
        ensure_field_definition(conn, k, label, dt, cat, sens)

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

def open_vault_with_recovery(recovery_key_b64: str):
    """
    Return (conn, db_key_raw, db_path).
    Uses recovery key instead of password.
    """
    db_path = resource_path("medical_records_v1.db")
    db_key_raw = unlock_db_key_with_recovery(db_path, recovery_key_b64)
    conn = init_db_with_db_key(db_key_raw)
    return conn, db_key_raw, db_path
