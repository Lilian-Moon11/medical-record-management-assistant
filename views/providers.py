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

from utils.ui_helpers import append_dialog, show_snack, themed_panel, pt_scale, make_info_button
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
    if not hasattr(page.mrma, "_pending_provider_delete"):
        page.mrma._pending_provider_delete = None  # (provider_id, provider_name)

    if not hasattr(page.mrma, "_editing_provider_id"):
        page.mrma._editing_provider_id = None  # None=new, int=edit

    # ----------------------------
    # Table (created early so funcs can reference it)
    # ----------------------------
    _show_source = True
    _show_updated = bool(getattr(page.mrma, "_show_updated", False))

    prov_cols = [
        ft.DataColumn(ft.Text("Name")),
        ft.DataColumn(ft.Text("Specialty")),
        ft.DataColumn(ft.Text("Clinic")),
        ft.DataColumn(ft.Text("Phone")),
    ]
    if _show_source:
        prov_cols.append(ft.DataColumn(ft.Text("Source")))
    if _show_updated:
        prov_cols.append(ft.DataColumn(ft.Text("Updated")))
    prov_cols += [
        ft.DataColumn(ft.Text("Edit")),
        ft.DataColumn(ft.Text("Delete")),
    ]

    # ---- Sort state (nonlocal, like documents.py) ----
    sort_column = 0   # default: Name
    sort_ascending = True
    _search_holder: list = [None]  # [0] = search_field ref, set after creation

    def sort_table(e: ft.DataColumnSortEvent):
        nonlocal sort_column, sort_ascending
        if sort_column == e.column_index:
            sort_ascending = not sort_ascending
        else:
            sort_column = e.column_index
            sort_ascending = True
        table.sort_column_index = sort_column
        table.sort_ascending = sort_ascending
        sf = _search_holder[0]
        refresh_table(sf.value if sf else None)

    # Wire on_sort to sortable columns (Name=0, Specialty=1, Clinic=2)
    prov_cols[0] = ft.DataColumn(ft.Text("Name"),      on_sort=sort_table)
    prov_cols[1] = ft.DataColumn(ft.Text("Specialty"), on_sort=sort_table)
    prov_cols[2] = ft.DataColumn(ft.Text("Clinic"),    on_sort=sort_table)

    table = ft.DataTable(
        columns=prov_cols,
        rows=[],
        sort_column_index=sort_column,
        sort_ascending=sort_ascending,
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
            # r: (id, name, specialty, clinic, phone, fax, email, address, notes, source, source_file_name, created_at, updated_at)
            pid, name, specialty, clinic, phone, _fax, _email, _addr, _notes, source, source_file_name, _c, _u = r

            cells = [
                        ft.DataCell(ft.Text(name or "")),
                        ft.DataCell(ft.Text(specialty or "")),
                        ft.DataCell(ft.Text(clinic or "")),
                        ft.DataCell(ft.Text(phone or "")),
            ]
            if _show_source:
                if (source or "").lower() == "ai" and source_file_name:
                    def _open_ai_doc(e, fname=source_file_name):
                        import os, tempfile, time as _time
                        from crypto.file_crypto import get_or_create_file_master_key, decrypt_bytes
                        from utils.open_file import open_file_cross_platform
                        try:
                            cur = page.db_connection.cursor()
                            cur.execute(
                                "SELECT file_path FROM documents WHERE patient_id=? AND file_name=? ORDER BY id DESC LIMIT 1",
                                (patient_id, fname),
                            )
                            row = cur.fetchone()
                            if not row or not row[0]:
                                show_snack(page, "Source file not found.", "red")
                                return
                            from core.paths import resolve_doc_path
                            resolved = str(resolve_doc_path(row[0]))
                            if not os.path.exists(resolved):
                                show_snack(page, "Source file not found.", "red")
                                return
                            fmk = get_or_create_file_master_key(page.db_connection, dmk_raw=page.db_key_raw)
                            with open(resolved, "rb") as f:
                                ciphertext = f.read()
                            plaintext = decrypt_bytes(fmk, ciphertext)
                            _, ext = os.path.splitext(fname)
                            tmp = os.path.join(tempfile.gettempdir(), f"mrma_dec_{int(_time.time())}{ext or '.pdf'}")
                            with open(tmp, "wb") as f:
                                f.write(plaintext)
                            open_file_cross_platform(tmp)
                            show_snack(page, f"Opened {fname}", "blue")
                        except Exception as ex:
                            show_snack(page, f"Open failed: {ex}", "red")

                    cells.append(ft.DataCell(
                        ft.TextButton(
                            source_file_name,
                            on_click=_open_ai_doc,
                            tooltip="Open source document",
                            style=ft.ButtonStyle(color=ft.Colors.BLUE, padding=0),
                        )
                    ))
                else:
                    cells.append(ft.DataCell(ft.Text(source.capitalize() if source else "User")))
            if _show_updated:
                cells.append(ft.DataCell(ft.Text(_u or "")))
            cells += [
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

            table.rows.append(
                ft.DataRow(cells=cells)
            )

    # ----------------------------
    # Table refresh (ONLY call update() safely)
    # ----------------------------
    def refresh_table(search_text: str | None = None):
        nonlocal sort_column, sort_ascending
        try:
            rows = list_providers(page.db_connection, patient_id, search=search_text, limit=500)
        except Exception as ex:
            show_snack(page, f"Load failed: {ex}", "red")
            rows = []

        # Sort in-memory by selected column
        def _sort_key(r):
            # r: (id, name, specialty, clinic, phone, fax, email, address, notes, source, source_file_name, created_at, updated_at)
            if sort_column == 0:
                return str(r[1] or "").lower()
            elif sort_column == 1:
                return str(r[2] or "").lower()
            elif sort_column == 2:
                return str(r[3] or "").lower()
            return ""

        rows = sorted(rows, key=_sort_key, reverse=not sort_ascending)
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
    _search_holder[0] = search_field  # wire into sort_table closure

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
        if getattr(page.mrma, "_provider_edit_dlg", None) is not None:
            return page.mrma._provider_edit_dlg

        page.mrma._prov_name = ft.TextField(label="Name*", autofocus=True)
        page.mrma._prov_specialty = ft.TextField(label="Specialty")
        page.mrma._prov_clinic = ft.TextField(label="Clinic")
        page.mrma._prov_phone = ft.TextField(label="Phone")
        page.mrma._prov_fax = ft.TextField(label="Fax")
        page.mrma._prov_email = ft.TextField(label="Email")
        page.mrma._prov_address = ft.TextField(label="Address", multiline=True, min_lines=2, max_lines=3)
        page.mrma._prov_notes = ft.TextField(label="Notes", multiline=True, min_lines=2, max_lines=4)

        def _close(_=None):
            page.mrma._provider_edit_dlg.open = False
            page.update()

        def _save(_=None):
            name = (page.mrma._prov_name.value or "").strip()
            if not name:
                show_snack(page, "Provider name is required.", "red")
                return

            try:
                pid = getattr(page.mrma, "_editing_provider_id", None)
                if pid is None:
                    create_provider(
                        page.db_connection,
                        patient_id,
                        name=name,
                        specialty=(page.mrma._prov_specialty.value or "").strip() or None,
                        clinic=(page.mrma._prov_clinic.value or "").strip() or None,
                        phone=(page.mrma._prov_phone.value or "").strip() or None,
                        fax=(page.mrma._prov_fax.value or "").strip() or None,
                        email=(page.mrma._prov_email.value or "").strip() or None,
                        address=(page.mrma._prov_address.value or "").strip() or None,
                        notes=(page.mrma._prov_notes.value or "").strip() or None,
                    )
                    show_snack(page, "Provider added.", "blue")
                else:
                    updated = update_provider(
                        page.db_connection,
                        patient_id,
                        provider_id=int(pid),
                        name=name,
                        specialty=(page.mrma._prov_specialty.value or "").strip() or None,
                        clinic=(page.mrma._prov_clinic.value or "").strip() or None,
                        phone=(page.mrma._prov_phone.value or "").strip() or None,
                        fax=(page.mrma._prov_fax.value or "").strip() or None,
                        email=(page.mrma._prov_email.value or "").strip() or None,
                        address=(page.mrma._prov_address.value or "").strip() or None,
                        notes=(page.mrma._prov_notes.value or "").strip() or None,
                    )
                    if updated:
                        show_snack(page, "Provider updated.", "blue")
                    else:
                        show_snack(page, "Provider not found.", "orange")

                _close()
                refresh_table(search_field.value)
            except Exception as ex:
                show_snack(page, f"Save failed: {ex}", "red")

        page.mrma._provider_edit_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Provider"),
            content=ft.Container(
                width=pt_scale(page, 520),
                content=ft.Column(
                    [
                        page.mrma._prov_name,
                        ft.Row([page.mrma._prov_specialty, page.mrma._prov_clinic], wrap=True),
                        ft.Row([page.mrma._prov_phone, page.mrma._prov_fax], wrap=True),
                        page.mrma._prov_email,
                        page.mrma._prov_address,
                        page.mrma._prov_notes,
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

        append_dialog(page, page.mrma._provider_edit_dlg)
        page.update()
        return page.mrma._provider_edit_dlg

    def open_new_provider(_=None):
        page.mrma._editing_provider_id = None
        dlg = _ensure_provider_edit_dialog()
        dlg.title = ft.Text("Add Provider")

        page.mrma._prov_name.value = ""
        page.mrma._prov_specialty.value = ""
        page.mrma._prov_clinic.value = ""
        page.mrma._prov_phone.value = ""
        page.mrma._prov_fax.value = ""
        page.mrma._prov_email.value = ""
        page.mrma._prov_address.value = ""
        page.mrma._prov_notes.value = ""

        dlg.open = True
        page.update()

    def open_edit_provider(provider_row):
        pid, name, specialty, clinic, phone, fax, email, address, notes, source, source_file_name, _c, _u = provider_row

        page.mrma._editing_provider_id = int(pid)
        dlg = _ensure_provider_edit_dialog()
        dlg.title = ft.Text("Edit Provider")

        page.mrma._prov_name.value = name or ""
        page.mrma._prov_specialty.value = specialty or ""
        page.mrma._prov_clinic.value = clinic or ""
        page.mrma._prov_phone.value = phone or ""
        page.mrma._prov_fax.value = fax or ""
        page.mrma._prov_email.value = email or ""
        page.mrma._prov_address.value = address or ""
        page.mrma._prov_notes.value = notes or ""

        dlg.open = True
        page.update()

    # ----------------------------
    # Dialog: Confirm Delete
    # ----------------------------
    def _ensure_provider_delete_dialog():
        if getattr(page.mrma, "_provider_delete_dlg", None) is not None:
            return page.mrma._provider_delete_dlg

        page.mrma._provider_delete_text = ft.Text("")

        def _close(_=None):
            page.mrma._provider_delete_dlg.open = False
            page.mrma._pending_provider_delete = None
            page.update()

        def _confirm(_=None):
            pending = page.mrma._pending_provider_delete
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

        page.mrma._provider_delete_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Confirm Delete"),
            content=page.mrma._provider_delete_text,
            actions=[
                ft.TextButton("Cancel", on_click=_close),
                ft.FilledButton("Delete", icon=ft.Icons.DELETE, on_click=_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close,
        )

        append_dialog(page, page.mrma._provider_delete_dlg)
        page.update()
        return page.mrma._provider_delete_dlg

    def open_delete_provider(provider_id: int, provider_name: str):
        page.mrma._pending_provider_delete = (int(provider_id), provider_name or "")
        dlg = _ensure_provider_delete_dialog()
        page.mrma._provider_delete_text.value = f'Delete provider "{provider_name}"?'
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

    _info_btn = make_info_button(page, "Provider Directory", [
        "This is primarily used for completion of release of information forms, but can be used for any purpose you choose.",
    ])

    header = ft.Row(
        [
            ft.Text("Provider Directory", size=20, weight="bold"),
            ft.Container(expand=True),
            ft.FilledButton("Add Provider", icon=ft.Icons.PERSON_ADD, on_click=open_new_provider),
            _info_btn,
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
