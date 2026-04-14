# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Family History view — visual genealogy tree + hereditary risk summary.
#
# Design principles:
#   - Gender-neutral relation labels (genealogical position only).
#   - Optional "name" field per entry so distinct people can be identified
#     and targeted in "related to" relationships.
#   - Optional "biological_sex" field for sex-linked condition context only.
#   - Half-siblings are visually distinct (amber border) with a legend.
#   - "Related to" data is stored per entry and shown in detail dialogs.
#
# Data stored as JSON in patient_field_values EAV table, key "family_history.list".
# Each entry:
#   {
#     "relation":        "Parent",          # from RELATION_LIST
#     "name":            "Jane",            # optional name/nickname
#     "condition":       "Type 2 Diabetes",
#     "biological_sex":  "Female",          # Female|Male|Intersex|Unknown (optional)
#     "related_to_type": "Sibling of",      # optional relationship to another member
#     "related_to_name": "Alex",            # name of that member
#     "notes":           "..."
#   }
#
# Multiple entries per person are allowed (one per condition).
# People with the same (relation, name) are grouped as one tree node.
#
# Tree layout (top → bottom):
#   Row 0  — Grandparent nodes  (up to 4)
#   Row 1  — Parent + Parent's Sibling nodes
#   Row 2  — Sibling + Half-Sibling + ★ YOU ★  (half-sibs: amber border)
#   Row 3  — Child nodes  (conditional — only if any child entries exist)
# -----------------------------------------------------------------------------

import flet as ft
import json
from collections import defaultdict

from database import get_patient_field_map, upsert_patient_field_value
from utils.ui_helpers import append_dialog, pt_scale, show_snack, themed_panel, make_info_button


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FIELD_KEY = "family_history.list"

RELATION_LIST = [
    "Parent",
    "Grandparent",
    "Sibling",
    "Half-Sibling",
    "Parent's Sibling",
    "Child",
    "Other",
]

RELATED_TO_TYPES = [
    "Sibling of",
    "Parent of",
    "Child of",
    "Half-Sibling of",
    "Grandparent of",
    "Parent's Sibling of",
]

BIOLOGICAL_SEX_OPTIONS = [
    "Female",
    "Male",
    "Intersex",
    "Unknown",
    "Prefer not to say",
]

_SEX_ICON = {
    "Female":            "♀",
    "Male":              "♂",
    "Intersex":          "⚧",
    "Unknown":           "?",
    "Prefer not to say": "",
}
_SEX_COLOR = {
    "Female":            ft.Colors.PINK_400,
    "Male":              ft.Colors.BLUE_400,
    "Intersex":          ft.Colors.PURPLE_400,
    "Unknown":           ft.Colors.GREY_500,
    "Prefer not to say": ft.Colors.GREY_300,
}

SHARED_PARENT_OPTIONS = ["Maternal side", "Paternal side", "Both"]

FIRST_DEGREE = {"Parent", "Sibling", "Half-Sibling", "Child"}
SECOND_DEGREE = {"Grandparent", "Parent's Sibling"}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _load(page, patient_id: int) -> list[dict]:
    try:
        vm = get_patient_field_map(page.db_connection, patient_id)
        raw = (vm.get(_FIELD_KEY) or {}).get("value")
        items = json.loads(raw or "[]")
        return [x for x in items if isinstance(x, dict)]
    except Exception:
        return []


def _save_items(page, patient_id: int, items: list[dict]):
    try:
        upsert_patient_field_value(
            page.db_connection, patient_id, _FIELD_KEY, json.dumps(items), "user"
        )
    except Exception as ex:
        show_snack(page, f"Save failed: {ex}", "red")


def _group_by_relation(items: list[dict]) -> dict[str, list[tuple[str, list[dict]]]]:
    """
    Returns {relation: [(name, [entries_for_person]), ...]}

    People with the same (relation, name) are grouped as one person.
    Unnamed entries within the same relation are each their own person slot
    (name key = auto-index string so they stay separate).
    """
    # First pass — bucket by (relation, name)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    unnamed_idx: dict[str, int] = defaultdict(int)

    for it in items:
        rel  = (it.get("relation") or "Other").strip()
        name = (it.get("name") or "").strip()
        if not name:
            # Give each unnamed entry its own slot
            idx  = unnamed_idx[rel]
            unnamed_idx[rel] += 1
            key  = (rel, f"__unnamed_{idx}")
        else:
            key = (rel, name)
        buckets[key].append(it)

    # Second pass — group into {relation: [(display_name, entries), ...]}
    by_rel: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for (rel, name_key), entries in buckets.items():
        display_name = "" if name_key.startswith("__unnamed_") else name_key
        by_rel[rel].append((display_name, entries))

    return dict(by_rel)


def _all_named_members(items: list[dict]) -> list[str]:
    """Return sorted list of unique non-empty names across all entries."""
    names = sorted({
        (it.get("name") or "").strip()
        for it in items
        if (it.get("name") or "").strip()
    })
    return names


def _degree_label(relation: str) -> str:
    if relation in FIRST_DEGREE:
        return "1st"
    if relation in SECOND_DEGREE:
        return "2nd"
    return "ext"


def _sex_indicator(entries: list[dict]) -> tuple[str, object]:
    for e in entries:
        bs = (e.get("biological_sex") or "").strip()
        if bs and bs not in ("Unknown", "Prefer not to say", ""):
            return _SEX_ICON.get(bs, "?"), _SEX_COLOR.get(bs)
    return "", None


def _refresh_view(page: ft.Page):
    if getattr(page, "content_area", None):
        page.content_area.content = get_family_history_view(page)
        page.content_area.update()


# ---------------------------------------------------------------------------
# Node card
# ---------------------------------------------------------------------------
def _node_card(
    page: ft.Page,
    relation: str,
    display_name: str,
    entries: list[dict],
    on_click=None,
    is_you: bool = False,
) -> ft.Control:
    s = pt_scale(page, 1)
    is_half_sib = (relation == "Half-Sibling")

    if is_you:
        return ft.Container(
            width=110 * s,
            height=72 * s,
            border_radius=10 * s,
            bgcolor=ft.Colors.TEAL_700,
            border=ft.border.all(3 * s, ft.Colors.TEAL_300),
            alignment=ft.Alignment(x=0, y=0),
            tooltip="You — your own diagnoses live in Health Record",
            content=ft.Column(
                [ft.Text("⭐  YOU", size=13 * s, weight="bold",
                         color=ft.Colors.WHITE, text_align=ft.TextAlign.CENTER)],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    cond_count = len(entries)
    badge_text  = (f"{cond_count} condition{'s' if cond_count != 1 else ''}"
                   if cond_count else "No conditions")
    badge_color = ft.Colors.ORANGE_600 if cond_count > 0 else ft.Colors.GREY_500

    # Name line (bold) + relation subtitle
    top_label = display_name if display_name else relation
    sub_label  = relation if display_name else ""

    # Sex indicator
    glyph, g_color = _sex_indicator(entries)

    # Half-sibling via-parent chip
    shared_chip = None
    if is_half_sib and entries:
        shared = entries[0].get("shared_parent") or entries[0].get("related_to_name") or ""
        if shared:
            shared_chip = ft.Text(f"via {shared}", size=7 * s,
                                  color=ft.Colors.AMBER_300, italic=True,
                                  text_align=ft.TextAlign.CENTER)

    name_row: list[ft.Control] = []
    if glyph:
        name_row.append(ft.Text(glyph, size=10 * s, color=g_color,
                                tooltip=f"Biological sex: {entries[0].get('biological_sex','')}"))
    name_row.append(ft.Text(top_label, size=9 * s, weight="bold",
                             text_align=ft.TextAlign.CENTER, color=ft.Colors.ON_SURFACE,
                             expand=True))

    card_items: list[ft.Control] = [
        ft.Row(name_row, alignment=ft.MainAxisAlignment.CENTER,
               vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
    ]
    if sub_label:
        card_items.append(ft.Text(sub_label, size=7 * s, color=ft.Colors.ON_SURFACE_VARIANT,
                                  text_align=ft.TextAlign.CENTER))
    card_items.append(
        ft.Container(
            content=ft.Text(badge_text, size=7 * s, color=ft.Colors.WHITE,
                            text_align=ft.TextAlign.CENTER),
            bgcolor=badge_color,
            border_radius=6 * s,
            padding=ft.padding.symmetric(horizontal=4 * s, vertical=1 * s),
        )
    )
    if shared_chip:
        card_items.append(shared_chip)

    # Half-sibling: amber 2px border to signal "dashed/shared" relationship
    border_color  = ft.Colors.AMBER_500 if is_half_sib else ft.Colors.OUTLINE_VARIANT
    border_width  = 2 * s           if is_half_sib else 1 * s
    card_bgcolor  = ft.Colors.SURFACE_CONTAINER_HIGHEST if cond_count > 0 else ft.Colors.SURFACE_CONTAINER_HIGH

    return ft.Container(
        width=100 * s,
        height=72 * s,
        border_radius=8 * s,
        bgcolor=card_bgcolor,
        border=ft.border.all(border_width, border_color),
        alignment=ft.Alignment(x=0, y=0),
        ink=on_click is not None,
        on_click=on_click,
        tooltip=f"Click to view {display_name or relation}" if on_click else None,
        content=ft.Column(
            card_items,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=2,
        ),
    )


def _empty_slot(page: ft.Page) -> ft.Control:
    s = pt_scale(page, 1)
    return ft.Container(
        width=100 * s, height=72 * s,
        border_radius=8 * s,
        bgcolor=ft.Colors.SURFACE_CONTAINER,
        border=ft.border.all(1 * s, ft.Colors.OUTLINE_VARIANT),
        opacity=0.2,
    )


# ---------------------------------------------------------------------------
# Tree legend
# ---------------------------------------------------------------------------
def _build_legend(page: ft.Page) -> ft.Control:
    s = pt_scale(page, 1)
    def _swatch(color, border_w, label):
        return ft.Row([
            ft.Container(
                width=24 * s, height=16 * s,
                border_radius=4 * s,
                border=ft.border.all(border_w * s, color),
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            ),
            ft.Text(label, size=11 * s, color=ft.Colors.GREY_500),
        ], spacing=6)

    return ft.Row([
        _swatch(ft.Colors.OUTLINE_VARIANT, 1, "Direct relative"),
        ft.Container(width=16 * s),
        _swatch(ft.Colors.AMBER_500, 2, "Half-sibling (shared parent)"),
        ft.Container(width=16 * s),
        ft.Row([
            ft.Container(
                width=24 * s, height=16 * s,
                border_radius=4 * s,
                bgcolor=ft.Colors.TEAL_700,
                border=ft.border.all(2 * s, ft.Colors.TEAL_300),
            ),
            ft.Text("You", size=11 * s, color=ft.Colors.GREY_500),
        ], spacing=6),
    ], spacing=0)


# ---------------------------------------------------------------------------
# Detail dialog (tap a node — edit conditions, bio sex, save all at once)
# ---------------------------------------------------------------------------
def _ensure_detail_dialog(page: ft.Page):
    if hasattr(page.mrma, "_fh_detail_dlg"):
        return

    page.mrma._fh_detail_relation = ft.Text("", size=18, weight="bold")
    page.mrma._fh_detail_conds_col = ft.Column(tight=True, spacing=4, scroll=ft.ScrollMode.AUTO,
                                           height=160)
    page.mrma._fh_detail_new_cond = ft.TextField(
        label="Add condition (press Enter to add)",
        expand=True,
    )
    page.mrma._fh_detail_bio_sex = ft.Dropdown(
        label="Biological Sex (optional)",
        options=[ft.dropdown.Option(o) for o in BIOLOGICAL_SEX_OPTIONS],
        width=260,
    )
    page.mrma._fh_detail_shared_dd = ft.Dropdown(
        label="Shared Parent Side",
        options=[ft.dropdown.Option(o) for o in SHARED_PARENT_OPTIONS],
        visible=False, width=220,
    )
    page.mrma._fh_detail_notes_tf = ft.TextField(label="Notes (optional)", expand=True)

    # Internal mutable state for this session
    _cond_list: list[str] = []   # conditions being edited

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
                    on_click=lambda e, i=idx: _remove_cond(i),
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
            rel      = page.mrma._fh_detail_dlg._relation
            name     = page.mrma._fh_detail_dlg._name
            rt_type  = page.mrma._fh_detail_dlg._rt_type
            rt_name  = page.mrma._fh_detail_dlg._rt_name
            pat = page.current_profile
            if not pat:
                show_snack(page, "No patient profile.", "red")
                return
            patient_id = pat[0]
            items = _load(page, patient_id)
            # Remove ALL existing entries for this person
            items = [it for it in items
                     if not ((it.get("relation") or "") == rel
                             and (it.get("name") or "").strip() == name)]
            # Write back one entry per condition with shared person-level fields
            bio_sex = page.mrma._fh_detail_bio_sex.value or "Unknown"
            notes   = (page.mrma._fh_detail_notes_tf.value or "").strip()
            shared  = page.mrma._fh_detail_shared_dd.value or ""
            for cond in _cond_list:
                entry: dict = {
                    "relation":       rel,
                    "name":           name,
                    "condition":      cond,
                    "biological_sex": bio_sex,
                    "notes":          notes,
                }
                if rel == "Half-Sibling" and shared:
                    entry["shared_parent"] = shared
                if rt_type and rt_name:
                    entry["related_to_type"] = rt_type
                    entry["related_to_name"] = rt_name
                items.append(entry)
            _save_items(page, patient_id, items)
            _close()
            _refresh_view(page)
            show_snack(page, "Changes saved.", "green")
        except Exception as ex:
            import traceback; traceback.print_exc()
            show_snack(page, f"Save error: {ex}", "red")

    # Store rebuild fn so _open_detail_for can call it
    page.mrma._fh_detail_rebuild = _rebuild_list
    page.mrma._fh_detail_cond_list = _cond_list

    page.mrma._fh_detail_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Row([ft.Icon(ft.Icons.PERSON, color=ft.Colors.TEAL_400),
                      page.mrma._fh_detail_relation], spacing=8),
        content=ft.Container(
            width=500,
            content=ft.Column(
                [
                    ft.Text("Conditions:", size=12, italic=True,
                            color=ft.Colors.GREY_500),
                    page.mrma._fh_detail_conds_col,
                    page.mrma._fh_detail_new_cond,
                    ft.Divider(),
                    page.mrma._fh_detail_bio_sex,
                    ft.Text(
                        "Biological sex is per person, not per condition."
                        " Used only to flag sex-linked conditions (e.g. BRCA, hemophilia).",
                        size=11, italic=True, color=ft.Colors.GREY_500,
                    ),
                    page.mrma._fh_detail_shared_dd,
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
    page.mrma._fh_detail_dlg._relation = ""
    page.mrma._fh_detail_dlg._name     = ""
    page.mrma._fh_detail_dlg._rt_type  = ""
    page.mrma._fh_detail_dlg._rt_name  = ""
    append_dialog(page, page.mrma._fh_detail_dlg)


def _open_detail_for(page: ft.Page, relation: str, display_name: str, entries: list[dict]):
    _ensure_detail_dialog(page)
    dlg = page.mrma._fh_detail_dlg
    dlg._relation = relation
    dlg._name     = display_name
    dlg._rt_type  = entries[0].get("related_to_type", "") if entries else ""
    dlg._rt_name  = entries[0].get("related_to_name", "") if entries else ""

    title_text = display_name if display_name else relation
    page.mrma._fh_detail_relation.value = f"{title_text} ({relation})" if display_name else relation

    # Load per-person bio sex from first entry that has a value
    bio_sex_val = None
    for e in entries:
        bs = (e.get("biological_sex") or "").strip()
        if bs and bs != "Unknown":
            bio_sex_val = bs
            break
    page.mrma._fh_detail_bio_sex.value = bio_sex_val

    # Load per-person notes from first entry
    page.mrma._fh_detail_notes_tf.value = entries[0].get("notes", "") if entries else ""

    # Half-sibling shared parent
    page.mrma._fh_detail_shared_dd.visible = (relation == "Half-Sibling")
    page.mrma._fh_detail_shared_dd.value   = (
        entries[0].get("shared_parent", "") if entries else ""
    )

    # Reset new condition field
    page.mrma._fh_detail_new_cond.value = ""

    # Populate editable condition list
    cond_list = page.mrma._fh_detail_cond_list
    cond_list.clear()
    for e in entries:
        cond = (e.get("condition") or "").strip()
        if cond:
            cond_list.append(cond)

    # Show connection annotation if present
    rt_type = dlg._rt_type
    rt_name = dlg._rt_name

    page.mrma._fh_detail_rebuild()

    dlg.open = True
    page.update()


# ---------------------------------------------------------------------------
# Add-family-member dialog
# ---------------------------------------------------------------------------
def _ensure_add_dialog(page: ft.Page):
    if hasattr(page.mrma, "_fh_add_dlg"):
        return

    _name_tf = ft.TextField(label="Name / Nickname (optional)", expand=True,
                             hint_text="e.g. Papa, Nana, Auncle Moka")
    _rel_dd  = ft.Dropdown(
        label="Relation *",
        options=[ft.dropdown.Option(r) for r in RELATION_LIST],
        autofocus=True, expand=True,
    )
    _shared_dd = ft.Dropdown(
        label="Shared Parent Side",
        options=[ft.dropdown.Option(o) for o in SHARED_PARENT_OPTIONS],
        visible=False, width=260,
    )
    _cond_tf = ft.TextField(label="Condition / Diagnosis *", expand=True)

    _bio_sex_dd = ft.Dropdown(
        label="Biological Sex (optional)",
        options=[ft.dropdown.Option(o) for o in BIOLOGICAL_SEX_OPTIONS],
        expand=True,
    )
    _bio_sex_hint = ft.Text(
        "For sex-linked condition context (e.g. BRCA, hemophilia).",
        size=11, italic=True, color=ft.Colors.GREY_500,
    )

    # "Connect to" section — phrased as a natural sentence
    _rt_type_dd = ft.Dropdown(
        label="is the...",
        hint_text="Parent / Child / Sibling...",
        options=[ft.dropdown.Option(t) for t in RELATED_TO_TYPES],
        expand=True,
    )
    _rt_name_dd = ft.Dropdown(
        label="...of (select existing member)",
        options=[],
        expand=True,
    )
    _rt_no_members = ft.Text(
        "Add more family members first to connect them to each other.",
        size=11, italic=True, color=ft.Colors.GREY_400,
    )
    _rt_row = ft.Row([_rt_type_dd, _rt_name_dd], spacing=8, expand=True, visible=False)
    _rt_section = ft.Column([
        ft.Text("Connect to an existing family member (optional):",
                size=12, weight="bold"),
        ft.Text(
            "e.g. Add \"Grandparent\" → connect as \"Parent of\" → Papa (Parent)."
            " No need to say maternal or paternal.",
            size=11, italic=True, color=ft.Colors.GREY_500,
        ),
        _rt_no_members,
        _rt_row,
    ], spacing=6)

    _notes_tf = ft.TextField(label="Notes", multiline=True, min_lines=2, expand=True)

    def _on_rel_change(e):
        _shared_dd.visible = (_rel_dd.value == "Half-Sibling")
        _shared_dd.update()

    _rel_dd.on_change = _on_rel_change

    _closing = [False]   # re-entrancy guard for _close

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
                "relation":       rel,
                "name":           (_name_tf.value or "").strip(),
                "condition":      cond,
                "biological_sex": _bio_sex_dd.value or "Unknown",
                "notes":          (_notes_tf.value or "").strip(),
            }
            if rel == "Half-Sibling" and _shared_dd.value:
                entry["shared_parent"] = _shared_dd.value
            if _rt_type_dd.value and _rt_name_dd.value:
                entry["related_to_type"] = _rt_type_dd.value
                entry["related_to_name"] = _rt_name_dd.value
            items = _load(page, patient_id)
            items.append(entry)
            _save_items(page, patient_id, items)
            _close()
            _refresh_view(page)
            show_snack(page, "Family member added.", "green")
        except Exception as ex:
            import traceback; traceback.print_exc()
            show_snack(page, f"Save error: {ex}", "red")

    page.mrma._fh_add_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Add Family Member"),
        content=ft.Container(
            width=520,
            content=ft.Column(
                [
                    _name_tf,
                    _rel_dd,
                    _shared_dd,
                    _cond_tf,
                    _bio_sex_dd,
                    _bio_sex_hint,
                    ft.Divider(),
                    _rt_section,
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
    page.mrma._fh_add_dlg._widgets = (
        _name_tf, _rel_dd, _shared_dd, _cond_tf,
        _bio_sex_dd, _rt_type_dd, _rt_name_dd, _rt_section, _rt_row, _rt_no_members, _notes_tf,
    )


def _open_add_dialog(page: ft.Page):
    _ensure_add_dialog(page)
    (name_tf, rel_dd, shared_dd, cond_tf,
     bio_sex_dd, rt_type_dd, rt_name_dd, rt_section,
     rt_row, rt_no_members, notes_tf) = page.mrma._fh_add_dlg._widgets

    # Reset fields
    name_tf.value     = ""
    rel_dd.value      = None
    shared_dd.value   = None
    shared_dd.visible = False
    cond_tf.value     = ""
    bio_sex_dd.value  = None
    rt_type_dd.value  = None
    rt_name_dd.value  = None
    notes_tf.value    = ""

    # Populate "connect to" options — all existing people (named + unnamed by relation)
    pat = page.current_profile
    if pat:
        existing = _load(page, pat[0])
        seen: set[str] = set()
        options: list[str] = []
        for it in existing:
            name = (it.get("name") or "").strip()
            rel  = (it.get("relation") or "").strip()
            label = f"{name} ({rel})" if name else rel
            if label and label not in seen:
                seen.add(label)
                options.append(label)
        rt_name_dd.options = [ft.dropdown.Option(o) for o in sorted(options)]
        has_options = bool(options)
    else:
        rt_name_dd.options = []
        has_options = False

    # Always show the connect section; toggle between hint and dropdowns
    rt_row.visible        = has_options
    rt_no_members.visible = not has_options

    page.mrma._fh_add_dlg.open = True
    page.update()


# ---------------------------------------------------------------------------
# Tree connector helpers
# ---------------------------------------------------------------------------
def _conn_gap_solid(s, color=None) -> ft.Control:
    """Gap-width container with a solid horizontal line through its center."""
    c = color or ft.Colors.ON_SURFACE_VARIANT
    return ft.Container(
        width=14 * s, height=72 * s,
        content=ft.Container(width=14 * s, height=2 * s, bgcolor=c),
        alignment=ft.Alignment(x=0, y=0),
    )


def _conn_gap_dotted(s, color=None) -> ft.Control:
    """Gap-width container with a dotted horizontal line through its center."""
    c = color or ft.Colors.AMBER_400
    dot, sp = 3 * s, 2 * s
    dots: list[ft.Control] = []
    for i in range(4):
        if i:
            dots.append(ft.Container(width=sp, height=2 * s))
        dots.append(ft.Container(width=dot, height=2 * s, bgcolor=c))
    return ft.Container(
        width=14 * s, height=72 * s,
        content=ft.Row(dots, spacing=0),
        alignment=ft.Alignment(x=0, y=0),
    )


def _vstem_row(s, has_parents: bool) -> ft.Control:
    """Plain gap row between parent and sibling rows."""
    return ft.Container(height=18 * s)


# ---------------------------------------------------------------------------
# Tree builder
# ---------------------------------------------------------------------------
def _build_tree(
    page: ft.Page,
    by_relation: dict[str, list[tuple[str, list[dict]]]]
) -> ft.Control:
    s   = pt_scale(page, 1)

    try:
        import flet.canvas as cv
        _cv_ok = True
    except Exception:
        _cv_ok = False

    CARD_W   = 100 * s
    CARD_H   = 72  * s
    GAP      = 14  * s
    ROW_GAP  = 18  * s
    CANVAS_W = int(800 * s)   # fixed width; rows center within this

    def mk_gap():
        return ft.Container(width=GAP)

    def make_node(relation, display_name, entries):
        return _node_card(
            page, relation, display_name, entries,
            on_click=lambda e, r=relation, n=display_name, ent=entries:
                _open_detail_for(page, r, n, ent),
        )

    def nodes_for(relation: str, max_nodes: int = 3) -> list[ft.Control]:
        people = by_relation.get(relation, [])
        if not people:
            return [_empty_slot(page)]
        return [make_node(relation, name, entries)
                for name, entries in people[:max_nodes]]

    def spaced_row(controls: list[ft.Control]) -> ft.Row:
        items: list[ft.Control] = []
        for i, c in enumerate(controls):
            if i:
                items.append(mk_gap())
            items.append(c)
        return ft.Row(items, alignment=ft.MainAxisAlignment.CENTER,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0)

    # ── Row 0: Grandparents ──
    gp_nodes = nodes_for("Grandparent", 4)
    gp_row   = spaced_row(gp_nodes)

    # ── Row 1: Parents + Parent's Siblings ──
    has_ps  = "Parent's Sibling" in by_relation
    has_par = "Parent"           in by_relation

    ps_nodes  = nodes_for("Parent's Sibling", 2)
    par_nodes = nodes_for("Parent", 2)

    p_items: list[ft.Control] = []
    for n in ps_nodes[:1]:
        p_items.append(n)
        p_items.append(_conn_gap_solid(s) if (has_ps and has_par) else mk_gap())
    p_items += par_nodes
    for n in ps_nodes[1:2]:
        p_items.append(_conn_gap_solid(s) if (has_ps and has_par) else mk_gap())
        p_items.append(n)
    parents_row = spaced_row(p_items)

    # ── Row 2: Siblings + Half-Siblings + YOU ──
    has_hs = "Half-Sibling" in by_relation

    sib_nodes      = nodes_for("Sibling", 3)
    half_sib_nodes = nodes_for("Half-Sibling", 2)
    you_node       = _node_card(page, "YOU", "", [], is_you=True)

    sib_items: list[ft.Control] = []
    for n in sib_nodes:
        sib_items += [n, mk_gap()]
    for i, n in enumerate(half_sib_nodes):
        sib_items.append(n)
        last = (i == len(half_sib_nodes) - 1)
        sib_items.append(_conn_gap_dotted(s) if (has_hs and last) else mk_gap())
    sib_items.append(you_node)
    siblings_row = spaced_row(sib_items)

    # ── Position math (all rows are centered in CANVAS_W) ──
    # Row 1 slots: [PS_left(1), par_nodes(n), PS_right(0-1)]
    n_par      = len(par_nodes)
    n_ps_right = 1 if len(ps_nodes) > 1 else 0
    n_row1     = 1 + n_par + n_ps_right
    row1_w     = n_row1 * CARD_W + (n_row1 - 1) * GAP
    row1_left  = (CANVAS_W - row1_w) / 2
    par_xs     = [row1_left + (1 + i) * (CARD_W + GAP) + CARD_W / 2
                  for i in range(n_par)]
    par_cx     = sum(par_xs) / len(par_xs)    # H-center of parent card(s)

    # Row 2 slots: [sib_nodes(n), half_sib_nodes(n), YOU(1)]
    n_sib   = len(sib_nodes)
    n_hs    = len(half_sib_nodes)
    n_row2  = n_sib + n_hs + 1
    row2_w  = n_row2 * CARD_W + (n_row2 - 1) * GAP
    row2_left = (CANVAS_W - row2_w) / 2
    you_x   = row2_left + (n_row2 - 1) * (CARD_W + GAP) + CARD_W / 2

    # Y positions (spacing=0; gaps are explicit Container heights in the column)
    y0b = CARD_H                     # gp row bottom
    y1t = CARD_H + ROW_GAP          # parent row top
    y1b = 2 * CARD_H + ROW_GAP      # parent row bottom
    y2t = 2 * CARD_H + 2 * ROW_GAP  # sibling row top

    has_ch = "Child" in by_relation
    y2b = 2 * CARD_H + 3 * ROW_GAP
    tree_h = int((3 * CARD_H + 3 * ROW_GAP) if has_ch else (y2t + CARD_H))

    LW       = max(1, int(2 * s))
    LINE_CLR = ft.Colors.with_opacity(0.55, ft.Colors.ON_SURFACE_VARIANT)

    def _lconn(cx_from: float, cx_to: float) -> ft.Control:
        """ROW_GAP-tall zone with an L-shaped line from cx_from (above) to cx_to (below)."""
        h   = int(ROW_GAP)
        mid = h // 2
        lf  = int(cx_from) - LW // 2
        lt  = int(cx_to)   - LW // 2
        lx  = min(lf, lt)
        hw  = abs(int(cx_from) - int(cx_to)) + LW
        return ft.Stack([
            ft.Container(width=CANVAS_W, height=h),
            ft.Container(left=lf, top=0,             width=LW, height=mid,     bgcolor=LINE_CLR),
            ft.Container(left=lx, top=mid - LW // 2, width=hw, height=LW,      bgcolor=LINE_CLR),
            ft.Container(left=lt, top=mid,            width=LW, height=h - mid, bgcolor=LINE_CLR),
        ])

    def _gap() -> ft.Control:
        return ft.Container(height=int(ROW_GAP))

    # ── Assemble rows ──
    rows: list[ft.Control] = [gp_row]
    rows.append(_lconn(CANVAS_W / 2, par_cx) if (has_par and "Grandparent" in by_relation) else _gap())
    rows.append(parents_row)
    rows.append(_lconn(par_cx, you_x) if has_par else _gap())
    rows.append(siblings_row)

    if has_ch:
        rows += [_gap(), spaced_row(nodes_for("Child", 4))]

    other_nodes = by_relation.get("Other", [])
    if other_nodes:
        other_cards = [make_node("Other", n, e) for n, e in other_nodes[:4]]
        rows.append(ft.Container(height=int(8 * s)))
        rows.append(ft.Row(
            [ft.Text("Other relatives:", size=11 * s,
                     color=ft.Colors.GREY_500, italic=True),
             *[ft.Container(content=c, margin=ft.margin.only(left=4 * s))
               for c in other_cards]],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
        ))

    return ft.Container(
        width=CANVAS_W,
        content=ft.Column(rows, spacing=0,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER),
    )





# ---------------------------------------------------------------------------
# Risk summary
# ---------------------------------------------------------------------------
def _build_risk_summary(page: ft.Page, items: list[dict]) -> ft.Control:
    s = pt_scale(page, 1)

    first: list[dict]    = []
    second: list[dict]   = []
    extended: list[dict] = []

    for it in items:
        rel  = (it.get("relation") or "").strip()
        cond = (it.get("condition") or "").strip()
        if not cond:
            continue
        entry = {**it, "relation": rel, "condition": cond}
        deg   = _degree_label(rel)
        if deg == "1st":
            first.append(entry)
        elif deg == "2nd":
            second.append(entry)
        else:
            extended.append(entry)

    def _cond_row(e: dict) -> ft.Control:
        rel   = e.get("relation", "")
        name  = (e.get("name") or "").strip()
        cond  = e.get("condition", "")
        bs    = (e.get("biological_sex") or "").strip()
        glyph = _SEX_ICON.get(bs, "") if bs not in ("Unknown", "Prefer not to say", "") else ""
        g_col = _SEX_COLOR.get(bs, ft.Colors.GREY_400)
        who   = name if name else rel

        row_c: list[ft.Control] = [
            ft.Icon(ft.Icons.WARNING_AMBER, size=13 * s, color=ft.Colors.ORANGE_400),
            ft.Text(cond, size=12 * s, expand=True),
        ]
        if glyph:
            row_c.append(ft.Text(glyph, size=12 * s, color=g_col,
                                  tooltip=f"Biological sex: {bs}"))
        row_c.append(ft.Text(f"({who})", size=11 * s,
                              color=ft.Colors.GREY_500, italic=True))
        return ft.Row(row_c, spacing=4)

    def _make_col(title: str, subtitle: str, entries: list[dict]) -> ft.Control:
        rows: list[ft.Control] = [
            ft.Text(title, size=14 * s, weight="bold"),
            ft.Text(subtitle, size=11 * s, italic=True, color=ft.Colors.GREY_500),
            ft.Divider(height=8 * s),
        ]
        if entries:
            seen: set[tuple] = set()
            for e in entries:
                key = (e["relation"], e.get("name", ""), e["condition"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(_cond_row(e))
        else:
            rows.append(ft.Text("None recorded", italic=True,
                                color=ft.Colors.GREY_400, size=12 * s))
        return ft.Column(rows, spacing=4, expand=True)

    disclaimer = ft.Container(
        bgcolor=ft.Colors.BLUE_50,
        border_radius=8 * s,
        border=ft.border.all(1 * s, ft.Colors.BLUE_200),
        padding=ft.padding.symmetric(horizontal=12 * s, vertical=8 * s),
        content=ft.Row([
            ft.Icon(ft.Icons.INFO_OUTLINE, color=ft.Colors.BLUE_600, size=16 * s),
            ft.Column([
                ft.Text("These conditions appear in your family history. "
                        "They are NOT your personal diagnoses.",
                        size=12 * s, color=ft.Colors.BLUE_700, italic=True),
                ft.Text("♀ ♂ ⚧  indicators show biological sex where relevant "
                        "to sex-linked conditions.",
                        size=11 * s, color=ft.Colors.BLUE_500),
            ], spacing=2, expand=True),
        ], spacing=8),
    )

    degree_row = ft.Row(
        [
            _make_col("1st Degree Relatives",
                      "Parents, siblings, children, half-siblings", first),
            ft.VerticalDivider(width=1),
            _make_col("2nd Degree Relatives",
                      "Grandparents, parents' siblings", second),
        ],
        vertical_alignment=ft.CrossAxisAlignment.START,
        expand=True,
        spacing=16 * s,
    )

    parts: list[ft.Control] = [disclaimer, ft.Container(height=8 * s), degree_row]

    if extended:
        parts.append(ft.Divider())
        parts.append(ft.Text("Extended / Other Relatives", size=13 * s, weight="bold"))
        seen_ext: set[tuple] = set()
        for e in extended:
            key = (e["relation"], e.get("name", ""), e["condition"])
            if key in seen_ext:
                continue
            seen_ext.add(key)
            parts.append(_cond_row(e))

    return ft.Column(parts, spacing=8 * s)


# ---------------------------------------------------------------------------
# Main view
# ---------------------------------------------------------------------------
def get_family_history_view(page: ft.Page) -> ft.Control:
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    items       = _load(page, patient_id)
    by_relation = _group_by_relation(items)

    s = pt_scale(page, 1)

    _ensure_detail_dialog(page)
    _ensure_add_dialog(page)

    _info_btn = make_info_button(page, "Family History", [
        "There is only support here for 1st and 2nd degree relatives since those are what current science agrees are relevant to hereditary risk, but you can add more if you want to. I don't know what will happen, but more power to you.",
        "Your own diagnoses live in the Health Record tab, not here.",
    ])

    # ── Header ──
    header = ft.Row([
        ft.Row([
            ft.Icon(ft.Icons.ACCOUNT_TREE, color=ft.Colors.TEAL_600),
            ft.Text("Family History", size=24 * s, weight="bold"),
        ], spacing=10),
        ft.Container(expand=True),
        ft.FilledButton("Add Family Member", icon=ft.Icons.PERSON_ADD,
                        on_click=lambda _: _open_add_dialog(page)),
        _info_btn,
    ])

    # ── Tree panel ──
    legend      = _build_legend(page)
    tree_widget = _build_tree(page, by_relation)
    tree_panel  = themed_panel(
        page,
        ft.Column([
            ft.Row([
                ft.Text("Genealogy Tree", size=16 * s, weight="bold",
                        color=ft.Colors.GREY_600),
                ft.Container(expand=True),
                legend,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=12 * s),
            ft.Row([tree_widget], scroll=ft.ScrollMode.AUTO),
        ], spacing=0),
        padding=pt_scale(page, 16),
    )

    # ── Empty state ──
    if not items:
        empty_hint = ft.Container(
            padding=ft.padding.all(20 * s),
            content=ft.Column([
                ft.Icon(ft.Icons.ACCOUNT_TREE, size=56, color=ft.Colors.GREY_400),
                ft.Text("No family history recorded.", size=16, color=ft.Colors.GREY_500),
                ft.Text(
                    "Tap \"Add Family Member\" to record a relative's diagnosis,\n"
                    "or upload a document for AI extraction.",
                    size=13, color=ft.Colors.GREY_400, italic=True,
                    text_align=ft.TextAlign.CENTER,
                ),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment(x=0, y=0),
        )
        body = ft.Column(
            [tree_panel, ft.Container(height=12 * s), empty_hint],
            expand=True, scroll=ft.ScrollMode.AUTO,
        )
    else:
        risk_body  = _build_risk_summary(page, items)
        risk_panel = themed_panel(
            page,
            ft.Column([
                ft.Text("Hereditary Risk Factors in Your Family",
                        size=16 * s, weight="bold"),
                ft.Container(height=8 * s),
                risk_body,
            ], spacing=0),
            padding=pt_scale(page, 16),
        )
        body = ft.Column(
            [tree_panel, ft.Container(height=12 * s), risk_panel],
            expand=True, scroll=ft.ScrollMode.AUTO,
        )

    return ft.Container(
        padding=pt_scale(page, 20),
        expand=True,
        content=ft.Column([header, ft.Divider(), body], expand=True),
    )
