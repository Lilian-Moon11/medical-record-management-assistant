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
import flet as ft

from database import open_or_create_vault, get_profile
from core.startup import run_self_test
from core import app_state


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

    login_view = ft.Column(
        [
            ft.Icon(ft.Icons.SECURITY, size=64, color="blue"),
            ft.Text("Secure Login", size=30),
            password_field,
            ft.Button("Unlock Database", on_click=attempt_login),
            error_text,
            ft.TextButton("Forgot password?", on_click=lambda e: on_open_forgot_password()),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # Expose a small API back to main for logout/reset
    login_view._password_field = password_field
    login_view._error_text = error_text
    login_view._attempt_login = attempt_login

    return login_view