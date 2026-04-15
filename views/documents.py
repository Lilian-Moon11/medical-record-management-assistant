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
import logging
import flet as ft
import os
import asyncio
import tempfile
import threading
from crypto.file_crypto import get_or_create_file_master_key, encrypt_bytes, decrypt_bytes
from cryptography.fernet import InvalidToken
from utils.open_file import open_file_cross_platform
from datetime import datetime
from ai.ingestion import run_ingestion
from core import paths

from database import (
    get_patient_documents,
    add_document,
    delete_document,
)
from database.clinical import get_pending_suggestion_count
from utils.ui_helpers import append_dialog, pt_scale, show_snack, make_info_button

logger = logging.getLogger(__name__)


def get_documents_view(page: ft.Page):
    # 1. SETUP
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    if not hasattr(page.mrma, "_doc_search_term"):
        page.mrma._doc_search_term = ""

    search_field = ft.TextField(
        value=page.mrma._doc_search_term,
        label="Search Records",
        prefix_icon=ft.Icons.SEARCH,
        width=300,
        dense=True,
    )

    if not hasattr(page.mrma, "_delete_dialog_open"):
        page.mrma._delete_dialog_open = False

    if not hasattr(page.mrma, "_pending_delete"):
        page.mrma._pending_delete = None

    def close_delete_dlg(_=None):
        dlg = page.mrma._delete_dlg
        dlg.open = False
        try:
            dlg.update()
        except Exception:
            pass
        page.mrma._pending_delete = None
        page.mrma._delete_dialog_open = False
        page.update()

    def confirm_delete(_=None):
        pending = page.mrma._pending_delete
        if not pending:
            close_delete_dlg()
            return
        doc_id, _name = pending
        close_delete_dlg()  # close dialog immediately for UX

        # Perform delete synchronously so the table refreshes right away
        try:
            delete_document(page.db_connection, int(doc_id))

            if getattr(page, "content_area", None):
                page.content_area.content = get_documents_view(page)
                page.content_area.update()
            else:
                refresh_table(search_field.value, update_ui=True)
                
            show_snack(page, "Record and file deleted.", "blue")
        except Exception as ex:
            logger.error("Document delete error: %s", ex)
            show_snack(page, f"Delete failed: {ex}", "red")

    if not hasattr(page.mrma, "_delete_dlg") or page.mrma._delete_dlg is None:
        page.mrma._delete_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Confirm Delete"),
            content=ft.Text(""),
            actions=[
                ft.ElevatedButton("Cancel", on_click=close_delete_dlg),
                ft.ElevatedButton("Delete", icon=ft.Icons.DELETE, on_click=confirm_delete),
            ],
            on_dismiss=close_delete_dlg,
        )
        append_dialog(page, page.mrma._delete_dlg)

    all_docs = []

    # 2. FILE SAVING & CONTROLS
    sort_column = 2
    sort_ascending = False

    def sort_table(e: ft.DataColumnSortEvent):
        nonlocal sort_column, sort_ascending
        if sort_column == e.column_index:
            sort_ascending = not sort_ascending
        else:
            sort_column = e.column_index
            sort_ascending = True

        data_table.sort_column_index = sort_column
        data_table.sort_ascending = sort_ascending
        refresh_table(search_field.value, update_ui=True)

    data_table = ft.DataTable(
        sort_column_index=sort_column,
        sort_ascending=sort_ascending,
        columns=[
            ft.DataColumn(ft.Text("Type")),
            ft.DataColumn(ft.Text("File Name")),
            ft.DataColumn(ft.Text("Upload Date"), on_sort=sort_table),
            ft.DataColumn(ft.Text("Visit Date"), on_sort=sort_table),
            ft.DataColumn(ft.Text("Specialty"), on_sort=sort_table),
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
        if not path:
            show_snack(page, "File not found.", "red")
            return
        from core.paths import resolve_doc_path
        resolved = str(resolve_doc_path(path))
        if not os.path.exists(resolved):
            show_snack(page, "File not found.", "red")
            return

        try:
            # Decrypt to a temp PDF for viewing
            fmk = get_or_create_file_master_key(page.db_connection, dmk_raw=page.db_key_raw)
            with open(resolved, "rb") as f:
                ciphertext = f.read()
            plaintext = decrypt_bytes(fmk, ciphertext)

            # Write temp file with original extension
            _, file_ext = os.path.splitext(human_name)
            if not file_ext: file_ext = ".pdf"
            tmp_dir = tempfile.gettempdir()
            tmp_path = os.path.join(tmp_dir, f"mrma_decrypted_{patient_id}_{int(datetime.now().timestamp())}{file_ext}")
            with open(tmp_path, "wb") as f:
                f.write(plaintext)

            open_file_cross_platform(tmp_path)

            show_snack(page, "Opened a temporary decrypted copy (will be cleaned up later).", "orange")
        except InvalidToken:
            show_snack(page, "This file appears to be corrupted or was encrypted with a different key.", "red")
        except Exception as ex:
            logger.error("Document open error: %s", ex)
            show_snack(page, f"Open failed: {ex}", "red")

    def open_doc_click(e: ft.ControlEvent):
        enc_path, human_name = e.control.data
        asyncio.create_task(open_doc_async(enc_path, human_name))

    def delete_handler(e: ft.ControlEvent):
        data = getattr(e.control, "data", None)
        if not data:
            return
        doc_id, name = data

        if getattr(page.mrma, "_delete_dialog_open", False):
            return
        page.mrma._delete_dialog_open = True

        page.mrma._pending_delete = (int(doc_id), str(name))

        dlg = page.mrma._delete_dlg
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
        nonlocal all_docs, sort_column, sort_ascending
        rows: list[ft.DataRow] = []
        ft_filter = (filter_text or "").lower()

        try:
            all_docs = get_patient_documents(page.db_connection, patient_id)
        except Exception:
            all_docs = []

        # Sort all_docs based on selected column
        def sort_key(doc):
            idx_map = {2: 2, 3: 4, 4: 5} # Column index to tuple index: 2=Upload Date, 3=Visit Date, 4=Specialty
            val = doc[idx_map.get(sort_column, 2)]
            return str(val).lower() if val else ""
            
        all_docs.sort(key=sort_key, reverse=not sort_ascending)

        for doc in all_docs:
            try:
                # new get_patient_documents return shape unpack
                doc_id, file_name, upload_date, file_path, visit_date, specialty = doc
            except Exception:
                try:
                    # fallback if schema missing somehow
                    doc_id, file_name, upload_date, file_path = doc
                    visit_date, specialty = None, None
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
                        ft.DataCell(ft.Text(str(visit_date) if visit_date else "")),
                        ft.DataCell(ft.Text(str(specialty) if specialty else "")),
                        ft.DataCell(
                            ft.IconButton(
                                ft.Icons.OPEN_IN_NEW,
                                tooltip="Open Temp Decrypted Copy",
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
                page.update()
            except Exception:
                pass

    def on_search_change(e: ft.ControlEvent):
        page.mrma._doc_search_term = e.control.value
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

        # Destination: user data dir / data / <patient_id> / (via paths module)
        dest_dir = str(paths.data_dir / str(patient_id))
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

            doc_id = add_document(
                page.db_connection,
                patient_id,
                file_name,     # human label
                enc_path,      # encrypted disk path
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            )

            refresh_table(search_field.value, update_ui=True)
            show_snack(page, "Document uploaded securely.", "blue")

            # Run ingestion + candidate matching in background
            _uploaded_doc_id = doc_id
            _uploaded_file_name = file_name
            def _ingest():
                try:
                    run_ingestion(
                        page.db_connection,
                        page.db_key_raw,
                        patient_id,
                        str(paths.data_dir),
                    )
                    show_snack(page, "AI Extraction Complete! Check your Dashboard.", "green")

                    # Refresh the review button in-place on whatever tab is active.
                    # If Overview is showing, update its live button directly.
                    if hasattr(page.mrma, "_refresh_overview_review_btn"):
                        try:
                            page.mrma._refresh_overview_review_btn()
                        except Exception:
                            pass
                    # If user is on a different tab, rebuild that tab so its badge appears too.
                    if hasattr(page.mrma, "_get_view_for_index") and getattr(page, "nav_rail", None) and getattr(page, "content_area", None):
                        try:
                            idx = page.nav_rail.selected_index
                            if idx != 0:  # Overview already updated itself above
                                page.content_area.content = page.mrma._get_view_for_index(idx)
                                page.content_area.update()
                        except Exception as refresh_ex:
                            logger.debug("Auto-refresh failed: %s", refresh_ex)

                except Exception as ex:
                    logger.error("Ingestion error: %s", ex)

                # ── Candidate matching for records requests ───────────────────
                try:
                    from database.records_requests import check_upload_for_matches
                    cur = page.db_connection.cursor()
                    cur.execute(
                        "SELECT parsed_text FROM documents WHERE id=?",
                        (_uploaded_doc_id,),
                    )
                    row = cur.fetchone()
                    parsed_text = row[0] if row else None
                    matched = check_upload_for_matches(
                        page.db_connection,
                        patient_id,
                        doc_id=_uploaded_doc_id,
                        file_name=_uploaded_file_name,
                        parsed_text=parsed_text,
                    )
                    if matched and hasattr(page.mrma, "_refresh_requests_panel"):
                        try:
                            page.mrma._refresh_requests_panel()
                        except Exception:
                            pass
                except Exception as match_ex:
                    logger.debug("Candidate match error: %s", match_ex)

            threading.Thread(target=_ingest, daemon=True).start()

        except Exception as ex:
            logger.error("Upload error: %s", ex)
            show_snack(page, f"Error: {str(ex)}", "red")

    # 7. INITIAL LAYOUT BUILD
    refresh_table(page.mrma._doc_search_term, update_ui=False)

    # --- AI Inbox badge ---
    pending_count = get_pending_suggestion_count(page.db_connection, patient_id)
    review_btn = ft.Container()
    if pending_count > 0:
        from ui.ai_review_dialog import show_ai_review_dialog
        def _open_review(_):
            show_ai_review_dialog(page, patient_id, on_close=lambda: refresh_table(search_field.value, update_ui=True))
        review_btn = ft.FilledButton(
            f"Review Suggestions ({pending_count})",
            icon=ft.Icons.NEW_RELEASES,
            style=ft.ButtonStyle(bgcolor=ft.Colors.ORANGE_600, color=ft.Colors.WHITE),
            on_click=_open_review,
        )

    _info_btn = make_info_button(page, "Medical Records", [
        "Upload any medical document (PDF, image, etc.) using the \"Upload Document\" button.",
        "After uploading, the document is processed in the background. Once complete, an orange \"Review Suggestions\" button will appear on the Overview tab, click it to review and accept extracted health data.",
        "Click a column header (Upload Date, Visit Date, Specialty) to sort the table by that column. Click again to reverse the order.",
        "Documents are encrypted on your device. The Open button decrypts a temporary copy for viewing.",
    ])

    return ft.Container(
        padding=pt_scale(page, 20),
        expand=True,
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("Medical Records", size=pt_scale(page, 24), weight="bold"),
                        ft.Container(expand=True),
                        review_btn,
                        ft.Container(width=pt_scale(page, 10)) if pending_count > 0 else ft.Container(),
                        ft.FilledButton(
                            "Upload Document",
                            icon=ft.Icons.UPLOAD_FILE,
                            on_click=upload_document_click,
                        ),
                        _info_btn,
                    ]
                ),
                ft.Divider(),
                search_field,
                ft.Divider(),
                ft.Column([data_table], scroll=ft.ScrollMode.AUTO, expand=True),
            ],
            expand=True,
        ),
    )
