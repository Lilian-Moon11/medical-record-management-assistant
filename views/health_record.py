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
    make_info_button,
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


def _migrate_surgeries(items: List[dict]) -> List[dict]:
    """Migrate legacy 'provider' field → 'surgeon' (facility left blank)."""
    for item in items:
        if "provider" in item:
            if not item.get("surgeon"):
                item["surgeon"] = item.pop("provider")
            else:
                del item["provider"]
    return items


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
# 1) JSON LIST PANELS — DataTable-based (Allergies, Meds, Insurance, etc.)
# -----------------------------------------------------------------------------

# Per-column TextField width hints (in pt, before scaling).
# Notes / main-content columns are intentionally wide to match the
# Demographics "Value" column width.  Horizontal scroll handles overflow.
_FIELD_WIDTHS: dict = {
    # Primary identifier columns
    "substance":      180,
    "name":           220,
    "payer":          180,
    # Secondary descriptor columns
    "reaction":       200,
    "symptoms":       200,
    "dose":            90,
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
        self.panel_revealed = self._page._panel_vis.get(self.field_key, True)

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
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
            if hasattr(ft.Colors, "OUTLINE_VARIANT") else None,
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
            if k == "is_current":
                cols.append(ft.DataColumn(ft.Text(lbl)))
            else:
                cols.append(ft.DataColumn(ft.Text(lbl), on_sort=self._on_col_sort))
        _ss = bool(getattr(self._page, "_show_source", False))
        _su = bool(getattr(self._page, "_show_updated", False))
        if _ss:
            cols.append(ft.DataColumn(ft.Text("Source")))
        if _su:
            cols.append(ft.DataColumn(ft.Text("Updated")))
        cols.append(ft.DataColumn(ft.Text("Actions")))
        return cols

    # ------------------------------------------------------------------
    # Row building
    # ------------------------------------------------------------------
    def _build_table_rows(self) -> None:
        _ss = bool(getattr(self._page, "_show_source", False))
        _su = bool(getattr(self._page, "_show_updated", False))
        self._ctrl_refs = {}
        rows: List[ft.DataRow] = []

        for item in self._items:
            item_id = item.get("_id") or uuid.uuid4().hex[:8]
            item["_id"] = item_id
            vis_key = f"{self.field_key}_{item_id}"
            default_vis = not self.is_section_sensitive
            revealed = self._page._field_vis.get(vis_key, default_vis)

            ctrl_map: dict = {}
            cells: List[ft.DataCell] = []

            for k, _lbl in self.columns:
                if k == "is_current":
                    cb = ft.Checkbox(
                        value=bool(item.get(k, False)),
                        on_change=lambda e, iid=item_id: self._save_row(iid),
                    )
                    ctrl_map[k] = cb
                    cells.append(ft.DataCell(cb))
                else:
                    col_w = pt_scale(self._page, _FIELD_WIDTHS.get(k, _DEFAULT_FIELD_WIDTH))
                    tf = ft.TextField(
                        value=str(item.get(k, "") or ""),
                        dense=True,
                        border_radius=4,
                        password=self.is_section_sensitive and not revealed,
                        can_reveal_password=False,
                        width=col_w,
                    )
                    ctrl_map[k] = tf
                    cells.append(ft.DataCell(tf))

            self._ctrl_refs[item_id] = ctrl_map

            # --- Provenance cells ---
            if _ss:
                src_val = str(item.get("_source", "") or "User")
                ai_fname = item.get("_ai_source", "")
                if src_val.lower() == "ai" and ai_fname:
                    def _open_ai_doc(e, fname=ai_fname):
                        import os, tempfile, time as _time
                        from crypto.file_crypto import (
                            get_or_create_file_master_key, decrypt_bytes,
                        )
                        try:
                            cur = self._page.db_connection.cursor()
                            cur.execute(
                                "SELECT file_path FROM documents "
                                "WHERE patient_id=? AND file_name=? "
                                "ORDER BY id DESC LIMIT 1",
                                (self.patient_id, fname),
                            )
                            row = cur.fetchone()
                            if not row or not row[0] or not os.path.exists(row[0]):
                                show_snack(self._page, "Source file not found.", "red")
                                return
                            fmk = get_or_create_file_master_key(
                                self._page.db_connection,
                                dmk_raw=self._page.db_key_raw,
                            )
                            with open(row[0], "rb") as f:
                                ciphertext = f.read()
                            plaintext = decrypt_bytes(fmk, ciphertext)
                            _, ext = os.path.splitext(fname)
                            tmp = os.path.join(
                                tempfile.gettempdir(),
                                f"mrma_dec_{int(_time.time())}{ext or '.pdf'}",
                            )
                            with open(tmp, "wb") as f:
                                f.write(plaintext)
                            os.startfile(tmp)
                            show_snack(self._page, f"Opened {fname}", "blue")
                        except Exception as ex:
                            show_snack(self._page, f"Open failed: {ex}", "red")

                    src_ctrl: ft.Control = ft.TextButton(
                        ai_fname,
                        on_click=_open_ai_doc,
                        tooltip="Open source document",
                        style=ft.ButtonStyle(color=ft.Colors.BLUE, padding=0),
                    )
                else:
                    src_ctrl = ft.Text(src_val)
                cells.append(ft.DataCell(src_ctrl))

            if _su:
                upd_val = str(item.get("_updated", "") or "\u2014")
                cells.append(ft.DataCell(ft.Text(upd_val)))

            # --- Action cell (eye? + save + delete) ---
            action_ctrls: List[ft.Control] = []
            if self.is_section_sensitive:
                eye = make_eye_btn(self._page, revealed)
                eye.on_click = lambda e, iid=item_id: self._toggle_row_reveal(iid)
                action_ctrls.append(eye)
            action_ctrls += [
                ft.IconButton(
                    ft.Icons.SAVE,
                    tooltip="Save row",
                    icon_size=18,
                    on_click=lambda e, iid=item_id: self._save_row(iid),
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
                if k != "is_current"
            )
        ]
        self.on_save(clean)

    # ------------------------------------------------------------------
    # Row operations
    # ------------------------------------------------------------------
    def _save_row(self, item_id: str) -> None:
        d = self._collect_item(item_id)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        d["_source"] = "user"
        d["_updated"] = now_str
        for i, item in enumerate(self._items):
            if item.get("_id") == item_id:
                self._items[i] = d
                break
        self._persist()
        show_snack(self._page, "Saved row.", "green")

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

    # ------------------------------------------------------------------
    # Sensitivity toggles
    # ------------------------------------------------------------------
    def toggle_panel_reveal(self, e=None) -> None:
        if not self.is_section_sensitive:
            return
        self.panel_revealed = not self.panel_revealed
        self._page._panel_vis[self.field_key] = self.panel_revealed
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
            self._page._field_vis[vis_key] = self.panel_revealed
            for k, _ in self.columns:
                ctrl = self._ctrl_refs.get(iid, {}).get(k)
                if isinstance(ctrl, ft.TextField):
                    ctrl.password = not self.panel_revealed
                    _safe_update(ctrl)

    def _toggle_row_reveal(self, item_id: str) -> None:
        vis_key = f"{self.field_key}_{item_id}"
        current = self._page._field_vis.get(vis_key, not self.is_section_sensitive)
        new_state = not current
        self._page._field_vis[vis_key] = new_state
        for k, _ in self.columns:
            ctrl = self._ctrl_refs.get(item_id, {}).get(k)
            if isinstance(ctrl, ft.TextField):
                ctrl.password = not new_state
                _safe_update(ctrl)

    # ------------------------------------------------------------------
    # Column-header sort
    # ------------------------------------------------------------------
    def _on_col_sort(self, e: ft.DataColumnSortEvent) -> None:
        col_idx = e.column_index
        if col_idx >= len(self.columns):
            return
        col_key = self.columns[col_idx][0]
        if col_key == "is_current":
            return

        # Snapshot live TextField values before sorting
        for item in self._items:
            iid = item.get("_id")
            if iid and iid in self._ctrl_refs:
                for k, _ in self.columns:
                    ctrl = self._ctrl_refs[iid].get(k)
                    if ctrl and not isinstance(ctrl, ft.Checkbox):
                        item[k] = (ctrl.value or "").strip()

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
        self._defs_list = list(defs_list)  # store for re-sort

        _ensure_sets(page)
        self._show_source = bool(getattr(page, "_show_source", False))
        self._show_updated = bool(getattr(page, "_show_updated", False))
        
        self.panel_key = f"cat_{slugify_label(self.category_name)}"
        self.panel_revealed = self._page._panel_vis.get(self.panel_key, True)

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
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
            if hasattr(ft.Colors, "OUTLINE_VARIANT") else None,
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
    # Optional identity fields — not required, fully deletable
    ensure_field_definition(page.db_connection, "patient.pronouns", "Pronouns", data_type="text", category="Demographics")
    ensure_field_definition(page.db_connection, "patient.biological_sex", "Biological Sex", data_type="text", category="Demographics")


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
        "family_history.list",
        "immunization.list",
        "section.demographics",
        "section.other",
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
            [("substance", "Substance"), ("reaction", "Reaction"), ("notes", "Notes")],
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
                ("is_current", "Current?"),
                ("name", "Name"),
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
            _migrate_surgeries(_load_json_list((value_map.get(surgeries_key, {}) or {}).get("value"))),
            [
                ("name", "Procedure Name"),
                ("date", "Date"),
                ("surgeon", "Surgeon"),
                ("facility", "Facility"),
                ("notes", "Notes")
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

    # Categories that have their own dedicated sidebar tabs — exclude from Health Record
    _EXCLUDED_CATS = {"demographics", "family history", "immunizations", "vaccines"}

    def _cat_sort(n: str):
        return 99 if n.lower() == "other" else 10

    for cat in sorted(grouped.keys(), key=_cat_sort):
        if cat == "Demographics":
            continue
        if cat.lower() in _EXCLUDED_CATS:
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

    _info_btn = make_info_button(page, "Health Record", [
        "The \"Edit Visibility\" button lets you mark sections as sensitive, adding eye icons that can be used to hide or reveal information.",
    ])

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
                        _info_btn,
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