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
from database.patient import update_profile, get_profile
from utils.ui_helpers import pt_scale, themed_panel, show_snack 
from utils.pdf_gen import generate_summary_pdf 
from ui.wizards.paperwork_wizard import PaperworkWizard

def get_overview_view(page: ft.Page):
    patient = page.current_profile
    if patient is None:
        return _create_profile_ui(page)

    # Logic: Inline Notes Save
    def save_notes(e):
        try:
            update_profile(
                page.db_connection,
                patient[0], # id
                patient[1], # name (keep same)
                patient[2], # dob (keep same)
                notes_input.value,
            )
            page.current_profile = get_profile(page.db_connection)
            show_snack(page, "Notes saved successfully.", "green")
        except Exception as ex:
            show_snack(page, f"Error saving notes: {ex}", "red")

    # Logic: PDF Summary Trigger (2.1)
    def handle_generate_pdf(e):
        import os
        try:
            path = generate_summary_pdf(page.db_connection, patient[0])
            show_snack(page, "PDF Generated!", "green")
            os.startfile(path)
        except Exception as ex:
            show_snack(page, f"PDF Error: {ex}", "red")

    def start_paperwork_wizard(e):
        wizard = PaperworkWizard(page)
        wizard.open()

    # Define the notes input with its own save button
    notes_input = ft.TextField(
        value=patient[3] or "",
        label="",
        multiline=True,
        min_lines=5,
        max_lines=10,
        expand=True,
    )

    notes_section = themed_panel(
        page,
        ft.Column([
            ft.Row([
                ft.Text("Notes", weight="bold", size=pt_scale(page, 18)),
                ft.IconButton(ft.Icons.SAVE, tooltip="Save Notes", on_click=save_notes)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            notes_input
        ])
    )

    return ft.Container(
        padding=pt_scale(page, 20),
        content=ft.Column(
            [
                # Header Section
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ACCOUNT_CIRCLE, size=pt_scale(page, 60), color=ft.Colors.BLUE_GREY),
                        ft.Column(
                            [
                                ft.Text(patient[1], size=pt_scale(page, 26), weight="bold"),
                                ft.Text(f"DOB: {patient[2] or '(not set)'}", size=pt_scale(page, 14)),
                            ],
                            spacing=0
                        ),
                        ft.Container(expand=True),
                        # Action Buttons
                        ft.FilledButton(
                            "Complete Paperwork", 
                            icon=ft.Icons.ASSIGNMENT_OUTLINED, 
                            on_click=start_paperwork_wizard
                        ),
                        ft.Container(width=pt_scale(page, 10)),
                        ft.FilledButton(
                            "Generate Summary", 
                            icon=ft.Icons.PICTURE_AS_PDF, 
                            on_click=handle_generate_pdf
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(),
                
                # Dashboard Content
                ft.ResponsiveRow([
                    ft.Column([notes_section], col={"sm": 12, "md": 8}),
                    # Future: Add a "Pinned Meds" or "Pinned Conditions" card here in col 4
                ])
            ],
            scroll=ft.ScrollMode.AUTO
        ),

        
    )

def _create_profile_ui(page: ft.Page):
    """
    Sub-view: Shown ONLY if the database is empty (first run).
    """
    name_input = ft.TextField(label="Full Name", autofocus=True)
    dob_input = ft.TextField(label="Date of Birth (YYYY-MM-DD)")
    notes_input = ft.TextField(label="Initial Medical Notes", multiline=True, min_lines=3)

    def do_create(e):
        if not name_input.value:
            return show_snack(page, "Name is required to create a profile.", "red")
        
        # Create the record in the encrypted DB
        create_profile(
            page.db_connection, 
            name_input.value, 
            dob_input.value, 
            notes_input.value
        )
        
        # Reload the global profile state
        page.current_profile = get_profile(page.db_connection)
        
        # Refresh the view to the main Dashboard
        page.content_area.content = get_overview_view(page)
        page.content_area.update()
        show_snack(page, "Profile created successfully!", "green")

    return ft.Container(
        padding=pt_scale(page, 40),
        content=ft.Column([
            ft.Text("Welcome! Create Your Patient Profile", size=pt_scale(page, 28), weight="bold"),
            ft.Text("This data stays local and encrypted on your device.", italic=True),
            ft.Divider(),
            name_input,
            dob_input,
            notes_input,
            ft.FilledButton("Create Profile", icon=ft.Icons.SAVE, on_click=do_create)
        ], spacing=pt_scale(page, 20))
    )