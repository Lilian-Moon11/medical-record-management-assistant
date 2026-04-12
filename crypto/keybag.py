# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Keybag management for vault encryption and recovery.
#
# This module implements the on-disk "keybag" format that safely stores the
# Database Master Key (DMK) in wrapped form, enabling normal unlock with a
# user password and emergency recovery with a separate recovery key.
#
# Responsibilities include:
# - Creating and persisting a keybag alongside the vault database
# - Deriving wrapping keys via PBKDF2-HMAC-SHA256 (salt + iteration count stored)
# - Wrapping/unwrapping the DMK using password or recovery key (Fernet)
# - Rotating the recovery key (re-wrapping the DMK without changing the DMK)
# - Re-wrapping the DMK under a new password after recovery or password change
# - Providing a safe password verification helper (unwrap attempt -> True/False)
#
# Design goals:
# - Keep the raw DMK out of storage (only encrypted/wrapped forms on disk)
# - Support both password-based access and recovery-key-based access
# - Fail closed with clear errors when credentials or key material do not match
# -----------------------------------------------------------------------------

from __future__ import annotations

import os
import json
import base64
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.fernet import Fernet, InvalidToken

# ---- Configuration ----

KEYBAG_VERSION = 1
DEFAULT_KDF_ITERS = 390_000
RECOVERY_KEY_BYTES = 32  # 256-bit recovery key


# ---- Helpers ----

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s.encode("utf-8"))

def generate_recovery_key_b64() -> str:
    """Generate a new recovery key (base64) WITHOUT persisting anything."""
    return _b64e(os.urandom(RECOVERY_KEY_BYTES))

def _derive_wrap_key(secret: bytes, salt: bytes, iters: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iters,
    )
    raw = kdf.derive(secret)
    return base64.urlsafe_b64encode(raw)


def _keybag_path_for_db(db_path: str) -> str:
    return db_path + ".keybag"


# ---- Public API ----

def load_keybag(db_path: str) -> Optional[dict]:
    path = _keybag_path_for_db(db_path)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_new_keybag(db_path: str, password: str) -> tuple[bytes, str]:
    """
    Create a new Database Master Key (DMK), wrap it with:
      - password
      - recovery key

    Returns: (dmk_raw, recovery_key_b64)
    """
    if not password:
        raise ValueError("Password is required.")

    dmk = os.urandom(32)
    recovery_key = os.urandom(RECOVERY_KEY_BYTES)

    salt = os.urandom(16)

    pwd_wrap_key = _derive_wrap_key(password.encode("utf-8"), salt, DEFAULT_KDF_ITERS)
    rec_wrap_key = _derive_wrap_key(recovery_key, salt, DEFAULT_KDF_ITERS)

    wrapped_pwd = Fernet(pwd_wrap_key).encrypt(dmk)
    wrapped_rec = Fernet(rec_wrap_key).encrypt(dmk)

    keybag = {
        "version": KEYBAG_VERSION,
        "kdf": {
            "algorithm": "PBKDF2-HMAC-SHA256",
            "iterations": DEFAULT_KDF_ITERS,
            "salt_b64": _b64e(salt),
        },
        "wrapped_db_key": {
            "password_b64": _b64e(wrapped_pwd),
            "recovery_b64": _b64e(wrapped_rec),
        },
    }

    path = _keybag_path_for_db(db_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(keybag, f, indent=2)

    return dmk, _b64e(recovery_key)


def unlock_db_key_with_password(db_path: str, password: str) -> bytes:
    kb = load_keybag(db_path)
    if kb is None:
        raise RuntimeError("No keybag found.")

    salt = _b64d(kb["kdf"]["salt_b64"])
    iters = kb["kdf"]["iterations"]
    wrapped = _b64d(kb["wrapped_db_key"]["password_b64"])

    wrap_key = _derive_wrap_key(password.encode("utf-8"), salt, iters)
    try:
        return Fernet(wrap_key).decrypt(wrapped)
    except InvalidToken as ex:
        raise RuntimeError("Incorrect password.") from ex


def unlock_db_key_with_recovery(db_path: str, recovery_key_b64: str) -> bytes:
    kb = load_keybag(db_path)
    if kb is None:
        raise RuntimeError("No keybag found.")

    recovery_key = _b64d(recovery_key_b64)
    salt = _b64d(kb["kdf"]["salt_b64"])
    iters = kb["kdf"]["iterations"]
    wrapped = _b64d(kb["wrapped_db_key"]["recovery_b64"])

    wrap_key = _derive_wrap_key(recovery_key, salt, iters)
    try:
        return Fernet(wrap_key).decrypt(wrapped)
    except InvalidToken as ex:
        raise RuntimeError("Invalid recovery key.") from ex


def rotate_recovery_key(db_path: str, dmk_raw: bytes, new_recovery_key_b64: Optional[str] = None) -> str:
    """
    Rotate recovery key by re-wrapping the DMK.
    If new_recovery_key_b64 is provided, uses that exact key (commit step).
    Otherwise generates a new recovery key.
    """
    kb = load_keybag(db_path)
    if kb is None:
        raise RuntimeError("No keybag found.")

    # Use staged key if provided; otherwise generate one
    if new_recovery_key_b64:
        new_recovery = _b64d(new_recovery_key_b64)
    else:
        new_recovery = os.urandom(RECOVERY_KEY_BYTES)

    salt = _b64d(kb["kdf"]["salt_b64"])
    iters = kb["kdf"]["iterations"]

    wrap_key = _derive_wrap_key(new_recovery, salt, iters)
    wrapped = Fernet(wrap_key).encrypt(dmk_raw)

    kb["wrapped_db_key"]["recovery_b64"] = _b64e(wrapped)

    with open(_keybag_path_for_db(db_path), "w", encoding="utf-8") as f:
        json.dump(kb, f, indent=2)

    return _b64e(new_recovery)

def set_new_password(db_path: str, dmk_raw: bytes, new_password: str) -> None:
    """
    Re-wrap DMK with a NEW password (used after recovery or password change).
    """
    if not new_password:
        raise ValueError("New password is required.")

    kb = load_keybag(db_path)
    if kb is None:
        raise RuntimeError("No keybag found.")

    salt = _b64d(kb["kdf"]["salt_b64"])
    iters = kb["kdf"]["iterations"]

    pwd_wrap_key = _derive_wrap_key(new_password.encode("utf-8"), salt, iters)
    wrapped_pwd = Fernet(pwd_wrap_key).encrypt(dmk_raw)

    kb["wrapped_db_key"]["password_b64"] = _b64e(wrapped_pwd)

    with open(_keybag_path_for_db(db_path), "w", encoding="utf-8") as f:
        json.dump(kb, f, indent=2)


def rotate_recovery_key_with_old(
    db_path: str,
    dmk_raw: bytes,
    old_recovery_key_b64: str,
) -> str:
    """
    Rotate recovery key AFTER proving you have the old recovery key.
    Returns new recovery key (base64).
    """
    # This verifies the old key is valid (and also proves you're allowed to rotate)
    _ = unlock_db_key_with_recovery(db_path, old_recovery_key_b64)

    return rotate_recovery_key(db_path, dmk_raw)

def verify_password(db_path: str, password: str) -> bool:
    """
    Cryptographically verify the password by attempting to unwrap the
    password-wrapped DMK in the keybag. Returns True/False.
    """
    if not password:
        return False
    try:
        _ = unlock_db_key_with_password(db_path, password)
        return True
    except Exception:
        return False