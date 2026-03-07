# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# UI settings application and view routing helpers.
#
# This module centralizes two related concerns:
# - Reading persisted UI preferences from the database and applying them to the
#   active Flet page (theme mode, high contrast, large text / UI scale)
# - Providing a safe view-router function that maps NavigationRail indexes to
#   view builders, with defensive error handling for rendering failures
#
# Responsibilities include:
# - Loading settings from app_settings via get_setting with sensible defaults
# - Applying theme_mode and optional high-contrast theme overrides
# - Updating accessibility-related page state (is_high_contrast, ui_scale)
# - Refreshing the currently displayed view when the dashboard shell exists
# - Returning a page-bound get_view_for_index(index) function that:
#   - Routes to Overview / Health Record / Documents / Settings views
#   - Wraps view creation in a friendly error screen with traceback for debugging
# -----------------------------------------------------------------------------

from __future__ import annotations
import traceback
import flet as ft

from database import get_setting
from core import app_state
from views.documents import get_documents_view
from views.overview import get_overview_view
from views.health_record import get_health_record_view
from views.settings import get_settings_view
from views.providers import get_providers_view
from views.labs import get_labs_view


def apply_settings(page, *, get_view_for_index):
    """
    Reads persisted settings from DB and applies them to the page.
    Refreshes current view if dashboard is present.
    """
    if not page.db_connection:
        return

    try:
        theme_pref = get_setting(page.db_connection, "ui.theme", "system")
        high_contrast = get_setting(page.db_connection, "ui.high_contrast", "0") == "1"
        large_text = get_setting(page.db_connection, "ui.large_text", "0") == "1"

        page.theme_mode = {
            "dark": ft.ThemeMode.DARK,
            "light": ft.ThemeMode.LIGHT,
            "system": ft.ThemeMode.SYSTEM,
        }.get(theme_pref, ft.ThemeMode.SYSTEM)

        if high_contrast:
            page.theme = ft.Theme(color_scheme_seed=ft.Colors.YELLOW)
        else:
            page.theme = None

        page.is_high_contrast = high_contrast
        page.ui_scale = 1.25 if large_text else 1.0

        # Provenance columns in Health Record / Labs / Providers
        page._show_source = get_setting(page.db_connection, "ui.show_source", "0") == "1"
        page._show_updated = get_setting(page.db_connection, "ui.show_updated", "0") == "1"

        # Refresh UI if dashboard exists
        if getattr(page, "nav_rail", None) and getattr(page, "content_area", None):
            idx = page.nav_rail.selected_index
            page.content_area.content = get_view_for_index(idx)
            page.content_area.update()

        page.update()
    except Exception as e:
        print(f"Settings Error: {e}")


def make_get_view_for_index(page, *, apply_settings_callback):
    """
    Returns a function get_view_for_index(index) bound to the page.
    """
    def get_view_for_index(index: int):
         # BLOCK ACCESS IF VAULT NOT UNLOCKED
        if not app_state.is_unlocked(page):
            return ft.Column(
                [
                    ft.Icon(ft.Icons.LOCK, size=40, color="red"),
                    ft.Text("Vault is locked.", size=20, weight="bold"),
                    ft.Text("Please log in to access your health record"),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        
        try:
            if index == 0:
                return get_overview_view(page)
            elif index == 1:
                return get_health_record_view(page)
            elif index == 2:
                return get_labs_view(page)
            elif index == 3:
                return get_documents_view(page)
            elif index == 4:
                return get_providers_view(page)
            elif index == 5:
                return get_settings_view(page, apply_settings_callback=apply_settings_callback)

            return ft.Text("Unknown View")
        except Exception as ex:
            return ft.Column(
                [
                    ft.Icon(ft.Icons.ERROR, color="red", size=40),
                    ft.Text(f"Error loading view #{index}:", color="red", weight="bold"),
                    ft.Text(str(ex), color="red"),
                    ft.Text(traceback.format_exc(), size=10, font_family="Consolas"),
                ],
                scroll=True,
            )

    return get_view_for_index