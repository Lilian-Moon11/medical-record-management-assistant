# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Application entry point and composition root.
#
# This module wires together the UI shell, routing, dialogs, login flow,
# and session state management to form the running Medical Record Management Assistant app.
#
# Responsibilities include:
# - Initializing the Flet window and root container
# - Initializing centralized page/session state via app_state
# - Creating and linking routing + settings logic (view factory + theme apply)
# - Registering global dialogs once (recovery ceremony, forgot password)
# - Building the login view and handling post-unlock transitions
# - Coordinating logout by clearing sensitive session state and returning
#   the user safely to the login screen
#
# Architectural role:
# - Acts as the composition layer (not business logic)
# - Keeps modules decoupled by passing explicit callbacks between:
#     - login (authentication)
#     - dialogs (recovery flows)
#     - navigation (dashboard shell)
#     - routing (view resolution + settings application)
# - Ensures all sensitive key material remains memory-only and is cleared
#   on logout through app_state
# -----------------------------------------------------------------------------


import os
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")  # no stale .pyc after edits

import flet as ft

from utils.ui_helpers import pt_scale, show_snack

from core import paths  # noqa: F401 — bootstraps app directories on import
from core import app_state
from ui import routing, navigation, dialogs, login

import glob
import tempfile
import logging
from logging.handlers import RotatingFileHandler
import time
import asyncio
from database import get_setting

def setup_global_logging():
    log_dir = paths.app_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "mrma.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

    # Silence noisy downstream libraries (especially the harmless WinError 10054 in asyncio)
    logging.getLogger("flet").setLevel(logging.WARNING)
    logging.getLogger("flet_core").setLevel(logging.WARNING)
    logging.getLogger("flet_desktop").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

setup_global_logging()

def cleanup_decrypted_temp_files():
    try:
        tmp_dir = tempfile.gettempdir()
        pattern = os.path.join(tmp_dir, "mrma_decrypted_*.*")
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except OSError:
                pass
    except Exception:
        pass

import atexit
atexit.register(cleanup_decrypted_temp_files)

def main(page: ft.Page):
    # Aggressively wipe legacy temporary files on boot
    cleanup_decrypted_temp_files()
    
    # Also wipe them when the user exits the app
    page.on_disconnect = lambda e: cleanup_decrypted_temp_files()
    # --- window / shell ---
    page.title = "Medical Record Management Assistant"
    page.window.width = 1000
    page.window.height = 800
    page.theme_mode = ft.ThemeMode.SYSTEM

    # --- state ---
    app_state.init_page_state(page)

    page.mrma._last_activity = time.time()

    def _update_activity(e=None):
        page.mrma._last_activity = time.time()
        
    page.on_keyboard_event = _update_activity

    page.root = ft.Container(expand=True, on_hover=_update_activity)
    page.add(page.root)

    # --- routing & settings ---
    # apply_settings needs get_view_for_index; get_view_for_index needs apply_settings callback
    get_view_for_index = None

    def apply_settings_callback():
        routing.apply_settings(page, get_view_for_index=get_view_for_index)

    get_view_for_index = routing.make_get_view_for_index(page, apply_settings_callback=apply_settings_callback)
    page.mrma._get_view_for_index = get_view_for_index

    # --- dialogs (register once; safe to call multiple times) ---
    dialogs.ensure_dialogs_registered(page, s=pt_scale, show_snack=show_snack)

    # --- logout (returns to login view) ---
    def logout():
        app_state.clear_session(page)

        # reset login UI
        try:
            login_view._password_field.value = ""
            login_view._error_text.visible = False
        except Exception:
            pass

        page.root.content = login_view
        page.update()

    # --- after unlock ---
    def on_unlocked():
        apply_settings_callback()
        navigation.show_main_dashboard(page, get_view_for_index=get_view_for_index)

        # Check backup reminder
        try:
            import time
            from database import set_setting, get_setting
            from utils.ui_helpers import append_dialog
            last_prompt_str = get_setting(page.db_connection, "ui.last_backup_prompt_unix", "0")
            if time.time() - float(last_prompt_str) > 604800:
                set_setting(page.db_connection, "ui.last_backup_prompt_unix", str(time.time()))
                def _close_dlg(e):
                    dlg.open = False
                    page.update()
                
                def _go_settings(e):
                    _close_dlg(e)
                    page.nav_rail.selected_index = 7
                    page.content_area.content = get_view_for_index(7)
                    page.update()

                dlg = ft.AlertDialog(
                    title=ft.Text("Backup Reminder"),
                    content=ft.Text("It has been over a week since your last vault backup. Would you like to export your vault to a secure ZIP file now?"),
                    actions=[
                        ft.TextButton("Dismiss", on_click=_close_dlg),
                        ft.FilledButton("Go to Settings", on_click=_go_settings),
                    ],
                )
                append_dialog(page, dlg)
                dlg.open = True
                page.update()
        except Exception:
            pass

    def on_show_recovery(recovery_key: str):
        dialogs.show_recovery_ceremony(page, recovery_key, s=pt_scale, show_snack=show_snack)

    def on_open_forgot_password():
        dialogs.open_forgot_password(page, s=pt_scale, show_snack=show_snack)

    async def session_watchdog():
        while True:
            await asyncio.sleep(60) # check every minute
            if not app_state.is_unlocked(page):
                continue
            
            timeout_str = get_setting(page.db_connection, "ui.auto_lock_minutes", "15")
            try:
                timeout_mins = int(timeout_str)
            except ValueError:
                timeout_mins = 15
                
            if timeout_mins <= 0:
                continue
                
            elapsed = time.time() - getattr(page.mrma, "_last_activity", time.time())
            if elapsed > (timeout_mins * 60):
                show_snack(page, f"Session expired after {timeout_mins} minutes of inactivity.", "orange")
                logout()

    page.run_task(session_watchdog)

    # --- login view ---
    login_view = login.build_login_view(
        page,
        on_unlocked=on_unlocked,
        on_show_recovery=on_show_recovery,
        on_open_forgot_password=on_open_forgot_password,
        show_snack=show_snack,
    )

    # --- start at login ---
    page.root.content = login_view
    page.update()


if __name__ == "__main__":
    ft.run(main)