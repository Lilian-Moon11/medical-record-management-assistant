# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# Social & Family History view - degree-grouped family history summary
# and structured social history questionnaire.
# -----------------------------------------------------------------------------

from __future__ import annotations
import flet as ft

from utils.ui_helpers import (
    pt_scale, themed_panel, make_info_button, append_dialog, show_snack,
)
from views.components.family_helpers import _load, _save_items
from views.components.family_risk import build_risk_summary
from views.components.social_history import build_social_history
from views.components.family_dialogs import open_detail_for, open_add_dialog


def get_family_history_view(page: ft.Page) -> ft.Control:
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    def on_refresh():
        if getattr(page, "content_area", None):
            page.content_area.content = get_family_history_view(page)
            page.content_area.update()

    items = _load(page, patient_id)
    s = pt_scale(page, 1)

    _info_btn = make_info_button(page, "Social & Family History", [
        "Add family members and their diagnosed conditions to build your family health summary.",
        "Conditions are grouped by degree (1st, 2nd, extended) - the same format used on medical intake forms.",
        "Click the pencil icon next to a condition to edit that family member's details.",
        "Social History tracks lifestyle factors like alcohol, tobacco, exercise, and diet.",
        "Your own diagnoses live in the Health Record tab, not here.",
    ])

    header = ft.Row([
        ft.Row([
            ft.Icon(ft.Icons.GROUPS, color=ft.Colors.TEAL_600),
            ft.Text("Social & Family History", size=24 * s, weight="bold"),
        ], spacing=10),
        ft.Container(expand=True),
        ft.FilledButton("Add Family Member", icon=ft.Icons.PERSON_ADD,
                        on_click=lambda _: open_add_dialog(page, on_refresh)),
        _info_btn,
    ])

    # -- Family history content --
    def on_node_click(relation: str, display_name: str, entries: list[dict]):
        open_detail_for(page, relation, display_name, entries, on_refresh)

    if items:
        fh_content = build_risk_summary(page, items, on_node_click=on_node_click)
    else:
        fh_content = ft.Container(
            padding=ft.padding.all(20 * s),
            content=ft.Column([
                ft.Icon(ft.Icons.GROUPS, size=56, color=ft.Colors.GREY_400),
                ft.Text("No family history recorded.", size=16,
                        color=ft.Colors.GREY_500),
                ft.Text(
                    "Tap \"Add Family Member\" to record a relative's diagnosis.",
                    size=13, color=ft.Colors.GREY_400, italic=True,
                    text_align=ft.TextAlign.CENTER,
                ),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment(x=0, y=0),
        )

    # -- Consolidated notes dialog --
    # Groups per-member notes into one view; edits write back to entries.
    def _open_notes(_):
        # Group entries by person
        people: dict[tuple[str, str], str] = {}  # (rel, name) -> notes
        for it in items:
            rel  = (it.get("relation") or "").strip()
            name = (it.get("name") or "").strip()
            key  = (rel, name)
            if key not in people:
                people[key] = (it.get("notes") or "").strip()

        # Build a TextField per person
        _note_fields: dict[tuple[str, str], ft.TextField] = {}
        rows: list[ft.Control] = []

        if not people:
            rows.append(ft.Text("No family members recorded yet.",
                                italic=True, color=ft.Colors.GREY_500))
        else:
            for (rel, name), notes in people.items():
                label = f"{name} ({rel})" if name else rel
                tf = ft.TextField(
                    label=label,
                    value=notes,
                    multiline=True, min_lines=1, max_lines=4,
                    expand=True, dense=True,
                )
                _note_fields[(rel, name)] = tf
                rows.append(tf)

        _closing = [False]

        def _close(_=None):
            if _closing[0]:
                return
            _closing[0] = True
            notes_dlg.open = False
            page.update()
            _closing[0] = False

        def _save_notes(_=None):
            # Write each person's notes back to all their entries
            updated_items = list(items)
            for (rel, name), tf in _note_fields.items():
                new_note = (tf.value or "").strip()
                for it in updated_items:
                    it_rel  = (it.get("relation") or "").strip()
                    it_name = (it.get("name") or "").strip()
                    if it_rel == rel and it_name == name:
                        it["notes"] = new_note
            _save_items(page, patient_id, updated_items)
            _close()
            on_refresh()

        notes_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.NOTE_ALT, color=ft.Colors.TEAL_400),
                ft.Text("Family History Notes", weight="bold"),
            ], spacing=8),
            content=ft.Container(
                width=500,
                content=ft.Column(rows, spacing=10, tight=True,
                                  scroll=ft.ScrollMode.AUTO),
            ),
            actions=[
                ft.TextButton("Cancel", on_click=_close),
                ft.FilledButton("Save", icon=ft.Icons.SAVE,
                                on_click=_save_notes),
            ],
            on_dismiss=_close,
        )
        append_dialog(page, notes_dlg)
        notes_dlg.open = True
        page.update()

    # Only show the Notes button if there are family members
    notes_btn = ft.TextButton(
        "Notes",
        icon=ft.Icons.NOTE_ALT,
        on_click=_open_notes,
        tooltip="View and edit notes for all family members",
    ) if items else ft.Container()

    fh_panel = themed_panel(
        page,
        ft.Column([
            ft.Row([
                ft.Text("Family History", size=18 * s, weight="bold"),
                ft.Container(expand=True),
                notes_btn,
            ]),
            fh_content,
        ], spacing=4 * s),
        padding=pt_scale(page, 16),
    )

    # -- Social history section --
    social_widget = build_social_history(page)
    social_panel = themed_panel(
        page,
        ft.Column([
            ft.Text("Social History", size=18 * s, weight="bold"),
            ft.Container(height=4 * s),
            social_widget,
        ], spacing=0),
        padding=pt_scale(page, 16),
    )

    body = ft.Column(
        [fh_panel, ft.Container(height=12 * s), social_panel],
        expand=True, scroll=ft.ScrollMode.AUTO,
    )

    return ft.Container(
        padding=pt_scale(page, 20),
        expand=True,
        content=ft.Column([header, ft.Divider(), body], expand=True),
    )
