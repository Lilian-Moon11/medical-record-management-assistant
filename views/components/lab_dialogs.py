# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import flet as ft
import re
from datetime import date
from utils.ui_helpers import append_dialog, pt_scale, show_snack
from database import (
    list_lab_reports,
    create_lab_report,
    add_lab_result,
    update_lab_result,
    delete_lab_result,
    get_document_metadata,
    get_or_create_report_for_date,
    cleanup_empty_reports,
    list_test_names_for_date,
)
from views.components.lab_helpers import _parse_value_num, _flag_result

# ----------------------------
# Info dialog (coding/details with source doc)
# ----------------------------
def _ensure_result_info_dialog(page: ft.Page):
    if getattr(page.mrma, "_lab_result_info_dlg", None) is not None:
        return page.mrma._lab_result_info_dlg

    page.mrma._lab_result_info_title = ft.Text("Measurement Details", weight="bold")
    page.mrma._lab_result_info_body = ft.Column([], tight=True, scroll=True)

    def _close(_=None):
        page.mrma._lab_result_info_dlg.open = False
        page.update()

    page.mrma._lab_result_info_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Row([
            page.mrma._lab_result_info_title,
            ft.IconButton(ft.Icons.CLOSE, on_click=_close)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        content=ft.Container(
            width=pt_scale(page, 520),
            content=page.mrma._lab_result_info_body,
        ),
        actions=[ft.FilledButton("Close", icon=ft.Icons.CLOSE, on_click=_close)],
        actions_alignment=ft.MainAxisAlignment.END,
        on_dismiss=_close,
    )

    append_dialog(page, page.mrma._lab_result_info_dlg)
    page.update()
    return page.mrma._lab_result_info_dlg


def open_result_info(page: ft.Page, result_row):
    """
    result_row shape (from list_all_results_for_test):
    (result_id, test_name, value_text, value_num, unit, ref_range_text,
     ref_low, ref_high, ref_unit, abnormal_flag, result_date, notes,
     report_id, source_document_id, collected_date, created_at, updated_at)
    """
    (
        result_id, test_name, value_text, value_num, unit,
        ref_range_text, ref_low, ref_high, ref_unit,
        flag, result_date, notes,
        report_id, source_document_id, collected_date,
        _c, _u,
    ) = result_row

    dlg = _ensure_result_info_dialog(page)

    # Build reference range string
    rr = ""
    if ref_range_text:
        rr = ref_range_text
    elif ref_low is not None or ref_high is not None:
        lo = "" if ref_low is None else str(ref_low)
        hi = "" if ref_high is None else str(ref_high)
        ru = ref_unit or unit or ""
        rr = f"{lo} - {hi} {ru}".strip()

    # Build value display — prefer value_text; only show value_num if
    # value_text is absent (avoids "120/80 / 120.0" duplication)
    if value_text:
        value_display = value_text
    elif value_num is not None:
        value_display = str(value_num)
    else:
        value_display = ""

    # Source document info — make it a clickable hyperlink
    source_control: ft.Control
    if source_document_id:
        src_label = None
        try:
            doc_meta = get_document_metadata(page.db_connection, int(source_document_id))
            if doc_meta:
                src_label = doc_meta[0]  # file_name
        except Exception:
            pass
        if not src_label:
            src_label = f"Document #{source_document_id}"

        def _nav_to_doc(e, dname=src_label):
            page.mrma._doc_search_term = dname
            page.go("/documents")

        source_control = ft.Text(
            spans=[
                ft.TextSpan("Source: ", style=ft.TextStyle(italic=True)),
                ft.TextSpan(
                    src_label,
                    style=ft.TextStyle(color=ft.Colors.BLUE),
                    on_click=_nav_to_doc,
                )
            ],
            tooltip=f"View source document: {src_label}"
        )
    else:
        source_control = ft.Text("Source: Manual entry", italic=True)

    # "View all from this date" button
    info_date = result_date or collected_date or ""
    date_controls = []
    if info_date:
        patient = getattr(page, "current_profile", None)
        patient_id = patient[0] if patient else None
        if patient_id:
            try:
                same_date_tests = list_test_names_for_date(
                    page.db_connection, patient_id, info_date,
                    category=page.mrma._labs_category,
                )
            except Exception:
                same_date_tests = []
            if len(same_date_tests) > 1:
                test_list_str = ", ".join(same_date_tests)

                def _view_all_for_date(e, d=info_date, tests=same_date_tests):
                    # Close the current info dialog
                    page.mrma._lab_result_info_dlg.open = False
                    
                    def _close_list(ev=None):
                        list_dlg.open = False
                        page.update()

                    # Build list of tests
                    test_controls = [ft.Text(f"• {t}") for t in tests]
                    
                    list_dlg = ft.AlertDialog(
                        title=ft.Row([
                            ft.Text(f"Tests from {d}"),
                            ft.IconButton(ft.Icons.CLOSE, on_click=_close_list)
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        content=ft.Column(test_controls, tight=True, scroll=True),
                        actions=[ft.FilledButton("Close", on_click=_close_list)],
                        actions_alignment=ft.MainAxisAlignment.END,
                        on_dismiss=_close_list,
                    )
                    append_dialog(page, list_dlg)
                    list_dlg.open = True
                    page.update()

                date_controls.append(
                    ft.TextButton(
                        f"View all from {info_date} ({len(same_date_tests)} tests)",
                        icon=ft.Icons.CALENDAR_MONTH,
                        on_click=_view_all_for_date,
                        tooltip=test_list_str,
                    )
                )

    page.mrma._lab_result_info_title.value = test_name or f"Result #{result_id}"
    body_controls = [
        ft.Text(f"Value: {value_display} {unit or ''}".strip()),
        ft.Text(f"Flag: {_flag_result(flag)}"),
        ft.Text(f"Date: {info_date}".strip()),
        ft.Divider(),
        ft.Text(f"Reference range: {rr or '(not provided)'}"),
        ft.Divider(),
        source_control,
        ft.Text(f"Updated: {_u or 'Unknown'}", size=12, italic=True),
        ft.Text(f"Report ID: {report_id}", size=11,
                 color=ft.Colors.ON_SURFACE_VARIANT
                 if hasattr(ft.Colors, "ON_SURFACE_VARIANT") else None),
    ]
    body_controls.extend(date_controls)
    page.mrma._lab_result_info_body.controls = body_controls

    dlg.open = True
    page.update()


# ----------------------------
# Dialog: Add Lab Data (add a result to an existing or new report)
# ----------------------------
def _ensure_result_edit_dialog(page: ft.Page, patient_id: int, refresh_callback):
    if getattr(page.mrma, "_lab_result_edit_dlg", None) is not None:
        return page.mrma._lab_result_edit_dlg

    page.mrma._lab_result_edit_title = ft.Text("Measurement / Result")
    page.mrma._lx_test = ft.TextField(label="Metric / Test name*", autofocus=True)
    page.mrma._lx_value_text = ft.TextField(label="Value (text)*")
    page.mrma._lx_unit = ft.TextField(label="Unit")
    page.mrma._lx_ref_range = ft.TextField(label="Reference range (e.g. 70-130)")
    page.mrma._lx_flag = ft.TextField(label="Abnormal flag (H/L/A/N)")
    page.mrma._lx_date = ft.TextField(label="Result date (YYYY-MM-DD)")
    page.mrma._lx_notes = ft.TextField(label="Notes", multiline=True, min_lines=2, max_lines=4)

    def _close(_=None):
        page.mrma._lab_result_edit_dlg.open = False
        page.update()

    def _save(_=None):
        test_name_val = (page.mrma._lx_test.value or "").strip()
        value_text = (page.mrma._lx_value_text.value or "").strip()

        if not test_name_val:
            show_snack(page, "Test name is required.", "red")
            return
        if not value_text:
            show_snack(page, "Value is required.", "red")
            return

        try:
            result_id = getattr(page.mrma, "_editing_lab_result_id", None)
            old_report_id = getattr(page.mrma, "_editing_lab_result_report_id", None)

            unit = (page.mrma._lx_unit.value or "").strip() or None
            flag = (page.mrma._lx_flag.value or "").strip() or None
            notes = (page.mrma._lx_notes.value or "").strip() or None
            value_num = _parse_value_num(value_text)

            rdate = (page.mrma._lx_date.value or "").strip() or None
            if not rdate:
                rdate = date.today().isoformat()

            # Auto-resolve report by date
            report_id = get_or_create_report_for_date(
                page.db_connection, patient_id, rdate
            )

            # Parse ref range text into low/high
            ref_range_raw = (page.mrma._lx_ref_range.value or "").strip() or None
            ref_low = None
            ref_high = None
            if ref_range_raw:
                m = re.match(r"([\d.]+)\s*[-–]\s*([\d.]+)", ref_range_raw)
                if m:
                    try:
                        ref_low = float(m.group(1))
                        ref_high = float(m.group(2))
                    except ValueError:
                        pass

            if result_id is None:
                new_id = add_lab_result(
                    page.db_connection,
                    patient_id,
                    int(report_id),
                    test_name=test_name_val,
                    value_text=value_text,
                    value_num=value_num,
                    unit=unit,
                    ref_range_text=ref_range_raw,
                    ref_low=ref_low,
                    ref_high=ref_high,
                    ref_unit=None,
                    abnormal_flag=flag,
                    result_date=rdate,
                    notes=notes,
                    category=page.mrma._labs_category,
                )
                show_snack(page, f"Result added (#{new_id}).", "blue")
            else:
                updated = update_lab_result(
                    page.db_connection,
                    patient_id,
                    int(report_id),
                    int(result_id),
                    test_name=test_name_val,
                    value_text=value_text,
                    value_num=value_num,
                    unit=unit,
                    ref_range_text=ref_range_raw,
                    ref_low=ref_low,
                    ref_high=ref_high,
                    ref_unit=None,
                    abnormal_flag=flag,
                    result_date=rdate,
                    notes=notes,
                    category=page.mrma._labs_category,
                )
                # If the report changed (date was edited), update the result's report_id
                if old_report_id and int(old_report_id) != int(report_id):
                    page.db_connection.execute(
                        "UPDATE lab_results SET report_id = ? WHERE id = ? AND patient_id = ?",
                        (report_id, result_id, patient_id),
                    )
                    page.db_connection.commit()

                show_snack(
                    page,
                    "Result updated." if updated else "Result not found.",
                    "blue" if updated else "orange",
                )

            # Clean up any orphaned reports
            cleanup_empty_reports(page.db_connection, patient_id)

            _close()
            # Refresh test menu and current test view
            if refresh_callback:
                refresh_callback(test_name_val)

        except Exception as ex:
            show_snack(page, f"Save result failed: {ex}", "red")

    page.mrma._lx_test.on_submit = _save
    page.mrma._lx_value_text.on_submit = _save
    page.mrma._lx_unit.on_submit = _save
    page.mrma._lx_ref_range.on_submit = _save
    page.mrma._lx_flag.on_submit = _save
    page.mrma._lx_date.on_submit = _save

    page.mrma._lab_result_edit_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Row([
            page.mrma._lab_result_edit_title,
            ft.IconButton(ft.Icons.CLOSE, on_click=_close)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        content=ft.Container(
            width=pt_scale(page, 520),
            content=ft.Column(
                [
                    page.mrma._lx_test,
                    ft.Row([page.mrma._lx_value_text, page.mrma._lx_unit], wrap=True),
                    page.mrma._lx_ref_range,
                    ft.Row([page.mrma._lx_flag, page.mrma._lx_date], wrap=True),
                    page.mrma._lx_notes,
                ],
                tight=True,
                scroll=True,
            ),
        ),
        actions=[
            ft.TextButton("Cancel", on_click=_close),
            ft.FilledButton("Save", icon=ft.Icons.SAVE, on_click=_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        on_dismiss=_close,
    )

    append_dialog(page, page.mrma._lab_result_edit_dlg)
    page.update()
    return page.mrma._lab_result_edit_dlg


def open_add_lab_data(page: ft.Page, patient_id: int, refresh_callback):
    selected_test = page.mrma._labs_selected_test_name or ""

    page.mrma._editing_lab_result_id = None
    page.mrma._editing_lab_result_report_id = None

    dlg = _ensure_result_edit_dialog(page, patient_id, refresh_callback)
    page.mrma._lab_result_edit_title.value = "Add Lab Data"

    page.mrma._lx_test.value = selected_test
    page.mrma._lx_value_text.value = ""
    page.mrma._lx_unit.value = ""
    page.mrma._lx_ref_range.value = ""
    page.mrma._lx_flag.value = ""
    page.mrma._lx_date.value = ""
    page.mrma._lx_notes.value = ""

    dlg.open = True
    page.update()


def open_edit_result(page: ft.Page, result_row, patient_id: int, refresh_callback):
    """Open the edit dialog pre-populated with existing values."""
    (
        result_id, test_name, value_text, value_num, unit,
        ref_range_text, ref_low, ref_high, ref_unit,
        flag, result_date, notes,
        report_id, source_document_id, collected_date,
        _c, _u,
    ) = result_row

    page.mrma._editing_lab_result_id = int(result_id)
    page.mrma._editing_lab_result_report_id = int(report_id)

    dlg = _ensure_result_edit_dialog(page, patient_id, refresh_callback)
    page.mrma._lab_result_edit_title.value = "Edit Result"

    page.mrma._lx_test.value = test_name or ""
    page.mrma._lx_value_text.value = value_text or ""
    page.mrma._lx_unit.value = unit or ""
    page.mrma._lx_ref_range.value = ref_range_text or ""
    page.mrma._lx_flag.value = flag or ""
    page.mrma._lx_date.value = result_date or ""
    page.mrma._lx_notes.value = notes or ""

    dlg.open = True
    page.update()


# ----------------------------
# Dialog: Confirm Delete Result
# ----------------------------
def _ensure_result_delete_dialog(page: ft.Page, patient_id: int, refresh_callback):
    if getattr(page.mrma, "_lab_result_delete_dlg", None) is not None:
        return page.mrma._lab_result_delete_dlg

    page.mrma._lab_result_delete_text = ft.Text("")

    def _close(_=None):
        page.mrma._lab_result_delete_dlg.open = False
        page.mrma._pending_lab_result_delete = None
        page.update()

    def _confirm(_=None):
        pending = page.mrma._pending_lab_result_delete
        if not pending:
            _close()
            return

        result_id, _label = pending
        try:
            deleted = delete_lab_result(page.db_connection, patient_id, int(result_id))
            _close()

            # Clean up orphaned reports after deletion
            cleanup_empty_reports(page.db_connection, patient_id)

            # Refresh current view
            if refresh_callback:
                refresh_callback(None)

            if deleted:
                show_snack(page, "Result deleted.", "blue")
            else:
                show_snack(page, "Result not found.", "orange")
        except Exception as ex:
            show_snack(page, f"Delete result failed: {ex}", "red")

    page.mrma._lab_result_delete_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Row([
            ft.Text("Confirm Delete"),
            ft.IconButton(ft.Icons.CLOSE, on_click=_close)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        content=page.mrma._lab_result_delete_text,
        actions=[
            ft.TextButton("Cancel", on_click=_close),
            ft.FilledButton("Delete", icon=ft.Icons.DELETE, on_click=_confirm),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        on_dismiss=_close,
    )

    append_dialog(page, page.mrma._lab_result_delete_dlg)
    page.update()
    return page.mrma._lab_result_delete_dlg


def open_delete_result(page: ft.Page, result_id: int, label: str, patient_id: int, refresh_callback):
    page.mrma._pending_lab_result_delete = (int(result_id), label or "")
    dlg = _ensure_result_delete_dialog(page, patient_id, refresh_callback)
    page.mrma._lab_result_delete_text.value = f'Delete result "{label}"?'
    dlg.open = True
    page.update()
