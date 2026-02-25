# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Encrypted database vault bootstrap and settings persistence (SQLCipher).
#
# This module is the single entry point for opening the app’s encrypted SQLite
# database using SQLCipher. It integrates with the keybag-based cryptography
# layer to:
# - Create a new vault on first run (generate and store wrapped DB key + recovery
#   key material via `create_new_keybag`)
# - Open an existing vault using either:
#   - A user password (`unlock_db_key_with_password`)
#   - A recovery key (`unlock_db_key_with_recovery`)
#
# Core responsibilities:
# - Resolve the database file path in both dev and packaged (PyInstaller) builds
# - Apply the raw DB key to SQLCipher via PRAGMA key
# - Validate successful decryption by querying sqlite_master (fail closed with a
#   clear error on wrong key/corruption)
# - Ensure schema exists by calling the schema initializer after unlock
# - Provide a small app_settings KV store (get_setting / set_setting)
#
# Security/UX design goals:
# - Never store the user password in the database
# - Reject invalid keys early before any UI proceeds (“fail closed”)
# - Keep vault open/create flows explicit and deterministic
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
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def _now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def _sqlcipher_set_key(cursor, db_key_raw: bytes):
    hexkey = db_key_raw.hex()
    cursor.execute(f'PRAGMA key = "x\'{hexkey}\'";')

def init_db_with_db_key(db_key_raw: bytes):
    db_path = resource_path("medical_records_v1.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn.cursor()
    _sqlcipher_set_key(cursor, db_key_raw)
    try:
        cursor.execute("SELECT count(*) FROM sqlite_master;")
    except Exception:
        conn.close()
        raise ValueError("Invalid DB key or corrupted database.")
    from .schema import _ensure_schema
    _ensure_schema(conn)
    return conn

def open_or_create_vault(password: str):
    db_path = resource_path("medical_records_v1.db")
    kb = load_keybag(db_path)
    recovery_key = None
    if kb is None:
        db_key_raw, recovery_key = create_new_keybag(db_path, password)
    else:
        db_key_raw = unlock_db_key_with_password(db_path, password)
    conn = init_db_with_db_key(db_key_raw)
    return conn, db_key_raw, db_path, recovery_key

def open_vault_with_recovery(recovery_key_b64: str):
    db_path = resource_path("medical_records_v1.db")
    db_key_raw = unlock_db_key_with_recovery(db_path, recovery_key_b64)
    conn = init_db_with_db_key(db_key_raw)
    return conn, db_key_raw, db_path

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
        cur.execute("INSERT INTO app_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()