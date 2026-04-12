# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Centralized page/session state management.
#
# This module defines and manages all expected attributes stored on the Flet
# page object, keeping session state consistent, explicit, and easy to reset.
#
# Responsibilities include:
# - Initializing all page-level state in one predictable location
#   (DB connection, crypto keys, UI flags, navigation references)
# - Storing unlocked session data after successful vault login
#   (connection, DMK, vault path, in-memory password, recovery key)
# - Clearing sensitive session state on logout, including:
#   - Closing the database connection safely
#   - Removing key material from memory
#   - Resetting UI shell references
# - Providing a simple helper to check whether the vault is unlocked
#
# Design goals:
# - Avoid scattered page attribute mutations throughout the app
# - Make session lifecycle (init -> unlock -> clear) explicit and auditable
# - Ensure sensitive material (password, keys) is kept in memory only
# -----------------------------------------------------------------------------

from __future__ import annotations
from typing import Optional, Any
import os
import shutil


def init_page_state(page) -> None:
    """Initialize all expected page attributes in one place."""
    page.current_profile = None
    page.db_connection = None
    page.is_high_contrast = False
    page.ui_scale = 1.0
    page.db_key_raw = None
    page.db_path = None
    page.db_password = None  # kept ONLY in memory
    page.recovery_key_first_run = None

    # UI shell references
    page.nav_rail = None
    page.content_area = None


def set_unlocked_session(page, *, conn, dmk_raw: bytes, db_path: str, password: str, recovery_key: Optional[str]) -> None:
    page.db_connection = conn
    page.db_key_raw = dmk_raw
    page.db_path = db_path
    page.db_password = password
    page.recovery_key_first_run = recovery_key


def clear_session(page) -> None:
    # Close DB connection
    try:
        if getattr(page, "db_connection", None):
            page.db_connection.close()
    except Exception:
        pass

    # Clear sensitive state
    page.db_connection = None
    page.current_profile = None
    page.db_key_raw = None
    page.db_path = None
    page.db_password = None
    page.recovery_key_first_run = None

    # Clear shell refs
    page.nav_rail = None
    page.content_area = None


def is_unlocked(page) -> bool:
    return bool(getattr(page, "db_connection", None))

def clear_unlocked_session(page) -> None:
    # Backwards-compatible alias for login.py
    clear_session(page)


def wipe_local_data(page) -> None:
    """Securely erase the local vault, encrypted documents, and AI data.

    Intended for shared-device / library-mode cleanup. The caller is
    expected to destroy the application window immediately after this
    returns.
    """
    from core import paths  # local import to avoid circular deps

    # 1. Close the DB connection and scrub in-memory secrets.
    clear_session(page)

    # 2. Remove vault database + keybag
    for fp in (paths.db_path, paths.keybag_path):
        try:
            if fp.exists():
                fp.unlink()
        except OSError:
            pass

    # 3. Remove encrypted document storage
    if paths.data_dir.exists():
        shutil.rmtree(paths.data_dir, ignore_errors=True)

    # 4. Remove AI artifacts (models, embeddings)
    if paths.ai_dir.exists():
        shutil.rmtree(paths.ai_dir, ignore_errors=True)

    # 5. Remove exports
    if paths.export_dir.exists():
        shutil.rmtree(paths.export_dir, ignore_errors=True)