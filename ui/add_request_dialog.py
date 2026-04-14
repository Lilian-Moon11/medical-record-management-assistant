# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# A simple AlertDialog for manually creating a records request.
#
# Opened from the "+" icon in the Records Request panel on the Overview tab.
# On save, calls back to the caller so the panel can refresh itself without a
# full page rebuild.
# -----------------------------------------------------------------------------

import flet as ft
from datetime import datetime, timedelta
from database.records_requests import create_request
from utils.ui_helpers import append_dialog, pt_scale, show_snack


def open_add_request_dialog(
    page: ft.Page,
    patient_id: int,
    on_saved: callable,
) -> None:
    """Mount and open the Add Request dialog.

    Args:
        page:       The Flet page (for overlay access and scaling).
        patient_id: Current patient's DB id.
        on_saved:   Zero-argument callable triggered after a successful save.
    """

    # ── Fields ────────────────────────────────────────────────────────────────
    provider_field = ft.TextField(
        label="Provider / Office Name *",
        hint_text="e.g. Stanford Medicine, Dr. Reyes",
        autofocus=True,
    )
    department_field = ft.TextField(
        label="Department (optional)",
        hint_text="Helpful for large networks, e.g. Oncology",
    )
    date_requested_field = ft.TextField(
        label="Date Requested",
        value=datetime.today().strftime("%Y-%m-%d"),
        hint_text="YYYY-MM-DD",
    )
    due_date_field = ft.TextField(
        label="Due Date",
        value=(datetime.today() + timedelta(days=30)).strftime("%Y-%m-%d"),
        hint_text="YYYY-MM-DD  (default: 30 days)",
    )
    notes_field = ft.TextField(
        label="Notes (optional)",
        multiline=True,
        min_lines=2,
        max_lines=4,
    )

    # ── Dialog lifecycle ──────────────────────────────────────────────────────
    _dlg_key = "_add_request_dlg"

    def _close(_e=None):
        dlg = getattr(page, _dlg_key, None)
        if dlg:
            dlg.open = False
            try:
                dlg.update()
            except Exception:
                pass
        page.update()

    def _save(_e=None):
        provider = provider_field.value.strip()
        if not provider:
            provider_field.error_text = "Provider name is required."
            provider_field.update()
            return
        provider_field.error_text = None

        department = department_field.value.strip() or None
        date_req = date_requested_field.value.strip() or datetime.today().strftime("%Y-%m-%d")
        due = due_date_field.value.strip() or None
        notes = notes_field.value.strip() or None
        source = "manual" if due else "default"
        if not due:
            due = (datetime.today() + timedelta(days=30)).strftime("%Y-%m-%d")

        try:
            create_request(
                page.db_connection,
                patient_id,
                provider,
                department,
                date_req,
                due,
                source,
                notes,
            )
            show_snack(page, "Request added.", "green")
            _close()
            on_saved()
        except Exception as ex:
            show_snack(page, f"Error saving request: {ex}", "red")

    # ── Build dialog (create-once per session) ────────────────────────────────
    if not hasattr(page, _dlg_key):
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.ASSIGNMENT_ADD, color=ft.Colors.PRIMARY),
                ft.Text("Add Records Request", weight="bold", size=pt_scale(page, 18)),
            ], spacing=8),
            content=ft.Container(
                width=pt_scale(page, 420),
                content=ft.Column([
                    provider_field,
                    department_field,
                    ft.Row([date_requested_field, due_date_field], spacing=8),
                    notes_field,
                    ft.Text(
                        "* Due date defaults to 30 days if left unchanged.",
                        size=pt_scale(page, 11),
                        color=ft.Colors.SECONDARY,
                        italic=True,
                    ),
                ], spacing=pt_scale(page, 10), tight=True),
            ),
            actions=[
                ft.TextButton("Cancel", on_click=_close),
                ft.FilledButton("Save Request", icon=ft.Icons.SAVE, on_click=_save),
            ],
            on_dismiss=_close,
        )
        setattr(page, _dlg_key, dlg)
        append_dialog(page, dlg)
    else:
        # Reset fields for reuse
        dlg = getattr(page, _dlg_key)
        provider_field.value = ""
        provider_field.error_text = None
        department_field.value = ""
        date_requested_field.value = datetime.today().strftime("%Y-%m-%d")
        due_date_field.value = (datetime.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        notes_field.value = ""
        # Rebuild content with fresh field refs so closures bind correctly
        dlg.content = ft.Container(
            width=pt_scale(page, 420),
            content=ft.Column([
                provider_field,
                department_field,
                ft.Row([date_requested_field, due_date_field], spacing=8),
                notes_field,
                ft.Text(
                    "* Due date defaults to 30 days if left unchanged.",
                    size=pt_scale(page, 11),
                    color=ft.Colors.SECONDARY,
                    italic=True,
                ),
            ], spacing=pt_scale(page, 10), tight=True),
        )
        dlg.actions = [
            ft.TextButton("Cancel", on_click=_close),
            ft.FilledButton("Save Request", icon=ft.Icons.SAVE, on_click=_save),
        ]

    dlg = getattr(page, _dlg_key)
    dlg.open = True
    try:
        dlg.update()
    except Exception:
        pass
    page.update()
