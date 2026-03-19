# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Documents view for managing patient-associated files.
#
# Includes:
# - Display of uploaded patient documents in a searchable table
# - Safe document ingestion with filename collision handling
# - Native OS document opening
# - Accessible confirmation UI for destructive actions (delete)
# - AI (Beta): "Ask about your documents" query stub (Phase 5.0)
#
# DESIGN NOTES:
# - Uses page.overlay for confirmation dialogs to ensure reliable rendering
#   when the app is mounted under a custom page.root container
# - The delete confirmation dialog is created once and reused (kept mounted
#   in page.overlay) to avoid intermittent action-button click issues
# - Delete confirmations close the UI immediately, then perform filesystem
#   and database operations asynchronously to avoid blocking the UI thread
# - Dialogs are non-modal to allow click-outside dismissal where supported
# - Files are stored encrypted on disk (*.enc); "Open" explicitly decrypts
#   to a temporary PDF for viewing via the native OS
# - Document IDs are treated as opaque identifiers; user-facing ordering
#   is handled via sort/filter logic rather than relying on database IDs
# -----------------------------------------------------------------------------
import flet as ft
import os
import asyncio
import tempfile
from crypto.file_crypto import get_or_create_file_master_key, encrypt_bytes, decrypt_bytes
from datetime import datetime

from database import (
    get_patient_documents,
    add_document,
    delete_document,
    get_document_path,
)
from utils.ui_helpers import pt_scale, show_snack


def get_documents_view(page: ft.Page):
    # 1. SETUP
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    search_field = ft.TextField(
        label="Search Records",
        prefix_icon=ft.Icons.SEARCH,
        width=300,
        dense=True,
    )

    if not hasattr(page, "_delete_dialog_open"):
        page._delete_dialog_open = False

    if not hasattr(page, "_pending_delete"):
        page._pending_delete = None

    def close_delete_dlg(_=None):
        dlg = page._delete_dlg
        dlg.open = False
        try:
            dlg.update()
        except Exception:
            pass
        page._pending_delete = None
        page._delete_dialog_open = False
        page.update()

    def confirm_delete(_=None):
        pending = page._pending_delete
        if not pending:
            close_delete_dlg()
            return
        doc_id, _name = pending
        close_delete_dlg()  # close dialog immediately for UX

        # Perform delete synchronously so the table refreshes right away
        try:
            file_path = get_document_path(page.db_connection, int(doc_id))

            if isinstance(file_path, (tuple, list)):
                file_path = file_path[1] if file_path else None

            delete_document(page.db_connection, int(doc_id))

            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as ex:
                    print(f"Could not delete file {file_path}: {ex}")
                    show_snack(page, "Deleted record, but file could not be removed.", "orange")

            refresh_table(search_field.value, update_ui=True)
            show_snack(page, "Record and file deleted.", "blue")
        except Exception as ex:
            print("DELETE ERROR:", ex)
            show_snack(page, f"Delete failed: {ex}", "red")

    if not hasattr(page, "_delete_dlg") or page._delete_dlg is None:
        page._delete_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Confirm Delete"),
            content=ft.Text(""),
            actions=[
                ft.ElevatedButton("Cancel", on_click=close_delete_dlg),
                ft.ElevatedButton("Delete", icon=ft.Icons.DELETE, on_click=confirm_delete),
            ],
            on_dismiss=close_delete_dlg,
        )
        page.overlay.append(page._delete_dlg)

    all_docs = []

    # 2. CONTROLS
    data_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Type")),
            ft.DataColumn(ft.Text("File Name")),
            ft.DataColumn(ft.Text("Date Added")),
            ft.DataColumn(ft.Text("Open")),
            ft.DataColumn(ft.Text("Delete")),
        ],
        rows=[],
        border=ft.border.all(1, ft.Colors.GREY_400),
        vertical_lines=ft.border.BorderSide(1, ft.Colors.GREY_200),
    )


    # --- helper: treat "report.pdf" and "report (1).pdf" as the same base name
    def base_key(filename: str) -> str:
        root, ext = os.path.splitext(filename or "")
        root = root.strip()

        # Strip trailing "(number)" or " (number)"
        if root.endswith(")") and "(" in root:
            left = root.rsplit("(", 1)[0].rstrip()
            inside = root.rsplit("(", 1)[1][:-1].strip()  # drop ")"
            if inside.isdigit() and left:
                root = left

        return (root + ext).lower()

    # 3. HELPER FUNCTIONS
    async def open_doc_async(path: str | None, human_name: str = "record.pdf"):
        if not path or not os.path.exists(path):
            show_snack(page, "File not found.", "red")
            return

        try:
            # Decrypt to a temp PDF for viewing
            fmk = get_or_create_file_master_key(page.db_connection, dmk_raw=page.db_key_raw)
            with open(path, "rb") as f:
                ciphertext = f.read()
            plaintext = decrypt_bytes(fmk, ciphertext)

            # Write temp PDF
            safe_name = human_name if human_name.lower().endswith(".pdf") else f"{human_name}.pdf"
            tmp_dir = tempfile.gettempdir()
            tmp_path = os.path.join(tmp_dir, f"lpa_decrypted_{patient_id}_{int(datetime.now().timestamp())}.pdf")
            with open(tmp_path, "wb") as f:
                f.write(plaintext)

            file_url = "file:///" + tmp_path.replace("\\", "/")
            await ft.UrlLauncher().launch_url(file_url)

            show_snack(page, "Opened a temporary decrypted copy (will be cleaned up later).", "orange")
        except Exception as ex:
            print("OPEN ERROR:", ex)
            show_snack(page, f"Open failed: {ex}", "red")

    def open_doc_click(e: ft.ControlEvent):
        enc_path, human_name = e.control.data
        asyncio.create_task(open_doc_async(enc_path, human_name))

    def delete_handler(e: ft.ControlEvent):
        data = getattr(e.control, "data", None)
        if not data:
            return
        doc_id, name = data

        if getattr(page, "_delete_dialog_open", False):
            return
        page._delete_dialog_open = True

        page._pending_delete = (int(doc_id), str(name))

        dlg = page._delete_dlg
        dlg.title = ft.Text("Confirm Delete")
        dlg.content = ft.Text(
            f"Permanently delete '{name}'?\n\n"
            "This removes it from the app and deletes the file from disk."
        )
        dlg.open = True

        # Dialog + page update (both helps reliability)
        try:
            dlg.update()
        except Exception:
            pass
        page.update()

    def refresh_table(filter_text: str = "", update_ui: bool = False):
        nonlocal all_docs
        rows: list[ft.DataRow] = []
        ft_filter = (filter_text or "").lower()

        try:
            all_docs = get_patient_documents(page.db_connection, patient_id)
        except Exception:
            all_docs = []

        for doc in all_docs:
            try:
                doc_id, file_name, upload_date, file_path = doc
            except Exception:
                continue

            if ft_filter and ft_filter not in str(file_name).lower():
                continue

            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Icon(ft.Icons.INSERT_DRIVE_FILE, color="blue")),
                        ft.DataCell(ft.Text(str(file_name))),
                        ft.DataCell(ft.Text(str(upload_date))),
                        ft.DataCell(
                            ft.IconButton(
                                ft.Icons.OPEN_IN_NEW,
                                data=(file_path, file_name),
                                on_click=open_doc_click,
                            )
                        ),
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.DELETE,
                                tooltip="Delete",
                                data=(doc_id, file_name),
                                on_click=delete_handler,
                            )
                        ),
                    ]
                )
            )

        data_table.rows = rows

        if update_ui:
            try:
                data_table.update()
            except Exception:
                pass

    def on_search_change(e: ft.ControlEvent):
        refresh_table(e.control.value, update_ui=True)

    search_field.on_change = on_search_change

    # 5. UPLOAD LOGIC
    async def upload_document_click(e: ft.ControlEvent):
        files = await ft.FilePicker().pick_files(
            allow_multiple=False,
            dialog_title="Select Medical Record",
        )

        if not files:
            return

        picked = files[0]
        src_path = getattr(picked, "path", None) or getattr(picked, "file_path", None)

        if not src_path:
            show_snack(page, "Picker returned no local path.", "red")
            return

        # Path Calculation
        current_file_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_file_dir)
        dest_dir = os.path.join(project_root, "data", str(patient_id))
        os.makedirs(dest_dir, exist_ok=True)

        # --- RENAME LOGIC ---
        original_name = picked.name
        file_name = original_name
        name_root, name_ext = os.path.splitext(file_name)
        counter = 1

        def enc_target(name: str) -> str:
            return os.path.join(dest_dir, name + ".enc")

        # Avoid overwriting existing encrypted files
        while os.path.exists(enc_target(file_name)):
            file_name = f"{name_root} ({counter}){name_ext}"
            counter += 1

        enc_path = enc_target(file_name)

        # --- ENCRYPT + STORE ---
        try:
            with open(src_path, "rb") as f:
                plaintext = f.read()

            fmk = get_or_create_file_master_key(page.db_connection, dmk_raw=page.db_key_raw)
            ciphertext = encrypt_bytes(fmk, plaintext)

            with open(enc_path, "wb") as f:
                f.write(ciphertext)

            add_document(
                page.db_connection,
                patient_id,
                file_name,     # human label
                enc_path,      # encrypted disk path
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            )

            refresh_table(search_field.value, update_ui=True)
            show_snack(page, "Document uploaded securely.", "blue")

        except Exception as ex:
            print(f"Upload Error: {ex}")
            show_snack(page, f"Error: {str(ex)}", "red")

    # 6. AI QUERY STUB (Phase 5.0) -----------------------------------------------
    # Checks whether any chunks have been indexed for this patient.
    # If not, the input is disabled with an accessible plain-language message.

    def _has_indexed_chunks() -> bool:
        try:
            cur = page.db_connection.cursor()
            cur.execute(
                "SELECT 1 FROM document_chunks WHERE patient_id=? LIMIT 1",
                (patient_id,),
            )
            return cur.fetchone() is not None
        except Exception:
            return False

    ai_question = ft.TextField(
        label="Ask a question about your documents",
        hint_text="e.g. What medications am I currently taking?",
        multiline=False,
        expand=True,
    )
    ai_response = ft.Text(
        "",
        selectable=True,
        size=pt_scale(page, 13),
    )
    ai_citations = ft.Column([], spacing=2)

    _chunks_ready = _has_indexed_chunks()

    if not _chunks_ready:
        ai_question.disabled = True
        ai_question.hint_text = (
            "Document analysis is not ready yet. "
            "Upload documents and allow the app to process them first."
        )

    def _handle_ai_query(e: ft.ControlEvent):
        question = ai_question.value.strip()
        if not question:
            return
        ai_response.value = "Thinking..."
        ai_citations.controls.clear()
        try:
            page.update()
        except Exception:
            pass

        try:
            from ai.query import query_documents
            result = query_documents(
                page.db_connection,
                patient_id,
                question,
            )
            ai_response.value = result.get("response", "")
            ai_citations.controls = [
                ft.Text(
                    f"Source: {c.get('source_file_name', 'unknown')}, "
                    f"page {c.get('page_number', '?')}",
                    size=pt_scale(page, 11),
                    color=ft.Colors.GREY_600,
                    italic=True,
                )
                for c in result.get("citations", [])
            ]
        except Exception as ex:
            ai_response.value = f"Could not complete the query: {ex}"

        try:
            page.update()
        except Exception:
            pass

    ai_card = ft.Card(
        content=ft.Container(
            padding=pt_scale(page, 16),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.AUTO_AWESOME, color=ft.Colors.PURPLE_400),
                            ft.Text(
                                "Ask About Your Documents",
                                weight="bold",
                                size=pt_scale(page, 15),
                            ),
                            ft.Container(
                                content=ft.Text(
                                    "AI (Beta)",
                                    size=pt_scale(page, 10),
                                    color=ft.Colors.WHITE,
                                ),
                                bgcolor=ft.Colors.PURPLE_400,
                                padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                border_radius=4,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Text(
                        "Ask a plain-language question. Answers come from your uploaded documents only.",
                        size=pt_scale(page, 12),
                        color=ft.Colors.GREY_600,
                    ),
                    ft.Row(
                        [
                            ai_question,
                            ft.ElevatedButton(
                                "Ask",
                                icon=ft.Icons.SEND,
                                disabled=not _chunks_ready,
                                on_click=_handle_ai_query,
                            ),
                        ],
                        spacing=8,
                    ),
                    ai_response,
                    ai_citations,
                ],
                spacing=10,
            ),
        ),
    )

    # 7. INITIAL LAYOUT BUILD
    refresh_table(update_ui=False)

    return ft.Container(
        padding=pt_scale(page, 20),
        expand=True,
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("Medical Records", size=pt_scale(page, 24), weight="bold"),
                        ft.Container(expand=True),
                        ft.Button(
                            "Upload Document",
                            icon=ft.Icons.UPLOAD_FILE,
                            on_click=upload_document_click,
                        ),
                    ]
                ),
                ft.Divider(),
                search_field,
                ft.Divider(),
                ft.Column([data_table], scroll=ft.ScrollMode.AUTO, expand=True),
                ft.Divider(),
                ai_card,
            ],
            expand=True,
        ),
    )
