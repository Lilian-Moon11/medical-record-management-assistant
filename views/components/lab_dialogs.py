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
        modal=True,
        title=page.mrma._lab_result_info_title,
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

    v_parts = []
    if value_text:
        v_parts.append(value_text)
    if value_num is not None:
        v_parts.append(str(value_num))
    value_display = " / ".join(v_parts) if v_parts else ""

    # Source document info
    source_text = "Source: Manual entry"
    if source_document_id:
        try:
            doc_meta = get_document_metadata(page.db_connection, int(source_document_id))
            if doc_meta:
                source_text = f"Source: {doc_meta[0]}"  # file_name
            else:
                source_text = f"Source: Document #{source_document_id}"
        except Exception:
            source_text = f"Source: Document #{source_document_id}"

    page.mrma._lab_result_info_title.value = test_name or f"Result #{result_id}"
    page.mrma._lab_result_info_body.controls = [
        ft.Text(f"Value: {value_display} {unit or ''}".strip()),
        ft.Text(f"Flag: {_flag_result(flag)}"),
        ft.Text(f"Date: {result_date or ''}".strip()),
        ft.Divider(),
        ft.Text(f"Reference range: {rr or '(not provided)'}"),
        ft.Text(f"Notes: {notes or ''}".strip()),
        ft.Divider(),
        ft.Text(source_text, italic=True),
        ft.Text(f"Report ID: {report_id}", size=11,
                 color=ft.Colors.ON_SURFACE_VARIANT
                 if hasattr(ft.Colors, "ON_SURFACE_VARIANT") else None),
    ]

    dlg.open = True
    page.update()


# ----------------------------
# Dialog: Add Lab Data (add a result to an existing or new report)
# ----------------------------
def _populate_report_dropdown(page: ft.Page, patient_id: int):
    """Fill the report dropdown with existing reports."""
    try:
        reports = list_lab_reports(page.db_connection, patient_id, search="", limit=100)
    except Exception:
        reports = []

    options = [ft.dropdown.Option(key="", text="(Create new report)")]
    for r in reports:
        rid, _doc, collected, _reported, provider, facility, _notes, _c, _u = r
        label_parts = []
        if collected:
            label_parts.append(collected)
        if facility:
            label_parts.append(facility)
        if provider:
            label_parts.append(provider)
        label = " - ".join(label_parts) if label_parts else f"Report #{rid}"
        options.append(ft.dropdown.Option(key=str(rid), text=label))

    page.mrma._lx_report_selector.options = options


def _ensure_result_edit_dialog(page: ft.Page, patient_id: int, refresh_callback):
    if getattr(page.mrma, "_lab_result_edit_dlg", None) is not None:
        return page.mrma._lab_result_edit_dlg

    page.mrma._lx_test = ft.TextField(label="Metric / Test name*", autofocus=True)
    page.mrma._lx_value_text = ft.TextField(label="Value (text)*")
    page.mrma._lx_unit = ft.TextField(label="Unit")
    page.mrma._lx_ref_range = ft.TextField(label="Reference range (e.g. 70-130)")
    page.mrma._lx_flag = ft.TextField(label="Abnormal flag (H/L/A/N)")
    page.mrma._lx_date = ft.TextField(label="Result date (YYYY-MM-DD)")
    page.mrma._lx_notes = ft.TextField(label="Notes", multiline=True, min_lines=2, max_lines=4)
    page.mrma._lx_report_selector = ft.Dropdown(
        label="Attach to report",
        width=pt_scale(page, 460),
    )

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
            # Get or create report
            report_id = None
            if page.mrma._lx_report_selector.value:
                report_id = int(page.mrma._lx_report_selector.value)

            result_id = getattr(page.mrma, "_editing_lab_result_id", None)
            report_id_for_edit = getattr(page.mrma, "_editing_lab_result_report_id", None)

            if result_id is not None:
                # Editing existing result
                report_id = report_id_for_edit or report_id

            if not report_id:
                # Create a new report with today's date
                today = date.today().isoformat()
                report_id = create_lab_report(
                    page.db_connection,
                    patient_id,
                    collected_date=today,
                    reported_date=today,
                )

            unit = (page.mrma._lx_unit.value or "").strip() or None
            flag = (page.mrma._lx_flag.value or "").strip() or None
            notes = (page.mrma._lx_notes.value or "").strip() or None
            value_num = _parse_value_num(value_text)

            rdate = (page.mrma._lx_date.value or "").strip() or None
            if not rdate:
                rdate = date.today().isoformat()

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
                show_snack(
                    page,
                    "Result updated." if updated else "Result not found.",
                    "blue" if updated else "orange",
                )

            _close()
            # Refresh test menu and current test view
            if refresh_callback:
                refresh_callback(test_name_val)

        except Exception as ex:
            show_snack(page, f"Save result failed: {ex}", "red")

    page.mrma._lab_result_edit_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Measurement / Result"),
        content=ft.Container(
            width=pt_scale(page, 520),
            content=ft.Column(
                [
                    page.mrma._lx_test,
                    ft.Row([page.mrma._lx_value_text, page.mrma._lx_unit], wrap=True),
                    page.mrma._lx_ref_range,
                    ft.Row([page.mrma._lx_flag, page.mrma._lx_date], wrap=True),
                    page.mrma._lx_notes,
                    page.mrma._lx_report_selector,
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
    dlg.title = ft.Text("Add Lab Data")

    page.mrma._lx_test.value = selected_test
    page.mrma._lx_value_text.value = ""
    page.mrma._lx_unit.value = ""
    page.mrma._lx_ref_range.value = ""
    page.mrma._lx_flag.value = ""
    page.mrma._lx_date.value = ""
    page.mrma._lx_notes.value = ""

    _populate_report_dropdown(page, patient_id)
    page.mrma._lx_report_selector.value = ""

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
    dlg.title = ft.Text("Edit Result")

    page.mrma._lx_test.value = test_name or ""
    page.mrma._lx_value_text.value = value_text or ""
    page.mrma._lx_unit.value = unit or ""
    page.mrma._lx_ref_range.value = ref_range_text or ""
    page.mrma._lx_flag.value = flag or ""
    page.mrma._lx_date.value = result_date or ""
    page.mrma._lx_notes.value = notes or ""

    _populate_report_dropdown(page, patient_id)
    page.mrma._lx_report_selector.value = str(report_id)

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
        title=ft.Text("Confirm Delete"),
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
