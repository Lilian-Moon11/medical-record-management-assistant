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
# and session state management to form the running Local Patient Advocate app.
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


import flet as ft

from utils.ui_helpers import pt_scale, show_snack

from core import app_state
from ui import routing, navigation, dialogs, login


def main(page: ft.Page):
    # --- window / shell ---
    page.title = "Local Patient Advocate"
    page.window.width = 1000
    page.window.height = 800
    page.theme_mode = ft.ThemeMode.SYSTEM

    page.root = ft.Container(expand=True)
    page.add(page.root)

    # --- state ---
    app_state.init_page_state(page)

    # --- routing & settings ---
    # apply_settings needs get_view_for_index; get_view_for_index needs apply_settings callback
    get_view_for_index = None

    def apply_settings_callback():
        routing.apply_settings(page, get_view_for_index=get_view_for_index)

    get_view_for_index = routing.make_get_view_for_index(page, apply_settings_callback=apply_settings_callback)

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

    def on_show_recovery(recovery_key: str):
        dialogs.show_recovery_ceremony(page, recovery_key, s=pt_scale, show_snack=show_snack)

    def on_open_forgot_password():
        dialogs.open_forgot_password(page, s=pt_scale, show_snack=show_snack)

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