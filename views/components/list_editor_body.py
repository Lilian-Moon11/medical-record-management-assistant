# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import flet as ft
from datetime import datetime
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.ui_helpers import (
    OUTLINE_VARIANT,
    pt_scale,
    show_snack,
    make_eye_btn,
    append_dialog,
)
from views.components.helpers import (
    _ensure_sets, 
    _safe_update, 
    _make_list_delete_dialog
)

_FIELD_WIDTHS: dict = {
    # Primary identifier columns
    "substance":      180,
    "name":           220,
    "payer":          180,
    # Secondary descriptor columns
    "reaction":       200,
    "symptoms":       200,
    "dose":            90,
    "route":           110,
    "frequency":      130,
    # Date columns
    "date":           110,
    "onset_date":     110,
    "diagnosis_date": 120,
    # Surgeon split
    "surgeon":        200,
    "facility":       200,
    # Insurance detail columns
    "member_id":      130,
    "group_no":        80,
    "bin":             70,
    "pcn":             70,
    "phone":          120,
    # Long-text / notes — wide like the Value column in Demographics
    "notes":          340,
}
_DEFAULT_FIELD_WIDTH = 150  # fallback for unlisted keys

class ListEditorBody(ft.Column):
    """
    Renders a JSON-backed list (e.g. allergies, medications) as a ft.DataTable
    with inline-editable TextFields per cell.  Matches the visual style of
    CategoryPanel / Demographics.  Replaces the previous ListRow approach.
    """

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
        self.panel_revealed = self._page.mrma._panel_vis.get(self.field_key, True)

        # Sort state
        self._sort_col_key: str | None = None
        self._sort_col_idx: int | None = None
        self._sort_asc: bool = True

        # Ensure every item has a stable _id
        for item in items:
            if not item.get("_id"):
                item["_id"] = uuid.uuid4().hex[:8]
        self._items: List[dict] = list(items)

        # {item_id: {field_key: control}}
        self._ctrl_refs: dict = {}

        # --- DataTable ---
        self.data_table = ft.DataTable(
            columns=self._build_col_headers(),
            rows=[],
            column_spacing=pt_scale(page, 12),
            heading_row_height=pt_scale(page, 40),
            data_row_min_height=pt_scale(page, 44),
            data_row_max_height=pt_scale(page, 56),
            heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
            border=ft.Border.all(1, OUTLINE_VARIANT),
            border_radius=8,
        )
        self._build_table_rows()

        # --- Header ---
        self.add_btn = ft.FilledButton("Add", icon=ft.Icons.ADD, on_click=self.add_row)
        header_controls: List[Any] = [ft.Text(title, size=pt_scale(page, 18), weight="bold")]
        if self.is_section_sensitive:
            self.eye_btn = make_eye_btn(self._page, self.panel_revealed)
            self.eye_btn.tooltip = "Hide All" if self.panel_revealed else "Reveal All"
            self.eye_btn.on_click = self.toggle_panel_reveal
            header_controls.append(self.eye_btn)
        else:
            self.eye_btn = None
        header_controls.extend([ft.Container(expand=True), self.add_btn])

        self.controls = [
            ft.Row(header_controls),
            ft.Row([self.data_table], scroll=ft.ScrollMode.ALWAYS),
        ]

    # ------------------------------------------------------------------
    # Column header construction
    # ------------------------------------------------------------------
    def _build_col_headers(self) -> List[ft.DataColumn]:
        cols: List[ft.DataColumn] = []
        for k, lbl in self.columns:
            if k.startswith("is_"):
                cols.append(ft.DataColumn(ft.Text(lbl)))
            else:
                cols.append(ft.DataColumn(ft.Text(lbl), on_sort=self._on_col_sort))
        cols.append(ft.DataColumn(ft.Text("Info")))
        cols.append(ft.DataColumn(ft.Text("Actions")))
        return cols

    # ------------------------------------------------------------------
    # Row building
    # ------------------------------------------------------------------
    def _build_table_rows(self) -> None:
        self._ctrl_refs = {}
        rows: List[ft.DataRow] = []

        for item in self._items:
            item_id = item.get("_id") or uuid.uuid4().hex[:8]
            item["_id"] = item_id
            vis_key = f"{self.field_key}_{item_id}"
            default_vis = not self.is_section_sensitive
            revealed = self._page.mrma._field_vis.get(vis_key, default_vis)

            ctrl_map: dict = {}
            cells: List[ft.DataCell] = []

            for k, _lbl in self.columns:
                if k.startswith("is_"):
                    # For newly added rows that don't have the key yet, default to True for active/current fields
                    default_val = True if item.get(k) is None else bool(item.get(k))
                    cb = ft.Checkbox(
                        value=default_val,
                        on_change=lambda e, iid=item_id: self._save_row(iid),
                    )
                    ctrl_map[k] = cb
                    cells.append(ft.DataCell(cb))
                else:
                    col_w = pt_scale(self._page, _FIELD_WIDTHS.get(k, _DEFAULT_FIELD_WIDTH))
                    val_str = str(item.get(k, "") or "")
                    
                    if self.is_section_sensitive and not revealed:
                        display_str = "••••••••" if val_str else ""
                    else:
                        display_str = val_str

                    # Read-only container that opens the edit dialog when clicked
                    text_ctrl = ft.Text(display_str, no_wrap=False, size=pt_scale(self._page, 13))
                    container = ft.Container(
                        content=text_ctrl,
                        width=col_w,
                        padding=ft.padding.symmetric(vertical=pt_scale(self._page, 8)),
                        on_click=lambda e, iid=item_id: self._open_edit_dialog(iid),
                        ink=True,
                    )
                    ctrl_map[k] = text_ctrl # store ref to Text to toggle visibility
                    cells.append(ft.DataCell(container))

            self._ctrl_refs[item_id] = ctrl_map

            # --- Info cell ---
            def info_click(e, iid=item_id):
                self._open_info_dialog(iid)
            info_btn = ft.IconButton(icon=ft.Icons.INFO_OUTLINE, tooltip="View details", on_click=info_click)
            cells.append(ft.DataCell(info_btn))

            # --- Action cell (eye? + save + delete) ---
            action_ctrls: List[ft.Control] = []
            if self.is_section_sensitive:
                eye = make_eye_btn(self._page, revealed)
                eye.on_click = lambda e, iid=item_id: self._toggle_row_reveal(iid)
                action_ctrls.append(eye)
            action_ctrls += [
                ft.IconButton(
                    ft.Icons.EDIT,
                    tooltip="Edit row",
                    icon_size=18,
                    on_click=lambda e, iid=item_id: self._open_edit_dialog(iid),
                ),
                ft.IconButton(
                    ft.Icons.DELETE,
                    tooltip="Remove row",
                    icon_size=18,
                    on_click=lambda e, iid=item_id: self._delete_row(iid),
                ),
            ]
            cells.append(ft.DataCell(ft.Row(action_ctrls, tight=True, spacing=0)))

            rows.append(ft.DataRow(cells=cells))

        self.data_table.rows = rows

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def _collect_item(self, item_id: str) -> dict:
        """Read live control values for one item into a dict."""
        ctrls = self._ctrl_refs.get(item_id, {})
        orig = next((x for x in self._items if x.get("_id") == item_id), {})
        d: dict = {}
        for k, _ in self.columns:
            ctrl = ctrls.get(k)
            if ctrl is None:
                d[k] = orig.get(k, "")
            elif isinstance(ctrl, ft.Checkbox):
                d[k] = bool(ctrl.value)
            else:
                d[k] = (ctrl.value or "").strip()
        d["_id"] = item_id
        # Preserve underscore-prefixed metadata
        for mk, mv in orig.items():
            if mk.startswith("_") and mk != "_id":
                d[mk] = mv
        return d

    def _persist(self) -> None:
        """Write non-empty items to storage via on_save."""
        clean = [
            d for d in self._items
            if any(
                str(d.get(k, "")).strip() != ""
                for k, _ in self.columns
                if not k.startswith("is_")
            )
        ]
        self.on_save(clean)

    # ------------------------------------------------------------------
    # Row operations
    # ------------------------------------------------------------------
    def _open_edit_dialog(self, item_id: str) -> None:
        item = next((x for x in self._items if x.get("_id") == item_id), None)
        if not item: return

        dlg_fields = {}
        controls = []
        for k, lbl in self.columns:
            if k.startswith("is_"):
                # Default to True for new items
                default_val = True if item.get(k) is None else bool(item.get(k))
                cb = ft.Checkbox(label=lbl, value=default_val)
                dlg_fields[k] = cb
                controls.append(cb)
            else:
                tf = ft.TextField(
                    label=lbl,
                    value=str(item.get(k, "") or ""),
                    dense=True,
                    multiline=(k in ["notes", "symptoms"]),
                    min_lines=5 if k in ["notes", "symptoms"] else 1
                )
                dlg_fields[k] = tf
                controls.append(tf)

        def _save(e=None):
            d = dict(item)
            for k, ctrl in dlg_fields.items():
                if isinstance(ctrl, ft.Checkbox):
                    d[k] = bool(ctrl.value)
                else:
                    d[k] = (ctrl.value or "").strip()
            
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            d["_updated"] = now_str

            for i, it in enumerate(self._items):
                if it.get("_id") == item_id:
                    self._items[i] = d
                    break
            
            self._persist()
            self._build_table_rows()
            _safe_update(self.data_table)
            dlg.open = False
            _safe_update(dlg)
            show_snack(self._page, "Saved row.", "green")

        def _cancel(e=None):
            dlg.open = False
            _safe_update(dlg)

        for k, ctrl in dlg_fields.items():
            if isinstance(ctrl, ft.TextField) and ctrl.multiline is False:
                ctrl.on_submit = _save

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Text("Edit Item"),
                ft.IconButton(ft.Icons.CLOSE, on_click=_cancel)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            content=ft.Column(controls, tight=True, scroll=ft.ScrollMode.AUTO, width=pt_scale(self._page, 600)),
            actions=[
                ft.TextButton("Cancel", on_click=_cancel),
                ft.FilledButton("Save", on_click=_save)
            ],
            on_dismiss=_cancel
        )
        
        append_dialog(self._page, dlg)
        dlg.open = True
        self._page.update()

    def _save_row(self, item_id: str) -> None:
        # Kept for compatibility with inline checkboxes (e.g. is_current)
        d = next((x for x in self._items if x.get("_id") == item_id), {})
        # Update any checkbox state directly from UI
        ctrls = self._ctrl_refs.get(item_id, {})
        for k, _ in self.columns:
            ctrl = ctrls.get(k)
            if isinstance(ctrl, ft.Checkbox):
                d[k] = bool(ctrl.value)
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        d["_updated"] = now_str
        self._persist()
        show_snack(self._page, "Saved.", "green")

    def _delete_row(self, item_id: str) -> None:
        dlg = _make_list_delete_dialog(self._page)

        def confirm(_):
            dlg.open = False
            _safe_update(dlg)
            self._items = [x for x in self._items if x.get("_id") != item_id]
            self._persist()
            self._build_table_rows()
            _safe_update(self.data_table)
            show_snack(self._page, "Item deleted.", "green")

        def cancel(_):
            dlg.open = False
            _safe_update(dlg)

        dlg.actions[0].on_click = cancel
        dlg.actions[1].on_click = confirm
        dlg.open = True
        self._page.update()

    def add_row(self, e=None) -> None:
        new_item = {"_id": uuid.uuid4().hex[:8]}
        self._items.append(new_item)
        self._build_table_rows()
        _safe_update(self.data_table)
        self._open_edit_dialog(new_item["_id"])

    def _open_info_dialog(self, item_id: str) -> None:
        from utils.ui_helpers import append_dialog
        
        item = next((x for x in self._items if x.get("_id") == item_id), {})
        if not item: return

        ai_fname = item.get("_ai_source", "")
        updated_val = str(item.get("_updated", "") or "\u2014")

        # Keep existing external decrypt-and-open behavior
        if ai_fname:
            def _open_ai_doc(e, fname=ai_fname):
                import os, tempfile, time as _time
                from crypto.file_crypto import get_or_create_file_master_key, decrypt_bytes
                from utils.open_file import open_file_cross_platform
                try:
                    cur = self._page.db_connection.cursor()
                    cur.execute(
                        "SELECT file_path FROM documents WHERE patient_id=? AND file_name=? ORDER BY id DESC LIMIT 1",
                        (self.patient_id, fname),
                    )
                    row = cur.fetchone()
                    if not row or not row[0]:
                        show_snack(self._page, "Source file not found.", "red")
                        return
                    from core.paths import resolve_doc_path
                    resolved = str(resolve_doc_path(row[0]))
                    if not os.path.exists(resolved):
                        show_snack(self._page, "Source file not found.", "red")
                        return
                    fmk = get_or_create_file_master_key(self._page.db_connection, dmk_raw=self._page.db_key_raw)
                    with open(resolved, "rb") as f:
                        ciphertext = f.read()
                    plaintext = decrypt_bytes(fmk, ciphertext)
                    _, ext = os.path.splitext(fname)
                    tmp = os.path.join(tempfile.gettempdir(), f"mrma_dec_{int(_time.time())}{ext or '.pdf'}")
                    with open(tmp, "wb") as f:
                        f.write(plaintext)
                    open_file_cross_platform(tmp)
                    show_snack(self._page, f"Opened {fname}", "blue")
                except Exception as ex:
                    show_snack(self._page, f"Open failed: {ex}", "red")

            source_control = ft.Text(
                spans=[
                    ft.TextSpan("Source: ", style=ft.TextStyle(italic=True)),
                    ft.TextSpan(
                        ai_fname,
                        style=ft.TextStyle(color=ft.Colors.BLUE),
                        on_click=_open_ai_doc,
                    )
                ],
                tooltip=f"Open source document: {ai_fname}"
            )
        else:
            source_control = ft.Text("Source: Manual entry", italic=True)

        body_controls = []
        for k, lbl in self.columns:
            val = str(item.get(k, "") or "")
            if val:
                body_controls.append(ft.Text(f"{lbl}: {val}"))
        
        if not body_controls:
            body_controls.append(ft.Text("No data available."))
            
        body_controls.append(ft.Divider())
        body_controls.append(source_control)
        body_controls.append(ft.Text(f"Updated: {updated_val}", size=12, italic=True))

        def _close(e=None):
            dlg.open = False
            _safe_update(dlg)

        dlg = ft.AlertDialog(
            title=ft.Row([
                ft.Text("Details", weight="bold"),
                ft.IconButton(ft.Icons.CLOSE, on_click=_close)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            content=ft.Column(body_controls, tight=True, scroll=True),
            actions=[ft.FilledButton("Close", on_click=_close)],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close
        )
        append_dialog(self._page, dlg)
        dlg.open = True
        self._page.update()

    # ------------------------------------------------------------------
    # Sensitivity toggles
    # ------------------------------------------------------------------
    def toggle_panel_reveal(self, e=None) -> None:
        if not self.is_section_sensitive:
            return
        self.panel_revealed = not self.panel_revealed
        self._page.mrma._panel_vis[self.field_key] = self.panel_revealed
        if self.eye_btn:
            self.eye_btn.icon = (
                ft.Icons.VISIBILITY_OFF if self.panel_revealed else ft.Icons.VISIBILITY
            )
            self.eye_btn.tooltip = "Hide All" if self.panel_revealed else "Reveal All"
            _safe_update(self.eye_btn)
        for item in self._items:
            iid = item.get("_id")
            if not iid:
                continue
            vis_key = f"{self.field_key}_{iid}"
            self._page.mrma._field_vis[vis_key] = self.panel_revealed
            for k, _ in self.columns:
                ctrl = self._ctrl_refs.get(iid, {}).get(k)
                if isinstance(ctrl, ft.Text):
                    val_str = str(item.get(k, "") or "")
                    ctrl.value = val_str if self.panel_revealed else ("••••••••" if val_str else "")
                    _safe_update(ctrl)

    def _toggle_row_reveal(self, item_id: str) -> None:
        vis_key = f"{self.field_key}_{item_id}"
        current = self._page.mrma._field_vis.get(vis_key, not self.is_section_sensitive)
        new_state = not current
        self._page.mrma._field_vis[vis_key] = new_state
        for k, _ in self.columns:
            ctrl = self._ctrl_refs.get(item_id, {}).get(k)
            if isinstance(ctrl, ft.Text):
                item = next((x for x in self._items if x.get("_id") == item_id), {})
                val_str = str(item.get(k, "") or "")
                ctrl.value = val_str if new_state else ("••••••••" if val_str else "")
                _safe_update(ctrl)

    # ------------------------------------------------------------------
    # Column-header sort
    # ------------------------------------------------------------------
    def _on_col_sort(self, e: ft.DataColumnSortEvent) -> None:
        col_idx = e.column_index
        if col_idx >= len(self.columns):
            return
        col_key = self.columns[col_idx][0]
        if col_key.startswith("is_"):
            return

        # No need to snapshot live TextField values anymore since they are stored in item directly
        if self._sort_col_key == col_key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col_key = col_key
            self._sort_col_idx = col_idx
            self._sort_asc = True

        def _key(d: dict):
            v = d.get(col_key, "")
            return str(v or "").lower()

        self._items.sort(key=_key, reverse=not self._sort_asc)
        self._build_table_rows()
        self.data_table.sort_column_index = self._sort_col_idx or col_idx
        self.data_table.sort_ascending = self._sort_asc
