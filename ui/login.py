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

from __future__ import annotations
import os
import asyncio
import flet as ft

from database import open_or_create_vault, get_profile, resource_path
from core.startup import run_self_test
from core import app_state
from utils.airlock import import_profile, peek_manifest


def build_login_view(
    page: ft.Page,
    *,
    on_unlocked,               # callback: after session unlocked + profile loaded + dashboard shown
    on_show_recovery,          # callback: show recovery ceremony
    on_open_forgot_password,   # callback: open forgot dialog
    show_snack,                # function
):
    password_field = ft.TextField(label="Database Password", password=True)
    error_text = ft.Text(color="red", visible=False)

    def attempt_login(e=None):
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
            conn, dmk_raw, db_path, recovery_key = open_or_create_vault(pwd)

            res = run_self_test(
                db_path=db_path,
                conn=conn,
                db_key_raw=dmk_raw,
                password=pwd,
            )
            if not res.ok:
                show_snack(page, res.user_message, "red")
                print("SELFTEST FAIL:", res.dev_details)

                try:
                    conn.close()
                except Exception:
                    pass

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

            # Load profile and proceed
            page.current_profile = get_profile(page.db_connection)
            on_unlocked()

        except Exception as ex:
            show_snack(page, f"Login failed: {ex}", "red")
            print("LOGIN FAIL:", ex)

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

    # ── Upload Existing Profile (import from airlock ZIP) ─────────────
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

    def _do_import(_=None):
        pwd = (_import_pwd_field.value or "").strip()
        if not pwd:
            _import_pwd_error.value = "Please enter the password."
            page.update()
            return

        zip_path = getattr(page, "_airlock_zip_path", None)
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
            conn, dmk_raw, db_path, recovery_key = open_or_create_vault(pwd)

            data_dir = os.path.join(os.path.dirname(db_path), "data")
            os.makedirs(data_dir, exist_ok=True)

            counts = import_profile(
                conn=conn,
                dmk_raw=dmk_raw,
                data_dir=data_dir,
                zip_path=zip_path,
                zip_password=pwd,
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

    _import_pwd_field.on_submit = _do_import

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
            ft.Button("Import", icon=ft.Icons.UPLOAD_FILE, on_click=_do_import),
        ],
    )
    page.overlay.append(_import_pwd_dlg)

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

        page._airlock_zip_path = zip_path

        _import_pwd_field.value = ""
        _import_pwd_error.value = ""
        _import_pwd_dlg.open = True
        page.update()

    login_view = ft.Column(
        [
            ft.Icon(ft.Icons.SECURITY, size=64, color="blue"),
            ft.Text("Secure Login", size=30),
            password_field,
            ft.Button("Unlock Database", on_click=attempt_login),
            error_text,
            ft.TextButton("Forgot password?", on_click=lambda e: on_open_forgot_password()),
            ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
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
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # Expose a small API back to main for logout/reset
    login_view._password_field = password_field
    login_view._error_text = error_text
    login_view._attempt_login = attempt_login

    return login_view