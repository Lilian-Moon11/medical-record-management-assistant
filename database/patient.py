# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Patient profile and dynamic field persistence helpers.
#
# This module provides lightweight database functions for storing and retrieving
# the core patient profile (name, DOB, notes) and a flexible “field definitions +
# per-patient values” system used to support customizable Patient Info screens.
#
# Responsibilities include:
# - CRUD operations for the single active patient profile record
# - Defining custom fields (field_definitions) with category, data type, and
#   sensitivity flags, using idempotent inserts for safe seeding/upgrades
# - Storing and updating per-patient field values (patient_field_values) via
#   upsert semantics and timestamping for provenance/auditing
# - Listing field definitions and available categories for UI building
# - Maintaining referential cleanup when deleting a field definition by removing
#   any associated saved values first
#
# Design goals:
# - Keep the DB layer simple and predictable (small functions, explicit commits)
# - Support user-defined fields without schema changes
# - Track “source” and “updated_at” to support provenance-aware UI
# -----------------------------------------------------------------------------

from datetime import datetime

def _now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

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

def ensure_field_definition(conn, field_key, label, data_type="text", category="General", is_sensitive=0, commit=True):
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO field_definitions (field_key, label, data_type, category, is_sensitive, created_at) VALUES (?, ?, ?, ?, ?, ?)", 
                (field_key, label, data_type, category, is_sensitive, _now_ts()))
    if commit: conn.commit()

def get_patient_field_map(conn, patient_id):
    cur = conn.cursor()
    cur.execute("SELECT field_key, value_text, source, updated_at FROM patient_field_values WHERE patient_id = ?", (patient_id,))
    rows = cur.fetchall()
    return {k: {"value": v, "source": s, "updated_at": u} for (k, v, s, u) in rows}

def upsert_patient_field_value(conn, patient_id, field_key, value_text, source="user"):
    cur = conn.cursor()
    cur.execute("INSERT INTO patient_field_values (patient_id, field_key, value_text, source, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(patient_id, field_key) DO UPDATE SET value_text=excluded.value_text, source=excluded.source, updated_at=excluded.updated_at", 
                (patient_id, field_key, value_text, source, _now_ts()))
    conn.commit()

def list_field_definitions(conn):
    cur = conn.cursor()
    cur.execute("SELECT field_key, label, data_type, category, is_sensitive FROM field_definitions ORDER BY category, label")
    return cur.fetchall()

def field_definition_exists(conn, field_key: str):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM field_definitions WHERE field_key=? LIMIT 1", (field_key,))
    return cur.fetchone() is not None

def delete_field_definition(conn, field_key: str):
    cur = conn.cursor()
    cur.execute("DELETE FROM patient_field_values WHERE field_key = ?", (field_key,))
    cur.execute("DELETE FROM field_definitions WHERE field_key = ?", (field_key,))
    conn.commit()

def update_field_definition_label(conn, field_key: str, new_label: str):
    cur = conn.cursor()
    cur.execute("UPDATE field_definitions SET label=? WHERE field_key=?", (new_label, field_key))
    conn.commit()

def update_field_definition_sensitivity(conn, field_key: str, is_sensitive: int):
    cur = conn.cursor()
    cur.execute("UPDATE field_definitions SET is_sensitive=? WHERE field_key=?", (1 if is_sensitive else 0, field_key))
    conn.commit()

def list_distinct_field_categories(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT COALESCE(NULLIF(TRIM(category), ''), 'General') AS cat FROM field_definitions ORDER BY cat COLLATE NOCASE ASC")
    return [r[0] for r in cur.fetchall()]