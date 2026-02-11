# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# App entry point and UI shell for Local Patient Advocate.
#
# This module initializes the Flet window and global page state, manages secure
# vault login/recovery, applies persisted UI preferences, and routes navigation
# to the primary view modules.
#
# Responsibilities include:
# - Initializing the Flet page/window and app-wide state (DB connection, profile,
#   accessibility flags, UI scale, vault path/key material)
# - Handling secure unlock flows:
#   - normal password unlock (create or open vault)
#   - recovery-key unlock + password reset + recovery key rotation
# - Running cryptographic self-tests after unlock/recovery to fail closed on
#   key mismatch or suspected corruption
# - Loading and applying persisted UI settings (theme, high contrast, large text)
#   and refreshing the active view immediately on change
# - Providing top-level navigation (NavigationRail) and view routing
#   (Overview / Patient Info / Documents / Settings)
# - Centralizing error handling for view rendering and critical startup failures
# - Retaining the active database password in memory for the session only
#   (never persisted) to support encrypted file access
# -----------------------------------------------------------------------------

import flet as ft
import traceback
from database import open_or_create_vault, open_vault_with_recovery, get_profile, get_setting
from views.documents import get_documents_view
from views.overview import get_overview_view
from views.patient_info import get_patient_info_view
from views.settings import get_settings_view
from crypto.keybag import set_new_password, rotate_recovery_key_with_old
from utils import s, show_snack
from crypto.selftest import run_crypto_self_test

def main(page: ft.Page):
    page.title = "Local Patient Advocate"
    page.window.width = 1000
    page.window.height = 800
    page.root = ft.Container(expand=True)
    page.add(page.root)
    page.theme_mode = ft.ThemeMode.SYSTEM

    # --- STATE ---
    page.current_profile = None
    page.db_connection = None
    page.is_high_contrast = False
    page.ui_scale = 1.0
    page.db_key_raw = None
    page.db_path = None
    page.recovery_key_first_run = None


    # --- MAIN LOGIC ---
    def apply_settings():
        if not page.db_connection: return
        
        try:
            theme_pref = get_setting(page.db_connection, "ui.theme", "system")
            high_contrast = get_setting(page.db_connection, "ui.high_contrast", "0") == "1"
            large_text = get_setting(page.db_connection, "ui.large_text", "0") == "1"

            page.theme_mode = {
                "dark": ft.ThemeMode.DARK,
                "light": ft.ThemeMode.LIGHT,
                "system": ft.ThemeMode.SYSTEM
            }.get(theme_pref, ft.ThemeMode.SYSTEM)

            if high_contrast:
                page.theme = ft.Theme(color_scheme_seed=ft.Colors.YELLOW)
            else:
                page.theme = None
                
            page.is_high_contrast = high_contrast
            page.ui_scale = 1.25 if large_text else 1.0
            
            # Refresh UI 
            if hasattr(page, "nav_rail") and page.nav_rail and hasattr(page, "content_area") and page.content_area:
                idx = page.nav_rail.selected_index
                page.content_area.content = get_view_for_index(idx)
                page.content_area.update()
                
            page.update()
        except Exception as e:
            print(f"Settings Error: {e}")

    def get_view_for_index(index):
        try:
            # 0: Overview
            if index == 0: 
                return get_overview_view(page)
            # 1: Patient Info
            elif index == 1: 
                return get_patient_info_view(page)
            # 2: Documents
            elif index == 2: 
                return get_documents_view(page)
            # 3: Settings
            elif index == 3: 
                return get_settings_view(page, apply_settings_callback=apply_settings)
            
            return ft.Text("Unknown View")
        except Exception as ex:
            return ft.Column([
                ft.Icon(ft.Icons.ERROR, color="red", size=40),
                ft.Text(f"Error loading view #{index}:", color="red", weight="bold"),
                ft.Text(str(ex), color="red"),
                ft.Text(traceback.format_exc(), size=10, font_family="Consolas")
            ], scroll=True)

    def show_critical_error(ex: Exception):
        # ✅ No page.clean(); just replace root content
        page.root.content = ft.Container(
            padding=20,
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.ERROR, color="red", size=48),
                    ft.Text("CRITICAL ERROR", color="red", size=24, weight="bold"),
                    ft.Text(str(ex)),
                    ft.Text(traceback.format_exc(), size=10, font_family="Consolas"),
                ],
                scroll=True,
            ),
        )
        page.update()

    def show_main_dashboard():
        try:
            content_area = ft.Container(expand=True, padding=20)
            page.content_area = content_area

            def nav_change(e):
                content_area.content = get_view_for_index(e.control.selected_index)
                content_area.update()

            prev_idx = getattr(getattr(page, "nav_rail", None), "selected_index", 0) or 0

            rail = ft.NavigationRail(
                selected_index=prev_idx,
                label_type=ft.NavigationRailLabelType.ALL,
                min_width=100,
                min_extended_width=200,
                destinations=[
                    ft.NavigationRailDestination(icon=ft.Icons.DASHBOARD, label="Overview"),
                    ft.NavigationRailDestination(icon=ft.Icons.BADGE, label="Patient Info"),
                    ft.NavigationRailDestination(icon=ft.Icons.FOLDER, label="Documents"),
                    ft.NavigationRailDestination(icon=ft.Icons.SETTINGS, label="Settings"),
                ],
                on_change=nav_change,
            )
            page.nav_rail = rail
            
            # Initial load
            content_area.content = get_view_for_index(prev_idx)

            dashboard = ft.Row([rail, ft.VerticalDivider(width=1), content_area], expand=True)

            page.root.content = dashboard
            page.update()
            
        except Exception as ex:
            show_critical_error(ex)

    def open_forgot_password(e=None):
        # Create once, reuse forever
        if not hasattr(page, "_forgot_dlg") or page._forgot_dlg is None:
            page._forgot_recovery_field = ft.TextField(
                label="Recovery key",
                password=True,
                can_reveal_password=True,
                width=420,
            )
            page._forgot_new_pwd_field = ft.TextField(
                label="New database password",
                password=True,
                can_reveal_password=True,
                width=420,
            )
            page._forgot_new_pwd2_field = ft.TextField(
                label="Confirm new password",
                password=True,
                can_reveal_password=True,
                width=420,
            )
            page._forgot_status = ft.Text("", color="red")

            def close(_=None):
                page._forgot_dlg.open = False
                page.update()

            def do_recover(_):
                rk = (page._forgot_recovery_field.value or "").strip()
                p1 = page._forgot_new_pwd_field.value or ""
                p2 = page._forgot_new_pwd2_field.value or ""

                if not rk:
                    page._forgot_status.value = "Recovery key is required."
                    page.update()
                    return
                if not p1 or len(p1) < 8:
                    page._forgot_status.value = "New password must be at least 8 characters."
                    page.update()
                    return
                if p1 != p2:
                    page._forgot_status.value = "Passwords do not match."
                    page.update()
                    return

                try:
                    conn, dmk_raw, db_path = open_vault_with_recovery(rk)
                    set_new_password(db_path, dmk_raw, p1)
                    new_rk = rotate_recovery_key_with_old(db_path, dmk_raw, rk)

                    # Switch session
                    page.db_connection = conn
                    page.db_key_raw = dmk_raw
                    page.db_path = db_path
                    page.db_password = p1

                    res = run_crypto_self_test(
                        db_path=page.db_path,
                        conn=page.db_connection,
                        db_key_raw=page.db_key_raw,
                        password=page.db_password,
                    )
                    if not res.ok:
                        show_snack(page, res.user_message, "red")
                        print("SELFTEST FAIL (RECOVERY):", res.dev_details)
                        return

                    page.recovery_key_first_run = new_rk

                    apply_settings()
                    page.current_profile = get_profile(page.db_connection)
                    show_main_dashboard()

                    show_snack(page, "Password reset. A new recovery key was generated.", "green")

                    # Clear fields
                    page._forgot_recovery_field.value = ""
                    page._forgot_new_pwd_field.value = ""
                    page._forgot_new_pwd2_field.value = ""
                    page._forgot_status.value = ""

                    close()
                    show_recovery_ceremony(new_rk)

                except Exception as ex:
                    page._forgot_status.value = str(ex)
                    page.update()

            page._forgot_dlg = ft.AlertDialog(
                modal=False,
                title=ft.Text("Recover account"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Enter your recovery key and choose a new password.\n"
                            "After recovery, a NEW recovery key will be generated."
                        ),
                        page._forgot_recovery_field,
                        page._forgot_new_pwd_field,
                        page._forgot_new_pwd2_field,
                        page._forgot_status,
                    ],
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close),
                    ft.Button("Reset password", icon=ft.Icons.LOCK_RESET, on_click=do_recover),
                ],
                on_dismiss=close,
            )

            page.overlay.append(page._forgot_dlg)

        # Reset UI state each open
        page._forgot_status.value = ""
        page._forgot_dlg.open = True
        page.update()  

    def attempt_login(e):
        pwd = (password_field.value or "").strip()
        if not pwd:
            return

        # open_or_create_vault returns (conn, db_key_raw, db_path, recovery_key)
        conn = None
        try:
            conn, dmk_raw, db_path, recovery_key = open_or_create_vault(pwd)

            res = run_crypto_self_test(
                db_path=db_path,
                conn=conn,
                db_key_raw=dmk_raw,
                password=pwd,
            )

            if not res.ok:
                show_snack(page, res.user_message, "red")
                print("SELFTEST FAIL:", res.dev_details)

                # Hard stop back to login
                try:
                    conn.close()
                except Exception:
                    pass

                password_field.value = ""
                page.update()
                return

            page.db_connection = conn
            page.db_key_raw = dmk_raw
            page.db_path = db_path
            page.db_password = pwd  # kept ONLY in memory
            page.recovery_key_first_run = recovery_key

            if recovery_key:
                show_recovery_ceremony(recovery_key)

            apply_settings()
            page.current_profile = get_profile(page.db_connection)
            show_main_dashboard()

        except Exception as ex:
            # open_or_create_vault itself can fail (bad password, corrupted db, etc.)
            show_snack(page, f"Login failed: {ex}", "red")
            print("LOGIN FAIL:", ex)

            try:
                if conn:
                    conn.close()
            except Exception:
                pass

            password_field.value = ""
            page.update()
            return

    def show_recovery_ceremony(recovery_key: str):
        # Create dialog once and reuse it
        if not hasattr(page, "_recovery_dlg") or page._recovery_dlg is None:

            page._recovery_key_text = ft.Text("", selectable=True, font_family="Consolas", size=s(page, 14))

            page._recovery_saved_check = ft.Checkbox(
                label="I saved this recovery key somewhere safe.",
                value=False,
            )

            page._recovery_status = ft.Text("", color="red", size=s(page, 12))

            async def copy_key(_):
                try:
                    await ft.Clipboard().set(recovery_key)
                    show_snack(page, "Recovery key copied to clipboard.", "green")
                except Exception as ex:
                    print("CLIPBOARD FAIL:", ex)
                    show_snack(page, "Could not copy to clipboard on this platform.", "orange")

            def close(_=None):
                if not page._recovery_saved_check.value:
                    page._recovery_status.value = "Please confirm you saved it before closing."
                    try:
                        page._recovery_dlg.update()
                    except Exception:
                        pass
                    page.update()
                    return

                page._recovery_dlg.open = False
                try:
                    page._recovery_dlg.update()
                except Exception:
                    pass
                page.update()

            page._recovery_done_btn = ft.Button(
                "I saved it",
                icon=ft.Icons.CHECK,
                on_click=close,
                disabled=True,
            )

            def on_check(_):
                page._recovery_done_btn.disabled = not bool(page._recovery_saved_check.value)
                try:
                    page._recovery_dlg.update()
                except Exception:
                    pass
                page.update()

            page._recovery_saved_check.on_change = on_check

            page._recovery_dlg = ft.AlertDialog(
                modal=False,  # same style as your delete dialog
                title=ft.Text("Save your recovery key", size=s(page, 18), weight="bold"),
                content=ft.Column(
                    [
                        ft.Text(
                            "This key lets you recover the vault if you forget your password.\n"
                            "If you lose BOTH your password and this key, the vault cannot be recovered.",
                            size=s(page, 14),
                        ),
                        ft.Container(
                            padding=s(page, 10),
                            border=ft.Border.all(2, ft.Colors.GREY),
                            border_radius=8,
                            content=page._recovery_key_text,
                        ),
                        ft.Row(
                            [
                                ft.Button("Copy", icon=ft.Icons.CONTENT_COPY, on_click=copy_key),
                            ]
                        ),
                        page._recovery_saved_check,
                        page._recovery_status,
                    ],
                    tight=True,
                    spacing=s(page, 10),
                ),
                actions=[page._recovery_done_btn],
                on_dismiss=close,
            )

            page.overlay.append(page._recovery_dlg)

        # Update values for this run
        page._recovery_key_text.value = recovery_key
        page._recovery_saved_check.value = False
        page._recovery_status.value = ""
        page._recovery_done_btn.disabled = True

        # Open it
        page._recovery_dlg.open = True
        try:
            page._recovery_dlg.update()
        except Exception:
            pass
        page.update()

    def logout():
        # Close DB connection
        try:
            if page.db_connection:
                page.db_connection.close()
        except Exception:
            pass

        # Clear sensitive per-user state
        page.db_connection = None
        page.current_profile = None

        # Clear dashboard shell references
        if hasattr(page, "nav_rail"):
            page.nav_rail = None
        if hasattr(page, "content_area"):
            page.content_area = None

        # Reset UI (back to login)
        password_field.value = ""
        error_text.visible = False
        page.root.content = login_view
        page.update()
        page.db_password = None

    # --- STARTUP UI ---
    password_field = ft.TextField(label="Database Password", password=True, on_submit=attempt_login)
    error_text = ft.Text(color="red", visible=False)

    login_view = ft.Column(
        [
            ft.Icon(ft.Icons.SECURITY, size=64, color="blue"),
            ft.Text("Secure Login", size=30),
            password_field,
            ft.Button("Unlock Database", on_click=attempt_login),
            error_text,
            ft.TextButton("Forgot password?", on_click=open_forgot_password),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    page.root.content = login_view
    page.update()

if __name__ == "__main__":
    ft.run(main)