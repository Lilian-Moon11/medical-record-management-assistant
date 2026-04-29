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
# frame (horizontal top navigation bar + content area) and a simplified
# "critical error" screen for unrecoverable failures.
#
# Responsibilities include:
# - Rendering a consistent main layout container with persistent navigation
# - Routing navigation changes to the active view via a provided callback
# - Preserving the previously selected navigation index when rebuilding the UI
# - Replacing the app root content with a clear, accessible error screen when
#   critical exceptions occur during startup or dashboard construction
#
# Accessibility (WAI-ARIA APG Tabs pattern):
# - The ACTIVE tab is rendered as a focusable TextButton (in the Tab order).
# - INACTIVE tabs are non-focusable Container controls (Tab-skipped).
# - Left/Right arrow keys cycle between tabs when the nav bar has focus.
# - Ctrl+1..8 keyboard shortcuts (handled in main.py) also switch tabs.
# - Tooltips provide accessible names for screen readers.
# -----------------------------------------------------------------------------

from __future__ import annotations
import types
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
        page.mrma._get_view_for_index = get_view_for_index

        _DESTINATIONS = [
            (ft.Icons.DASHBOARD, "Overview"),
            (ft.Icons.BADGE, "Health Record"),
            (ft.Icons.SHOW_CHART, "Vitals & Labs"),
            (ft.Icons.FOLDER, "Documents"),
            (ft.Icons.LOCAL_HOSPITAL, "Providers"),
            (ft.Icons.VACCINES, "Immunizations"),
            (ft.Icons.GROUPS, "Family & Social"),
            (ft.Icons.SETTINGS, "Settings"),
        ]

        prev_idx = getattr(
            getattr(page, "nav_rail", None), "selected_index", 0
        ) or 0

        nav_row = ft.Row(spacing=0)

        # ── Icon + label column shared by both active and inactive items ──
        def _item_content(icon, label, is_selected):
            return ft.Column(
                [
                    ft.Container(
                        content=ft.Icon(
                            icon,
                            size=20,
                            color=(
                                ft.Colors.ON_SECONDARY_CONTAINER
                                if is_selected
                                else ft.Colors.ON_SURFACE_VARIANT
                            ),
                        ),
                        bgcolor=(
                            ft.Colors.SECONDARY_CONTAINER
                            if is_selected
                            else None
                        ),
                        border_radius=12,
                        padding=ft.padding.symmetric(
                            horizontal=16, vertical=4
                        ),
                    ),
                    ft.Text(
                        label,
                        size=11,
                        text_align=ft.TextAlign.CENTER,
                        weight="bold" if is_selected else None,
                        color=(
                            ft.Colors.ON_SURFACE
                            if is_selected
                            else ft.Colors.ON_SURFACE_VARIANT
                        ),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
                tight=True,
            )

        # ── Build one nav item ───────────────────────────────────────────
        def _build_item(i, icon, label, is_selected):
            # APG Tabs: active tab is a focusable TextButton (Tab-reachable);
            # inactive tabs are non-focusable Containers (Tab-skipped).
            if is_selected:
                return ft.TextButton(
                    content=_item_content(icon, label, True),
                    on_click=lambda e, idx=i: _select(idx),
                    tooltip=f"{label} (selected, tab {i+1} of {len(_DESTINATIONS)})",
                    style=ft.ButtonStyle(
                        padding=ft.padding.symmetric(vertical=8, horizontal=4),
                        shape=ft.RoundedRectangleBorder(radius=8),
                        overlay_color=ft.Colors.with_opacity(
                            0.06, ft.Colors.ON_SURFACE
                        ),
                    ),
                    expand=True,
                )
            else:
                return ft.Container(
                    content=_item_content(icon, label, False),
                    padding=ft.padding.symmetric(vertical=8, horizontal=4),
                    expand=True,
                    alignment=ft.Alignment(0, 0),
                    on_click=lambda e, idx=i: _select(idx),
                    on_hover=lambda e: _hover(e),
                    border_radius=8,
                    tooltip=f"{label} (tab {i+1} of {len(_DESTINATIONS)})",
                )

        def _hover(e):
            """Subtle highlight on hover for unselected items."""
            e.control.bgcolor = (
                ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE)
                if e.data == "true"
                else None
            )
            try:
                e.control.update()
            except Exception:
                pass

        # ── Rebuild all nav items ────────────────────────────────────────
        def _rebuild_nav():
            nav_row.controls.clear()
            for i, (icon, label) in enumerate(_DESTINATIONS):
                is_sel = i == nav_state.selected_index
                nav_row.controls.append(_build_item(i, icon, label, is_sel))

        def _select(idx):
            nav_state.selected_index = idx
            _rebuild_nav()
            content_area.content = get_view_for_index(idx)
            try:
                nav_row.update()
                content_area.update()
            except Exception:
                pass

        def _move_tab(delta):
            """Move to next/previous tab (arrow key navigation per APG)."""
            n = len(_DESTINATIONS)
            new_idx = (nav_state.selected_index + delta) % n
            _select(new_idx)

        def _do_update():
            """Called externally (e.g. Ctrl+N shortcut) after selected_index changes."""
            _rebuild_nav()
            try:
                nav_row.update()
            except Exception:
                pass

        # Expose nav state so other modules and keyboard shortcuts can
        # read/write selected_index and trigger visual updates.
        nav_state = types.SimpleNamespace(
            selected_index=prev_idx,
            destinations=_DESTINATIONS,
            update=_do_update,
            move_tab=_move_tab,
        )
        page.nav_rail = nav_state

        _rebuild_nav()
        content_area.content = get_view_for_index(prev_idx)

        dashboard = ft.Column(
            [
                nav_row,
                ft.Divider(height=1, thickness=1),
                content_area,
            ],
            expand=True,
            spacing=0,
        )

        page.root.content = dashboard
        page.update()

    except Exception as ex:
        show_critical_error(page, ex)