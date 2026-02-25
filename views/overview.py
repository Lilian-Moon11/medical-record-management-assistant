# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# This is the "Home" tab. It displays the high-level patient identity (Name, DOB)
# and the free-text "Medical Notes". 
# -----------------------------------------------------------------------------

import flet as ft
from database import update_profile, create_profile, get_profile
from utils import s, themed_panel, show_snack

def get_overview_view(page: ft.Page):
    """
    Main entry point for the Overview tab.
    """
    # 1. Validation: Ensure we actually have a patient loaded.
    patient = page.current_profile
    if patient is None:
        return _create_profile_ui(page)

    # 2. Logic for "Edit Mode"
    # We define this internal helper to switch the UI to text fields.
    def edit_mode_toggle(e):
        # Pre-fill inputs with current data
        # patient = (id, name, dob, notes)
        name_input.value = patient[1]
        dob_input.value = patient[2]
        notes_input.value = patient[3]

        def save_changes(ev):
            try:
                # DB Update
                update_profile(
                    page.db_connection,
                    patient[0], # ID
                    name_input.value,
                    dob_input.value,
                    notes_input.value,
                )
                
                # Update global state so other tabs see the new name immediately
                # (We perform a fresh DB fetch to be safe)
                page.current_profile = get_profile(page.db_connection)
                
                # Refresh the view to show Read-Only mode again
                page.content_area.content = get_overview_view(page)
                page.content_area.update()
                show_snack(page, "Profile updated successfully.")
            except Exception as ex:
                show_snack(page, f"Error: {ex}", "red")

        def cancel_edit(_):
            page.content_area.content = get_overview_view(page)
            page.content_area.update()

        # Swap the view content to the Edit Form
        page.content_area.content = ft.Container(
            padding=s(page, 20),
            content=ft.Column([
                ft.Text("Edit Profile", size=s(page, 30), weight="bold"),
                name_input,
                dob_input,
                notes_input,
                ft.Row([
                    ft.Button("Save Changes", on_click=save_changes),
                    ft.Button("Cancel", on_click=cancel_edit, color="red"),
                ])
            ])
        )
        page.content_area.update()

    # 3. Define Controls (reused in both modes)
    name_input = ft.TextField(label="Full Name")
    dob_input = ft.TextField(label="Date of Birth (YYYY-MM-DD)")
    notes_input = ft.TextField(label="Medical Notes", multiline=True, height=150)

    # 4. Return the "Read-Only" Dashboard Layout
    return ft.Container(
        padding=s(page, 20),
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ACCOUNT_CIRCLE, size=s(page, 80), color=ft.Colors.BLUE_GREY),
                        ft.Column(
                            [
                                ft.Text(patient[1], size=s(page, 30), weight="bold"),
                                ft.Text(f"DOB: {patient[2] or '(not set)'}", size=s(page, 16)),
                            ]
                        ),
                        ft.Container(expand=True),
                        ft.Button("Edit", icon=ft.Icons.EDIT, on_click=edit_mode_toggle),
                    ]
                ),
                ft.Divider(),
                ft.Text("Medical Summary / Notes", weight="bold", size=s(page, 18)),
                # Use themed_panel so this box looks correct in High Contrast mode
                themed_panel(page, ft.Text(patient[3] or "", size=s(page, 16)), padding=s(page, 15)),
            ]
        ),
    )

def _create_profile_ui(page):
    """
    Sub-view: Shown ONLY if the database is empty (first run).
    """
    name_input = ft.TextField(label="Full Name")
    dob_input = ft.TextField(label="Date of Birth")
    notes_input = ft.TextField(label="Notes", multiline=True)

    def do_create(e):
        if not name_input.value: return
        create_profile(page.db_connection, name_input.value, dob_input.value, notes_input.value)
        
        # Reload profile and refresh
        page.current_profile = get_profile(page.db_connection)
        
        # Redirect to main dashboard
        page.content_area.content = get_overview_view(page)
        page.content_area.update()

    return ft.Column([
        ft.Text("Welcome! Create Patient Profile", size=s(page, 24)),
        name_input, dob_input, notes_input,
        ft.Button("Create", on_click=do_create)
    ])