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

    # Basic setup for AI Chat Assistant state
    if not hasattr(page, "_chat_history_state"):
        page._chat_history_state = []
    if not hasattr(page, "_chat_is_thinking"):
        page._chat_is_thinking = False
    if not hasattr(page, "_chat_input_val"):
        page._chat_input_val = ""

    chat_list = ft.ListView(
        spacing=10, 
        padding=10, 
        auto_scroll=True, 
        height=300,
        width=None,
    )

    def _has_indexed_chunks() -> bool:
        try:
            cur = page.db_connection.cursor()
            cur.execute(
                "SELECT 1 FROM document_chunks WHERE patient_id=? LIMIT 1",
                (patient[0],),
            )
            return cur.fetchone() is not None
        except Exception:
            return False

    _chunks_ready = _has_indexed_chunks()

    def on_chat_input_change(e):
        page._chat_input_val = e.control.value

    chat_input = ft.TextField(
        value=page._chat_input_val,
        hint_text="Ask about your records..." if _chunks_ready else "Please upload and process documents first.",
        disabled=not _chunks_ready or page._chat_is_thinking,
        expand=True,
        on_change=on_chat_input_change,
    )
    
    chat_submit_btn = ft.IconButton(
        icon=ft.Icons.SEND,
        disabled=not _chunks_ready or page._chat_is_thinking,
    )

    def append_message_ui(role, content, save=True):
        if save:
            msg_obj = {"role": role, "text": content}
            page._chat_history_state.append(msg_obj)
        else:
            msg_obj = None

        is_user = role == "user"
        bg_col = getattr(ft.Colors, "PURPLE_50", ft.Colors.BLUE_50) if is_user else ft.Colors.GREY_100
        align = ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START
        
        msg_text = ft.Text(content, selectable=True, size=pt_scale(page, 13), color=ft.Colors.BLACK87)
        row = ft.Row([
            ft.Container(
                content=msg_text,
                bgcolor=bg_col,
                padding=10,
                border_radius=8,
                expand=True if not is_user else False,
            )
        ], alignment=align)
        chat_list.controls.append(row)
        
        if save and role == "ai":
            return msg_obj, msg_text
        return msg_text

    # Rebuild chat UI from history on view load
    for memory in page._chat_history_state:
        append_message_ui(memory["role"], memory["text"], save=False)

    def handle_chat_submit(_=None):
        if not chat_input.value.strip(): return
        question = chat_input.value.strip()
        chat_input.value = ""
        page._chat_input_val = ""
        
        chat_input.disabled = True
        chat_submit_btn.disabled = True
        page._chat_is_thinking = True
        page.update()

        append_message_ui("user", question, save=True)
        
        # Insert a blank AI message to stream into
        state_obj, ai_msg_text = append_message_ui("ai", "Thinking... Feel free to navigate to another tab while you wait, this can take some time.", save=True)
        page.update()
        
        def _run_query():
            try:
                from ai.query import query_documents_stream
                ai_msg_text.value = ""
                state_obj["text"] = ""
                
                # Wrap the user question with a system instruction for concise, lookup-style answers
                lookup_prompt = (
                    "You are a medical record lookup tool. Answer ONLY with the specific facts requested. "
                    "Use this format: [Fact Label]: [Value] Ref: [source document name and page].\n"
                    "Do NOT write paragraphs. Do NOT add disclaimers or commentary. "
                    "If multiple facts are relevant, list each on its own line.\n\n"
                    f"Question: {question}"
                )
                generator = query_documents_stream(page.db_connection, patient[0], lookup_prompt)
                
                citations = []
                for chunk in generator:
                    if chunk["type"] == "chunk":
                        ai_msg_text.value += chunk["text"]
                        state_obj["text"] = ai_msg_text.value
                        try:
                            page.update()
                        except: pass
                    elif chunk["type"] == "citations":
                        citations = chunk["citations"]
                        
                if citations:
                    ai_msg_text.value += "\n\nCitations: " + ", ".join([f"[{c['source_file_name']} (pg {c['page_number']})]" for c in citations])
                    state_obj["text"] = ai_msg_text.value
                    try:
                        page.update()
                    except: pass
                    
            except Exception as e:
                ai_msg_text.value = f"Error: {e}"
                state_obj["text"] = ai_msg_text.value
                try:
                    page.update()
                except: pass
            finally:
                page._chat_is_thinking = False
                try:
                    chat_input.disabled = False
                    chat_submit_btn.disabled = False
                    page.update()
                except: pass

        import threading
        threading.Thread(target=_run_query, daemon=True).start()

    chat_input.on_submit = handle_chat_submit
    chat_submit_btn.on_click = handle_chat_submit

    ai_card = themed_panel(
        page,
        ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.AUTO_AWESOME, color=ft.Colors.PURPLE_400),
                ft.Text("Assistant", weight="bold", size=pt_scale(page, 18)),
                ft.Container(expand=True),
            ]),
            ft.Container(
                content=chat_list,
                border=ft.border.all(1, ft.Colors.GREY_300),
                border_radius=8,
            ),
            ft.Row([chat_input, chat_submit_btn])
        ])
    )

    def _count_pending_suggestions():
        try:
            cur = page.db_connection.cursor()
            cur.execute("SELECT COUNT(*) FROM ai_extraction_inbox WHERE patient_id=? AND status='pending'", (patient[0],))
            return cur.fetchone()[0]
        except: return 0
    
    pending_count = _count_pending_suggestions()
    
    review_btn = ft.Container()
    if pending_count > 0:
        from ui.ai_review_dialog import show_ai_review_dialog
        def _open_review(_):
            def _on_close():
                # Refresh entire view to reflect possible accepted suggestions in the UI instantly
                # or just hide the button if count dropped to 0
                page.update()
            show_ai_review_dialog(page, patient[0], on_close=_on_close)
        
        review_btn = ft.FilledButton(
            f"Review AI Suggestions ({pending_count})",
            icon=ft.Icons.NEW_RELEASES,
            style=ft.ButtonStyle(bgcolor=ft.Colors.ORANGE_600, color=ft.Colors.WHITE),
            on_click=_open_review
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
                        review_btn,
                        ft.Container(width=pt_scale(page, 10)) if pending_count > 0 else ft.Container(),
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
                    ft.Column([notes_section], col={"sm": 12, "md": 6}),
                    ft.Column([ai_card], col={"sm": 12, "md": 6}),
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