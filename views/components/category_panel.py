# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import flet as ft
from datetime import datetime
import uuid
from typing import List

from utils.ui_helpers import (
    OUTLINE_VARIANT,
    pt_scale,
    show_snack,
    make_eye_btn,
    clean_lbl,
    slugify_label,
)
from database import (
    upsert_patient_field_value,
    ensure_field_definition,
    field_definition_exists,
    update_field_definition_label,
    update_profile,
    get_profile,
)
from views.components.helpers import (
    _ensure_sets, 
    _safe_update
)

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
        self._defs_list = list(defs_list)  # store for re-sort

        _ensure_sets(page)
        self._show_source = bool(getattr(page.mrma, "_show_source", False))
        self._show_updated = bool(getattr(page.mrma, "_show_updated", False))
        
        self.panel_key = f"cat_{slugify_label(self.category_name)}"
        self.panel_revealed = self._page.mrma._panel_vis.get(self.panel_key, True)

        # Per-panel sort state stored on page
        _sc_key = f"_hrsort_{self.panel_key}_col"
        _sa_key = f"_hrsort_{self.panel_key}_asc"
        if not hasattr(page, _sc_key):
            setattr(page, _sc_key, 0)   # default: Field Name
        if not hasattr(page, _sa_key):
            setattr(page, _sa_key, True)
        self._sc_key = _sc_key
        self._sa_key = _sa_key

        self._rows: list[dict] = []

        # ---- Sort handler ----
        def _on_sort(e: ft.DataColumnSortEvent):
            cur_col = getattr(self._page, self._sc_key)
            if cur_col == e.column_index:
                setattr(self._page, self._sa_key, not getattr(self._page, self._sa_key))
            else:
                setattr(self._page, self._sc_key, e.column_index)
                setattr(self._page, self._sa_key, True)
            self._rebuild_rows()

        cols = [
            ft.DataColumn(ft.Text("Field Name"), on_sort=_on_sort),
            ft.DataColumn(ft.Text("Value"),      on_sort=_on_sort),
        ]
        if self._show_source:
            cols.append(ft.DataColumn(ft.Text("Source")))
        if self._show_updated:
            cols.append(ft.DataColumn(ft.Text("Updated")))
        cols.append(ft.DataColumn(ft.Text("Actions")))

        self.table = ft.DataTable(
            columns=cols,
            rows=[],
            sort_column_index=getattr(page, _sc_key),
            sort_ascending=getattr(page, _sa_key),
            column_spacing=pt_scale(page, 12),
            heading_row_height=pt_scale(page, 40),
            data_row_min_height=pt_scale(page, 44),
            data_row_max_height=pt_scale(page, 56),
            heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
            border=ft.Border.all(1, OUTLINE_VARIANT),
            border_radius=8,
        )
        self._populate_table()

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

        self.controls = [ft.Row(header_controls), ft.Row([self.table], scroll=ft.ScrollMode.ALWAYS)]

    def _sort_defs(self, defs: list) -> list:
        """Sort defs_list by the current sort column and direction."""
        col = getattr(self._page, self._sc_key, 0)
        asc = getattr(self._page, self._sa_key, True)

        def _key(d):
            if col == 0:  # Field Name / label
                lbl = d[1] if d else ""
                return str(lbl or "").lower()
            elif col == 1:  # Value
                fk = d[0] if d else None
                if fk == "core.name":
                    return str(self._page.current_profile[1] or "").lower()
                elif fk == "core.dob":
                    return str(self._page.current_profile[2] or "").lower()
                val = (self.value_map.get(fk, {}) or {}).get("value", "") if fk else ""
                return str(val or "").lower()
            return ""

        return sorted(defs, key=_key, reverse=not asc)

    def _populate_table(self):
        """Build table rows from _defs_list in current sort order."""
        sorted_defs = self._sort_defs(self._defs_list)
        self.table.rows = [self.create_row(d) for d in sorted_defs]
        self.table.sort_column_index = getattr(self._page, self._sc_key, 0)
        self.table.sort_ascending = getattr(self._page, self._sa_key, True)

    def _rebuild_rows(self):
        """Re-sort and rebuild rows then update the table."""
        self._rows = []  # reset sensitivity tracking
        self._populate_table()
        _safe_update(self.table)

    def toggle_category(self, e):
        if not self.is_section_sensitive:
            return
            
        self.panel_revealed = not self.panel_revealed
        self._page.mrma._panel_vis[self.panel_key] = self.panel_revealed

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
        row_revealed = self._page.mrma._field_vis.get(row_id, default_vis)

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
                self._page.mrma._field_vis[row_id] = state
                value_tf.password = not state
                eye_btn.icon = ft.Icons.VISIBILITY_OFF if state else ft.Icons.VISIBILITY
                eye_btn.tooltip = "Hide" if state else "Reveal"
                _safe_update(value_tf)
                _safe_update(eye_btn)

            def toggle_row_eye(_e):
                current = self._page.mrma._field_vis.get(row_id, default_vis)
                set_revealed(not current)

            eye_btn.on_click = toggle_row_eye

            self._rows.append({
                "set_revealed": set_revealed,
                "field_key": field_key
            })

        val_cell = ft.DataCell(value_tf)

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
                self._page.mrma._field_vis[row_id] = self._page.mrma._field_vis.pop(old_id, True)
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
                self._page.mrma._open_delete_dialog(field_key, field_tf.value, row, self.table)

        del_btn.on_click = delete_click

        cells: List[ft.DataCell] = [
            ft.DataCell(field_tf),
            val_cell,
        ]
        if self._show_source:
            cells.append(ft.DataCell(src_text))
        if self._show_updated:
            cells.append(ft.DataCell(upd_text))
        # Actions: eye (if sensitive) → save → delete
        action_ctrls: List[ft.Control] = []
        if eye_btn:
            action_ctrls.append(eye_btn)
        action_ctrls += [
            ft.IconButton(icon=ft.Icons.SAVE, tooltip="Save", on_click=save_click, icon_size=18),
            del_btn,
        ]
        cells.append(ft.DataCell(ft.Row(action_ctrls, tight=True, spacing=0)))

        row.cells = cells
        return row
