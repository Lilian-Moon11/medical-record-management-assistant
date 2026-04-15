import flet as ft
from utils.ui_helpers import append_dialog, show_snack
from views.components.family_helpers import (
    _load,
    _save_items,
    RELATION_LIST,
    RELATED_TO_TYPES,
    BIOLOGICAL_SEX_OPTIONS,
    SHARED_PARENT_OPTIONS,
)


# ---------------------------------------------------------------------------
# Detail dialog (tap a node — edit conditions, bio sex, save all at once)
# ---------------------------------------------------------------------------
def _ensure_detail_dialog(page: ft.Page, on_refresh):
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
            rel      = page.mrma._fh_detail_state["relation"]
            name     = page.mrma._fh_detail_state["name"]
            rt_type  = page.mrma._fh_detail_state["rt_type"]
            rt_name  = page.mrma._fh_detail_state["rt_name"]
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
            on_refresh()
            show_snack(page, "Changes saved.", "green")
        except Exception as ex:
            import logging; logging.error("Family history detail save error", exc_info=True)
            show_snack(page, f"Save error: {ex}", "red")

    # Store rebuild fn so open_detail_for can call it
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
    page.mrma._fh_detail_state = {
        "relation": "", "name": "", "rt_type": "", "rt_name": "",
    }
    append_dialog(page, page.mrma._fh_detail_dlg)


def open_detail_for(page: ft.Page, relation: str, display_name: str, entries: list[dict], on_refresh):
    _ensure_detail_dialog(page, on_refresh)
    dlg = page.mrma._fh_detail_dlg
    page.mrma._fh_detail_state["relation"] = relation
    page.mrma._fh_detail_state["name"]     = display_name
    page.mrma._fh_detail_state["rt_type"]  = entries[0].get("related_to_type", "") if entries else ""
    page.mrma._fh_detail_state["rt_name"]  = entries[0].get("related_to_name", "") if entries else ""

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

    page.mrma._fh_detail_rebuild()

    dlg.open = True
    page.update()


# ---------------------------------------------------------------------------
# Add-family-member dialog
# ---------------------------------------------------------------------------
def _ensure_add_dialog(page: ft.Page, on_refresh):
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
            on_refresh()
            show_snack(page, "Family member added.", "green")
        except Exception as ex:
            import logging; logging.error("Family history add save error", exc_info=True)
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
    page.mrma._fh_add_widgets = (
        _name_tf, _rel_dd, _shared_dd, _cond_tf,
        _bio_sex_dd, _rt_type_dd, _rt_name_dd, _rt_section, _rt_row, _rt_no_members, _notes_tf,
    )


def open_add_dialog(page: ft.Page, on_refresh):
    _ensure_add_dialog(page, on_refresh)
    (name_tf, rel_dd, shared_dd, cond_tf,
     bio_sex_dd, rt_type_dd, rt_name_dd, rt_section,
     rt_row, rt_no_members, notes_tf) = page.mrma._fh_add_widgets

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
