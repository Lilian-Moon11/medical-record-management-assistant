# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Vaccines & Immunizations view.
#
# Displays all recorded vaccines in a sortable table.
# Supports manual add, edit, and delete.
# AI-extracted entries (immunization.list) appear automatically.
# Data is stored as JSON in the patient_field_values EAV table.
# -----------------------------------------------------------------------------

import flet as ft
import json
from datetime import datetime

from database import get_patient_field_map, upsert_patient_field_value
from utils.ui_helpers import append_dialog, pt_scale, show_snack, themed_panel, make_info_button


_FIELD_KEY = "immunization.list"


def _load(page, patient_id: int) -> list[dict]:
    try:
        value_map = get_patient_field_map(page.db_connection, patient_id)
        raw = (value_map.get(_FIELD_KEY) or {}).get("value")
        items = json.loads(raw or "[]")
        return [x for x in items if isinstance(x, dict)]
    except Exception:
        return []


def _save(page, patient_id: int, items: list[dict]):
    try:
        upsert_patient_field_value(
            page.db_connection, patient_id, _FIELD_KEY, json.dumps(items), "user"
        )
    except Exception as ex:
        show_snack(page, f"Save failed: {ex}", "red")


def get_vaccines_view(page: ft.Page):
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    vaccines = _load(page, patient_id)

    # ---- Sort state persisted on page (survives view rebuilds) ----
    if not hasattr(page.mrma, "_vax_sort_col"):
        page.mrma._vax_sort_col = 1   # default: Date
    if not hasattr(page.mrma, "_vax_sort_asc"):
        page.mrma._vax_sort_asc = False  # newest first

    def _sort_key(v: dict):
        col = page.mrma._vax_sort_col
        if col == 0:   # Vaccine name
            return str(v.get("vaccine", "") or "").lower()
        elif col == 1: # Date
            return str(v.get("date", "") or "")
        elif col == 3: # Administered By
            return str(v.get("administered_by", "") or "").lower()
        return ""

    vaccines.sort(key=_sort_key, reverse=not page.mrma._vax_sort_asc)

    # ── Shared detail/edit dialog ───────────────────────────────────────────
    _vac_name  = ft.TextField(label="Vaccine Name *", autofocus=True, expand=True)
    _vac_date  = ft.TextField(label="Date Administered (YYYY-MM-DD)", expand=True)
    _vac_lot   = ft.TextField(label="Lot #", expand=True)
    _vac_admin = ft.TextField(label="Administered By", expand=True)
    _vac_notes = ft.TextField(label="Notes", multiline=True, min_lines=2, expand=True)

    _edit_idx = {"value": None}  # None = new entry

    def _clear_fields():
        _vac_name.value = ""
        _vac_date.value = ""
        _vac_lot.value = ""
        _vac_admin.value = ""
        _vac_notes.value = ""

    def _populate_fields(v: dict):
        _vac_name.value  = v.get("vaccine", "")
        _vac_date.value  = v.get("date", "")
        _vac_lot.value   = v.get("lot", "")
        _vac_admin.value = v.get("administered_by", "")
        _vac_notes.value = v.get("notes", "")

    def _refresh_view():
        if getattr(page, "content_area", None):
            page.content_area.content = get_vaccines_view(page)
            page.content_area.update()

    def _close_dlg(_=None):
        if hasattr(page.mrma, "_vax_dlg"):
            page.mrma._vax_dlg.open = False
            try:
                page.mrma._vax_dlg.update()
            except Exception:
                pass
        page.update()

    def _save_entry(_=None):
        if not (_vac_name.value or "").strip():
            show_snack(page, "Vaccine name is required.", "orange")
            return

        entry = {
            "vaccine":         _vac_name.value.strip(),
            "date":            _vac_date.value.strip(),
            "lot":             _vac_lot.value.strip(),
            "administered_by": _vac_admin.value.strip(),
            "notes":           _vac_notes.value.strip(),
        }

        if _edit_idx["value"] is None:
            vaccines.insert(0, entry)
        else:
            vaccines[_edit_idx["value"]] = entry

        _save(page, patient_id, vaccines)
        _close_dlg()
        _refresh_view()

    if not hasattr(page.mrma, "_vax_dlg"):
        page.mrma._vax_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Vaccine Record"),
            content=ft.Container(
                width=460,
                content=ft.Column(
                    [_vac_name, _vac_date, _vac_lot, _vac_admin, _vac_notes],
                    tight=True,
                    spacing=10,
                )
            ),
            actions=[
                ft.TextButton("Cancel", on_click=_close_dlg),
                ft.FilledButton("Save", icon=ft.Icons.SAVE, on_click=_save_entry),
            ],
            on_dismiss=_close_dlg,
        )
        append_dialog(page, page.mrma._vax_dlg)

    def _open_add(_=None):
        _edit_idx["value"] = None
        _clear_fields()
        page.mrma._vax_dlg.title = ft.Text("Add Vaccine")
        page.mrma._vax_dlg.open = True
        page.update()

    def _open_edit(idx: int):
        _edit_idx["value"] = idx
        _populate_fields(vaccines[idx])
        page.mrma._vax_dlg.title = ft.Text("Edit Vaccine")
        page.mrma._vax_dlg.open = True
        page.update()

    # ── Delete confirmation dialog (ensure-once pattern) ──────────────────
    def _ensure_vax_delete_dialog():
        if getattr(page.mrma, "_vax_del_dlg", None) is not None:
            return page.mrma._vax_del_dlg

        page.mrma._vax_del_text = ft.Text("")
        page.mrma._pending_vax_delete = None

        def _close(_=None):
            page.mrma._vax_del_dlg.open = False
            page.mrma._pending_vax_delete = None
            page.update()

        def _confirm(_=None):
            pending = page.mrma._pending_vax_delete
            if pending is None:
                _close()
                return
            try:
                vaccines.pop(pending)
                _save(page, patient_id, vaccines)
            except Exception as ex:
                show_snack(page, f"Delete failed: {ex}", "red")
            _close()
            _refresh_view()

        page.mrma._vax_del_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Confirm Delete"),
            content=page.mrma._vax_del_text,
            actions=[
                ft.TextButton("Cancel", on_click=_close),
                ft.FilledButton("Delete", icon=ft.Icons.DELETE, on_click=_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close,
        )

        append_dialog(page, page.mrma._vax_del_dlg)
        page.update()
        return page.mrma._vax_del_dlg

    def _delete(idx: int, _=None):
        vax = vaccines[idx]
        name = vax.get("vaccine", "this vaccine record")
        page.mrma._pending_vax_delete = idx
        dlg = _ensure_vax_delete_dialog()
        page.mrma._vax_del_text.value = f'Delete vaccine "{name}"?'
        dlg.open = True
        page.update()

    # ── Table ───────────────────────────────────────────────────────────────
    rows: list[ft.DataRow] = []
    for i, v in enumerate(vaccines):
        idx = i  # capture
        source = v.get("_source", "")
        source_chip = ft.Container(
            content=ft.Text("AI", size=9, color=ft.Colors.WHITE),
            bgcolor=ft.Colors.BLUE_600,
            border_radius=4,
            padding=ft.padding.symmetric(horizontal=4, vertical=1),
            tooltip=f"Extracted from: {v.get('_ai_source', 'document')}",
            visible=source == "ai",
        )
        rows.append(
            ft.DataRow(cells=[
                ft.DataCell(ft.Row([ft.Text(v.get("vaccine", ""), weight="w500"), source_chip], spacing=6)),
                ft.DataCell(ft.Text(v.get("date", ""))),
                ft.DataCell(ft.Text(v.get("lot", ""))),
                ft.DataCell(ft.Text(v.get("administered_by", ""))),
                ft.DataCell(ft.Text(v.get("notes", ""), max_lines=1)),
                ft.DataCell(ft.Row([
                    ft.IconButton(
                        ft.Icons.EDIT, icon_size=18, tooltip="Edit",
                        on_click=lambda e, i=idx: _open_edit(i),
                    ),
                    ft.IconButton(
                        ft.Icons.DELETE, icon_size=18, tooltip="Delete",
                        on_click=lambda e, i=idx: _delete(i),
                    ),
                ], spacing=0)),
            ])
        )

    def _on_sort(e: ft.DataColumnSortEvent):
        if page.mrma._vax_sort_col == e.column_index:
            page.mrma._vax_sort_asc = not page.mrma._vax_sort_asc
        else:
            page.mrma._vax_sort_col = e.column_index
            page.mrma._vax_sort_asc = True
        _refresh_view()

    table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Vaccine"),         on_sort=_on_sort),
            ft.DataColumn(ft.Text("Date"),             on_sort=_on_sort),
            ft.DataColumn(ft.Text("Lot #")),
            ft.DataColumn(ft.Text("Administered By"),  on_sort=_on_sort),
            ft.DataColumn(ft.Text("Notes")),
            ft.DataColumn(ft.Text("Actions")),
        ],
        rows=rows,
        sort_column_index=page.mrma._vax_sort_col,
        sort_ascending=page.mrma._vax_sort_asc,
        border=ft.border.all(1, ft.Colors.GREY_400),
        vertical_lines=ft.border.BorderSide(1, ft.Colors.GREY_100),
    )

    empty_state = ft.Column(
        [
            ft.Icon(ft.Icons.VACCINES, size=56, color=ft.Colors.GREY_400),
            ft.Text("No vaccines recorded.", size=16, color=ft.Colors.GREY_500),
            ft.Text("Add one manually or upload a document for AI extraction.",
                    size=13, color=ft.Colors.GREY_400, italic=True),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=8,
    ) if not vaccines else None

    body = ft.Column(
        [ft.Column([table], scroll=ft.ScrollMode.AUTO)] if vaccines else [
            ft.Container(empty_state, alignment=ft.Alignment(x=0, y=0), expand=True, padding=40)
        ],
        expand=True,
    )

    return ft.Container(
        padding=pt_scale(page, 20),
        expand=True,
        content=ft.Column(
            [
                ft.Row([
                    ft.Row([
                        ft.Icon(ft.Icons.VACCINES, color=ft.Colors.TEAL_600),
                        ft.Text("Vaccines & Immunizations",
                                size=pt_scale(page, 24), weight="bold"),
                    ], spacing=10),
                    ft.Container(expand=True),
                    ft.FilledButton(
                        "Add Vaccine",
                        icon=ft.Icons.ADD,
                        on_click=_open_add,
                    ),
                    make_info_button(page, "Vaccines & Immunizations", [
                        "Thanks for getting vaccinated. You're doing your part to keep yourself and others safe.",
                    ]),
                ]),
                ft.Divider(),
                body,
            ],
            expand=True,
        ),
    )
