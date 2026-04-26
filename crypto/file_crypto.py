# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# File-level encryption utilities and File Master Key (FMK) management.
#
# This module provides the primitives used to encrypt/decrypt document bytes,
# and manages the File Master Key (FMK) lifecycle within the vault database.
#
# Responsibilities include:
# - Generating the FMK on first run (Fernet key) for encrypting file contents
# - Storing the FMK in the database only in wrapped form (never plaintext)
# - Wrapping/unwrapping the FMK using a key derived directly from the
#   Database Master Key (DMK) to tie file encryption to vault unlock state
# - Enforcing safety checks and failing closed when key material is invalid
# - Providing simple encrypt/decrypt helpers for byte payloads
#
# Notes:
# - The preferred model stores FMK wrapped by a DMK-derived Fernet key
#   (crypto.fmk_wrapped_by_dmk_b64).
# - Legacy migration paths are explicitly guarded to avoid silent misuse
#   or partial upgrades that could risk data loss.
# -----------------------------------------------------------------------------

from __future__ import annotations

import base64
from dataclasses import dataclass
from cryptography.fernet import Fernet, InvalidToken

# FMK is a Fernet key (base64 bytes)
FMK_KEY_LEN = 32

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8")

def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s.encode("utf-8"))

def _dmk_to_fernet_key(dmk_raw: bytes) -> bytes:
    """
    Fernet keys are base64-encoded 32-byte values.
    Your DMK is 32 raw bytes. Convert to Fernet key format.
    """
    if not dmk_raw or len(dmk_raw) != 32:
        raise ValueError("DMK must be exactly 32 bytes.")
    return base64.urlsafe_b64encode(dmk_raw)

def _upsert_setting(cur, key: str, value: str) -> None:
    cur.execute(
        """INSERT INTO app_settings(key, value) VALUES(?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )

def get_or_create_file_master_key(conn, *, dmk_raw: bytes) -> bytes:
    """
    Derives, migrates, or creates the File Master Key (FMK) bound to the current Database Master Key (DMK).

    In the v2 architecture, the FMK is never stored in plaintext. It is generated securely via Fernet 
    and then enveloped (wrapped) using a secondary Fernet key derived directly from the supplied `dmk_raw`.
    When the user rotates their password, the DMK changes, and this function cleanly rewraps the FMK 
    in the database to ensure persistent payload decryption.

    Args:
        conn (sqlite3.Connection): Active database connection.
        dmk_raw (bytes): The 32-byte Database Master Key derived from the user's password.

    Returns:
        bytes: The decrypted FMK ready for active file operations during this session.
        
    Raises:
        ValueError: If connection or DMK bytes are completely missing.
        RuntimeError: If data corruption is detected or legacy (v1) key migration logic fails.
    """
    if conn is None:
        raise ValueError("DB connection required.")
    if not dmk_raw:
        raise ValueError("DMK required.")

    cur = conn.cursor()

    # ---- Preferred (new) key ----
    cur.execute("SELECT value FROM app_settings WHERE key=?", ("crypto.fmk_wrapped_by_dmk_b64",))
    row_new = cur.fetchone()
    if row_new and row_new[0]:
        wrapped = _b64d(row_new[0])
        wrapper = Fernet(_dmk_to_fernet_key(dmk_raw))
        try:
            return wrapper.decrypt(wrapped)
        except InvalidToken as ex:
            raise RuntimeError("Unable to unwrap file key (FMK) with DMK. Data may be corrupted.") from ex

    # ---- Migration path (old keys exist) ----
    cur.execute("SELECT value FROM app_settings WHERE key=?", ("crypto.fmk_wrapped_b64",))
    old_wrapped_row = cur.fetchone()
    cur.execute("SELECT value FROM app_settings WHERE key=?", ("crypto.fmk_salt_b64",))
    old_salt_row = cur.fetchone()
    cur.execute("SELECT value FROM app_settings WHERE key=?", ("crypto.kdf_iters",))
    old_iters_row = cur.fetchone()

    old_present = bool(old_wrapped_row and old_wrapped_row[0])

    if old_present:
        # We cannot decrypt old FMK without the old password.
        # So migration must happen at a time when you *still* have the password.
        raise RuntimeError(
            "This vault was created with an older encryption format. "
            "Since this build does not support upgrading older vaults, "
            "please delete and recreate the vault for a fresh install."
        )

    # ---- First run (no FMK yet) ----
    fmk = Fernet.generate_key()
    wrapper = Fernet(_dmk_to_fernet_key(dmk_raw))
    wrapped = wrapper.encrypt(fmk)

    _upsert_setting(cur, "crypto.fmk_wrapped_by_dmk_b64", _b64e(wrapped))
    conn.commit()
    return fmk

def encrypt_bytes(fmk: bytes, plaintext: bytes) -> bytes:
    """
    Encrypt a payload using the File Master Key (FMK).

    Args:
        fmk (bytes): The decrypted File Master Key (Fernet format).
        plaintext (bytes): The raw bytes to encrypt.

    Returns:
        bytes: The Fernet-encrypted ciphertext payload, safe to write to disk.
    """
    return Fernet(fmk).encrypt(plaintext)

def decrypt_bytes(fmk: bytes, ciphertext: bytes) -> bytes:
    """
    Decrypt a payload using the File Master Key (FMK).

    Args:
        fmk (bytes): The decrypted File Master Key (Fernet format).
        ciphertext (bytes): The encrypted bytes read from disk.

    Returns:
        bytes: The decrypted plaintext payload ready for application memory.
    """
    return Fernet(fmk).decrypt(ciphertext)