# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Cryptographic integrity and safety validation for the encrypted vault.
#
# This module performs a lightweight but rigorous self-test to verify that
# encryption keys, key storage, and encrypted data are internally consistent
# before allowing continued use of the vault.
#
# Responsibilities include:
# - Verifying vault database presence and basic connection sanity
# - Loading and validating the on-disk keybag
# - Confirming password-based key unwrapping matches the active session key
# - Ensuring the File Master Key (FMK) can be unwrapped via the DB Master Key
# - Performing an encrypt/decrypt round-trip integrity check
# - Optionally attempting to decrypt a single existing encrypted document
#
# The self-test is designed to fail closed: any inconsistency results in a
# clear user-facing safety warning and detailed developer diagnostics, helping
# prevent silent data corruption or irreversible cryptographic misuse.
# -----------------------------------------------------------------------------


from dataclasses import dataclass
import os
import traceback

from crypto.keybag import load_keybag, unlock_db_key_with_password
from crypto.file_crypto import get_or_create_file_master_key, encrypt_bytes, decrypt_bytes
from database import get_patient_documents


@dataclass
class SelfTestResult:
    ok: bool
    user_message: str
    dev_details: str = ""


def run_crypto_self_test(
    *,
    db_path: str,
    conn,
    db_key_raw: bytes,
    password: str | None,
) -> SelfTestResult:
    """
    v2 self-test goals (DMK-wrapped FMK):
    - keybag exists + parseable
    - (if password available) password unwraps DMK and matches in-session db_key_raw
    - FMK unwrap works using DMK (creates if missing) + encrypt/decrypt round-trip
    - (if any docs exist) attempt to decrypt ONE .enc doc (lightweight)
    """

    try:
        # ---- basic sanity ----
        if not db_path:
            return SelfTestResult(False, "Vault path missing.", "db_path was None/empty")

        if not os.path.exists(db_path):
            return SelfTestResult(
                False,
                "Vault database file not found. If you moved files, restore from a backup.",
                f"DB file missing at: {db_path}",
            )

        if not db_key_raw or len(db_key_raw) != 32:
            return SelfTestResult(
                False,
                "Vault keys are missing. This vault cannot be opened safely.",
                f"db_key_raw invalid length: {0 if not db_key_raw else len(db_key_raw)}",
            )

        if conn is None:
            return SelfTestResult(False, "Vault connection missing.", "conn was None")

        # ---- keybag presence + parseability ----
        kb = load_keybag(db_path)  # returns dict or None; JSON issues will throw and be caught below
        if kb is None:
            return SelfTestResult(
                False,
                "Vault keys are missing. This vault cannot be opened safely.",
                "Keybag not found on disk.",
            )

        # ---- cryptographic verification: password unwrap matches active DMK ----
        # (Only possible if password provided; recovery-only sessions may not have it.)
        if password:
            dmk2 = unlock_db_key_with_password(db_path, password)
            if dmk2 != db_key_raw:
                return SelfTestResult(
                    False,
                    "Safety check failed: your password does not match this vault's encryption keys.",
                    "DMK mismatch: password-unwrapped key != session db_key_raw",
                )

        # ---- FMK unwrap (DMK-wrapped model) + round-trip ----
        fmk = get_or_create_file_master_key(conn, dmk_raw=db_key_raw)

        test_plain = b"lpa-selftest"
        ct = encrypt_bytes(fmk, test_plain)
        pt = decrypt_bytes(fmk, ct)
        if pt != test_plain:
            return SelfTestResult(
                False,
                "Safety check failed: encryption test did not pass. Do not continue.",
                "FMK round-trip mismatch.",
            )

        # ---- decrypt one existing encrypted doc (if any) ----
        # Lightweight: do not scan everything.
        cur = conn.cursor()
        cur.execute("SELECT id FROM patients LIMIT 1")
        p = cur.fetchone()
        if p:
            patient_id = p[0]
            docs = get_patient_documents(conn, patient_id)
            if docs:
                enc_path = docs[0][3]
                if enc_path and os.path.exists(enc_path):
                    with open(enc_path, "rb") as f:
                        blob = f.read()
                    _ = decrypt_bytes(fmk, blob)  # raises if wrong key or corrupted

        return SelfTestResult(True, "Vault safety check passed.", "")

    except Exception as ex:
        return SelfTestResult(
            False,
            "Safety check failed. Your vault may be corrupted or the encryption keys don't match. "
            "Do not continue. Restore from a backup if you have one.",
            # dev_details: include traceback for you, never show this raw to users
            f"{type(ex).__name__}: {ex}\n{traceback.format_exc()}",
        )