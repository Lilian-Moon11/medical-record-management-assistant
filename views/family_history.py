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

from __future__ import annotations
import flet as ft

from utils.ui_helpers import pt_scale, themed_panel, make_info_button
from views.components.family_helpers import _load, _group_by_relation
from views.components.family_tree import build_legend, build_tree
from views.components.family_risk import build_risk_summary
from views.components.family_dialogs import open_detail_for, open_add_dialog

# ---------------------------------------------------------------------------
# Main view
# ---------------------------------------------------------------------------
def get_family_history_view(page: ft.Page) -> ft.Control:
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    def on_refresh():
        if getattr(page, "content_area", None):
            page.content_area.content = get_family_history_view(page)
            page.content_area.update()

    items       = _load(page, patient_id)
    by_relation = _group_by_relation(items)

    s = pt_scale(page, 1)

    _info_btn = make_info_button(page, "Family History", [
        "Add family members and their diagnosed conditions to build a visual genealogy tree and hereditary risk summary.",
        "There is only support here for 1st and 2nd degree relatives since those are what current science agrees are relevant to hereditary risk, but you can add more if you want to. I don't know what will happen, but more power to you.",
        "Click any node to view details or edit entries for that person.",
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
                        on_click=lambda _: open_add_dialog(page, on_refresh)),
        _info_btn,
    ])

    # ── Tree panel ──
    legend      = build_legend(page)
    
    def on_node_click(relation: str, display_name: str, entries: list[dict]):
        open_detail_for(page, relation, display_name, entries, on_refresh)
        
    tree_widget = build_tree(page, by_relation, on_node_click)
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
        risk_body  = build_risk_summary(page, items)
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
