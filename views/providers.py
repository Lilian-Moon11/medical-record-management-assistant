# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Provider Directory
#
# Local CRUD UI for providers scoped to patient_id.
# - Search (LIKE) for non-technical users
# - Add/Edit via stable overlay dialog pattern (created once, appended once)
# - Delete via confirmation dialog + snackbar
# -----------------------------------------------------------------------------

from __future__ import annotations
import flet as ft

from utils.ui_helpers import show_snack, themed_panel, pt_scale
from database import (
    list_providers,
    create_provider,
    update_provider,
    delete_provider,
)


def get_providers_view(page: ft.Page):
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    # ----------------------------
    # Stable dialog state holders
    # ----------------------------
    if not hasattr(page, "_pending_provider_delete"):
        page._pending_provider_delete = None  # (provider_id, provider_name)

    if not hasattr(page, "_editing_provider_id"):
        page._editing_provider_id = None  # None=new, int=edit

    # ----------------------------
    # Table (created early so funcs can reference it)
    # ----------------------------
    table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Name")),
            ft.DataColumn(ft.Text("Specialty")),
            ft.DataColumn(ft.Text("Clinic")),
            ft.DataColumn(ft.Text("Phone")),
            ft.DataColumn(ft.Text("Edit")),
            ft.DataColumn(ft.Text("Delete")),
        ],
        rows=[],
        column_spacing=pt_scale(page, 14),
        heading_row_height=pt_scale(page, 40),
        data_row_min_height=pt_scale(page, 40),
        data_row_max_height=pt_scale(page, 52),
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
        if hasattr(ft.Colors, "OUTLINE_VARIANT") else None,
        border_radius=8,
    )

    table_container = ft.Container(content=table, expand=True)

    # ----------------------------
    # Row builder helper
    # ----------------------------
    def _build_rows(rows):
        table.rows = []
        for r in rows:
            # r: (id, name, specialty, clinic, phone, fax, email, address, notes, created_at, updated_at)
            pid, name, specialty, clinic, phone, _fax, _email, _addr, _notes, _c, _u = r

            table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(name or "")),
                        ft.DataCell(ft.Text(specialty or "")),
                        ft.DataCell(ft.Text(clinic or "")),
                        ft.DataCell(ft.Text(phone or "")),
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.EDIT,
                                tooltip="Edit provider",
                                on_click=lambda e, rr=r: open_edit_provider(rr),
                            )
                        ),
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.DELETE,
                                tooltip="Delete provider",
                                on_click=lambda e, pid=int(pid), nm=(name or ""): open_delete_provider(pid, nm),
                            )
                        ),
                    ]
                )
            )

    # ----------------------------
    # Table refresh (ONLY call update() safely)
    # ----------------------------
    def refresh_table(search_text: str | None = None):
        try:
            rows = list_providers(page.db_connection, patient_id, search=search_text, limit=500)
        except Exception as ex:
            show_snack(page, f"Load failed: {ex}", "red")
            rows = []

        _build_rows(rows)

        # Only update if mounted; DO NOT touch table.page
        try:
            table.update()
            page.update()
        except Exception:
            pass

    # ----------------------------
    # Controls (search + buttons)
    # ----------------------------
    def do_search(_=None):
        refresh_table(search_field.value)

    search_field = ft.TextField(
        label="Search providers",
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        width=pt_scale(page, 340),
        on_submit=do_search,  # Enter triggers search
    )

    search_btn = ft.FilledButton(
        "Search",
        icon=ft.Icons.SEARCH,
        on_click=do_search,
    )

    def do_clear(_=None):
        search_field.value = ""
        try:
            search_field.update()
        except Exception:
            pass
        refresh_table("")  # reload all rows

    clear_btn = ft.OutlinedButton(
        "Clear",
        icon=ft.Icons.CLOSE,
        on_click=do_clear,
    )

    # ----------------------------
    # Dialog: Add/Edit Provider
    # ----------------------------
    def _ensure_provider_edit_dialog():
        if getattr(page, "_provider_edit_dlg", None) is not None:
            return page._provider_edit_dlg

        page._prov_name = ft.TextField(label="Name*", autofocus=True)
        page._prov_specialty = ft.TextField(label="Specialty")
        page._prov_clinic = ft.TextField(label="Clinic")
        page._prov_phone = ft.TextField(label="Phone")
        page._prov_fax = ft.TextField(label="Fax")
        page._prov_email = ft.TextField(label="Email")
        page._prov_address = ft.TextField(label="Address", multiline=True, min_lines=2, max_lines=3)
        page._prov_notes = ft.TextField(label="Notes", multiline=True, min_lines=2, max_lines=4)

        def _close(_=None):
            page._provider_edit_dlg.open = False
            page.update()

        def _save(_=None):
            name = (page._prov_name.value or "").strip()
            if not name:
                show_snack(page, "Provider name is required.", "red")
                return

            try:
                pid = getattr(page, "_editing_provider_id", None)
                if pid is None:
                    create_provider(
                        page.db_connection,
                        patient_id,
                        name=name,
                        specialty=(page._prov_specialty.value or "").strip() or None,
                        clinic=(page._prov_clinic.value or "").strip() or None,
                        phone=(page._prov_phone.value or "").strip() or None,
                        fax=(page._prov_fax.value or "").strip() or None,
                        email=(page._prov_email.value or "").strip() or None,
                        address=(page._prov_address.value or "").strip() or None,
                        notes=(page._prov_notes.value or "").strip() or None,
                    )
                    show_snack(page, "Provider added.", "blue")
                else:
                    updated = update_provider(
                        page.db_connection,
                        patient_id,
                        provider_id=int(pid),
                        name=name,
                        specialty=(page._prov_specialty.value or "").strip() or None,
                        clinic=(page._prov_clinic.value or "").strip() or None,
                        phone=(page._prov_phone.value or "").strip() or None,
                        fax=(page._prov_fax.value or "").strip() or None,
                        email=(page._prov_email.value or "").strip() or None,
                        address=(page._prov_address.value or "").strip() or None,
                        notes=(page._prov_notes.value or "").strip() or None,
                    )
                    if updated:
                        show_snack(page, "Provider updated.", "blue")
                    else:
                        show_snack(page, "Provider not found.", "orange")

                _close()
                refresh_table(search_field.value)
            except Exception as ex:
                show_snack(page, f"Save failed: {ex}", "red")

        page._provider_edit_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Provider"),
            content=ft.Container(
                width=pt_scale(page, 520),
                content=ft.Column(
                    [
                        page._prov_name,
                        ft.Row([page._prov_specialty, page._prov_clinic], wrap=True),
                        ft.Row([page._prov_phone, page._prov_fax], wrap=True),
                        page._prov_email,
                        page._prov_address,
                        page._prov_notes,
                    ],
                    tight=True,
                    scroll=True,
                ),
            ),
            actions=[
                ft.TextButton("Cancel", on_click=_close),
                ft.FilledButton("Save", icon=ft.Icons.SAVE, on_click=_save),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close,
        )

        page.overlay.append(page._provider_edit_dlg)
        page.update()
        return page._provider_edit_dlg

    def open_new_provider(_=None):
        page._editing_provider_id = None
        dlg = _ensure_provider_edit_dialog()
        dlg.title = ft.Text("Add Provider")

        page._prov_name.value = ""
        page._prov_specialty.value = ""
        page._prov_clinic.value = ""
        page._prov_phone.value = ""
        page._prov_fax.value = ""
        page._prov_email.value = ""
        page._prov_address.value = ""
        page._prov_notes.value = ""

        dlg.open = True
        page.update()

    def open_edit_provider(provider_row):
        pid, name, specialty, clinic, phone, fax, email, address, notes, _c, _u = provider_row

        page._editing_provider_id = int(pid)
        dlg = _ensure_provider_edit_dialog()
        dlg.title = ft.Text("Edit Provider")

        page._prov_name.value = name or ""
        page._prov_specialty.value = specialty or ""
        page._prov_clinic.value = clinic or ""
        page._prov_phone.value = phone or ""
        page._prov_fax.value = fax or ""
        page._prov_email.value = email or ""
        page._prov_address.value = address or ""
        page._prov_notes.value = notes or ""

        dlg.open = True
        page.update()

    # ----------------------------
    # Dialog: Confirm Delete
    # ----------------------------
    def _ensure_provider_delete_dialog():
        if getattr(page, "_provider_delete_dlg", None) is not None:
            return page._provider_delete_dlg

        page._provider_delete_text = ft.Text("")

        def _close(_=None):
            page._provider_delete_dlg.open = False
            page._pending_provider_delete = None
            page.update()

        def _confirm(_=None):
            pending = page._pending_provider_delete
            if not pending:
                _close()
                return

            provider_id, _name = pending
            try:
                deleted = delete_provider(page.db_connection, patient_id, int(provider_id))
                _close()
                refresh_table(search_field.value)

                if deleted:
                    show_snack(page, "Provider deleted.", "blue")
                else:
                    show_snack(page, "Provider not found.", "orange")
            except Exception as ex:
                show_snack(page, f"Delete failed: {ex}", "red")

        page._provider_delete_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Confirm Delete"),
            content=page._provider_delete_text,
            actions=[
                ft.TextButton("Cancel", on_click=_close),
                ft.FilledButton("Delete", icon=ft.Icons.DELETE, on_click=_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close,
        )

        page.overlay.append(page._provider_delete_dlg)
        page.update()
        return page._provider_delete_dlg

    def open_delete_provider(provider_id: int, provider_name: str):
        page._pending_provider_delete = (int(provider_id), provider_name or "")
        dlg = _ensure_provider_delete_dialog()
        page._provider_delete_text.value = f'Delete provider "{provider_name}"?'
        dlg.open = True
        page.update()

    # ----------------------------
    # Initial load (QUIET: no update calls)
    # ----------------------------
    try:
        initial_rows = list_providers(page.db_connection, patient_id, search="", limit=500)
    except Exception as ex:
        show_snack(page, f"Load failed: {ex}", "red")
        initial_rows = []
    _build_rows(initial_rows)

    header = ft.Row(
        [
            ft.Text("Provider Directory", size=20, weight="bold"),
            ft.Container(expand=True),
            ft.FilledButton("Add Provider", icon=ft.Icons.PERSON_ADD, on_click=open_new_provider),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    return themed_panel(
        page,
        ft.Column(
            [
                header,
                ft.Row([search_field, search_btn, clear_btn], wrap=True),
                ft.Divider(),
                table_container,
            ],
            expand=True,
            scroll=True,
        ),
        padding=pt_scale(page, 16),
        radius=10,
    )
