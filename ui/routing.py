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
from views.vaccines import get_vaccines_view
from views.family_history import get_family_history_view



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
        # Large text: stored as float string e.g. "1.0", "1.25"; legacy "0"/"1" handled
        _lt_raw = get_setting(page.db_connection, "ui.large_text", "1.0")
        try:
            _lt_scale = float(_lt_raw)
            # Legacy: "0" -> 1.0, "1" -> 1.25
            if _lt_scale == 0.0:
                _lt_scale = 1.0
            elif _lt_scale == 1.0 and _lt_raw == "1":
                _lt_scale = 1.25
        except ValueError:
            _lt_scale = 1.0
        page.ui_scale = _lt_scale

        # High contrast overrides theme_mode to always use dark (black bg + bright text).
        # When HC is off, honour the user's selected theme.
        if high_contrast:
            page.theme_mode = ft.ThemeMode.DARK
            page.bgcolor = "#000000"

            _hc_scheme = ft.ColorScheme(
                primary="#FFE633",
                on_primary="#000000",
                secondary="#FFE633",
                on_secondary="#000000",
                primary_container="#2a2600",
                on_primary_container="#FFE633",
                surface="#111111",
                on_surface="#FFFFFF",
                on_surface_variant="#DDDDDD",
                surface_container="#111111",
                surface_container_high="#1a1a1a",
                surface_container_highest="#222222",
                surface_container_low="#0a0a0a",
                surface_container_lowest="#000000",
                outline="#FFFFFF",
                outline_variant="#555555",
                error="#FF6B6B",
                on_error="#000000",
            )
            _hc_theme = ft.Theme(
                color_scheme=_hc_scheme,
                text_theme=ft.TextTheme(
                    body_large=ft.TextStyle(color="#FFFFFF"),
                    body_medium=ft.TextStyle(color="#FFFFFF"),
                    body_small=ft.TextStyle(color="#DDDDDD"),
                    label_large=ft.TextStyle(color="#FFFFFF"),
                    label_medium=ft.TextStyle(color="#FFFFFF"),
                    title_large=ft.TextStyle(color="#FFE633"),
                    title_medium=ft.TextStyle(color="#FFE633"),
                    headline_medium=ft.TextStyle(color="#FFE633"),
                ),
            )
            page.theme = _hc_theme
            page.dark_theme = _hc_theme
        else:
            page.theme_mode = {
                "dark": ft.ThemeMode.DARK,
                "light": ft.ThemeMode.LIGHT,
                "system": ft.ThemeMode.SYSTEM,
            }.get(theme_pref, ft.ThemeMode.SYSTEM)
            page.bgcolor = None
            page.theme = None
            page.dark_theme = None

        page.is_high_contrast = high_contrast

        # Provenance columns in Health Record / Labs / Providers
        page._show_source = get_setting(page.db_connection, "ui.show_source", "0") == "1"
        page._show_updated = get_setting(page.db_connection, "ui.show_updated", "0") == "1"

        # Flush page-level changes (theme_mode, bgcolor, theme) first.
        page.update()

        # Rebuild the current view so scale/source/updated columns reflect new state.
        if getattr(page, "nav_rail", None) and getattr(page, "content_area", None):
            idx = page.nav_rail.selected_index
            page.content_area.content = get_view_for_index(idx)
            page.content_area.update()

    except Exception as e:
        import traceback
        print(f"[apply_settings] Error: {e}\n{traceback.format_exc()}")




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
                return get_vaccines_view(page)
            elif index == 6:
                return get_family_history_view(page)
            elif index == 7:
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