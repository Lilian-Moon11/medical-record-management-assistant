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
import threading

# Lock protecting the extraction-active flag so clear_session and
# the background ingestion thread coordinate safely.
_extraction_lock = threading.Lock()


class MRMAState:
    pass

def init_page_state(page) -> None:
    """Initialize all expected page attributes in one place."""
    page.mrma = MRMAState()  # Global UI state encapsulation
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
    """Clear sensitive state.  If an extraction is running, defer the DB
    connection close until the extraction thread calls finish_deferred_cleanup()."""
    deferred = False
    with _extraction_lock:
        if getattr(page.mrma, "_extraction_active", False):
            # Extraction still running — keep the connection alive for the thread.
            # Store the connection so finish_deferred_cleanup can close it later.
            page.mrma._deferred_conn = page.db_connection
            deferred = True

    if not deferred:
        # Close DB connection immediately
        try:
            if getattr(page, "db_connection", None):
                page.db_connection.close()
        except Exception:
            pass

    # Clear sensitive state from page regardless
    page.db_connection = None
    page.current_profile = None
    page.db_key_raw = None
    page.db_path = None
    page.db_password = None
    page.recovery_key_first_run = None

    # Clear shell refs
    page.nav_rail = None
    page.content_area = None

    # Clear stale overlay dialogs from previous session views
    try:
        page.overlay.clear()
    except Exception:
        pass
    old_mrma = page.mrma
    page.mrma = MRMAState()
    # Carry over deferred state if extraction is still running
    if deferred:
        page.mrma._extraction_active = True
        page.mrma._deferred_conn = old_mrma._deferred_conn


def is_unlocked(page) -> bool:
    return bool(getattr(page, "db_connection", None))


def mark_extraction_active(page) -> None:
    """Called by the ingestion thread at start."""
    with _extraction_lock:
        page.mrma._extraction_active = True


def mark_extraction_done(page) -> None:
    """Called by the ingestion thread at end.  Closes the DB connection if
    a logout happened while extraction was running."""
    with _extraction_lock:
        page.mrma._extraction_active = False
        deferred_conn = getattr(page.mrma, "_deferred_conn", None)
        page.mrma._deferred_conn = None

    if deferred_conn:
        try:
            deferred_conn.close()
        except Exception:
            pass


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

    # 4. Remove patient-specific AI artifacts (embeddings, caches)
    #    but PRESERVE the models/ directory — it contains generic pre-trained
    #    weights (no PHI) and costs ~2.5 GB to re-download.
    if paths.ai_dir.exists():
        for child in paths.ai_dir.iterdir():
            if child.name == "models":
                continue  # keep downloaded model weights
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            except OSError:
                pass

    # 5. Remove exports
    if paths.export_dir.exists():
        shutil.rmtree(paths.export_dir, ignore_errors=True)