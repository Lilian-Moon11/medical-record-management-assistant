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
# Resolves the DB path via core.paths (platformdirs-based, cross-platform).
# resource_path() has been removed; all path resolution lives in core/paths.py.
# -----------------------------------------------------------------------------

from __future__ import annotations

from datetime import datetime

from sqlcipher3 import dbapi2 as sqlite3

from core import paths
from crypto.keybag import (
    create_new_keybag,
    load_keybag,
    unlock_db_key_with_password,
    unlock_db_key_with_recovery,
)


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _sqlcipher_set_key(cursor, db_key_raw: bytes) -> None:
    hexkey = db_key_raw.hex()
    cursor.execute(f"PRAGMA key = \"x'{hexkey}'\";")


def init_db_with_db_key(db_key_raw: bytes):
    """Open the SQLCipher database with a raw key and ensure the schema exists."""
    conn = sqlite3.connect(str(paths.db_path), check_same_thread=False)
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
    """
    Open the vault with a password, or create a new one on first run.
    Returns (conn, db_key_raw, db_path_str, recovery_key).
    recovery_key is non-None only on first-run vault creation.
    """
    db_path_str = str(paths.db_path)
    kb = load_keybag(db_path_str)
    recovery_key = None
    if kb is None:
        db_key_raw, recovery_key = create_new_keybag(db_path_str, password)
    else:
        db_key_raw = unlock_db_key_with_password(db_path_str, password)
    conn = init_db_with_db_key(db_key_raw)
    return conn, db_key_raw, db_path_str, recovery_key


def open_vault_with_recovery(recovery_key_b64: str):
    """Open the vault using a base64-encoded recovery key."""
    db_path_str = str(paths.db_path)
    db_key_raw = unlock_db_key_with_recovery(db_path_str, recovery_key_b64)
    conn = init_db_with_db_key(db_key_raw)
    return conn, db_key_raw, db_path_str


def get_setting(conn, key: str, default=None):
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_setting(conn, key: str, value) -> None:
    cur = conn.cursor()
    if value is None:
        cur.execute("DELETE FROM app_settings WHERE key=?", (key,))
    else:
        cur.execute(
            "INSERT INTO app_settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
    conn.commit()
