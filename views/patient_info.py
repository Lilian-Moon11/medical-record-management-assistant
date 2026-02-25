# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Patient Info view builder + UI state for editable profile fields and JSON-list
# sections (Allergies, Medications, Insurance) with optional “shield” sensitivity
# controls.
#
# This module renders the full Patient Info screen and provides:
# - A main orchestrator (`get_patient_info_view`) that loads field definitions
#   and saved values, groups fields into Demographics + custom categories, and
#   mounts each section into themed panels.
# - JSON list editors (`ListEditorBody` / `ListRow`) for structured lists stored
#   as JSON (add/save/delete rows), with an optional master “reveal/hide” toggle
#   that masks values when a section is marked sensitive.
# - Category tables (`CategoryPanel`) for single-value fields (core + custom),
#   supporting label edits for custom fields, value persistence, and guarded
#   deletion via dialogs registered in `ui.dialogs.ensure_patient_info_dialogs`.
# - Local per-page UI state (`page._field_vis`, `page._panel_vis`) to keep row-
#   level and panel-level reveal states stable across refresh/rerender, plus an
#   optional provenance display (`page._show_provenance`).
#
# Data + security/UX behaviors:
# - Uses DB field definitions + patient field map to drive UI composition.
# - Persists edits via `update_profile` for core fields and
#   `upsert_patient_field_value` for custom/list values.
# - Sensitivity (“shield”) is controlled by special section/list keys
#   (e.g., `section.demographics`, `section.other`, `allergyintolerance.list`);
#   when enabled, values default to masked until explicitly revealed.
# - Deletions are confirmed (list rows) or routed through the shared delete-field
#   dialog (custom field definitions), with core fields protected from deletion.
# -----------------------------------------------------------------------------

import flet as ft
from datetime import datetime
import json
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from database import (
    list_field_definitions,
    get_patient_field_map,
    upsert_patient_field_value,
    ensure_field_definition,
    field_definition_exists,
    update_field_definition_label,
    update_profile,
    get_profile,
)
from utils import (
    s,
    themed_panel,
    show_snack,
    is_sensitive_flag,
    clean_lbl,
    slugify_label,
    make_eye_btn,
)
from ui.dialogs import ensure_patient_info_dialogs


# -----------------------------------------------------------------------------
# State Management Helpers
# -----------------------------------------------------------------------------
def _ensure_sets(page: ft.Page) -> None:
    # Tracks individual row visibility: {"core.name": True, "allergies_1234": False}
    if not hasattr(page, "_field_vis"):
        page._field_vis = {}
    # Tracks the last state of a parent panel: {"section.demographics": True}
    if not hasattr(page, "_panel_vis"):
        page._panel_vis = {}
    if not hasattr(page, "_show_provenance"):
        page._show_provenance = False


def _safe_update(ctrl: Any) -> None:
    try:
        ctrl.update()
    except Exception:
        pass


def _load_json_list(raw: Optional[str]) -> List[dict]:
    try:
        return [x for x in json.loads(raw or "[]") if isinstance(x, dict)]
    except Exception:
        return []


def _make_list_delete_dialog(page: ft.Page) -> ft.AlertDialog:
    if hasattr(page, "_list_delete_dlg"):
        return page._list_delete_dlg

    dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Confirm Delete"),
        content=ft.Text("Are you sure you want to delete this row?"),
        actions=[
            ft.TextButton("Cancel"),
            ft.FilledButton("Delete", icon=ft.Icons.DELETE),
        ],
    )
    page._list_delete_dlg = dlg
    page.overlay.append(dlg)
    return dlg


# -----------------------------------------------------------------------------
# 1) JSON LIST PANELS (Allergies, Meds, Insurance)
# -----------------------------------------------------------------------------
class ListRow(ft.Container):
    def __init__(
        self,
        page: ft.Page,
        parent_panel: "ListEditorBody",
        item: dict,
        columns: List[Tuple[str, str]],
    ):
        super().__init__(padding=s(page, 8))
        self._page = page
        self.parent_panel = parent_panel
        self.columns = columns
        
        # Pull master switch from parent
        self.is_section_sensitive = self.parent_panel.is_section_sensitive

        self._item_id = item.get("_id") or str(uuid.uuid4().hex)[:8]
        self.row_id = f"{self.parent_panel.field_key}_{self._item_id}"

        # Default to hidden if the section feature is ON, otherwise always true
        default_vis = not self.is_section_sensitive
        self.row_revealed = self._page._field_vis.get(self.row_id, default_vis)

        self.tfs: Dict[str, ft.TextField] = {}
        for k, lbl in columns:
            self.tfs[k] = ft.TextField(
                label=lbl,
                value=str(item.get(k, "") or ""),
                password=not self.row_revealed if self.is_section_sensitive else False,
                can_reveal_password=False,
                expand=True,
                dense=True,
            )

        controls: List[Any] = [self.tfs[k] for k, _ in columns]

        # Only add the eye button if the master section switch is ON
        if self.is_section_sensitive:
            self.eye_btn = make_eye_btn(page, self.row_revealed)
            self.eye_btn.on_click = self.toggle_reveal
            controls.append(self.eye_btn)
        else:
            self.eye_btn = None

        controls.extend(
            [
                ft.IconButton(icon=ft.Icons.SAVE, tooltip="Save row", on_click=self.save_row),
                ft.IconButton(icon=ft.Icons.DELETE, tooltip="Remove", on_click=self.remove_row),
            ]
        )

        self.content = ft.Row(controls, vertical_alignment=ft.CrossAxisAlignment.START)

    def set_revealed(self, state: bool):
        if not self.is_section_sensitive:
            return
            
        self.row_revealed = state
        self._page._field_vis[self.row_id] = state
        
        for tf in self.tfs.values():
            tf.password = not state
            _safe_update(tf)
            
        if self.eye_btn:
            self.eye_btn.icon = ft.Icons.VISIBILITY_OFF if state else ft.Icons.VISIBILITY
            self.eye_btn.tooltip = "Hide" if state else "Reveal"
            _safe_update(self.eye_btn)

    def toggle_reveal(self, e=None):
        self.set_revealed(not self.row_revealed)

    def get_data_dict(self) -> dict:
        d = {k: (self.tfs[k].value or "").strip() for k, _ in self.columns}
        d["_id"] = self._item_id
        return d

    def save_row(self, e):
        self.parent_panel.save_all()
        show_snack(self._page, "Saved row.", "green")

    def remove_row(self, e):
        dlg = _make_list_delete_dialog(self._page)

        def confirm_del(_e):
            dlg.open = False
            _safe_update(dlg)
            self._page.update()
            self.parent_panel.remove_row(self)
            self.parent_panel.save_all()
            show_snack(self._page, "Item deleted.", "green")

        def cancel_del(_e):
            dlg.open = False
            _safe_update(dlg)
            self._page.update()

        dlg.actions[0].on_click = cancel_del
        dlg.actions[1].on_click = confirm_del
        dlg.open = True
        self._page.update()


class ListEditorBody(ft.Column):
    def __init__(
        self,
        page: ft.Page,
        patient_id: int,
        title: str,
        field_key: str,
        items: List[dict],
        columns: List[Tuple[str, str]],
        is_section_sensitive: bool,
        on_save: Callable[[List[dict]], None],
    ):
        super().__init__(tight=True, spacing=s(page, 10))
        self._page = page
        self.patient_id = patient_id
        self.field_key = field_key
        self.columns = columns
        self.is_section_sensitive = bool(is_section_sensitive)
        self.on_save = on_save

        _ensure_sets(page)
        self.panel_revealed = self._page._panel_vis.get(self.field_key, True)

        self.rows_col = ft.Column(spacing=s(page, 8), tight=True)
        self.row_components: List[ListRow] = []

        for it in items:
            self.add_row_component(it)

        self.add_btn = ft.FilledButton("Add", icon=ft.Icons.ADD, on_click=self.add_row)

        header_controls: List[Any] = [ft.Text(title, size=s(page, 18), weight="bold")]

        # Add panel master eye ONLY if feature is ON
        if self.is_section_sensitive:
            self.eye_btn = make_eye_btn(self._page, self.panel_revealed)
            self.eye_btn.tooltip = "Hide All" if self.panel_revealed else "Reveal All"
            self.eye_btn.on_click = self.toggle_panel_reveal
            header_controls.append(self.eye_btn)
        else:
            self.eye_btn = None

        header_controls.extend([ft.Container(expand=True), self.add_btn])
        self.controls = [ft.Row(header_controls), self.rows_col]

    def toggle_panel_reveal(self, e):
        if not self.is_section_sensitive:
            return
            
        self.panel_revealed = not self.panel_revealed
        self._page._panel_vis[self.field_key] = self.panel_revealed

        if self.eye_btn:
            self.eye_btn.icon = ft.Icons.VISIBILITY_OFF if self.panel_revealed else ft.Icons.VISIBILITY
            self.eye_btn.tooltip = "Hide All" if self.panel_revealed else "Reveal All"
            _safe_update(self.eye_btn)

        for rc in self.row_components:
            rc.set_revealed(self.panel_revealed)

    def add_row_component(self, item: dict):
        rc = ListRow(self._page, self, item, self.columns)
        self.row_components.append(rc)
        self.rows_col.controls.append(rc)

    def add_row(self, e):
        self.add_row_component({})
        _safe_update(self)

    def remove_row(self, row_comp: ListRow):
        self.row_components.remove(row_comp)
        self.rows_col.controls.remove(row_comp)
        _safe_update(self)

    def save_all(self):
        cleaned: List[dict] = []
        for rc in self.row_components:
            d = rc.get_data_dict()
            if any((d.get(k) or "").strip() for k, _ in self.columns):
                cleaned.append(d)
        self.on_save(cleaned)


# -----------------------------------------------------------------------------
# 2) CATEGORY PANELS (Demographics + Other/custom fields)
# -----------------------------------------------------------------------------
class CategoryPanel(ft.Column):
    def __init__(
        self, 
        page: ft.Page, 
        patient_id: int, 
        category_name: str, 
        defs_list: list, 
        value_map: dict,
        is_section_sensitive: bool
    ):
        super().__init__(tight=True, spacing=s(page, 6))
        self._page = page
        self.patient_id = patient_id
        self.category_name = category_name
        self.value_map = value_map
        self.is_section_sensitive = bool(is_section_sensitive)

        _ensure_sets(page)
        self._show_prov = bool(getattr(page, "_show_provenance", False))
        
        self.panel_key = f"cat_{slugify_label(self.category_name)}"
        self.panel_revealed = self._page._panel_vis.get(self.panel_key, True)

        self._rows: list[dict] = []

        cols = [
            ft.DataColumn(ft.Text("Field Name")),
            ft.DataColumn(ft.Text("Value")),
        ]
        if self._show_prov:
            cols += [
                ft.DataColumn(ft.Text("Source")),
                ft.DataColumn(ft.Text("Updated")),
            ]
        cols += [
            ft.DataColumn(ft.Text("Save")),
            ft.DataColumn(ft.Text("Delete")),
        ]

        self.table = ft.DataTable(columns=cols, rows=[])
        for d in defs_list:
            self.table.rows.append(self.create_row(d))

        header_controls = [ft.Text(self.category_name, size=s(page, 18), weight="bold")]

        if self.is_section_sensitive:
            self.cat_eye_btn = make_eye_btn(self._page, revealed=self.panel_revealed)
            self.cat_eye_btn.tooltip = "Hide All" if self.panel_revealed else "Reveal All"
            self.cat_eye_btn.on_click = self.toggle_category
            header_controls.append(self.cat_eye_btn)
        else:
            self.cat_eye_btn = None

        header_controls.extend([
            ft.Container(expand=True),
            ft.FilledButton("Add", icon=ft.Icons.ADD, on_click=self.add_row_click)
        ])

        self.controls = [ft.Row(header_controls), ft.Row([self.table], scroll=ft.ScrollMode.AUTO)]

    def toggle_category(self, e):
        if not self.is_section_sensitive:
            return
            
        self.panel_revealed = not self.panel_revealed
        self._page._panel_vis[self.panel_key] = self.panel_revealed

        if self.cat_eye_btn:
            self.cat_eye_btn.icon = ft.Icons.VISIBILITY_OFF if self.panel_revealed else ft.Icons.VISIBILITY
            self.cat_eye_btn.tooltip = "Hide All" if self.panel_revealed else "Reveal All"
            _safe_update(self.cat_eye_btn)

        for r in self._rows:
            r["set_revealed"](self.panel_revealed)

    def add_row_click(self, e):
        self.table.rows.append(self.create_row(None))
        _safe_update(self.table)

    def create_row(self, d_tuple):
        field_key = d_tuple[0] if d_tuple else None
        label = d_tuple[1] if d_tuple else ""

        is_core = field_key in ("core.name", "core.dob")
        can_delete = not is_core
        
        row_id = field_key if field_key else f"new_{uuid.uuid4().hex[:8]}"

        if field_key == "core.name":
            val = self._page.current_profile[1] or ""
            src, upd = "system", ""
        elif field_key == "core.dob":
            val = self._page.current_profile[2] or ""
            src, upd = "system", ""
        else:
            existing = self.value_map.get(field_key, {}) if field_key else {}
            val = existing.get("value", "")
            src = existing.get("source", "")
            upd = existing.get("updated_at", "")

        if is_core:
            field_tf = ft.TextField(
                value=clean_lbl(label),
                dense=True,
                width=s(self._page, 200),
                read_only=True,
                border=ft.InputBorder.NONE,
                text_style=ft.TextStyle(weight="bold", color="grey"),
            )
        else:
            field_tf = ft.TextField(value=clean_lbl(label), dense=True, width=s(self._page, 200))

        default_vis = not self.is_section_sensitive
        row_revealed = self._page._field_vis.get(row_id, default_vis)

        value_tf = ft.TextField(
            value=str(val or ""),
            password=not row_revealed if self.is_section_sensitive else False,
            can_reveal_password=False,
            dense=True,
            width=s(self._page, 250),
        )

        eye_btn = None
        if self.is_section_sensitive:
            eye_btn = make_eye_btn(self._page, row_revealed)

            def set_revealed(state: bool):
                self._page._field_vis[row_id] = state
                value_tf.password = not state
                eye_btn.icon = ft.Icons.VISIBILITY_OFF if state else ft.Icons.VISIBILITY
                eye_btn.tooltip = "Hide" if state else "Reveal"
                _safe_update(value_tf)
                _safe_update(eye_btn)

            def toggle_row_eye(_e):
                current = self._page._field_vis.get(row_id, default_vis)
                set_revealed(not current)

            eye_btn.on_click = toggle_row_eye

            self._rows.append({
                "set_revealed": set_revealed,
                "field_key": field_key
            })

        val_cell_controls = [value_tf]
        if eye_btn:
            val_cell_controls.append(eye_btn)
            
        val_cell = ft.Row(val_cell_controls, spacing=s(self._page, 4))

        src_text = ft.Text(src)
        upd_text = ft.Text(upd)
        row = ft.DataRow(cells=[])
        del_btn = ft.IconButton(icon=ft.Icons.DELETE, tooltip="Delete", disabled=not can_delete)

        def save_click(e):
            nonlocal row_id  # Informs Python that row_id belongs to the outer create_row scope
            new_lbl = (field_tf.value or "").strip()
            new_val = (value_tf.value or "").strip()
            
            if not new_lbl:
                return show_snack(self._page, "Field Name is required", "red")

            fk = field_key
            if not fk:
                # Logic for creating a brand-new custom field
                base = f"custom.{slugify_label(self.category_name)}.{slugify_label(new_lbl)}"
                fk = base
                n = 2
                while field_definition_exists(self._page.db_connection, fk):
                    fk = f"{base}.{n}"
                    n += 1
                
                # Persist the new definition to the DB
                ensure_field_definition(
                    self._page.db_connection, 
                    fk, 
                    new_lbl, 
                    category=self.category_name, 
                    is_sensitive=0
                )
                del_btn.disabled = False
                
                # Update the row's tracking ID from 'new_xxxx' to the actual DB key
                old_id = row_id
                row_id = fk
                self._page._field_vis[row_id] = self._page._field_vis.pop(old_id, True)
            else:
                # Update the label of an existing custom field (Core fields are read-only)
                if not is_core:
                    update_field_definition_label(self._page.db_connection, fk, new_lbl)

            # Handle Core Profile fields vs. Generic custom fields
            if fk == "core.name":
                p = self._page.current_profile
                update_profile(self._page.db_connection, p[0], new_val, p[2], p[3])
                self._page.current_profile = get_profile(self._page.db_connection)
                src_text.value, upd_text.value = "system", ""
            elif fk == "core.dob":
                p = self._page.current_profile
                update_profile(self._page.db_connection, p[0], p[1], new_val, p[3])
                self._page.current_profile = get_profile(self._page.db_connection)
                src_text.value, upd_text.value = "system", ""
            else:
                # Standard custom field value update
                upsert_patient_field_value(
                    self._page.db_connection, 
                    self.patient_id, 
                    fk, 
                    new_val, 
                    "user"
                )
                src_text.value = "user"
                upd_text.value = datetime.now().strftime("%Y-%m-%d %H:%M")

            _safe_update(row)
            show_snack(self._page, "Saved.", "green")

        def delete_click(e):
            if not field_key:
                self.table.rows.remove(row)
                _safe_update(self.table)
            else:
                self._page._open_delete_dialog(field_key, field_tf.value, row, self.table)

        del_btn.on_click = delete_click

        cells: List[ft.DataCell] = [
            ft.DataCell(field_tf),
            ft.DataCell(val_cell),
        ]
        if self._show_prov:
            cells += [ft.DataCell(src_text), ft.DataCell(upd_text)]
        cells += [
            ft.DataCell(ft.IconButton(icon=ft.Icons.SAVE, tooltip="Save", on_click=save_click)),
            ft.DataCell(del_btn),
        ]

        row.cells = cells
        return row


# -----------------------------------------------------------------------------
# 3) MAIN ORCHESTRATOR
# -----------------------------------------------------------------------------
def get_patient_info_view(page: ft.Page):
    patient = page.current_profile
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    _ensure_sets(page)

    def refresh():
        if getattr(page, "content_area", None):
            page.content_area.content = get_patient_info_view(page)
            page.content_area.update()
        else:
            page.update()

    page._patient_info_refresh = refresh

    ensure_patient_info_dialogs(page, refresh)

    allergies_key = "allergyintolerance.list"
    meds_key = "medicationstatement.current_list"
    insurance_key = "insurance.list"

    def open_delete_dialog(fk, lbl, row_ref, table_ref):
        page._delete_inline_row = row_ref
        page._delete_inline_table = table_ref
        page.open_delete_field_dialog(fk, clean_lbl(lbl))

    page._open_delete_dialog = open_delete_dialog

    defs = list_field_definitions(page.db_connection)
    value_map = get_patient_field_map(page.db_connection, patient_id)

    def is_sens(key: str) -> bool:
        return is_sensitive_flag(next((d[4] for d in defs if d[0] == key), 0))

    grouped: Dict[str, List[tuple]] = {"Demographics": [], "Other": []}
    
    hidden_seeds = {
        allergies_key, 
        meds_key, 
        insurance_key, 
        "providers.list",
        "section.demographics", 
        "section.other"
    }

    for d in defs:
        if d[0] in hidden_seeds:
            continue
        if d[0] in ("core.name", "core.dob"):
            grouped["Demographics"].append(d)
        else:
            cat = (d[3] or "Other").strip()
            if cat.lower() in ("allergies", "medications", "insurance", "providers"):
                cat = "Other"
            grouped.setdefault(cat, []).append(d)

    demo_keys = [d[0] for d in grouped["Demographics"]]
    if "core.name" not in demo_keys:
        grouped["Demographics"].append(("core.name", "Full Name", "text", "Demographics", 0))
    if "core.dob" not in demo_keys:
        grouped["Demographics"].append(("core.dob", "Date of Birth", "date", "Demographics", 0))

    def _demo_sort(d):
        if d[0] == "core.name":
            return (0, "")
        if d[0] == "core.dob":
            return (1, "")
        return (2, str(d[1]).lower())

    grouped["Demographics"].sort(key=_demo_sort)

    sections: List[Any] = []

    demographics_panel = themed_panel(
        page,
        CategoryPanel(
            page, 
            patient_id, 
            "Demographics", 
            grouped["Demographics"], 
            value_map,
            is_section_sensitive=is_sens("section.demographics")
        ),
        padding=s(page, 12),
    )
    sections += [demographics_panel, ft.Container(height=s(page, 10))]

    allergies_panel = themed_panel(
        page,
        ListEditorBody(
            page,
            patient_id,
            "Allergies / Intolerances",
            allergies_key,
            _load_json_list((value_map.get(allergies_key, {}) or {}).get("value")),
            [("substance", "Substance"), ("reaction", "Reaction"), ("severity", "Severity"), ("notes", "Notes")],
            is_section_sensitive=is_sens(allergies_key),
            on_save=lambda items: upsert_patient_field_value(page.db_connection, patient_id, allergies_key, json.dumps(items), "user"),
        ),
        padding=s(page, 12),
    )
    sections += [allergies_panel, ft.Container(height=s(page, 10))]

    meds_panel = themed_panel(
        page,
        ListEditorBody(
            page,
            patient_id,
            "Current Medications",
            meds_key,
            _load_json_list((value_map.get(meds_key, {}) or {}).get("value")),
            [("name", "Medication"), ("dose", "Dose"), ("frequency", "Frequency"), ("notes", "Notes")],
            is_section_sensitive=is_sens(meds_key),
            on_save=lambda items: upsert_patient_field_value(page.db_connection, patient_id, meds_key, json.dumps(items), "user"),
        ),
        padding=s(page, 12),
    )
    sections += [meds_panel, ft.Container(height=s(page, 10))]

    insurance_panel = themed_panel(
        page,
        ListEditorBody(
            page,
            patient_id,
            "Insurance",
            insurance_key,
            _load_json_list((value_map.get(insurance_key, {}) or {}).get("value")),
            [
                ("payer", "Payer / Plan"),
                ("member_id", "Member ID"),
                ("group_no", "Group #"),
                ("bin", "BIN"),
                ("pcn", "PCN"),
                ("phone", "Provider Phone"),
                ("notes", "Notes"),
            ],
            is_section_sensitive=is_sens(insurance_key),
            on_save=lambda items: upsert_patient_field_value(page.db_connection, patient_id, insurance_key, json.dumps(items), "user"),
        ),
        padding=s(page, 12),
    )
    sections += [insurance_panel, ft.Container(height=s(page, 10))]

    def _cat_sort(n: str):
        return 99 if n.lower() == "other" else 10

    for cat in sorted(grouped.keys(), key=_cat_sort):
        if cat == "Demographics":
            continue
        panel = CategoryPanel(
            page, 
            patient_id, 
            cat, 
            grouped[cat], 
            value_map,
            # Assigning custom categories to the "section.other" master switch
            is_section_sensitive=is_sens("section.other") 
        )
        sections.append(themed_panel(page, panel, padding=s(page, 12)))
        sections.append(ft.Container(height=s(page, 10)))

    return ft.Container(
        padding=s(page, 20),
        content=ft.ListView(
            controls=[
                ft.Row(
                    [
                        ft.Text("Patient Info", size=s(page, 22), weight="bold"),
                        ft.Container(expand=True),
                        ft.FilledTonalButton(
                            "Edit Sensitivity",
                            icon=ft.Icons.SHIELD,
                            on_click=lambda _: page.open_bulk_edit_dlg(),
                        ),
                    ]
                ),
                ft.Divider(),
                *sections,
            ],
            expand=True,
            spacing=s(page, 12),
            auto_scroll=False,
        ),
    )