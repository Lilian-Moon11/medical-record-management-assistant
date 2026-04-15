from __future__ import annotations
from utils.ui_helpers import append_dialog, make_info_button
# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Login view builder for unlocking the local encrypted vault.
#
# This module renders the secure login UI and orchestrates the initial unlock
# workflow, including vault creation on first run, cryptographic self-testing,
# and initializing in-memory session state for the active app session.
#
# Responsibilities include:
# - Rendering the password entry UI and "Forgot password?" entry point
# - Opening or creating the encrypted vault using the provided password
# - Running startup self-tests to verify key consistency and fail closed on
#   suspected corruption or mismatched credentials
# - Storing unlocked session state (DB connection, DMK, vault path, password)
#   in memory only via app_state (never persisted to disk)
# - Triggering the recovery-key ceremony on first-run vault creation
# - Loading the active patient profile and handing control back to the app shell
#   via callbacks (on_unlocked / on_show_recovery / on_open_forgot_password)
# -----------------------------------------------------------------------------

import os
import time
import asyncio
import logging
import flet as ft

from database import open_or_create_vault, get_profile, vault_exists
from core.startup import run_self_test
from core import app_state
from utils.airlock import import_profile, peek_manifest, find_merge_candidates
logger = logging.getLogger(__name__)

# ── Rate-limiting constants ──────────────────────────────────────────────────

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 30


def build_login_view(
    page: ft.Page,
    *,
    on_unlocked,               # callback: after session unlocked + profile loaded + dashboard shown
    on_show_recovery,          # callback: show recovery ceremony
    on_open_forgot_password,   # callback: open forgot dialog
    show_snack,                # function
):
    password_field = ft.TextField(label="Database Password", password=True)
    confirm_field = ft.TextField(label="Confirm Password", password=True, visible=False)
    error_text = ft.Text(color="red", visible=False)

    # ── Rate-limiting state ──────────────────────────────────────────────
    _login_state = {
        "failed_attempts": 0,
        "lockout_until": 0.0,
    }

    def _check_lockout() -> bool:
        """Return True if currently locked out (and update UI accordingly)."""
        remaining = _login_state["lockout_until"] - time.time()
        if remaining > 0:
            secs = int(remaining) + 1
            error_text.value = (
                f"Too many failed attempts. "
                f"Please wait {secs} second{'s' if secs != 1 else ''} before trying again."
            )
            error_text.visible = True
            password_field.disabled = True
            page.update()
            return True
        return False

    async def _run_lockout_countdown():
        """Live-update the lockout message every second until it expires."""
        password_field.disabled = True
        page.update()
        while True:
            remaining = _login_state["lockout_until"] - time.time()
            if remaining <= 0:
                break
            secs = int(remaining) + 1
            error_text.value = (
                f"Too many failed attempts. "
                f"Please wait {secs} second{'s' if secs != 1 else ''} before trying again."
            )
            error_text.visible = True
            page.update()
            await asyncio.sleep(1)
        # Lockout expired — clear the message and re-enable input
        error_text.value = ""
        error_text.visible = False
        password_field.disabled = False
        password_field.focus()
        page.update()

    def _record_failure():
        """Increment failure counter and engage lockout if threshold reached."""
        _login_state["failed_attempts"] += 1
        n = _login_state["failed_attempts"]
        remaining_attempts = MAX_FAILED_ATTEMPTS - n
        if n >= MAX_FAILED_ATTEMPTS:
            _login_state["lockout_until"] = time.time() + LOCKOUT_SECONDS
            _login_state["failed_attempts"] = 0  # reset counter for next window
            error_text.value = (
                f"Too many failed attempts. "
                f"You are temporarily locked out for {LOCKOUT_SECONDS} seconds."
            )
            error_text.visible = True
            # Start the live countdown
            page.run_task(_run_lockout_countdown)
        else:
            error_text.value = (
                f"Incorrect password. "
                f"{remaining_attempts} attempt{'s' if remaining_attempts != 1 else ''} remaining "
                f"before temporary lockout."
            )
        error_text.visible = True

    def _record_success():
        """Reset rate-limiting counters on successful login."""
        _login_state["failed_attempts"] = 0
        _login_state["lockout_until"] = 0.0
        error_text.visible = False

    # ── Detect whether vault already exists ──────────────────────────────
    _has_vault = vault_exists()

    # ── Unlock flow (existing vault) ─────────────────────────────────────
    def attempt_login(e=None):
        if _check_lockout():
            return

        pwd = (password_field.value or "").strip()
        if not pwd:
            return

        # Always clear any previous unlocked session first
        try:
            app_state.clear_unlocked_session(page)  # if you have it
        except Exception:
            # fallback if you don't have a helper
            page.db_connection = None
            page.db_key_raw = None
            page.db_path = None
            page.current_profile = None

        conn = None
        try:
            conn, dmk_raw, db_path, recovery_key = open_or_create_vault(
                pwd, allow_create=False
            )

            res = run_self_test(
                db_path=db_path,
                conn=conn,
                db_key_raw=dmk_raw,
                password=pwd,
            )
            if not res.ok:
                show_snack(page, res.user_message, "red")
                logger.warning("Self-test failed during login: %s", res.dev_details)

                try:
                    conn.close()
                except Exception:
                    pass

                _record_failure()
                password_field.value = ""
                page.update()
                return

            # store unlocked session (memory-only password)
            app_state.set_unlocked_session(
                page,
                conn=conn,
                dmk_raw=dmk_raw,
                db_path=db_path,
                password=pwd,
                recovery_key=recovery_key,
            )

            if recovery_key:
                on_show_recovery(recovery_key)
                # Wipe the sensitive key from page state now that the
                # ceremony callback holds its own copy.
                page.recovery_key_first_run = None

            # Load profile and proceed
            page.current_profile = get_profile(page.db_connection)
            _record_success()
            on_unlocked()

        except Exception as ex:
            msg = str(ex)
            if "Incorrect password" in msg:
                _record_failure()
            else:
                error_text.value = f"Login failed: {msg}"
                error_text.visible = True

            show_snack(page, f"Login failed: {ex}", "red")
            logger.warning("Login failed: %s", ex)

            # Always close the local conn if it exists
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

            # Always clear page/app session state so nothing remains accessible
            try:
                app_state.clear_session(page)
            except Exception:
                page.db_connection = None
                page.db_key_raw = None
                page.db_path = None
                page.db_password = None
                page.current_profile = None
                page.nav_rail = None
                page.content_area = None

            password_field.value = ""
            page.update()
            return

    password_field.on_submit = attempt_login

    # ── Create vault flow (first run) ────────────────────────────────────
    def attempt_create(e=None):
        pwd = (password_field.value or "").strip()
        confirm = (confirm_field.value or "").strip()

        if not pwd:
            error_text.value = "Please enter a password."
            error_text.visible = True
            page.update()
            return

        if pwd != confirm:
            error_text.value = "Passwords do not match. Please try again."
            error_text.visible = True
            page.update()
            return

        if len(pwd) < 6:
            error_text.value = "Password must be at least 6 characters."
            error_text.visible = True
            page.update()
            return

        conn = None
        try:
            conn, dmk_raw, db_path, recovery_key = open_or_create_vault(
                pwd, allow_create=True
            )

            res = run_self_test(
                db_path=db_path,
                conn=conn,
                db_key_raw=dmk_raw,
                password=pwd,
            )
            if not res.ok:
                show_snack(page, res.user_message, "red")
                logger.warning("Self-test failed during vault creation: %s", res.dev_details)
                try:
                    conn.close()
                except Exception:
                    pass
                password_field.value = ""
                confirm_field.value = ""
                page.update()
                return

            app_state.set_unlocked_session(
                page,
                conn=conn,
                dmk_raw=dmk_raw,
                db_path=db_path,
                password=pwd,
                recovery_key=recovery_key,
            )

            if recovery_key:
                on_show_recovery(recovery_key)
                page.recovery_key_first_run = None

            page.current_profile = get_profile(page.db_connection)
            on_unlocked()

        except Exception as ex:
            show_snack(page, f"Vault creation failed: {ex}", "red")
            logger.error("Vault creation failed: %s", ex)
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            try:
                app_state.clear_session(page)
            except Exception:
                pass
            password_field.value = ""
            confirm_field.value = ""
            page.update()

    confirm_field.on_submit = attempt_create

    # ── Upload Existing Profile (import from airlock ZIP) ─────────────────
    import_status = ft.Text("", size=14)
    import_progress = ft.ProgressRing(visible=False, width=20, height=20)

    # Password prompt dialog
    _import_pwd_field = ft.TextField(
        label="Password used when this profile was created",
        password=True,
        can_reveal_password=True,
        width=400,
        autofocus=True,
    )
    _import_pwd_error = ft.Text("", color="red")

    async def _do_import(_=None):
        pwd = (_import_pwd_field.value or "").strip()
        if not pwd:
            _import_pwd_error.value = "Please enter the password."
            page.update()
            return

        zip_path = getattr(page.mrma, "_airlock_zip_path", None)
        if not zip_path:
            return

        _import_pwd_dlg.open = False
        import_status.value = "Importing\u2026"
        import_status.color = ft.Colors.GREY
        import_progress.visible = True
        page.update()

        try:
            # Verify the ZIP can be read first
            try:
                manifest = peek_manifest(zip_path, pwd)
            except Exception:
                raise ValueError(
                    "Wrong password or file is not a valid profile backup."
                )

            # Open/create vault with this password
            conn, dmk_raw, db_path, recovery_key = open_or_create_vault(
                pwd, allow_create=True
            )
            data_dir = os.path.join(os.path.dirname(db_path), "data")
            os.makedirs(data_dir, exist_ok=True)
            # Check for duplicate patients by name+DOB
            merge_map = None
            candidates = find_merge_candidates(conn, manifest)
            if candidates:
                # Build merge description
                merge_event = asyncio.Event()
                merge_choice = {"merge": True}  # default to merge

                names = []
                for p in manifest.get("patients", []):
                    if p["id"] in candidates:
                        names.append(p.get("name", "Unknown"))

                def _on_merge(_=None):
                    merge_choice["merge"] = True
                    _merge_dlg.open = False
                    page.update()
                    merge_event.set()

                def _on_create_new(_=None):
                    merge_choice["merge"] = False
                    _merge_dlg.open = False
                    page.update()
                    merge_event.set()

                _merge_dlg = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Duplicate Patient Found"),
                    content=ft.Column([
                        ft.Text(
                            f"A patient matching '{', '.join(names)}' already exists "
                            f"in this vault.",
                            weight="bold",
                        ),
                        ft.Text(
                            "Would you like to merge the imported data into the "
                            "existing patient, or create a separate new patient?"
                        ),
                    ], tight=True, width=420),
                    actions=[
                        ft.TextButton("Create New", on_click=_on_create_new),
                        ft.FilledButton("Merge", on_click=_on_merge),
                    ],
                )
                append_dialog(page, _merge_dlg)
                _merge_dlg.open = True
                page.update()
                await merge_event.wait()

                if merge_choice["merge"]:
                    merge_map = candidates

            counts = import_profile(
                conn=conn,
                dmk_raw=dmk_raw,
                data_dir=data_dir,
                zip_path=zip_path,
                zip_password=pwd,
                merge_map=merge_map,
            )

            # Run self-test
            res = run_self_test(
                db_path=db_path,
                conn=conn,
                db_key_raw=dmk_raw,
                password=pwd,
            )
            if not res.ok:
                show_snack(page, res.user_message, "red")
                conn.close()
                import_status.value = "Import failed: self-test error."
                import_status.color = ft.Colors.RED
                import_progress.visible = False
                page.update()
                return

            # Store session
            app_state.set_unlocked_session(
                page,
                conn=conn,
                dmk_raw=dmk_raw,
                db_path=db_path,
                password=pwd,
                recovery_key=recovery_key,
            )

            if recovery_key:
                on_show_recovery(recovery_key)
                page.recovery_key_first_run = None

            page.current_profile = get_profile(page.db_connection)

            total = sum(counts.values())
            import_status.value = f"Imported {total} items successfully."
            import_status.color = ft.Colors.GREEN
            import_progress.visible = False
            page.update()

            show_snack(page, "Profile imported successfully.", ft.Colors.GREEN)
            on_unlocked()

        except Exception as ex:
            import_status.value = f"Import failed: {ex}"
            import_status.color = ft.Colors.RED
            import_progress.visible = False
            show_snack(page, f"Import failed: {ex}", ft.Colors.RED)
            page.update()

    _import_pwd_field.on_submit = lambda _: page.run_task(_do_import)

    def _close_import_dlg():
        _import_pwd_dlg.open = False
        page.update()

    _import_pwd_dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Enter profile password"),
        content=ft.Column(
            [
                ft.Text(
                    "Enter the password you used with this profile.\n"
                    "This same password will become your password on this computer.",
                    size=14,
                ),
                _import_pwd_field,
                _import_pwd_error,
            ],
            tight=True,
            width=420,
        ),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: _close_import_dlg()),
            ft.Button("Import", icon=ft.Icons.UPLOAD_FILE, on_click=lambda _: page.run_task(_do_import)),
        ],
    )
    append_dialog(page, _import_pwd_dlg)

    async def upload_profile_click(e):
        files = await ft.FilePicker().pick_files(
            dialog_title="Select profile backup file",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["zip"],
            allow_multiple=False,
        )
        if not files:
            return
        picked = files[0]
        zip_path = getattr(picked, "path", None) or getattr(picked, "file_path", None)
        if not zip_path:
            return

        page.mrma._airlock_zip_path = zip_path

        _import_pwd_field.value = ""
        _import_pwd_error.value = ""
        _import_pwd_dlg.open = True
        page.update()

    # ── Build the login view ─────────────────────────────────────────────
    # Choose button/label based on whether a vault already exists
    if _has_vault:
        action_button = ft.Button("Unlock Database", on_click=attempt_login)
        heading_text = "Secure Login"
        confirm_field.visible = False
    else:
        action_button = ft.Button("Create Vault", on_click=attempt_create)
        heading_text = "Create Your Vault"
        confirm_field.visible = True

    if _has_vault:
        _login_help = make_info_button(page, "Welcome Back", [
            "If you forgot your password, click \"Forgot password?\" to recover access using the recovery key given to you when you created your account.",
            "Your data never leaves this device. Everything is encrypted locally using AES-256.",
            "\"Upload Existing Profile\" lets you import a backup from another device (requires the backup's password).",
        ])
    else:
        _login_help = make_info_button(page, "Getting Started", [
            "Welcome to the Local Patient Advocate! This app helps you organize and manage your medical records securely on your own device.",
            "Choose a strong password (6+ characters). This password encrypts everything, there is no cloud account and no way to reset it remotely.",
            "After creating your vault you'll receive a recovery key. Save it somewhere safe (print it, write it down, store it on a USB). It's your only backup if you forget your password.",
            "Once inside, you can upload medical documents (PDFs, images), and the app will automatically extract health data like medications, allergies, and lab results.",
            "If you already have a backup from another computer, use \"Upload Existing Profile\" instead of creating a new vault.",
        ])

    login_view = ft.Column(
        [
            ft.Row(
                [ft.Container(expand=True), _login_help],
                alignment=ft.MainAxisAlignment.END,
            ),
            ft.Container(expand=True),  # top spacer
            ft.Icon(ft.Icons.SECURITY, size=64, color="blue"),
            ft.Text(heading_text, size=30),
            password_field,
            confirm_field,
            action_button,
            error_text,
            ft.TextButton("Forgot password?", on_click=lambda e: on_open_forgot_password()),
            ft.Container(expand=True),  # bottom spacer pushes upload to bottom
            ft.TextButton(
                "Upload Existing Profile",
                icon=ft.Icons.UPLOAD_FILE,
                on_click=upload_profile_click,
                tooltip="Import a profile backup from another computer",
            ),
            ft.Row([
                import_progress,
                import_status,
            ], alignment=ft.MainAxisAlignment.CENTER),
            ft.Container(height=20),  # small bottom padding
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )

    # Expose login controls to main.py for logout/reset via the
    # established page.mrma state container (not fragile _ attrs).
    page.mrma.login_password_field = password_field
    page.mrma.login_error_text = error_text

    return login_view