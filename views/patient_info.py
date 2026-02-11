# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# PURPOSE:
# Patient Info view for managing structured "FHIR-lite" patient fields.
#
# Includes:
# - Renders field_definitions as editable rows (value per field)
# - Inline save per row (no full page refresh required)
# - Adds new field definitions via an overlay-based dialog
#
# DESIGN NOTES:
# - Uses page.overlay for dialogs to ensure reliable rendering when the app is
#   mounted under a custom page.root container (page.dialog can fail silently)
# - Dialogs are non-modal to allow click-outside dismissal where supported
# - The "Add Field" dialog is created once and reused (kept mounted in overlay)
#   for stable button click handling across view re-renders/navigation
# - Navigation state is preserved; we re-render only the active view after a new
#   field is added so the new definition appears immediately
# -----------------------------------------------------------------------------

import flet as ft
from datetime import datetime

from database import (
    list_field_definitions,
    get_patient_field_map,
    upsert_patient_field_value,
    ensure_field_definition,
)
from utils import s, themed_panel, show_snack


def get_patient_info_view(page: ft.Page):
    patient = page.current_profile
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    # ---- Stable "Add Field" dialog (mounted once, reused) ----
    if not hasattr(page, "_add_field_dialog_open"):
        page._add_field_dialog_open = False

    # Create input controls once so handlers never close over stale locals
    if not hasattr(page, "_add_field_key_tf") or page._add_field_key_tf is None:
        page._add_field_key_tf = ft.TextField(label="Field key (e.g. patient.dentist)", autofocus=True)

    if not hasattr(page, "_add_field_label_tf") or page._add_field_label_tf is None:
        page._add_field_label_tf = ft.TextField(label="Label (e.g. Dentist Phone)")

    if not hasattr(page, "_add_field_cat_tf") or page._add_field_cat_tf is None:
        page._add_field_cat_tf = ft.TextField(label="Category", value="General")

    def close_add_field_dlg(_=None):
        dlg = page._add_field_dlg
        dlg.open = False
        try:
            dlg.update()
        except Exception:
            pass
        page._add_field_dialog_open = False
        page.update()

    def do_add_field(_ev=None):
        key_tf = page._add_field_key_tf
        label_tf = page._add_field_label_tf
        cat_tf = page._add_field_cat_tf

        key = (key_tf.value or "").strip()
        if not key:
            show_snack(page, "Field key is required.", "red")
            return

        try:
            ensure_field_definition(
                page.db_connection,
                key,
                (label_tf.value or "").strip() or key,  # default label to key if blank
                category=(cat_tf.value or "").strip() or "General",
            )
        except Exception as ex:
            show_snack(page, f"Could not add field: {ex}", "red")
            return

        close_add_field_dlg()

        # Refresh only the active view so the new row appears immediately
        if getattr(page, "content_area", None):
            page.content_area.content = get_patient_info_view(page)
            page.content_area.update()

        show_snack(page, "Field added.", "green")

    if not hasattr(page, "_add_field_dlg") or page._add_field_dlg is None:
        page._add_field_dlg = ft.AlertDialog(
            modal=False,  # allow click-outside dismissal where supported
            title=ft.Text("Add New Field"),
            content=ft.Column(
                [page._add_field_key_tf, page._add_field_label_tf, page._add_field_cat_tf],
                tight=True,
            ),
            actions=[
                ft.ElevatedButton("Cancel", on_click=close_add_field_dlg),
                ft.ElevatedButton("Add", icon=ft.Icons.ADD, on_click=do_add_field),
            ],
            on_dismiss=close_add_field_dlg,
        )
        if page._add_field_dlg not in page.overlay:
            page.overlay.append(page._add_field_dlg)

    def open_add_field_dialog(_e=None):
        # Prevent stacking dialogs (double-click safety)
        if getattr(page, "_add_field_dialog_open", False):
            return
        page._add_field_dialog_open = True

        # Reset fields each time dialog opens
        page._add_field_key_tf.value = ""
        page._add_field_label_tf.value = ""
        page._add_field_cat_tf.value = "General"

        dlg = page._add_field_dlg
        dlg.open = True

        # Updating dialog + page improves desktop reliability
        try:
            dlg.update()
        except Exception:
            pass
        page.update()

    # 1. Fetch Data
    defs = list_field_definitions(page.db_connection)
    value_map = get_patient_field_map(page.db_connection, patient_id)

    # 2. Logic: Saving a single row
    def save_value(field_key, tf, src_text, upd_text):
        """
        Saves just ONE field to the DB without reloading the whole page.
        """
        upsert_patient_field_value(
            page.db_connection,
            patient_id,
            field_key,
            tf.value or "",
            source="user",
        )

        # Visual Feedback: Update the "Source" and "Updated" columns instantly
        src_text.value = "user"
        upd_text.value = datetime.now().strftime("%Y-%m-%d %H:%M")
        src_text.update()
        upd_text.update()
        show_snack(page, "Saved.", "green")

    # 3. Build the Table Rows
    rows = []
    for field_key, label, data_type, category, is_sensitive in defs:
        existing = value_map.get(field_key, {})
        val = existing.get("value") or ""

        value_tf = ft.TextField(value=val, dense=True)
        src_text = ft.Text(existing.get("source") or "")
        upd_text = ft.Text(existing.get("updated_at") or "")

        rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(category)),
                    ft.DataCell(ft.Text(label)),
                    ft.DataCell(value_tf),
                    ft.DataCell(src_text),
                    ft.DataCell(upd_text),
                    ft.DataCell(
                        ft.IconButton(
                            ft.Icons.SAVE,
                            tooltip="Save this field",
                            on_click=lambda e, k=field_key, t=value_tf, s=src_text, u=upd_text: save_value(
                                k, t, s, u
                            ),
                        )
                    ),
                ]
            )
        )

    # 4. Return Layout
    return ft.Container(
        padding=s(page, 20),
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("Patient Info", size=s(page, 24), weight="bold"),
                        ft.Container(expand=True),
                        ft.Button("Add Field", icon=ft.Icons.ADD, on_click=open_add_field_dialog),
                    ]
                ),
                themed_panel(
                    page,
                    ft.Text("Tip: Click the save icon 💾 next to a field to save it."),
                    padding=s(page, 10),
                ),
                ft.Divider(),
                ft.DataTable(
                    columns=[
                        ft.DataColumn(ft.Text("Category")),
                        ft.DataColumn(ft.Text("Field")),
                        ft.DataColumn(ft.Text("Value")),
                        ft.DataColumn(ft.Text("Source")),
                        ft.DataColumn(ft.Text("Updated")),
                        ft.DataColumn(ft.Text("Save")),
                    ],
                    rows=rows,
                ),
            ]
        ),
    )