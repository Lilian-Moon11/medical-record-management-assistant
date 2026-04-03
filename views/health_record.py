# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Patient Info view builder + UI state for editable profile fields and JSON-list
# sections (Allergies, Medications, Insurance) with optional shield sensitivity
# controls.
#
# This module renders the full Patient Info screen and provides:
# - A main orchestrator (`get_patient_info_view`) that loads field definitions
#   and saved values, groups fields into Demographics + custom categories, and
#   mounts each section into themed panels.
# - JSON list editors (`ListEditorBody` / `ListRow`) for structured lists stored
#   as JSON (add/save/delete rows), with an optional master reveal/hide toggle
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
# - Sensitivity (shield) is controlled by special section/list keys
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
from utils.ui_helpers import (
    pt_scale,
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
    if not hasattr(page, "_show_source"):
        page._show_source = False
    if not hasattr(page, "_show_updated"):
        page._show_updated = False


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
        super().__init__(padding=pt_scale(page, 8))
        self._page = page
        self.parent_panel = parent_panel
        self.columns = columns
        self.is_section_sensitive = self.parent_panel.is_section_sensitive

        self._item_id = item.get("_id") or str(uuid.uuid4().hex)[:8]
        self.row_id = f"{self.parent_panel.field_key}_{self._item_id}"
        # Preserve underscore-prefixed metadata from the JSON item
        self._meta = {k: v for k, v in item.items() if k.startswith("_")}

        default_vis = not self.is_section_sensitive
        self.row_revealed = self._page._field_vis.get(self.row_id, default_vis)

        # Dictionary to hold our controls (TextFields or Checkboxes)
        self.controls_map: Dict[str, ft.Control] = {}
        row_controls: List[ft.Control] = []

        for k, lbl in columns:
            if k == "is_current":
                # Checkbox logic for "Current?" marker
                cb = ft.Checkbox(
                    label=lbl,
                    value=bool(item.get(k, False)),
                    on_change=lambda e: self.parent_panel.save_all() # Auto-save on toggle
                )
                self.controls_map[k] = cb
                row_controls.append(cb)
            else:
                # Standard text field logic
                tf = ft.TextField(
                    label=lbl,
                    value=str(item.get(k, "") or ""),
                    password=not self.row_revealed if self.is_section_sensitive else False,
                    can_reveal_password=False,
                    expand=True,
                    dense=True,
                )
                self.controls_map[k] = tf
                row_controls.append(tf)

        # Only add the eye button if the master section switch is ON
        if self.is_section_sensitive:
            self.eye_btn = make_eye_btn(page, self.row_revealed)
            self.eye_btn.on_click = self.toggle_reveal
            row_controls.append(self.eye_btn)
        else:
            self.eye_btn = None

        # Provenance: show source + updated per row from ITEM-LEVEL metadata
        self._prov_source_text = None
        self._prov_updated_text = None
        if bool(getattr(page, "_show_source", False)):
            src_val = item.get("_source") or getattr(self.parent_panel, "_source", "") or "User"
            
            if str(src_val).lower() == "ai" and item.get("_ai_source"):
                ai_filename = item.get("_ai_source")
                
                def _open_ai_doc(e, fname=ai_filename):
                    import asyncio
                    import os
                    import tempfile
                    from datetime import datetime
                    from crypto.file_crypto import get_or_create_file_master_key, decrypt_bytes
                    try:
                        cur = page.db_connection.cursor()
                        cur.execute("SELECT file_path FROM documents WHERE patient_id=? AND file_name=? ORDER BY id DESC LIMIT 1", (self.parent_panel.patient_id, fname))
                        row = cur.fetchone()
                        if not row or not row[0] or not os.path.exists(row[0]):
                            show_snack(page, "Original file not found.", "red")
                            return
                        enc_path = row[0]
                        fmk = get_or_create_file_master_key(page.db_connection, dmk_raw=page.db_key_raw)
                        with open(enc_path, "rb") as f:
                            ciphertext = f.read()
                        plaintext = decrypt_bytes(fmk, ciphertext)
                        
                        _, file_ext = os.path.splitext(fname)
                        if not file_ext: file_ext = ".pdf"
                        
                        tmp_dir = tempfile.gettempdir()
                        tmp_path = os.path.join(tmp_dir, f"lpa_decrypted_{self.parent_panel.patient_id}_{int(datetime.now().timestamp())}{file_ext}")
                        with open(tmp_path, "wb") as f:
                            f.write(plaintext)
                        
                        import os
                        os.startfile(tmp_path)
                        show_snack(page, f"Opened {fname}", "blue")
                    except Exception as ex:
                        show_snack(page, f"Failed to open source document: {ex}", "red")

                self._prov_source_text = ft.TextButton(
                    str(ai_filename),
                    on_click=_open_ai_doc,
                    tooltip="Open source document",
                    style=ft.ButtonStyle(
                        color=ft.Colors.BLUE,
                        padding=0,
                    ),
                    width=pt_scale(page, 120),
                )
            else:
                self._prov_source_text = ft.Text(src_val, width=pt_scale(page, 120))
            row_controls.append(self._prov_source_text)
        if bool(getattr(page, "_show_updated", False)):
            upd_val = item.get("_updated") or getattr(self.parent_panel, "_updated_at", "") or "\u2014"
            self._prov_updated_text = ft.Text(upd_val, width=pt_scale(page, 140))
            row_controls.append(self._prov_updated_text)

        row_controls.extend([
            ft.IconButton(icon=ft.Icons.SAVE, tooltip="Save row", on_click=self.save_row),
            ft.IconButton(icon=ft.Icons.DELETE, tooltip="Remove", on_click=self.remove_row),
        ])

        self.content = ft.Row(row_controls, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def set_revealed(self, state: bool):
        if not self.is_section_sensitive:
            return
        self.row_revealed = state
        self._page._field_vis[self.row_id] = state
        
        # Only hide text in TextFields; Checkboxes remain visible
        for ctrl in self.controls_map.values():
            if isinstance(ctrl, ft.TextField):
                ctrl.password = not state
                _safe_update(ctrl)
                
        if self.eye_btn:
            self.eye_btn.icon = ft.Icons.VISIBILITY_OFF if state else ft.Icons.VISIBILITY
            self.eye_btn.tooltip = "Hide" if state else "Reveal"
            _safe_update(self.eye_btn)

    def toggle_reveal(self, e=None):
        self.set_revealed(not self.row_revealed)

    def get_data_dict(self) -> dict:
        """Extracts the correct value type based on the control."""
        d = {}
        for k, ctrl in self.controls_map.items():
            if isinstance(ctrl, ft.Checkbox):
                d[k] = ctrl.value
            else:
                d[k] = (ctrl.value or "").strip()
        d["_id"] = self._item_id
        # Reattach stored metadata (_source, _updated, _ai_source)
        d.update(self._meta)
        return d

    def save_row(self, e):
        self.parent_panel.save_all(triggering_row=self)
        show_snack(self._page, "Saved row.", "green")

    def remove_row(self, e):
        dlg = _make_list_delete_dialog(self._page)
        def confirm_del(_e):
            dlg.open = False
            _safe_update(dlg)
            self.parent_panel.remove_row(self)
            self.parent_panel.save_all()
            show_snack(self._page, "Item deleted.", "green")
        def cancel_del(_e):
            dlg.open = False
            _safe_update(dlg)
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
        source: str = "",
        updated_at: str = "",
    ):
        super().__init__(tight=True, spacing=pt_scale(page, 10))
        self._page = page
        self.patient_id = patient_id
        self.field_key = field_key
        self.columns = columns
        self.is_section_sensitive = bool(is_section_sensitive)
        self.on_save = on_save
        self._source = source
        self._updated_at = updated_at

        _ensure_sets(page)
        self.panel_revealed = self._page._panel_vis.get(self.field_key, True)

        self.rows_col = ft.Column(spacing=pt_scale(page, 8), tight=True)
        self.row_components: List[ListRow] = []

        for it in items:
            self.add_row_component(it)

        self.add_btn = ft.FilledButton("Add", icon=ft.Icons.ADD, on_click=self.add_row)

        header_controls: List[Any] = [ft.Text(title, size=pt_scale(page, 18), weight="bold")]

        # Add panel master eye ONLY if feature is ON
        if self.is_section_sensitive:
            self.eye_btn = make_eye_btn(self._page, self.panel_revealed)
            self.eye_btn.tooltip = "Hide All" if self.panel_revealed else "Reveal All"
            self.eye_btn.on_click = self.toggle_panel_reveal
            header_controls.append(self.eye_btn)
        else:
            self.eye_btn = None

        header_controls.extend([ft.Container(expand=True), self.add_btn])

        panel_controls: List[Any] = [ft.Row(header_controls)]

        # Column headers for provenance (matching row controls)
        _ss = bool(getattr(page, "_show_source", False))
        _su = bool(getattr(page, "_show_updated", False))
        if _ss or _su:
            header_labels: List[Any] = []
            if _ss:
                header_labels.append(ft.Text("Source", size=pt_scale(page, 12), weight="bold", width=pt_scale(page, 120)))
            if _su:
                header_labels.append(ft.Text("Updated", size=pt_scale(page, 12), weight="bold", width=pt_scale(page, 140)))
            # Spacer to align with action buttons
            header_labels.extend([ft.Container(width=pt_scale(page, 40)), ft.Container(width=pt_scale(page, 40))])
            prov_header = ft.Row(header_labels, alignment=ft.MainAxisAlignment.END)
            panel_controls.append(prov_header)

        panel_controls.append(self.rows_col)
        self.controls = panel_controls

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

    def save_all(self, triggering_row=None):
        cleaned: List[dict] = []
        for rc in self.row_components:
            d = rc.get_data_dict()
            has_content = any(
                str(d.get(k, "")).strip() != "" 
                for k, _ in self.columns 
                if k != "is_current"
            )
            if not has_content:
                continue
            # Stamp per-item provenance only for the triggering row
            if triggering_row and rc is triggering_row:
                from datetime import datetime
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                d["_source"] = "user"
                d["_updated"] = now_str
                # Persist to _meta so future get_data_dict() calls include it
                rc._meta["_source"] = "user"
                rc._meta["_updated"] = now_str
                if rc._prov_updated_text:
                    rc._prov_updated_text.value = now_str
                    _safe_update(rc._prov_updated_text)
                if rc._prov_source_text:
                    rc._prov_source_text.value = "user"
                    _safe_update(rc._prov_source_text)
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
        super().__init__(tight=True, spacing=pt_scale(page, 6))
        self._page = page
        self.patient_id = patient_id
        self.category_name = category_name
        self.value_map = value_map
        self.is_section_sensitive = bool(is_section_sensitive)

        _ensure_sets(page)
        self._show_source = bool(getattr(page, "_show_source", False))
        self._show_updated = bool(getattr(page, "_show_updated", False))
        
        self.panel_key = f"cat_{slugify_label(self.category_name)}"
        self.panel_revealed = self._page._panel_vis.get(self.panel_key, True)

        self._rows: list[dict] = []

        cols = [
            ft.DataColumn(ft.Text("Field Name")),
            ft.DataColumn(ft.Text("Value")),
        ]
        if self._show_source:
            cols.append(ft.DataColumn(ft.Text("Source")))
        if self._show_updated:
            cols.append(ft.DataColumn(ft.Text("Updated")))
        cols += [
            ft.DataColumn(ft.Text("Save")),
            ft.DataColumn(ft.Text("Delete")),
        ]

        self.table = ft.DataTable(columns=cols, rows=[])
        for d in defs_list:
            self.table.rows.append(self.create_row(d))

        header_controls = [ft.Text(self.category_name, size=pt_scale(page, 18), weight="bold")]

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
                width=pt_scale(self._page, 200),
                read_only=True,
                border=ft.InputBorder.NONE,
                text_style=ft.TextStyle(weight="bold", color="grey"),
            )
        else:
            field_tf = ft.TextField(value=clean_lbl(label), dense=True, width=pt_scale(self._page, 200))

        default_vis = not self.is_section_sensitive
        row_revealed = self._page._field_vis.get(row_id, default_vis)

        can_be_password = self.is_section_sensitive
        value_tf = ft.TextField(
            value=str(val or ""),
            password=not row_revealed if can_be_password else False,
            can_reveal_password=False,
            dense=True,
            width=pt_scale(self._page, 450),
            multiline=not can_be_password,
            min_lines=1,
            max_lines=3 if not can_be_password else 1,
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
            
        val_cell = ft.Row(val_cell_controls, spacing=pt_scale(self._page, 4))

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
        if self._show_source:
            cells.append(ft.DataCell(src_text))
        if self._show_updated:
            cells.append(ft.DataCell(upd_text))
        cells += [
            ft.DataCell(ft.IconButton(icon=ft.Icons.SAVE, tooltip="Save", on_click=save_click)),
            ft.DataCell(del_btn),
        ]

        row.cells = cells
        return row


# -----------------------------------------------------------------------------
# 3) MAIN ORCHESTRATOR
# -----------------------------------------------------------------------------
def get_health_record_view(page: ft.Page):
    patient = page.current_profile
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    _ensure_sets(page)

    def refresh():
        if getattr(page, "content_area", None):
            page.content_area.content = get_health_record_view(page)
            page.content_area.update()
        else:
            page.update()

    page._health_record_refresh = refresh

    ensure_patient_info_dialogs(page, refresh)
    ensure_field_definition(page.db_connection, "conditions.list", "Conditions", data_type="json", category="History")
    ensure_field_definition(page.db_connection, "procedures.list", "Surgeries", data_type="json", category="History")

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
    
    allergies_key = "allergyintolerance.list"
    meds_key = "medicationstatement.current_list"
    insurance_key = "insurance.list"
    conditions_key = "conditions.list"
    surgeries_key = "procedures.list"

    hidden_seeds = {
        allergies_key, 
        meds_key, 
        insurance_key,
        conditions_key, 
        surgeries_key,
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
        padding=pt_scale(page, 12),
    )
    sections += [demographics_panel, ft.Container(height=pt_scale(page, 10))]

    def _list_meta(key):
        """Extract source and updated_at from value_map for a list field."""
        entry = value_map.get(key, {}) or {}
        return entry.get("source", ""), entry.get("updated_at", "")

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
            source=_list_meta(allergies_key)[0],
            updated_at=_list_meta(allergies_key)[1],
        ),
        padding=pt_scale(page, 12),
    )
    sections += [allergies_panel, ft.Container(height=pt_scale(page, 10))]

    meds_panel = themed_panel(
        page,
        ListEditorBody(
            page,
            patient_id,
            "Medications / Supplements", # Updated Title
            meds_key,
            _load_json_list((value_map.get(meds_key, {}) or {}).get("value")),
            [
                ("is_current", "Current?"),  # This will render as a checkbox logic
                ("name", "Name"),
                ("type", "Med/Supp"),
                ("dose", "Dose"),
                ("frequency", "Frequency"),
                ("notes", "Notes")
            ],
            is_section_sensitive=is_sens(meds_key),
            on_save=lambda items: upsert_patient_field_value(
                page.db_connection, patient_id, meds_key, json.dumps(items), "user"
            ),
            source=_list_meta(meds_key)[0],
            updated_at=_list_meta(meds_key)[1],
        ),
        padding=pt_scale(page, 12),
    )
    sections += [meds_panel, ft.Container(height=pt_scale(page, 10))]

    conditions_key = "conditions.list"
    # Add this to your sections list:
    conditions_panel = themed_panel(
        page,
        ListEditorBody(
            page,
            patient_id,
            "Conditions",
            conditions_key,
            _load_json_list((value_map.get(conditions_key, {}) or {}).get("value")),
            [
                ("name", "Condition"),
                ("onset_date", "Onset Date"),
                ("diagnosis_date", "Diagnosis Date"),
                ("symptoms", "Symptoms"),
                ("notes", "Notes")
            ],
            is_section_sensitive=is_sens("section.other"), # Grouped under "Other" shield
            on_save=lambda items: upsert_patient_field_value(
                page.db_connection, patient_id, conditions_key, json.dumps(items), "user"
            ),
            source=_list_meta(conditions_key)[0],
            updated_at=_list_meta(conditions_key)[1],
        ),
        padding=pt_scale(page, 12),
    )

    sections += [conditions_panel, ft.Container(height=pt_scale(page, 10))]

    surgeries_key = "procedures.list"
    surgeries_panel = themed_panel(
        page,
        ListEditorBody(
            page,
            patient_id,
            "Surgeries / Procedures",
            surgeries_key,
            _load_json_list((value_map.get(surgeries_key, {}) or {}).get("value")),
            [
                ("name", "Procedure Name"),
                ("date", "Date"),
                ("provider", "Surgeon/Facility"),
                ("notes", "Outcome/Notes")
            ],
            is_section_sensitive=is_sens("section.other"),
            on_save=lambda items: upsert_patient_field_value(
                page.db_connection, patient_id, surgeries_key, json.dumps(items), "user"
            ),
            source=_list_meta(surgeries_key)[0],
            updated_at=_list_meta(surgeries_key)[1],
        ),
        padding=pt_scale(page, 12),
    )

    sections += [surgeries_panel, ft.Container(height=pt_scale(page, 10))]

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
                ("phone", "Phone"),
                ("notes", "Notes"),
            ],
            is_section_sensitive=is_sens(insurance_key),
            on_save=lambda items: upsert_patient_field_value(page.db_connection, patient_id, insurance_key, json.dumps(items), "user"),
            source=_list_meta(insurance_key)[0],
            updated_at=_list_meta(insurance_key)[1],
        ),
        padding=pt_scale(page, 12),
    )
    sections += [insurance_panel, ft.Container(height=pt_scale(page, 10))]

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
        sections.append(themed_panel(page, panel, padding=pt_scale(page, 12)))
        sections.append(ft.Container(height=pt_scale(page, 10)))

    return ft.Container(
        padding=pt_scale(page, 20),
        content=ft.ListView(
            controls=[
                ft.Row(
                    [
                        ft.Text("Health Record", size=pt_scale(page, 22), weight="bold"),
                        ft.Container(expand=True),
                        ft.FilledTonalButton(
                            "Edit Visibility",
                            icon=ft.Icons.SHIELD,
                            on_click=lambda _: page.open_bulk_edit_dlg(),
                        ),
                    ]
                ),
                ft.Divider(),
                *sections,
            ],
            expand=True,
            spacing=pt_scale(page, 12),
            auto_scroll=False,
        ),
    )