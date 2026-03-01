# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Labs (Reports + Results)
#
# Local CRUD UI for lab reports and their lab results scoped to patient_id.
# - Reports: Search (LIKE), Add/Edit via stable overlay dialog, Delete w/ confirm + snack
# - Results: View results for a selected report, optional test-name search,
#            Add/Edit via stable overlay dialog, Delete w/ confirm + snack
#
# Design notes (Flet mounting safety):
# - Do NOT call control.update() during initial view construction (before mount).
# - Build initial DataTable rows "quietly" and rely on page re-render.
# - Dialogs are created once and appended to page.overlay once.
# -----------------------------------------------------------------------------

from __future__ import annotations
import flet as ft
import re
from datetime import date
from utils.ui_helpers import show_snack, themed_panel, s
from database import (
    # Reports
    list_lab_reports,
    create_lab_report,
    update_lab_report,
    delete_lab_report,
    # Results
    list_lab_results_for_report,
    add_lab_result,
    update_lab_result,
    delete_lab_result,
)


def get_labs_view(page: ft.Page):
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    # ----------------------------
    # Stable state holders
    # ----------------------------
    if not hasattr(page, "_labs_selected_report_id"):
        page._labs_selected_report_id = None  # int | None

    if not hasattr(page, "_editing_lab_report_id"):
        page._editing_lab_report_id = None  # None=new, int=edit

    if not hasattr(page, "_editing_lab_result_id"):
        page._editing_lab_result_id = None  # None=new, int=edit
    if not hasattr(page, "_editing_lab_result_report_id"):
        page._editing_lab_result_report_id = None  # report_id for result add/edit

    if not hasattr(page, "_pending_lab_report_delete"):
        page._pending_lab_report_delete = None  # (report_id, label)
    if not hasattr(page, "_pending_lab_result_delete"):
        page._pending_lab_result_delete = None  # (result_id, label)
    if not hasattr(page, "_labs_report_cache"):
        page._labs_report_cache = {}  # report_id -> report_row

    # ----------------------------
    # Reports table (created early)
    # ----------------------------
    reports_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Collected")),
            ft.DataColumn(ft.Text("Facility")),
            ft.DataColumn(ft.Text("Ordering Provider")),
            ft.DataColumn(ft.Text("Notes")),
            ft.DataColumn(ft.Text("Edit")),
            ft.DataColumn(ft.Text("Results")),
            ft.DataColumn(ft.Text("Delete")),
        ],
        rows=[],
        column_spacing=s(page, 14),
        heading_row_height=s(page, 40),
        data_row_min_height=s(page, 40),
        data_row_max_height=s(page, 56),
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
        if hasattr(ft.Colors, "OUTLINE_VARIANT") else None,
        border_radius=8,
    )

    reports_container = ft.Container(content=reports_table, expand=True)

    # ----------------------------
    # Results table (created early)
    # ----------------------------
    results_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Test")),
            ft.DataColumn(ft.Text("Value")),
            ft.DataColumn(ft.Text("Unit")),
            ft.DataColumn(ft.Text("")),      
            ft.DataColumn(ft.Text("Date")),
            ft.DataColumn(ft.Text("Info")),   
            ft.DataColumn(ft.Text("Edit")),
            ft.DataColumn(ft.Text("Delete")),
        ],
        rows=[],
        column_spacing=s(page, 14),
        heading_row_height=s(page, 40),
        data_row_min_height=s(page, 40),
        data_row_max_height=s(page, 56),
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
        if hasattr(ft.Colors, "OUTLINE_VARIANT") else None,
        border_radius=8,
    )

    results_container = ft.Container(content=results_table, expand=True)

    # ----------------------------
    # Helpers: report/result labels
    # ----------------------------
    def _report_label(r):
        # r: (id, source_document_id, collected_date, reported_date, ordering_provider, facility, notes, created_at, updated_at)
        _id, _doc, collected, reported, provider, facility, notes, _c, _u = r
        parts = []
        if collected:
            parts.append(collected)
        if facility:
            parts.append(facility)
        if provider:
            parts.append(provider)
        if not parts and reported:
            parts.append(reported)
        if not parts and notes:
            parts.append((notes or "")[:30])
        return " - ".join(parts) if parts else f"Report #{_id}"

    def _result_label(x):
        # x: (id, test_name, value_text, value_num, unit, ref_range_text, ref_low, ref_high, ref_unit, abnormal_flag, result_date, notes, created_at, updated_at)
        rid, test_name, value_text, _vn, unit, *_rest = x
        name = test_name or f"Result #{rid}"
        val = value_text or ""
        u = unit or ""
        return f"{name}: {val} {u}".strip()

    def _parse_value_num(value_text: str | None) -> float | None:
        """
        Extract a numeric value from user-entered lab text when it's clearly numeric.
        Keeps things like '<5', '>200', 'NEG', 'trace' as non-numeric (None) for now.
        """
        if not value_text:
            return None

        t = value_text.strip()
        if not t:
            return None

        # Comparators / qualitative values -> leave as text-only for now
        if any(sym in t for sym in ("<", ">", "<=", ">=")):
            return None

        m = re.search(r"[-+]?\d[\d,]*\.?\d*", t)
        if not m:
            return None

        try:
            return float(m.group(0).replace(",", ""))
        except Exception:
            return None
    
    def _flag_result(flag: str | None) -> str:
        if not flag:
            return "Normal / not flagged"
        f = flag.strip().upper()
        return {
            "H": "High",
            "L": "Low",
            "A": "Abnormal",
            "N": "Normal",
        }.get(f, flag)

    def _ensure_result_info_dialog():
        if getattr(page, "_lab_result_info_dlg", None) is not None:
            return page._lab_result_info_dlg

        page._lab_result_info_title = ft.Text("Lab Result Details", weight="bold")
        page._lab_result_info_body = ft.Column([], tight=True, scroll=True)

        def _close(_=None):
            page._lab_result_info_dlg.open = False
            page.update()

        page._lab_result_info_dlg = ft.AlertDialog(
            modal=True,
            title=page._lab_result_info_title,
            content=ft.Container(
                width=s(page, 520),
                content=page._lab_result_info_body,
            ),
            actions=[ft.FilledButton("Close", icon=ft.Icons.CLOSE, on_click=_close)],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close,
        )

        page.overlay.append(page._lab_result_info_dlg)
        page.update()
        return page._lab_result_info_dlg


    def open_result_info(result_row):
        """
        result_row shape:
        (id, test_name, value_text, value_num, unit, ref_range_text, ref_low, ref_high,
         ref_unit, abnormal_flag, result_date, notes, created_at, updated_at)
        """
        (
            result_id,
            test_name,
            value_text,
            value_num,
            unit,
            ref_range_text,
            ref_low,
            ref_high,
            ref_unit,
            flag,
            result_date,
            notes,
            _c,
            _u,
        ) = result_row

        dlg = _ensure_result_info_dialog()

        # Build a clean reference range string
        rr = ""
        if ref_range_text:
            rr = ref_range_text
        elif ref_low is not None or ref_high is not None:
            lo = "" if ref_low is None else str(ref_low)
            hi = "" if ref_high is None else str(ref_high)
            ru = ref_unit or unit or ""
            rr = f"{lo} - {hi} {ru}".strip()

        # Prefer value_text, but show numeric too if present
        v_parts = []
        if value_text:
            v_parts.append(value_text)
        if value_num is not None:
            v_parts.append(str(value_num))
        value_display = " / ".join(v_parts) if v_parts else ""

        page._lab_result_info_title.value = test_name or f"Result #{result_id}"
        page._lab_result_info_body.controls = [
            ft.Text(f"Value: {value_display} {unit or ''}".strip()),
            ft.Text(f"Flag: {_flag_result(flag)}"),
            ft.Text(f"Date: {result_date or ''}".strip()),
            ft.Divider(),
            ft.Text(f"Reference range: {rr or '(not provided)'}"),
            ft.Text(f"Notes: {notes or ''}".strip()),
        ]

        dlg.open = True
        page.update()

    def _abnormal_icon(flag: str | None) -> ft.Control:
        if not flag:
            return ft.Container(width=s(page, 24))

        f = flag.strip().upper()

        if f == "H":
            return ft.Icon(ft.Icons.TRENDING_UP, tooltip="High", color="red")
        if f == "L":
            return ft.Icon(ft.Icons.TRENDING_DOWN, tooltip="Low", color="red")
        if f == "A":
            return ft.Icon(ft.Icons.REPORT_PROBLEM_OUTLINED, tooltip="Abnormal", color="red")

        # N or anything else -> show nothing (keeps alignment)
        return ft.Container(width=s(page, 24))

    # ----------------------------
    # Build rows (no update calls)
    # ----------------------------
    def _build_report_rows(rows):
        reports_table.rows = []
        for r in rows:
            # r: (id, source_document_id, collected_date, reported_date, ordering_provider, facility, notes, created_at, updated_at)
            report_id, _doc_id, collected, _reported, ordering_provider, facility, notes, _c, _u = r

            # Cache the full row so we can look up collected/reported dates later
            page._labs_report_cache[int(report_id)] = r

            reports_table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(collected or "")),
                        ft.DataCell(ft.Text(facility or "")),
                        ft.DataCell(ft.Text(ordering_provider or "")),
                        ft.DataCell(ft.Text((notes or "")[:60])),

                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.EDIT,
                                tooltip="Edit report",
                                on_click=lambda e, rr=r: open_edit_report(rr),
                            )
                        ),

                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.LIST_ALT,
                                tooltip="View results",
                                on_click=lambda e, rid=int(r[0]): select_report(rid),
                            )
                        ),

                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.DELETE,
                                tooltip="Delete report",
                                on_click=lambda e, rid=int(r[0]), rr=r: open_delete_report(
                                    rid, _report_label(rr)
                                ),
                            )
                        ),
                    ]
                )
            )

    def _build_result_rows(rows):
        results_table.rows = []
        for x in rows:
            # x: (id, test_name, value_text, value_num, unit, ref_range_text, ref_low, ref_high, ref_unit, abnormal_flag, result_date, notes, created_at, updated_at)
            result_id, test_name, value_text, _vn, unit, _rrt, _rl, _rh, _ru, flag, result_date, _notes, _c, _u = x

            results_table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(test_name or "")),
                        ft.DataCell(ft.Text(value_text or "")),
                        ft.DataCell(ft.Text(unit or "")),
                        ft.DataCell(
                            ft.Container(
                                content=_abnormal_icon(flag),
                                alignment=ft.Alignment(0, 0),
                            )
                        ),
                        ft.DataCell(ft.Text(result_date or "")),
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.INFO_OUTLINE,
                                tooltip="View details",
                                on_click=lambda e, xx=x: open_result_info(xx),
                            )
                        ),
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.EDIT,
                                tooltip="Edit result",
                                on_click=lambda e, xx=x: open_edit_result(xx),
                            )
                        ),
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.DELETE,
                                tooltip="Delete result",
                                on_click=lambda e, xid=int(result_id), xx=x: open_delete_result(xid, _result_label(xx)),
                            )
                        ),
                    ]
                )
            )

    # ----------------------------
    # Refresh functions (safe updates)
    # ----------------------------
    def refresh_reports(search_text: str | None = None):
        try:
            rows = list_lab_reports(page.db_connection, patient_id, search=search_text, limit=500)
        except Exception as ex:
            show_snack(page, f"Load reports failed: {ex}", "red")
            rows = []

        _build_report_rows(rows)

        try:
            reports_table.update()
            page.update()
        except Exception:
            pass

    def refresh_results(report_id: int, search_test: str | None = None):
        if not report_id:
            results_table.rows = []
            try:
                results_table.update()
                page.update()
            except Exception:
                pass
            return

        try:
            rows = list_lab_results_for_report(
                page.db_connection,
                patient_id,
                int(report_id),
                search_test=search_test,
                limit=500,
            )
        except Exception as ex:
            show_snack(page, f"Load results failed: {ex}", "red")
            rows = []

        _build_result_rows(rows)

        try:
            results_table.update()
            page.update()
        except Exception:
            pass

    # ----------------------------
    # Selection + Results header
    # ----------------------------
    selected_report_text = ft.Text("Select a report to see results.", italic=True)
    results_header_row = ft.Row(
        [
            ft.Text("Results", size=18, weight="bold"),
            ft.Container(expand=True),
            ft.FilledButton(
                "Add Result",
                icon=ft.Icons.ADD,
                on_click=lambda e: open_new_result_for_selected(),
                disabled=True,  # enabled only when a report is selected
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    def _set_results_enabled(enabled: bool):
        results_header_row.controls[2].disabled = not enabled
        try:
            results_header_row.update()
            page.update()
        except Exception:
            pass

    def select_report(report_id: int):
        page._labs_selected_report_id = int(report_id)
        selected_report_text.value = f"Selected report: #{report_id}"
        _set_results_enabled(True)

        # Always repaint from DB first (unfiltered)
        refresh_results(int(report_id), "")

        # Then apply filter if user typed one
        if (results_search_field.value or "").strip():
            refresh_results(int(report_id), results_search_field.value)

        try:
            selected_report_text.update()
            page.update()
        except Exception:
            pass

    # ----------------------------
    # Search controls: Reports
    # ----------------------------
    def do_reports_search(_=None):
        refresh_reports(reports_search_field.value)

    reports_search_field = ft.TextField(
        label="Search reports (facility, provider, notes)",
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        width=s(page, 420),
        on_submit=do_reports_search,
    )

    reports_search_btn = ft.FilledButton("Search", icon=ft.Icons.SEARCH, on_click=do_reports_search)

    def do_reports_clear(_=None):
        reports_search_field.value = ""
        try:
            reports_search_field.update()
        except Exception:
            pass
        refresh_reports("")

    reports_clear_btn = ft.OutlinedButton("Clear", icon=ft.Icons.CLOSE, on_click=do_reports_clear)

    # ----------------------------
    # Search controls: Results
    # ----------------------------
    def do_results_search(_=None):
        rid = getattr(page, "_labs_selected_report_id", None)
        if not rid:
            show_snack(page, "Select a report first.", "orange")
            return
        refresh_results(int(rid), results_search_field.value)

    results_search_field = ft.TextField(
        label="Search results by test name",
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        width=s(page, 340),
        on_submit=do_results_search,
    )

    results_search_btn = ft.FilledButton("Search", icon=ft.Icons.SEARCH, on_click=do_results_search)

    def do_results_clear(_=None):
        results_search_field.value = ""
        try:
            results_search_field.update()
        except Exception:
            pass
        rid = getattr(page, "_labs_selected_report_id", None)
        if rid:
            refresh_results(int(rid), "")

    results_clear_btn = ft.OutlinedButton("Clear", icon=ft.Icons.CLOSE, on_click=do_results_clear)

    # ----------------------------
    # Dialog: Add/Edit Report
    # ----------------------------
    def _ensure_report_edit_dialog():
        if getattr(page, "_lab_report_edit_dlg", None) is not None:
            return page._lab_report_edit_dlg

        # Fields (created once)
        page._lr_collected = ft.TextField(label="Collected date (YYYY-MM-DD)")
        page._lr_reported = ft.TextField(label="Reported date (YYYY-MM-DD)")
        page._lr_provider = ft.TextField(label="Ordering provider")
        page._lr_facility = ft.TextField(label="Facility")
        page._lr_notes = ft.TextField(label="Notes", multiline=True, min_lines=2, max_lines=4)

        def _close(_=None):
            page._lab_report_edit_dlg.open = False
            page.update()

        def _save(_=None):
            try:
                rid = getattr(page, "_editing_lab_report_id", None)
                collected = (page._lr_collected.value or "").strip() or None
                reported = (page._lr_reported.value or "").strip() or None
                provider = (page._lr_provider.value or "").strip() or None
                facility = (page._lr_facility.value or "").strip() or None
                notes = (page._lr_notes.value or "").strip() or None

                if rid is None:
                    new_id = create_lab_report(
                        page.db_connection,
                        patient_id,
                        source_document_id=None,
                        collected_date=collected,
                        reported_date=reported,
                        ordering_provider=provider,
                        facility=facility,
                        notes=notes,
                    )
                    show_snack(page, f"Lab report added (#{new_id}).", "blue")
                else:
                    updated = update_lab_report(
                        page.db_connection,
                        patient_id,
                        int(rid),
                        source_document_id=None,
                        collected_date=collected,
                        reported_date=reported,
                        ordering_provider=provider,
                        facility=facility,
                        notes=notes,
                    )
                    show_snack(
                        page,
                        "Lab report updated." if updated else "Lab report not found.",
                        "blue" if updated else "orange",
                    )

                _close()
                refresh_reports(reports_search_field.value)

            except Exception as ex:
                show_snack(page, f"Save report failed: {ex}", "red")

        page._lab_report_edit_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Lab Report"),
            content=ft.Container(
                width=s(page, 520),
                content=ft.Column(
                    [
                        ft.Row([page._lr_collected, page._lr_reported], wrap=True),
                        page._lr_provider,
                        page._lr_facility,
                        page._lr_notes,
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

        page.overlay.append(page._lab_report_edit_dlg)
        page.update()
        return page._lab_report_edit_dlg

    def open_new_report(_=None):
        page._editing_lab_report_id = None
        dlg = _ensure_report_edit_dialog()
        dlg.title = ft.Text("Add Lab Report")

        page._lr_collected.value = ""
        page._lr_reported.value = ""
        page._lr_provider.value = ""
        page._lr_facility.value = ""
        page._lr_notes.value = ""

        dlg.open = True
        page.update()

    def open_edit_report(report_row):
        # (id, source_document_id, collected_date, reported_date, ordering_provider, facility, notes, created_at, updated_at)
        rid, _doc, collected, reported, provider, facility, notes, _c, _u = report_row
        page._editing_lab_report_id = int(rid)

        dlg = _ensure_report_edit_dialog()
        dlg.title = ft.Text("Edit Lab Report")

        page._lr_collected.value = collected or ""
        page._lr_reported.value = reported or ""
        page._lr_provider.value = provider or ""
        page._lr_facility.value = facility or ""
        page._lr_notes.value = notes or ""

        dlg.open = True
        page.update()

    # ----------------------------
    # Dialog: Confirm Delete Report (confirmation + snack)
    # ----------------------------
    def _ensure_report_delete_dialog():
        if getattr(page, "_lab_report_delete_dlg", None) is not None:
            return page._lab_report_delete_dlg

        page._lab_report_delete_text = ft.Text("")

        def _close(_=None):
            page._lab_report_delete_dlg.open = False
            page._pending_lab_report_delete = None
            page.update()

        def _confirm(_=None):
            pending = page._pending_lab_report_delete
            if not pending:
                _close()
                return

            report_id, _label = pending
            try:
                deleted = delete_lab_report(page.db_connection, patient_id, int(report_id))
                _close()
                refresh_reports(reports_search_field.value)

                # If the selected report was deleted, clear selection/results
                if getattr(page, "_labs_selected_report_id", None) == int(report_id):
                    page._labs_selected_report_id = None
                    selected_report_text.value = "Select a report to see results."
                    _set_results_enabled(False)
                    results_table.rows = []
                    try:
                        results_table.update()
                        selected_report_text.update()
                        page.update()
                    except Exception:
                        pass

                if deleted:
                    show_snack(page, "Lab report deleted.", "blue")
                else:
                    show_snack(page, "Lab report not found.", "orange")
            except Exception as ex:
                show_snack(page, f"Delete report failed: {ex}", "red")

        page._lab_report_delete_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Confirm Delete"),
            content=page._lab_report_delete_text,
            actions=[
                ft.TextButton("Cancel", on_click=_close),
                ft.FilledButton("Delete", icon=ft.Icons.DELETE, on_click=_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close,
        )

        page.overlay.append(page._lab_report_delete_dlg)
        page.update()
        return page._lab_report_delete_dlg

    def open_delete_report(report_id: int, label: str):
        page._pending_lab_report_delete = (int(report_id), label or "")
        dlg = _ensure_report_delete_dialog()
        page._lab_report_delete_text.value = f'Delete lab report "{label}"?\n\nThis will also delete all results in that report.'
        dlg.open = True
        page.update()

    # ----------------------------
    # Dialog: Add/Edit Result
    # ----------------------------
    def _ensure_result_edit_dialog():
        if getattr(page, "_lab_result_edit_dlg", None) is not None:
            return page._lab_result_edit_dlg

        page._lx_test = ft.TextField(label="Test name*", autofocus=True)
        page._lx_value_text = ft.TextField(label="Value (text)*")  # <-- YOU NEED THIS
        page._lx_unit = ft.TextField(label="Unit")
        page._lx_flag = ft.TextField(label="Abnormal flag (H/L/A/N)")
        page._lx_date = ft.TextField(label="Result date (YYYY-MM-DD)")
        page._lx_notes = ft.TextField(label="Notes", multiline=True, min_lines=2, max_lines=4)

        def _close(_=None):
            page._lab_result_edit_dlg.open = False
            page.update()

        def _default_result_date_for_report(report_id: int) -> str:
            cached = page._labs_report_cache.get(int(report_id))
            if cached:
                _id, _doc, collected, reported, *_rest = cached
                d = (collected or reported or "").strip()
                if d:
                    return d
            return date.today().isoformat()

        def _save(_=None):
            test_name = (page._lx_test.value or "").strip()
            value_text = (page._lx_value_text.value or "").strip()

            if not test_name:
                show_snack(page, "Test name is required.", "red")
                return
            if not value_text:
                show_snack(page, "Value is required.", "red")
                return

            try:
                report_id = getattr(page, "_editing_lab_result_report_id", None)
                if not report_id:
                    show_snack(page, "Select a report first.", "orange")
                    return

                result_id = getattr(page, "_editing_lab_result_id", None)

                unit = (page._lx_unit.value or "").strip() or None
                flag = (page._lx_flag.value or "").strip() or None
                notes = (page._lx_notes.value or "").strip() or None

                # Trend-friendly numeric extraction
                value_num = _parse_value_num(value_text)

                # Trend-friendly date defaulting
                rdate = (page._lx_date.value or "").strip() or None
                if not rdate:
                    rdate = _default_result_date_for_report(int(report_id))

                if result_id is None:
                    new_id = add_lab_result(
                        page.db_connection,
                        patient_id,
                        int(report_id),
                        test_name=test_name,
                        value_text=value_text,
                        value_num=value_num,
                        unit=unit,
                        ref_range_text=None,
                        ref_low=None,
                        ref_high=None,
                        ref_unit=None,
                        abnormal_flag=flag,
                        result_date=rdate,
                        notes=notes,
                    )
                    show_snack(page, f"Result added (#{new_id}).", "blue")
                else:
                    updated = update_lab_result(
                        page.db_connection,
                        patient_id,
                        int(report_id),
                        int(result_id),
                        test_name=test_name,
                        value_text=value_text,
                        value_num=value_num,  # <-- keep numeric data on edits
                        unit=unit,
                        ref_range_text=None,
                        ref_low=None,
                        ref_high=None,
                        ref_unit=None,
                        abnormal_flag=flag,
                        result_date=rdate,
                        notes=notes,
                    )
                    show_snack(page, "Result updated." if updated else "Result not found.", "blue" if updated else "orange")

                _close()
                select_report(int(report_id))

            except Exception as ex:
                show_snack(page, f"Save result failed: {ex}", "red")

        page._lab_result_edit_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Lab Result"),
            content=ft.Container(
                width=s(page, 520),
                content=ft.Column(
                    [
                        page._lx_test,
                        ft.Row([page._lx_value_text, page._lx_unit], wrap=True),
                        ft.Row([page._lx_flag, page._lx_date], wrap=True),
                        page._lx_notes,
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

        page.overlay.append(page._lab_result_edit_dlg)
        page.update()
        return page._lab_result_edit_dlg

    def open_new_result_for_selected(_=None):
        rid = getattr(page, "_labs_selected_report_id", None)
        if not rid:
            show_snack(page, "Select a report first.", "orange")
            return

        page._editing_lab_result_id = None
        page._editing_lab_result_report_id = int(rid)

        dlg = _ensure_result_edit_dialog()
        dlg.title = ft.Text("Add Result")

        page._lx_test.value = ""
        page._lx_value_text.value = ""
        page._lx_unit.value = ""
        page._lx_flag.value = ""
        page._lx_date.value = ""
        page._lx_notes.value = ""

        dlg.open = True
        page.update()

    def open_edit_result(result_row):
        # (id, test_name, value_text, value_num, unit, ref_range_text, ref_low, ref_high, ref_unit, abnormal_flag, result_date, notes, created_at, updated_at)
        rid = getattr(page, "_labs_selected_report_id", None)
        if not rid:
            show_snack(page, "Select a report first.", "orange")
            return

        result_id, test_name, value_text, _vn, unit, _rrt, _rl, _rh, _ru, flag, rdate, notes, _c, _u = result_row
        page._editing_lab_result_id = int(result_id)
        page._editing_lab_result_report_id = int(rid)

        dlg = _ensure_result_edit_dialog()
        dlg.title = ft.Text("Edit Result")

        page._lx_test.value = test_name or ""
        page._lx_value_text.value = value_text or ""
        page._lx_unit.value = unit or ""
        page._lx_flag.value = flag or ""
        page._lx_date.value = rdate or ""
        page._lx_notes.value = notes or ""

        dlg.open = True
        page.update()

    # ----------------------------
    # Dialog: Confirm Delete Result (confirmation + snack)
    # ----------------------------
    def _ensure_result_delete_dialog():
        if getattr(page, "_lab_result_delete_dlg", None) is not None:
            return page._lab_result_delete_dlg

        page._lab_result_delete_text = ft.Text("")

        def _close(_=None):
            page._lab_result_delete_dlg.open = False
            page._pending_lab_result_delete = None
            page.update()

        def _confirm(_=None):
            pending = page._pending_lab_result_delete
            if not pending:
                _close()
                return

            result_id, _label = pending
            try:
                deleted = delete_lab_result(page.db_connection, patient_id, int(result_id))
                _close()

                rid = getattr(page, "_labs_selected_report_id", None)
                if rid:
                    refresh_results(int(rid), results_search_field.value)

                if deleted:
                    show_snack(page, "Result deleted.", "blue")
                else:
                    show_snack(page, "Result not found.", "orange")
            except Exception as ex:
                show_snack(page, f"Delete result failed: {ex}", "red")

        page._lab_result_delete_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Confirm Delete"),
            content=page._lab_result_delete_text,
            actions=[
                ft.TextButton("Cancel", on_click=_close),
                ft.FilledButton("Delete", icon=ft.Icons.DELETE, on_click=_confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=_close,
        )

        page.overlay.append(page._lab_result_delete_dlg)
        page.update()
        return page._lab_result_delete_dlg

    def open_delete_result(result_id: int, label: str):
        page._pending_lab_result_delete = (int(result_id), label or "")
        dlg = _ensure_result_delete_dialog()
        page._lab_result_delete_text.value = f'Delete result "{label}"?'
        dlg.open = True
        page.update()

    # ----------------------------
    # Initial load (quiet)
    # ----------------------------
    try:
        initial_reports = list_lab_reports(page.db_connection, patient_id, search="", limit=500)
    except Exception as ex:
        show_snack(page, f"Load reports failed: {ex}", "red")
        initial_reports = []
    _build_report_rows(initial_reports)

    # results start empty until selection
    results_table.rows = []
    _set_results_enabled(False)

    # ----------------------------
    # Layout
    # ----------------------------
    reports_header = ft.Row(
        [
            ft.Text("Labs", size=20, weight="bold"),
            ft.Container(expand=True),
            ft.FilledButton("Add Lab Report", icon=ft.Icons.ADD, on_click=open_new_report),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    return themed_panel(
        page,
        ft.Column(
            [
                reports_header,
                ft.Row([reports_search_field, reports_search_btn, reports_clear_btn], wrap=True),
                ft.Divider(),
                reports_container,

                ft.Divider(height=s(page, 24)),
                ft.Row([ft.Text("Lab Results", size=20, weight="bold")]),
                selected_report_text,
                ft.Row([results_search_field, results_search_btn, results_clear_btn], wrap=True),
                results_header_row,
                ft.Divider(),
                results_container,
            ],
            expand=True,
            scroll=True,
        ),
        padding=s(page, 16),
        radius=10,
    )