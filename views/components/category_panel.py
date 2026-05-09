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
            ft.DataColumn(ft.Text("Info")),
            ft.DataColumn(ft.Text("Actions")),
        ]

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
        self._open_edit_dialog(None)

    def create_row(self, d_tuple):
        field_key = d_tuple[0] if d_tuple else None
        label = d_tuple[1] if d_tuple else ""

        is_core = field_key in ("core.name", "core.dob")
        can_delete = not is_core
        
        row_id = field_key if field_key else f"new_{uuid.uuid4().hex[:8]}"

        doc_id = None
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
            doc_id = existing.get("source_doc_id")

        # Instead of editable TextFields in the table, use read-only text in a clickable container
        field_text = ft.Text(
            clean_lbl(label),
            weight="bold" if is_core else "normal", 
            color="grey" if is_core else None,
            no_wrap=False
        )
        
        def edit_click(e):
            self._open_edit_dialog(d_tuple)
            
        field_container = ft.Container(
            content=field_text,
            width=pt_scale(self._page, 200),
            padding=ft.padding.symmetric(vertical=pt_scale(self._page, 8)),
            on_click=edit_click,
            ink=True,
            border_radius=4,
        )

        default_vis = not self.is_section_sensitive
        row_revealed = self._page.mrma._field_vis.get(row_id, default_vis)

        can_be_password = self.is_section_sensitive
        display_str = str(val or "")
        if can_be_password and not row_revealed:
            display_str = "••••••••" if display_str else ""

        value_text = ft.Text(display_str, no_wrap=False)
        
        def edit_click(e):
            self._open_edit_dialog(d_tuple)
            
        value_container = ft.Container(
            content=value_text,
            width=pt_scale(self._page, 450),
            padding=ft.padding.symmetric(vertical=pt_scale(self._page, 8)),
            on_click=edit_click,
            ink=True,
            border_radius=4,
        )

        eye_btn = None
        if self.is_section_sensitive:
            eye_btn = make_eye_btn(self._page, row_revealed)

            def set_revealed(state: bool):
                self._page.mrma._field_vis[row_id] = state
                value_text.value = str(val or "") if state else ("••••••••" if val else "")
                eye_btn.icon = ft.Icons.VISIBILITY_OFF if state else ft.Icons.VISIBILITY
                eye_btn.tooltip = "Hide" if state else "Reveal"
                _safe_update(value_text)
                _safe_update(eye_btn)

            def toggle_row_eye(_e):
                current = self._page.mrma._field_vis.get(row_id, default_vis)
                set_revealed(not current)

            eye_btn.on_click = toggle_row_eye

            self._rows.append({
                "set_revealed": set_revealed,
                "field_key": field_key
            })

        val_cell = ft.DataCell(value_container)

        row = ft.DataRow(cells=[])
        del_btn = ft.IconButton(icon=ft.Icons.DELETE, tooltip="Delete", disabled=not can_delete)

        def edit_click(e):
            self._open_edit_dialog(d_tuple)

        def delete_click(e):
            if not field_key:
                self.table.rows.remove(row)
                _safe_update(self.table)
            else:
                self._page.mrma._open_delete_dialog(field_key, field_text.value, row, self.table)

        del_btn.on_click = delete_click

        def info_click(e):
            self._open_info_dialog(clean_lbl(label), display_str, src, upd, doc_id)

        info_btn = ft.IconButton(icon=ft.Icons.INFO_OUTLINE, tooltip="View details", on_click=info_click)

        cells: List[ft.DataCell] = [
            ft.DataCell(field_container),
            val_cell,
            ft.DataCell(info_btn),
        ]
        # Actions: eye (if sensitive) → edit → delete
        action_ctrls: List[ft.Control] = []
        if eye_btn:
            action_ctrls.append(eye_btn)
        action_ctrls += [
            ft.IconButton(icon=ft.Icons.EDIT, tooltip="Edit", on_click=edit_click, icon_size=18),
            del_btn,
        ]
        cells.append(ft.DataCell(ft.Row(action_ctrls, tight=True, spacing=0)))

        row.cells = cells
        return row

    def _open_edit_dialog(self, d_tuple):
        from utils.ui_helpers import append_dialog, show_snack
        
        field_key = d_tuple[0] if d_tuple else None
        label = d_tuple[1] if d_tuple else ""

        is_core = field_key in ("core.name", "core.dob")

        if field_key == "core.name":
            val = self._page.current_profile[1] or ""
            src = "system"
        elif field_key == "core.dob":
            val = self._page.current_profile[2] or ""
            src = "system"
        else:
            existing = self.value_map.get(field_key, {}) if field_key else {}
            val = existing.get("value", "")
            src = existing.get("source", "")

        field_name_tf = ft.TextField(
            label="Field Name",
            value=clean_lbl(label),
            dense=True,
            read_only=is_core,
            width=pt_scale(self._page, 600)
        )
        
        value_tf = ft.TextField(
            label="Value",
            value=str(val or ""),
            dense=True,
            multiline=True,
            min_lines=5,
            width=pt_scale(self._page, 600)
        )

        def _save(e=None):
            new_lbl = (field_name_tf.value or "").strip()
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
                
                # Add to local defs list so it rebuilds correctly
                self._defs_list.append((fk, new_lbl, "text", self.category_name, 0))
            else:
                # Update the label of an existing custom field (Core fields are read-only)
                if not is_core:
                    update_field_definition_label(self._page.db_connection, fk, new_lbl)
                    # Update local defs list label
                    for i, d in enumerate(self._defs_list):
                        if d[0] == fk:
                            self._defs_list[i] = (d[0], new_lbl, d[2], d[3], d[4])
                            break

            # Handle Core Profile fields vs. Generic custom fields
            if fk == "core.name":
                p = self._page.current_profile
                update_profile(self._page.db_connection, p[0], new_val, p[2], p[3])
                self._page.current_profile = get_profile(self._page.db_connection)
            elif fk == "core.dob":
                p = self._page.current_profile
                update_profile(self._page.db_connection, p[0], p[1], new_val, p[3])
                self._page.current_profile = get_profile(self._page.db_connection)
            else:
                # Standard custom field value update
                upsert_patient_field_value(
                    self._page.db_connection, 
                    self.patient_id, 
                    fk, 
                    new_val, 
                    src
                )

            # Update value_map locally so rebuild uses new data
            if fk not in self.value_map:
                self.value_map[fk] = {}
            self.value_map[fk]["value"] = new_val
            if is_core:
                self.value_map[fk]["source"] = "system"
            
            # Rebuild table to show changes
            self._rebuild_rows()
            _safe_update(self.table)
            
            # Close dialog and refresh health record view completely if core changed
            dlg.open = False
            _safe_update(dlg)
            show_snack(self._page, "Saved.", "green")
            
            if is_core:
                try:
                    refresh_fn = getattr(self._page.mrma, "_refresh_hr_view", None)
                    if refresh_fn:
                        refresh_fn()
                except Exception:
                    pass

        def _cancel(e=None):
            dlg.open = False
            _safe_update(dlg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Text("Edit Field"),
                ft.IconButton(ft.Icons.CLOSE, on_click=_cancel)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            content=ft.Column([field_name_tf, value_tf], tight=True, scroll=ft.ScrollMode.AUTO, width=pt_scale(self._page, 600)),
            actions=[
                ft.TextButton("Cancel", on_click=_cancel),
                ft.FilledButton("Save", on_click=_save)
            ],
            on_dismiss=_cancel
        )
        
        append_dialog(self._page, dlg)
        dlg.open = True
        self._page.update()

    def _open_info_dialog(self, field_label, value, source, updated_at, source_doc_id=None):
        from utils.ui_helpers import append_dialog
        
        # Build source control
        source_control = None
        if source_doc_id:
            src_label = None
            try:
                cur = self._page.db_connection.cursor()
                cur.execute("SELECT file_name FROM documents WHERE id = ?", (source_doc_id,))
                row = cur.fetchone()
                if row:
                    src_label = row[0]
            except Exception:
                pass
                
            if src_label:
                def _nav_to_doc(e):
                    self._page.mrma._doc_search_term = src_label
                    self._page.go("/documents")
                
                source_control = ft.Text(
                    spans=[
                        ft.TextSpan("Source: ", style=ft.TextStyle(italic=True)),
                        ft.TextSpan(
                            src_label,
                            style=ft.TextStyle(color=ft.Colors.BLUE),
                            on_click=_nav_to_doc,
                        )
                    ],
                    tooltip=f"View source document: {src_label}"
                )
                
        if not source_control:
            source_lbl = source.capitalize() if source else 'Manual entry'
            if source_lbl.lower() == 'ai': source_lbl = 'AI extraction'
            source_control = ft.Text(f"Source: {source_lbl}", italic=True)

        def _close(e=None):
            dlg.open = False
            _safe_update(dlg)

        dlg = ft.AlertDialog(
            title=ft.Row([
                ft.Text("Details", weight="bold"),
                ft.IconButton(ft.Icons.CLOSE, on_click=_close)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            content=ft.Container(
                width=400,
                content=ft.Column([
                    ft.Text(f"Field: {field_label}"),
                    ft.Text(f"Value: {value}"),
                    ft.Divider(),
                    source_control,
                    ft.Text(f"Updated: {updated_at or 'Unknown'}", size=12, italic=True),
                ], tight=True)
            ),
            actions=[ft.FilledButton("Close", on_click=_close)],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close
        )
        append_dialog(self._page, dlg)
        dlg.open = True
        self._page.update()
