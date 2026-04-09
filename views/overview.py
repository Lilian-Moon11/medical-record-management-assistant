# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# This is the main "Home" tab (Overview/Dashboard). It displays:
# 1. High-level patient identity (Name, DOB) and action buttons at the top.
# 2. A two-column body below the header divider:
#    - LEFT:  Records Request Tracker — pending follow-ups for ROI requests,
#             with inline due-date editing and candidate-match banners.
#    - RIGHT: Free-text Medical Notes with save.
# 3. An "AI Inbox" alert that dynamically appears when new AI data arrives.
#
# Deep Memory State caches chat so generators are never lost if you
# switch away to another tab to look up information.
# -----------------------------------------------------------------------------

import flet as ft
from datetime import datetime, timedelta
from database.patient import update_profile, get_profile
from database.records_requests import (
    list_requests,
    mark_complete,
    delete_request,
    update_due_date,
    update_notes as update_request_notes,
    update_request_status,
)
from utils.ui_helpers import pt_scale, themed_panel, show_snack, make_info_button
from utils.pdf_gen import generate_summary_pdf
from ui.wizards.paperwork_wizard import PaperworkWizard


# ── Status chip helper ────────────────────────────────────────────────────────
_STATUS_COLORS = {
    "pending":   ft.Colors.BLUE_GREY_400,
    "candidate": ft.Colors.ORANGE_600,
    "complete":  ft.Colors.GREEN_600,
}
_STATUS_LABELS = {
    "pending":   "Pending",
    "candidate": "Match Found",
    "complete":  "Complete",
}


def _status_chip(page: ft.Page, status: str) -> ft.Container:
    color = _STATUS_COLORS.get(status, ft.Colors.GREY)
    label = _STATUS_LABELS.get(status, status.title())
    return ft.Container(
        content=ft.Text(label, size=pt_scale(page, 11), color=ft.Colors.WHITE, weight="bold"),
        bgcolor=color,
        border_radius=pt_scale(page, 10),
        padding=ft.padding.symmetric(horizontal=pt_scale(page, 8), vertical=pt_scale(page, 2)),
    )


# ── Inline date editor ────────────────────────────────────────────────────────
def _inline_date_row(
    page: ft.Page,
    request_id: int,
    initial_date: str | None,
    due_source: str,
    on_change: callable,
    source_doc_id: int | None = None,
) -> ft.Row:
    """Clickable date text that morphs into a TextField on click (Option A)."""
    # Determine color: red if overdue or due today, primary otherwise
    def _date_color(date_str: str | None) -> str:
        if not date_str:
            return ft.Colors.SECONDARY
        try:
            due = datetime.strptime(date_str, "%Y-%m-%d").date()
            if due <= datetime.today().date():
                return ft.Colors.RED
        except Exception:
            pass
        return ft.Colors.PRIMARY

    display_btn = ft.TextButton(
        initial_date or "Not set",
        on_click=None,  # assigned below
        style=ft.ButtonStyle(
            color=_date_color(initial_date),
            padding=ft.padding.all(0),
            overlay_color=ft.Colors.with_opacity(0.05, ft.Colors.PRIMARY),
        ),
        tooltip="Click to edit due date",
    )

    edit_field = ft.TextField(
        value=initial_date or "",
        hint_text="YYYY-MM-DD",
        dense=True,
        width=pt_scale(page, 120),
        visible=False,
        text_size=pt_scale(page, 13),
    )

    row_ref: list = []

    def _show_edit(_e=None):
        display_btn.visible = False
        edit_field.visible = True
        edit_field.focus()
        if row_ref:
            row_ref[0].update()

    def _commit(_e=None):
        val = (edit_field.value or "").strip()
        if val:
            update_due_date(page.db_connection, request_id, val, source="manual")
            display_btn.text = val
            display_btn.style = ft.ButtonStyle(
                color=_date_color(val),
                padding=ft.padding.all(0),
                overlay_color=ft.Colors.with_opacity(0.05, ft.Colors.PRIMARY),
            )
            on_change()
        display_btn.visible = True
        edit_field.visible = False
        if row_ref:
            row_ref[0].update()

    def _cancel(_e=None):
        display_btn.visible = True
        edit_field.visible = False
        if row_ref:
            row_ref[0].update()

    display_btn.on_click = _show_edit
    edit_field.on_submit = _commit
    edit_field.on_blur = _commit

    # ⓘ icon — opens the source ROI document directly; only shown when one exists
    def _open_source_doc(_e=None):
        if not source_doc_id:
            return
        import asyncio, tempfile, os as _os
        from crypto.file_crypto import get_or_create_file_master_key, decrypt_bytes

        async def _do_open():
            try:
                cur = page.db_connection.cursor()
                cur.execute(
                    "SELECT file_name, file_path FROM documents WHERE id=?",
                    (source_doc_id,),
                )
                row = cur.fetchone()
                if not row:
                    show_snack(page, "Source document not found.", "red")
                    return
                human_name, enc_path = row
                fmk = get_or_create_file_master_key(page.db_connection, dmk_raw=page.db_key_raw)
                with open(enc_path, "rb") as _f:
                    ciphertext = _f.read()
                plaintext = decrypt_bytes(fmk, ciphertext)
                _, ext = _os.path.splitext(human_name)
                tmp = tempfile.mktemp(suffix=ext or ".pdf")
                with open(tmp, "wb") as _f:
                    _f.write(plaintext)
                _os.startfile(tmp)
            except Exception as ex:
                show_snack(page, f"Could not open document: {ex}", "red")

        asyncio.create_task(_do_open())

    source_icon_btn = ft.IconButton(
        icon=ft.Icons.ARTICLE_OUTLINED,
        icon_size=pt_scale(page, 14),
        tooltip="Open source ROI form",
        on_click=_open_source_doc,
        visible=bool(source_doc_id),
        style=ft.ButtonStyle(
            color=ft.Colors.SECONDARY,
            padding=ft.padding.all(0),
        ),
    )

    row = ft.Row(
        [display_btn, edit_field, source_icon_btn],
        spacing=2,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    row_ref.append(row)
    return row


# ── Single request card ───────────────────────────────────────────────────────
def _build_request_card(
    page: ft.Page,
    patient_id: int,
    req: tuple,
    on_refresh: callable,
) -> ft.Container:
    """Build one card for a records request row."""
    (req_id, provider_name, department, date_requested,
     due_date, due_source, status, candidate_doc_id, notes, created_at,
     source_doc_id) = req

    display_provider = provider_name or department or "Unknown"
    is_complete = status == "complete"

    # ── Candidate banner ──────────────────────────────────────────────────────
    candidate_banner = ft.Container()
    if status == "candidate" and candidate_doc_id:
        try:
            cur = page.db_connection.cursor()
            cur.execute("SELECT file_name FROM documents WHERE id=?", (candidate_doc_id,))
            row = cur.fetchone()
            doc_name = row[0] if row else f"Document #{candidate_doc_id}"
        except Exception:
            doc_name = f"Document #{candidate_doc_id}"

        def _confirm_match(_e, rid=req_id):
            mark_complete(page.db_connection, rid)
            on_refresh()

        def _dismiss_match(_e, rid=req_id):
            update_request_status(page.db_connection, rid, "pending", candidate_doc_id=None)
            on_refresh()

        candidate_banner = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.FIND_IN_PAGE, size=pt_scale(page, 14), color=ft.Colors.ORANGE),
                ft.Text(
                    f'"{doc_name}" uploaded — does this fulfill this request?',
                    size=pt_scale(page, 12),
                    expand=True,
                    color=ft.Colors.ORANGE,
                ),
                ft.IconButton(
                    ft.Icons.CHECK_CIRCLE_OUTLINE,
                    icon_color=ft.Colors.GREEN,
                    tooltip="Yes, mark complete",
                    on_click=_confirm_match,
                    icon_size=pt_scale(page, 18),
                ),
                ft.IconButton(
                    ft.Icons.CANCEL_OUTLINED,
                    icon_color=ft.Colors.RED,
                    tooltip="Dismiss — not a match",
                    on_click=_dismiss_match,
                    icon_size=pt_scale(page, 18),
                ),
            ], spacing=4),
            bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.ORANGE),
            border_radius=pt_scale(page, 6),
            padding=ft.padding.symmetric(
                horizontal=pt_scale(page, 8), vertical=pt_scale(page, 4)
            ),
        )

    # ── Due date row ──────────────────────────────────────────────────────────
    date_row = _inline_date_row(
        page, req_id, due_date, due_source or "default",
        on_change=on_refresh, source_doc_id=source_doc_id,
    )

    # ── Mark complete / delete row ────────────────────────────────────────────
    def _mark_done(_e, rid=req_id):
        mark_complete(page.db_connection, rid)
        on_refresh()

    def _delete(_e, rid=req_id):
        delete_request(page.db_connection, rid)
        on_refresh()

    action_row = ft.Row(
        [
            ft.TextButton(
                "✓ Mark Complete",
                on_click=_mark_done,
                visible=not is_complete,
                style=ft.ButtonStyle(color=ft.Colors.GREEN),
            ),
            ft.Container(expand=True),
            ft.IconButton(
                ft.Icons.DELETE_OUTLINE,
                tooltip="Remove this request",
                icon_size=pt_scale(page, 16),
                icon_color=ft.Colors.RED_300,
                on_click=_delete,
            ),
        ],
        spacing=0,
    )

    card_content = ft.Column(
        [
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                display_provider,
                                weight="bold",
                                size=pt_scale(page, 14),
                                color=ft.Colors.SECONDARY if is_complete else None,
                            ),
                            ft.Text(
                                department or "",
                                size=pt_scale(page, 11),
                                color=ft.Colors.SECONDARY,
                                visible=bool(department and department != provider_name),
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    _status_chip(page, status),
                ],
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            ft.Row(
                [
                    ft.Icon(ft.Icons.CALENDAR_MONTH, size=pt_scale(page, 13), color=ft.Colors.SECONDARY),
                    ft.Text(
                        f"Requested: {date_requested}",
                        size=pt_scale(page, 12),
                        color=ft.Colors.SECONDARY,
                    ),
                    ft.Text("·", size=pt_scale(page, 12), color=ft.Colors.SECONDARY),
                    ft.Text("Due:", size=pt_scale(page, 12), color=ft.Colors.SECONDARY),
                    date_row,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            candidate_banner,
            action_row,
        ],
        spacing=pt_scale(page, 4),
        tight=True,
    )

    return themed_panel(
        page,
        card_content,
        padding=ft.padding.all(pt_scale(page, 10)),
        radius=8,
    )


# ── Requests panel ────────────────────────────────────────────────────────────
def _build_requests_panel(page: ft.Page, patient_id: int) -> ft.Column:
    """Builds the scrollable requests list column."""
    from ui.add_request_dialog import open_add_request_dialog

    requests_list = ft.Column(spacing=pt_scale(page, 8))

    def refresh(_=None):
        requests_list.controls.clear()
        rows = []
        try:
            rows = list_requests(page.db_connection, patient_id)
        except Exception as ex:
            print(f"Requests fetch error: {ex}")

        if not rows:
            requests_list.controls.append(
                ft.Container(
                    content=ft.Text(
                        "No pending records requests.\n"
                        "Complete an ROI form or use + to add one manually.",
                        text_align=ft.TextAlign.CENTER,
                        size=pt_scale(page, 13),
                        color=ft.Colors.SECONDARY,
                        italic=True,
                    ),
                    alignment=ft.alignment.Alignment(0, 0),
                    padding=ft.padding.symmetric(vertical=pt_scale(page, 20)),
                )
            )
        else:
            for req in rows:
                requests_list.controls.append(
                    _build_request_card(page, patient_id, req, on_refresh=refresh)
                )

        try:
            requests_list.update()
        except Exception:
            pass

    # Initial population
    refresh()

    # Expose refresh so outside code (wizard hook, upload hook) can call it
    page._refresh_requests_panel = refresh

    def _add(_e=None):
        open_add_request_dialog(page, patient_id, on_saved=refresh)

    header = ft.Row(
        [
            ft.Text("Records Requests", weight="bold", size=pt_scale(page, 16)),
            ft.Container(expand=True),
            ft.IconButton(
                ft.Icons.ADD_CIRCLE_OUTLINE,
                tooltip="Add request manually",
                icon_size=pt_scale(page, 20),
                on_click=_add,
            ),
        ]
    )

    return ft.Column(
        [header, requests_list],
        expand=True,
        spacing=pt_scale(page, 6),
    )


# ── Main view ─────────────────────────────────────────────────────────────────
def get_overview_view(page: ft.Page):
    patient = page.current_profile
    if patient is None:
        return _create_profile_ui(page)

    patient_id = patient[0]

    # --- Notes ---
    notes_input = ft.TextField(
        value=patient[3] or "",
        label="",
        multiline=True,
        min_lines=5,
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
                ft.Text("Notes", weight="bold", size=pt_scale(page, 16)),
                ft.IconButton(ft.Icons.SAVE, tooltip="Save Notes", on_click=save_notes),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            notes_input,
        ]),
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

    # --- AI Inbox badge (live-updatable) ---
    def _count_pending():
        try:
            cur = page.db_connection.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM ai_extraction_inbox WHERE patient_id=? AND status='pending'",
                (patient_id,),
            )
            return cur.fetchone()[0]
        except Exception:
            return 0

    from ui.ai_review_dialog import show_ai_review_dialog

    def _open_review(_):
        show_ai_review_dialog(page, patient_id, on_close=_refresh_review_btn)

    review_btn = ft.FilledButton(
        "Review Suggestions",
        icon=ft.Icons.NEW_RELEASES,
        style=ft.ButtonStyle(bgcolor=ft.Colors.ORANGE_600, color=ft.Colors.WHITE),
        on_click=_open_review,
        visible=False,
    )
    page._overview_review_btn = review_btn

    def _refresh_review_btn():
        count = _count_pending()
        review_btn.text = f"Review Suggestions ({count})"
        review_btn.visible = count > 0
        try:
            review_btn.update()
        except Exception:
            pass

    _refresh_review_btn()
    page._refresh_overview_review_btn = _refresh_review_btn

    _info_btn = make_info_button(page, "Overview", [
        "You will find question marks located in the top right of each tab (like the one that you clicked to get here) that will give you some information/suggestions/appreciation as you navigate.",
        "Inspiration for using the note space: a place to keep track of action items, things to remember to address at your next appointment, self affirmations. These notes can optionally be included when generating a summary PDF.",
        "The Records Requests panel tracks your ROI (Release of Information) follow-ups. A task is created automatically when you complete an ROI form. Click the due date to edit it inline.",
        "When you upload a document that matches a pending request's provider name, a candidate banner will appear — click ✓ to confirm or ✗ to dismiss.",
        "The orange \"Review Suggestions\" button appears here when new data has been extracted from a document you uploaded. Click it to accept or dismiss each suggestion.",
        "Use \"Complete Paperwork\" to auto-fill common medical forms using your saved health record data.",
        "Use \"Generate Summary\" to export a customisable PDF of your health record to share with providers.",
    ])

    # ── Header row (name, DOB, action buttons) ────────────────────────────────
    header_row = ft.Row(
        [
            ft.Icon(ft.Icons.ACCOUNT_CIRCLE, size=pt_scale(page, 52), color=ft.Colors.BLUE_GREY),
            ft.Column(
                [
                    ft.Text(patient[1], size=pt_scale(page, 24), weight="bold"),
                    ft.Text(f"DOB: {patient[2] or '(not set)'}", size=pt_scale(page, 13)),
                ],
                spacing=0,
            ),
            ft.Container(expand=True),
            review_btn,
            ft.Container(width=pt_scale(page, 8)),
            ft.FilledButton(
                "Complete Paperwork",
                icon=ft.Icons.ASSIGNMENT_OUTLINED,
                on_click=start_paperwork_wizard,
            ),
            ft.Container(width=pt_scale(page, 8)),
            ft.FilledButton(
                "Generate Summary",
                icon=ft.Icons.PICTURE_AS_PDF,
                on_click=handle_generate_pdf,
            ),
            _info_btn,
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ── Two-column body ───────────────────────────────────────────────────────
    requests_panel = _build_requests_panel(page, patient_id)

    body = ft.Row(
        [
            # LEFT: Requests tracker (55%)
            ft.Container(
                content=themed_panel(
                    page,
                    requests_panel,
                    padding=ft.padding.all(pt_scale(page, 12)),
                    radius=8,
                ),
                expand=55,
            ),
            ft.VerticalDivider(width=1),
            # RIGHT: Notes (45%)
            ft.Container(
                content=notes_section,
                expand=45,
            ),
        ],
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    return ft.Container(
        padding=pt_scale(page, 20),
        expand=True,
        content=ft.Column(
            [
                header_row,
                ft.Divider(),
                body,
            ],
            expand=True,
            spacing=pt_scale(page, 10),
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
        show_snack(page, "Profile created successfully!")

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