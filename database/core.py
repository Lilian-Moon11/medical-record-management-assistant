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
import threading

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


class ThreadSafeCursor:
    def __init__(self, raw_cursor, lock):
        self._cur = raw_cursor
        self._lock = lock
    
    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            return next(self._cur)
    
    def __getattr__(self, name):
        attr = getattr(self._cur, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                with self._lock:
                    return attr(*args, **kwargs)
            return wrapper
        return attr

class ThreadSafeConnection:
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._lock = threading.RLock()

    def cursor(self, *args, **kwargs):
        with self._lock:
            raw_cur = self._conn.cursor(*args, **kwargs)
        return ThreadSafeCursor(raw_cur, self._lock)
    
    def __getattr__(self, name):
        attr = getattr(self._conn, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                with self._lock:
                    return attr(*args, **kwargs)
            return wrapper
        return attr


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
    return ThreadSafeConnection(conn)


def vault_exists() -> bool:
    """Return True if a keybag (and therefore a vault) already exists on disk."""
    return load_keybag(str(paths.db_path)) is not None


def open_or_create_vault(password: str, *, allow_create: bool = False):
    """
    Open the vault with a password, or create a new one on first run.
    Returns (conn, db_key_raw, db_path_str, recovery_key).
    recovery_key is non-None only on first-run vault creation.

    If allow_create is False (the default) and no vault exists yet,
    raises a ValueError instead of silently creating one.  This
    prevents accidental vault creation from password typos.
    """
    db_path_str = str(paths.db_path)
    kb = load_keybag(db_path_str)
    recovery_key = None
    if kb is None:
        if not allow_create:
            raise ValueError(
                "No vault exists yet. Please use the Create Vault flow."
            )
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
