# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Loading-screen tips and the styled tip-card builder for the Paperwork Wizard.
#
# One random tip is shown per generation session, similar to video-game loading
# screens.  Keeping these separate from the wizard orchestrator makes it easy to
# add/edit tips and reuse the card widget elsewhere.
# -----------------------------------------------------------------------------

import random
import flet as ft
from utils.ui_helpers import pt_scale

# ---------------------------------------------------------------------------
# Loading-screen tips — one random tip is shown per generation session.
# ---------------------------------------------------------------------------
LOADING_TIPS = [
    # Overview
    "Overview tab: Question marks in the top right of each tab offer information, suggestions, and a little appreciation as you navigate.",
    "Overview tab: The Notes space is great for action items, things to bring up at your next appointment, or self affirmations. These notes can also be included in your summary PDF.",
    "Overview tab: The Records Requests panel tracks your ROI follow-ups. A task is created automatically when you complete an ROI form. Click the due date to edit it inline.",
    'Overview tab: The orange "Review Suggestions" button appears when new data has been extracted from an uploaded document. Click it to accept or dismiss each suggestion.',
    'Overview tab: Use "Generate Summary" to export a customizable PDF of your health record to share with providers.',
    # Health Record
    'Health Record tab: The "Edit Visibility" button lets you mark sections as sensitive, adding eye icons that can be used to hide or reveal information.',
    # Vitals & Labs
    "Vitals & Labs tab: There are two sub-tabs: Vitals for daily measurements like blood pressure or weight, and Clinical Labs for official test results.",
    "Vitals & Labs tab: Select a metric or test name from the left sidebar to see its trend chart and full history table.",
    "Vitals & Labs tab: The trend chart plots numeric values over time. Green dashed lines show the normal reference range when available.",
    'Vitals & Labs tab: Use "Add Data" to manually record a new test result including value, unit, reference range, and date. You can also enter vitals like blood pressure, heart rate, or weight.',
    "Vitals & Labs tab: Click a column header in the Historical Test Table to sort results. Click the Info icon on any row to see full details including notes and reference ranges.",
    # Medical Records
    'Medical Records tab: Upload any medical document (PDF, image, etc.) using the "Upload Document" button.',
    'Medical Records tab: After uploading, AI extraction runs in the background. An orange "Review Suggestions" button will appear on the Overview tab when it is ready.',
    "Medical Records tab: Click a column header (Upload Date, Visit Date, Specialty) to sort the table. Click again to reverse the order.",
    "Medical Records tab: Documents are encrypted on your device. The Open button decrypts a secure temporary copy for viewing.",
    # Providers
    "Provider Directory tab: This directory stores your healthcare providers for quick reference and for auto-filling release of information (ROI) forms via the paperwork wizard.",
    "Provider Directory tab: Click any column header (Name, Specialty, Clinic) to sort the table. Use the search bar to filter by any field.",
    # Immunizations
    "Immunizations tab: Thanks for getting immunized. You're doing your part to keep yourself and others safe.",
    "Immunizations tab: Click any sortable column header to reorder the table. Lot numbers and administering providers are optional but helpful for your records.",
    # Family History
    "Family History tab: Add family members and their diagnosed conditions to build a visual genealogy tree and hereditary risk summary.",
    "Family History tab: There is only support here for 1st and 2nd degree relatives since those are what science agrees matter for hereditary risk, but you can add more if you want to.",
    "Family History tab: Click any node to view details or edit entries for that person.",
    "Family History tab: Your own diagnoses live in the Health Record tab, not here.",
    # Settings
    'Settings tab: "Show source of information" reveals which document or action produced each health record entry, with a hyperlink to the source document where available.',
    "Settings tab: The Auto-Lock timeout will lock your vault after a period of inactivity, requiring you to re-enter your password. Set to 0 to disable.",
    'Settings tab: "Export My Data" creates an encrypted backup zip protected by your vault password. You can import this on another device using "Upload Existing Profile" on the login screen.',
    'Settings tab: Check "Save unencrypted data" to export a plain-text ZIP with a readable PDF summary and your raw documents. This requires password confirmation for safety.',
    "Settings tab: Your recovery key lets you restore your vault if you ever forget your password. Rotating it will invalidate your old key and generate a fresh one. Always save the new key before closing the dialog.",
    'Settings tab: "Wipe Session & Exit" is for shared/public computers to securely erase your local database and temporary files so no one can access your data after you leave.',
]


def make_tip_card(page: ft.Page) -> ft.Container:
    """Returns a styled card showing one randomly chosen loading tip."""
    tip = random.choice(LOADING_TIPS)
    return ft.Container(
        padding=ft.padding.symmetric(
            horizontal=pt_scale(page, 12),
            vertical=pt_scale(page, 10),
        ),
        border_radius=pt_scale(page, 8),
        bgcolor=ft.Colors.with_opacity(0.07, ft.Colors.PRIMARY),
        content=ft.Row(
            [
                ft.Icon(
                    ft.Icons.LIGHTBULB_OUTLINE,
                    color=ft.Colors.YELLOW,
                    size=pt_scale(page, 18),
                ),
                ft.Text(
                    tip,
                    expand=True,
                    size=pt_scale(page, 12),
                    color=ft.Colors.SECONDARY,
                    italic=True,
                ),
            ],
            spacing=pt_scale(page, 8),
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
    )
