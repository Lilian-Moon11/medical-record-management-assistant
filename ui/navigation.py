# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# App shell layout helpers for navigation and critical error display.
#
# This module provides small, reusable UI helpers that render the main dashboard
# frame (NavigationRail + content area) and a simplified “critical error” screen
# for unrecoverable failures.
#
# Responsibilities include:
# - Rendering a consistent main layout container with persistent navigation
# - Routing NavigationRail changes to the active view via a provided callback
# - Preserving the previously selected navigation index when rebuilding the UI
# - Replacing the app root content with a clear, accessible error screen when
#   critical exceptions occur during startup or dashboard construction
# -----------------------------------------------------------------------------

from __future__ import annotations
import flet as ft


def show_critical_error(page: ft.Page, ex: Exception):
    page.root.content = ft.Container(
        padding=20,
        content=ft.Column(
            [
                ft.Icon(ft.Icons.ERROR, color="red", size=48),
                ft.Text("CRITICAL ERROR", color="red", size=24, weight="bold"),
                ft.Text(str(ex)),
            ],
            scroll=True,
        ),
    )
    page.update()


def show_main_dashboard(page: ft.Page, *, get_view_for_index):
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
                ft.NavigationRailDestination(icon=ft.Icons.BADGE, label="Health Record"),
                ft.NavigationRailDestination(icon=ft.Icons.SHOW_CHART, label="Vitals & Labs"),
                ft.NavigationRailDestination(icon=ft.Icons.FOLDER, label="Documents"),
                ft.NavigationRailDestination(icon=ft.Icons.LOCAL_HOSPITAL, label="Providers"),
                ft.NavigationRailDestination(icon=ft.Icons.VACCINES, label="Immunizations"),
                ft.NavigationRailDestination(icon=ft.Icons.GROUPS, label="Social &\nFamily History"),
                ft.NavigationRailDestination(icon=ft.Icons.SETTINGS, label="Settings"),
            ],
            on_change=nav_change,
        )
        page.nav_rail = rail

        content_area.content = get_view_for_index(prev_idx)

        dashboard = ft.Row([rail, ft.VerticalDivider(width=1), content_area], expand=True)
        page.root.content = dashboard
        page.update()

    except Exception as ex:
        show_critical_error(page, ex)