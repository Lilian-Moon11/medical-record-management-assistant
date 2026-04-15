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
# - Local per-page UI state (`page.mrma._field_vis`, `page.mrma._panel_vis`) to keep row-
#   level and panel-level reveal states stable across refresh/rerender, plus an
#   optional provenance display (`page.mrma._show_provenance`).
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
    OUTLINE_VARIANT,
    append_dialog,
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


from views.components.helpers import (
    _ensure_sets,
    _load_json_list,
    _migrate_surgeries
)
from views.components.list_editor_body import ListEditorBody
from views.components.category_panel import CategoryPanel


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

    page.mrma._health_record_refresh = refresh

    search_field = ft.TextField(
        label="Search Health Record",
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        width=pt_scale(page, 340),
        value=getattr(page.mrma, "_hr_search_text", "")
    )

    def do_search(_=None):
        page.mrma._hr_search_text = search_field.value
        refresh()

    search_field.on_change = do_search
    
    st = getattr(page.mrma, "_hr_search_text", "").lower()

    def _filter_json(items):
        if not st: return items
        return [i for i in items if any(st in str(v).lower() for v in i.values())]

    def _filter_grouped(d):
        if not st: return True
        return st in str(d[1]).lower() or st in str(value_map.get(d[0], {}).get("value", "")).lower()

    search_field = ft.TextField(
        label="Search Health Record",
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        width=pt_scale(page, 340),
        value=getattr(page.mrma, "_hr_search_text", "")
    )

    def do_search(_=None):
        page.mrma._hr_search_text = search_field.value
        refresh()

    search_field.on_submit = do_search
    
    st = getattr(page.mrma, "_hr_search_text", "").lower()

    def _filter_json(items):
        if not st: return items
        return [i for i in items if any(st in str(v).lower() for v in i.values())]

    def _filter_grouped(d):
        if not st: return True
        return st in str(d[1]).lower() or st in str(value_map.get(d[0], {}).get("value", "")).lower()

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
        page.mrma._delete_inline_row = row_ref
        page.mrma._delete_inline_table = table_ref
        page.open_delete_field_dialog(fk, clean_lbl(lbl))

    page.mrma._open_delete_dialog = open_delete_dialog

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

    for cat in grouped:
        grouped[cat] = [d for d in grouped[cat] if _filter_grouped(d)]

    sections: List[Any] = []

    if grouped["Demographics"]:
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

    all_list = _filter_json(_load_json_list((value_map.get(allergies_key, {}) or {}).get("value")))
    if all_list or not st:
        allergies_panel = themed_panel(
            page,
            ListEditorBody(
                page,
                patient_id,
                "Allergies / Intolerances",
                allergies_key,
                all_list,
                [("substance", "Substance"), ("reaction", "Reaction"), ("notes", "Notes")],
                is_section_sensitive=is_sens(allergies_key),
                on_save=lambda items: upsert_patient_field_value(page.db_connection, patient_id, allergies_key, json.dumps(items), "user"),
                source=_list_meta(allergies_key)[0],
                updated_at=_list_meta(allergies_key)[1],
            ),
            padding=pt_scale(page, 12),
        )
        sections += [allergies_panel, ft.Container(height=pt_scale(page, 10))]

    med_list = _filter_json(_load_json_list((value_map.get(meds_key, {}) or {}).get("value")))
    if med_list or not st:
        meds_panel = themed_panel(
            page,
            ListEditorBody(
                page,
                patient_id,
                "Medications / Supplements", # Updated Title
                meds_key,
                med_list,
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

    cond_list = _filter_json(_load_json_list((value_map.get(conditions_key, {}) or {}).get("value")))
    if cond_list or not st:
        conditions_panel = themed_panel(
            page,
            ListEditorBody(
                page,
                patient_id,
                "Conditions",
                conditions_key,
                cond_list,
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

    surg_list = _filter_json(_migrate_surgeries(_load_json_list((value_map.get(surgeries_key, {}) or {}).get("value"))))
    if surg_list or not st:
        surgeries_panel = themed_panel(
            page,
            ListEditorBody(
                page,
                patient_id,
                "Surgeries / Procedures",
                surgeries_key,
                surg_list,
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

    ins_list = _filter_json(_load_json_list((value_map.get(insurance_key, {}) or {}).get("value")))
    if ins_list or not st:
        insurance_panel = themed_panel(
            page,
            ListEditorBody(
                page,
                patient_id,
                "Insurance",
                insurance_key,
                ins_list,
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
    _EXCLUDED_CATS = {"demographics", "family history", "immunizations", "immunizations"}

    def _cat_sort(n: str):
        return 99 if n.lower() == "other" else 10

    for cat in sorted(grouped.keys(), key=_cat_sort):
        if cat == "Demographics":
            continue
        if cat.lower() in _EXCLUDED_CATS:
            continue
        if not grouped[cat] and st:
            continue
        if not grouped[cat] and st:
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

    if not sections and st:
        sections.append(ft.Container(ft.Text("No matching records found.", color=ft.Colors.GREY, italic=True), padding=20))

    return ft.Container(
        padding=pt_scale(page, 20),
        content=ft.ListView(
            controls=[
                ft.Row(
                    [
                        ft.Text("Health Record", size=pt_scale(page, 22), weight="bold"),
                        ft.Container(expand=True),
                        search_field,
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