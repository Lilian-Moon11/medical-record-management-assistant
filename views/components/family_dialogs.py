# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import flet as ft
from utils.ui_helpers import append_dialog, show_snack
from views.components.family_helpers import (
    _load,
    _save_items,
    RELATION_LIST,
)


# ---------------------------------------------------------------------------
# Detail dialog (tap a person - edit conditions, save all at once)
# ---------------------------------------------------------------------------
def _ensure_detail_dialog(page: ft.Page, on_refresh):
    if hasattr(page.mrma, "_fh_detail_dlg"):
        return

    page.mrma._fh_detail_relation = ft.Text("", size=18, weight="bold")
    page.mrma._fh_detail_conds_col = ft.Column(
        tight=True, spacing=4, scroll=ft.ScrollMode.AUTO, height=160,
    )
    page.mrma._fh_detail_new_cond = ft.TextField(
        label="Add condition (press Enter to add)",
        expand=True,
    )
    page.mrma._fh_detail_notes_tf = ft.TextField(
        label="Notes (optional)", expand=True,
        multiline=True, min_lines=1, max_lines=3,
    )

    _cond_list: list[str] = []

    def _rebuild_list():
        col = page.mrma._fh_detail_conds_col
        col.controls.clear()
        if not _cond_list:
            col.controls.append(
                ft.Text("No conditions recorded yet.", italic=True,
                        color=ft.Colors.GREY_500, size=12)
            )
        for i, cond in enumerate(_cond_list):
            idx = i
            col.controls.append(ft.Row([
                ft.Icon(ft.Icons.CIRCLE, size=8, color=ft.Colors.TEAL_400),
                ft.Text(cond, expand=True, size=13),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_size=14,
                    tooltip="Remove",
                    on_click=lambda e, idx=idx: _remove_cond(idx),
                ),
            ], spacing=4))

    def _remove_cond(idx: int):
        if 0 <= idx < len(_cond_list):
            _cond_list.pop(idx)
            _rebuild_list()
            try:
                page.mrma._fh_detail_conds_col.update()
            except Exception:
                pass
            page.update()

    def _add_cond_inline(_=None):
        cond = (page.mrma._fh_detail_new_cond.value or "").strip()
        if not cond:
            return
        _cond_list.append(cond)
        page.mrma._fh_detail_new_cond.value = ""
        _rebuild_list()
        try:
            page.mrma._fh_detail_conds_col.update()
            page.mrma._fh_detail_new_cond.update()
        except Exception:
            pass
        page.update()

    page.mrma._fh_detail_new_cond.on_submit = _add_cond_inline

    _det_closing = [False]

    def _close(_=None):
        if _det_closing[0]:
            return
        _det_closing[0] = True
        page.mrma._fh_detail_dlg.open = False
        page.update()
        _det_closing[0] = False

    def _save(_=None):
        try:
            rel  = page.mrma._fh_detail_state["relation"]
            name = page.mrma._fh_detail_state["name"]
            pat = page.current_profile
            if not pat:
                show_snack(page, "No patient profile.", "red")
                return
            patient_id = pat[0]
            items = _load(page, patient_id)
            items = [it for it in items
                     if not ((it.get("relation") or "") == rel
                             and (it.get("name") or "").strip() == name)]
            notes = (page.mrma._fh_detail_notes_tf.value or "").strip()
            for cond in _cond_list:
                entry: dict = {
                    "relation":  rel,
                    "name":      name,
                    "condition": cond,
                    "notes":     notes,
                }
                items.append(entry)
            _save_items(page, patient_id, items)
            _close()
            on_refresh()
            show_snack(page, "Changes saved.", "green")
        except Exception as ex:
            import logging; logging.error("Family history detail save error", exc_info=True)
            show_snack(page, f"Save error: {ex}", "red")

    page.mrma._fh_detail_rebuild = _rebuild_list
    page.mrma._fh_detail_cond_list = _cond_list

    page.mrma._fh_detail_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Row([ft.Icon(ft.Icons.PERSON, color=ft.Colors.TEAL_400),
                      page.mrma._fh_detail_relation], spacing=8),
        content=ft.Container(
            width=480,
            content=ft.Column(
                [
                    ft.Text("Conditions:", size=12, italic=True,
                            color=ft.Colors.GREY_500),
                    page.mrma._fh_detail_conds_col,
                    page.mrma._fh_detail_new_cond,
                    ft.Divider(),
                    page.mrma._fh_detail_notes_tf,
                ],
                spacing=8, tight=True, scroll=ft.ScrollMode.AUTO,
            ),
        ),
        actions=[
            ft.TextButton("Cancel", on_click=_close),
            ft.FilledButton("Save Changes", icon=ft.Icons.SAVE, on_click=_save),
        ],
        on_dismiss=_close,
    )
    page.mrma._fh_detail_state = {"relation": "", "name": ""}
    append_dialog(page, page.mrma._fh_detail_dlg)


def open_detail_for(page: ft.Page, relation: str, display_name: str,
                    entries: list[dict], on_refresh):
    _ensure_detail_dialog(page, on_refresh)
    dlg = page.mrma._fh_detail_dlg
    page.mrma._fh_detail_state["relation"] = relation
    page.mrma._fh_detail_state["name"]     = display_name

    title_text = display_name if display_name else relation
    page.mrma._fh_detail_relation.value = (
        f"{title_text} ({relation})" if display_name else relation
    )

    page.mrma._fh_detail_notes_tf.value = (
        entries[0].get("notes", "") if entries else ""
    )
    page.mrma._fh_detail_new_cond.value = ""

    cond_list = page.mrma._fh_detail_cond_list
    cond_list.clear()
    for e in entries:
        cond = (e.get("condition") or "").strip()
        if cond:
            cond_list.append(cond)

    page.mrma._fh_detail_rebuild()

    dlg.open = True
    page.update()


# ---------------------------------------------------------------------------
# Add-family-member dialog
# ---------------------------------------------------------------------------
def _ensure_add_dialog(page: ft.Page, on_refresh):
    if hasattr(page.mrma, "_fh_add_dlg"):
        return

    _name_tf = ft.TextField(
        label="Name / Nickname (optional)", expand=True,
        hint_text="e.g. Papa, Nana, Auncle Moka",
    )
    _rel_dd = ft.Dropdown(
        label="Relation *",
        options=[ft.dropdown.Option(r) for r in RELATION_LIST],
        autofocus=True, expand=True,
    )
    _cond_tf = ft.TextField(label="Condition / Diagnosis *", expand=True)
    _notes_tf = ft.TextField(
        label="Notes", multiline=True, min_lines=2, expand=True,
    )

    _closing = [False]

    def _close(_=None):
        if _closing[0]:
            return
        _closing[0] = True
        page.mrma._fh_add_dlg.open = False
        page.update()
        _closing[0] = False

    def _save(_=None):
        try:
            rel  = (_rel_dd.value or "").strip()
            cond = (_cond_tf.value or "").strip()
            if not rel:
                show_snack(page, "Relation is required.", "orange")
                return
            if not cond:
                show_snack(page, "Condition is required.", "orange")
                return
            pat = page.current_profile
            if not pat:
                show_snack(page, "No patient profile loaded.", "red")
                return
            patient_id = pat[0]
            entry: dict = {
                "relation":  rel,
                "name":      (_name_tf.value or "").strip(),
                "condition": cond,
                "notes":     (_notes_tf.value or "").strip(),
            }
            items = _load(page, patient_id)
            items.append(entry)
            _save_items(page, patient_id, items)
            _close()
            on_refresh()
            show_snack(page, "Family member added.", "green")
        except Exception as ex:
            import logging; logging.error("Family history add save error", exc_info=True)
            show_snack(page, f"Save error: {ex}", "red")

    page.mrma._fh_add_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Add Family Member"),
        content=ft.Container(
            width=480,
            content=ft.Column(
                [
                    _name_tf,
                    _rel_dd,
                    _cond_tf,
                    ft.Divider(),
                    _notes_tf,
                ],
                spacing=10, tight=True, scroll=ft.ScrollMode.AUTO,
            ),
        ),
        actions=[
            ft.TextButton("Cancel", on_click=_close),
            ft.FilledButton("Save", icon=ft.Icons.SAVE, on_click=_save),
        ],
        on_dismiss=_close,
    )
    append_dialog(page, page.mrma._fh_add_dlg)
    page.mrma._fh_add_widgets = (_name_tf, _rel_dd, _cond_tf, _notes_tf)


def open_add_dialog(page: ft.Page, on_refresh):
    _ensure_add_dialog(page, on_refresh)
    (name_tf, rel_dd, cond_tf, notes_tf) = page.mrma._fh_add_widgets

    name_tf.value  = ""
    rel_dd.value   = None
    cond_tf.value  = ""
    notes_tf.value = ""

    page.mrma._fh_add_dlg.open = True
    page.update()
