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


def _ensure_add_request_dialog(page: ft.Page, on_saved: callable) -> ft.AlertDialog:
    if hasattr(page.mrma, "_add_req_dlg"):
        return page.mrma._add_req_dlg

    page.mrma._ar_provider = ft.TextField(
        label="Provider / Office Name *",
        hint_text="e.g. Stanford Medicine, Dr. Reyes",
        autofocus=True,
    )
    page.mrma._ar_department = ft.TextField(
        label="Department (optional)",
        hint_text="Helpful for large networks, e.g. Oncology",
    )
    page.mrma._ar_date_req = ft.TextField(
        label="Date Requested",
        value="",
        hint_text="YYYY-MM-DD",
    )
    page.mrma._ar_due_date = ft.TextField(
        label="Due Date",
        value="",
        hint_text="YYYY-MM-DD  (default: 30 days)",
    )
    page.mrma._ar_notes = ft.TextField(
        label="Notes (optional)",
        multiline=True,
        min_lines=2,
        max_lines=4,
    )

    def _close(_e=None):
        page.mrma._add_req_dlg.open = False
        try:
            page.mrma._add_req_dlg.update()
        except Exception:
            pass
        page.update()

    def _save(_e=None):
        patient_id = page.mrma._add_req_dlg._patient_id
        provider = page.mrma._ar_provider.value.strip()
        if not provider:
            page.mrma._ar_provider.error_text = "Provider name is required."
            page.mrma._ar_provider.update()
            return
        page.mrma._ar_provider.error_text = None

        department = page.mrma._ar_department.value.strip() or None
        date_req = page.mrma._ar_date_req.value.strip() or datetime.today().strftime("%Y-%m-%d")
        due = page.mrma._ar_due_date.value.strip() or None
        notes = page.mrma._ar_notes.value.strip() or None
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

    page.mrma._add_req_dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Icon(ft.Icons.ASSIGNMENT_ADD, color=ft.Colors.PRIMARY),
            ft.Text("Add Records Request", weight="bold", size=pt_scale(page, 18)),
        ], spacing=8),
        content=ft.Container(
            width=pt_scale(page, 420),
            content=ft.Column([
                page.mrma._ar_provider,
                page.mrma._ar_department,
                ft.Row([page.mrma._ar_date_req, page.mrma._ar_due_date], spacing=8),
                page.mrma._ar_notes,
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
    
    append_dialog(page, page.mrma._add_req_dlg)
    return page.mrma._add_req_dlg


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
    dlg = _ensure_add_request_dialog(page, on_saved)
    dlg._patient_id = patient_id

    # Reset fields
    page.mrma._ar_provider.value = ""
    page.mrma._ar_provider.error_text = None
    page.mrma._ar_department.value = ""
    page.mrma._ar_date_req.value = datetime.today().strftime("%Y-%m-%d")
    page.mrma._ar_due_date.value = (datetime.today() + timedelta(days=30)).strftime("%Y-%m-%d")
    page.mrma._ar_notes.value = ""

    dlg.open = True
    page.update()
