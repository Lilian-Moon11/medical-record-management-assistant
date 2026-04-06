# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# This is the main "Home" tab (Overview/Dashboard). It displays:
# 1. High-level patient identity (Name, DOB) and free-text "Medical Notes".
# 2. A live Chat Assistant Pipeline integrated directly on the dashboard
#    (uses Local RAG offline, guaranteeing privacy).
# 3. An "AI Inbox" alert that dynamically appears when new AI data arrives.
#
# Deep Memory State caches your chat so generators are never lost if you
# switch away to another tab to look up information.
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

    # --- Notes ---
    notes_input = ft.TextField(
        value=patient[3] or "",
        label="",
        multiline=True,
        min_lines=5,
        max_lines=10,
        expand=True,
    )

    def save_notes(e):
        try:
            update_profile(
                page.db_connection,
                patient[0],
                patient[1],
                patient[2],
                notes_input.value,
            )
            page.current_profile = get_profile(page.db_connection)
            show_snack(page, "Notes saved successfully.", "green")
        except Exception as ex:
            show_snack(page, f"Error saving notes: {ex}", "red")

    notes_section = themed_panel(
        page,
        ft.Column([
            ft.Row([
                ft.Text("Notes", weight="bold", size=pt_scale(page, 18)),
                ft.IconButton(ft.Icons.SAVE, tooltip="Save Notes", on_click=save_notes),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            notes_input,
        ])
    )

    # --- Summary PDF dialog (created once per session) ---
    if not hasattr(page, "_summary_options_dlg"):
        page._summary_opt_ins   = ft.Checkbox(label="Insurance Coverage",      value=True)
        page._summary_opt_all   = ft.Checkbox(label="Allergies & Alerts",      value=True)
        page._summary_opt_labs  = ft.Checkbox(label="Abnormal Labs (All-time)", value=True)
        page._summary_opt_meds  = ft.Checkbox(label="Current Medications",     value=True)
        page._summary_opt_cond  = ft.Checkbox(label="Active Conditions",       value=True)
        page._summary_opt_notes = ft.Checkbox(label="General Notes",           value=True)

        def _do_export(e):
            page._summary_options_dlg.open = False
            page.update()
            import os
            try:
                opts = {
                    "insurance":  page._summary_opt_ins.value,
                    "allergies":  page._summary_opt_all.value,
                    "labs":       page._summary_opt_labs.value,
                    "meds":       page._summary_opt_meds.value,
                    "conditions": page._summary_opt_cond.value,
                    "notes":      page._summary_opt_notes.value,
                }
                path = generate_summary_pdf(page.db_connection, patient[0], options=opts)
                show_snack(page, "PDF Generated!", "green")
                os.startfile(path)
            except Exception as ex:
                show_snack(page, f"PDF Error: {ex}", "red")

        def _close_dlg(e):
            page._summary_options_dlg.open = False
            page.update()

        page._summary_options_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Customize Summary", size=pt_scale(page, 18), weight="bold"),
            content=ft.Column([
                ft.Text("Select the sections to include in the PDF:", size=pt_scale(page, 14)),
                page._summary_opt_ins,
                page._summary_opt_all,
                page._summary_opt_labs,
                page._summary_opt_meds,
                page._summary_opt_cond,
                page._summary_opt_notes,
            ], tight=True),
            actions=[
                ft.TextButton("Cancel", on_click=_close_dlg),
                ft.FilledButton("Generate PDF", icon=ft.Icons.PICTURE_AS_PDF, on_click=_do_export),
            ],
            on_dismiss=_close_dlg,
        )
        page.overlay.append(page._summary_options_dlg)

    def handle_generate_pdf(e):
        page._summary_options_dlg.open = True
        page.update()

    def start_paperwork_wizard(e):
        wizard = PaperworkWizard(page)
        wizard.open()

    # --- AI Inbox badge ---
    def _count_pending():
        try:
            cur = page.db_connection.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM ai_extraction_inbox WHERE patient_id=? AND status='pending'",
                (patient[0],),
            )
            return cur.fetchone()[0]
        except:
            return 0

    pending_count = _count_pending()

    review_btn = ft.Container()
    if pending_count > 0:
        from ui.ai_review_dialog import show_ai_review_dialog

        def _open_review(_):
            show_ai_review_dialog(page, patient[0], on_close=lambda: page.update())

        review_btn = ft.FilledButton(
            f"Review AI Suggestions ({pending_count})",
            icon=ft.Icons.NEW_RELEASES,
            style=ft.ButtonStyle(bgcolor=ft.Colors.ORANGE_600, color=ft.Colors.WHITE),
            on_click=_open_review,
        )

    return ft.Container(
        padding=pt_scale(page, 20),
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ACCOUNT_CIRCLE, size=pt_scale(page, 60), color=ft.Colors.BLUE_GREY),
                        ft.Column(
                            [
                                ft.Text(patient[1], size=pt_scale(page, 26), weight="bold"),
                                ft.Text(f"DOB: {patient[2] or '(not set)'}", size=pt_scale(page, 14)),
                            ],
                            spacing=0,
                        ),
                        ft.Container(expand=True),
                        review_btn,
                        ft.Container(width=pt_scale(page, 10)) if pending_count > 0 else ft.Container(),
                        ft.FilledButton(
                            "Complete Paperwork",
                            icon=ft.Icons.ASSIGNMENT_OUTLINED,
                            on_click=start_paperwork_wizard,
                        ),
                        ft.Container(width=pt_scale(page, 10)),
                        ft.FilledButton(
                            "Generate Summary",
                            icon=ft.Icons.PICTURE_AS_PDF,
                            on_click=handle_generate_pdf,
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(),
                ft.ResponsiveRow([
                    ft.Column([notes_section], col={"sm": 12}),
                ]),
            ],
            scroll=ft.ScrollMode.AUTO,
        ),
    )


def _create_profile_ui(page: ft.Page):
    """Sub-view: Shown ONLY if the database is empty (first run)."""
    name_input  = ft.TextField(label="Full Name", autofocus=True)
    dob_input   = ft.TextField(label="Date of Birth (YYYY-MM-DD)")
    notes_input = ft.TextField(label="Initial Medical Notes", multiline=True, min_lines=3)

    def do_create(e):
        if not name_input.value:
            return show_snack(page, "Name is required to create a profile.", "red")
        from database.patient import create_profile
        create_profile(page.db_connection, name_input.value, dob_input.value, notes_input.value)
        page.current_profile = get_profile(page.db_connection)
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
            ft.FilledButton("Create Profile", icon=ft.Icons.SAVE, on_click=do_create),
        ], spacing=pt_scale(page, 20)),
    )