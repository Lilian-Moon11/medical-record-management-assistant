# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Labs — Test-Centric View with Trend Charts
#
# Redesigned lab viewer organized by test name (e.g. "Glucose", "A1c").
# Layout:
#   Left sidebar: searchable list of distinct test names
#   Right panel:  trend line chart + historical results DataTable
#
# The underlying data model is unchanged (lab_reports → lab_results).
# Reports remain for grouping and document linking; the UI surfaces the
# test-level view for easier trend tracking.
#
# Design notes (Flet mounting safety):
# - Do NOT call control.update() during initial view construction.
# - Dialogs created once, appended to page.overlay once.
# -----------------------------------------------------------------------------

from __future__ import annotations
import flet as ft
from utils.ui_helpers import OUTLINE_VARIANT, show_snack, themed_panel, pt_scale, make_info_button
from database import (
    list_distinct_test_names,
    list_all_results_for_test,
    get_document_metadata,
)
from views.components.lab_helpers import _flag_chip
from views.components.lab_chart import build_lab_chart
from views.components.lab_dialogs import (
    open_result_info,
    open_add_lab_data,
    open_edit_result,
    open_delete_result
)
def get_labs_view(page: ft.Page):
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    # ----------------------------
    # Stable state holders
    # ----------------------------
    if not hasattr(page.mrma, "_labs_selected_test_name"):
        page.mrma._labs_selected_test_name = None  # str | None
    if not hasattr(page.mrma, "_editing_lab_result_id"):
        page.mrma._editing_lab_result_id = None
    if not hasattr(page.mrma, "_editing_lab_result_report_id"):
        page.mrma._editing_lab_result_report_id = None
    if not hasattr(page.mrma, "_pending_lab_result_delete"):
        page.mrma._pending_lab_result_delete = None
    if not hasattr(page.mrma, "_labs_report_cache"):
        page.mrma._labs_report_cache = {}
    if not hasattr(page.mrma, "_labs_category"):
        page.mrma._labs_category = "Vitals"
    if not hasattr(page.mrma, "_labs_results_sort_col"):
        page.mrma._labs_results_sort_col = 0  # default: sort by Date
    if not hasattr(page.mrma, "_labs_results_sort_asc"):
        page.mrma._labs_results_sort_asc = False  # newest first

    # ----------------------------
    # ----------------------------
    # Chart container & Callbacks
    # ----------------------------
    CHART_H = pt_scale(page, 200)
    chart_container = ft.Container(expand=True, height=CHART_H)

    def on_refresh(test_name=None):
        if test_name:
            page.mrma._labs_selected_test_name = test_name
        _build_test_menu(test_search_field.value or None)
        refresh_for_test(page.mrma._labs_selected_test_name)

    # Historical results table
    # ----------------------------
    _show_source = bool(getattr(page.mrma, "_show_source", False))
    _show_updated = bool(getattr(page.mrma, "_show_updated", False))

    # ----------------------------
    # Sort handler for results table
    # ----------------------------
    def _on_results_sort(e: ft.DataColumnSortEvent):
        if page.mrma._labs_results_sort_col == e.column_index:
            page.mrma._labs_results_sort_asc = not page.mrma._labs_results_sort_asc
        else:
            page.mrma._labs_results_sort_col = e.column_index
            page.mrma._labs_results_sort_asc = True
        results_table.sort_column_index = page.mrma._labs_results_sort_col
        results_table.sort_ascending = page.mrma._labs_results_sort_asc
        refresh_for_test()  # re-fetch + re-sort + re-render

    results_cols = [
        ft.DataColumn(ft.Text("Date"),    on_sort=_on_results_sort),
        ft.DataColumn(ft.Text("Value"),   on_sort=_on_results_sort),
        ft.DataColumn(ft.Text("Unit"),    on_sort=_on_results_sort),
        ft.DataColumn(ft.Text("Flag"),    on_sort=_on_results_sort),
    ]
    if _show_source:
        results_cols.append(ft.DataColumn(ft.Text("Source")))
    if _show_updated:
        results_cols.append(ft.DataColumn(ft.Text("Updated")))
    results_cols += [
        ft.DataColumn(ft.Text("Info")),
        ft.DataColumn(ft.Text("Edit/Delete")),
    ]

    results_table = ft.DataTable(
        columns=results_cols,
        rows=[],
        sort_column_index=page.mrma._labs_results_sort_col,
        sort_ascending=page.mrma._labs_results_sort_asc,
        column_spacing=pt_scale(page, 14),
        heading_row_height=pt_scale(page, 40),
        data_row_min_height=pt_scale(page, 40),
        data_row_max_height=pt_scale(page, 56),
        heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
        border=ft.Border.all(1, OUTLINE_VARIANT),
        border_radius=8,
    )

    results_container = ft.Container(content=results_table, expand=True)

    # ----------------------------
    # Build historical table rows
    # ----------------------------
    def _build_result_rows(rows):
        results_table.rows = []
        for x in rows:
            # x shape: (result_id, test_name, value_text, value_num, unit,
            #           ref_range_text, ref_low, ref_high, ref_unit,
            #           abnormal_flag, result_date, notes,
            #           report_id, source_document_id, collected_date,
            #           created_at, updated_at)
            result_id = x[0]
            value_text = x[2]
            unit = x[4]
            ref_range_text = x[5]
            ref_low = x[6]
            ref_high = x[7]
            ref_unit = x[8]
            flag = x[9]
            result_date = x[10] or x[14] or ""

            # Build reference range display
            rr = ""
            if ref_range_text:
                rr = ref_range_text
            elif ref_low is not None or ref_high is not None:
                lo = "" if ref_low is None else str(ref_low)
                hi = "" if ref_high is None else str(ref_high)
                ru = ref_unit or unit or ""
                rr = f"{lo}-{hi} {ru}".strip()

            cells = [
                        ft.DataCell(ft.Text(result_date)),
                        ft.DataCell(ft.Text(value_text or "")),
                        ft.DataCell(ft.Text(unit or "")),
                        ft.DataCell(_flag_chip(flag)),
            ]
            if _show_source:
                # Source: resolve document name or show "User"
                src_doc_id = x[13]
                if src_doc_id:
                    try:
                        dm = get_document_metadata(page.db_connection, int(src_doc_id))
                        src_label = dm[0] if dm else f"Doc #{src_doc_id}"
                    except Exception:
                        src_label = f"Doc #{src_doc_id}"
                else:
                    src_label = "User"
                cells.append(ft.DataCell(ft.Text(src_label)))
            if _show_updated:
                cells.append(ft.DataCell(ft.Text(x[16] or "")))
            cells += [
                        ft.DataCell(
                            ft.IconButton(
                                icon=ft.Icons.INFO_OUTLINE,
                                tooltip="View details",
                                on_click=lambda e, xx=x: open_result_info(page, xx),
                            )
                        ),
                        ft.DataCell(
                            ft.Row([
                                ft.IconButton(
                                    icon=ft.Icons.EDIT,
                                    tooltip="Edit result",
                                    on_click=lambda e, xx=x: open_edit_result(page, xx, patient_id, on_refresh),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE,
                                    tooltip="Delete result",
                                    on_click=lambda e, xid=int(result_id), xx=x: open_delete_result(
                                        page, xid, f"{x[1] or ''}: {x[2] or ''}", patient_id, on_refresh
                                    ),
                                ),
                            ], tight=True, spacing=0)
                        ),
            ]

            results_table.rows.append(
                ft.DataRow(cells=cells)
            )

    # ----------------------------
    # Refresh / Select test
    # ----------------------------
    def refresh_for_test(test_name: str | None = None):
        """Load all results for the selected test, update chart + table."""
        tn = test_name or page.mrma._labs_selected_test_name
        if not tn:
            chart_container.content = ft.Text(
                "Select a metric from the menu.",
                italic=True,
            )
            results_table.rows = []
            test_title.value = "Select a metric or test"
            try:
                test_title.update()
                chart_container.update()
                results_table.update()
                page.update()
            except Exception:
                pass
            return

        try:
            rows = list_all_results_for_test(page.db_connection, patient_id, tn, category=page.mrma._labs_category)
        except Exception as ex:
            show_snack(page, f"Load results failed: {ex}", "red")
            rows = []

        # Sort rows before rendering
        col = page.mrma._labs_results_sort_col
        asc = page.mrma._labs_results_sort_asc

        def _results_sort_key(x):
            # x indices: 0=result_id, 2=value_text, 3=value_num, 4=unit, 9=flag, 10=result_date, 14=collected_date
            if col == 0:  # Date
                return str(x[10] or x[14] or "")
            elif col == 1:  # Value — sort numerically when possible
                vn = x[3]
                return (0, vn) if vn is not None else (1, str(x[2] or "").lower())
            elif col == 2:  # Unit
                return str(x[4] or "").lower()
            elif col == 3:  # Flag
                return str(x[9] or "N").upper()
            return ""

        rows = sorted(rows, key=_results_sort_key, reverse=not asc)

        # Update sort indicators on table
        results_table.sort_column_index = col
        results_table.sort_ascending = asc

        # Update header
        test_title.value = tn

        # Build chart (always uses original chronological order — pass unsorted)
        # Build chart (always uses original chronological order — pass unsorted)
        build_lab_chart(page, sorted(rows, key=lambda x: str(x[10] or x[14] or "")), chart_container)

        # Build table
        _build_result_rows(rows)

        try:
            test_title.update()
            chart_container.update()
            results_table.update()
            page.update()
        except Exception:
            pass

    # ----------------------------
    # Test menu sidebar
    # ----------------------------
    test_list_view = ft.ListView(expand=True, spacing=2, padding=4)

    def _build_test_menu(search: str | None = None):
        """Populate the test menu sidebar with distinct test names."""
        try:
            names = list_distinct_test_names(
                page.db_connection, patient_id, search=search, category=page.mrma._labs_category
            )
        except Exception as ex:
            show_snack(page, f"Load tests failed: {ex}", "red")
            names = []

        test_list_view.controls = []
        selected = page.mrma._labs_selected_test_name

        for name in names:
            is_selected = name == selected

            def _on_click(e, n=name):
                page.mrma._labs_selected_test_name = n
                add_data_btn.disabled = False
                refresh_for_test(n)
                _build_test_menu(test_search_field.value or None)

            test_list_view.controls.append(
                ft.Container(
                    content=ft.Text(
                        name,
                        size=14,
                        weight="bold" if is_selected else None,
                        color="white" if is_selected else None,
                    ),
                    bgcolor=ft.Colors.PRIMARY if is_selected else ft.Colors.TRANSPARENT,
                    padding=ft.Padding(left=12, right=12, top=8, bottom=8),
                    border_radius=6,
                    on_click=_on_click,
                    ink=True,
                )
            )

        try:
            test_list_view.update()
        except Exception:
            pass

    def _on_test_search(e=None):
        _build_test_menu(test_search_field.value or None)

    test_search_field = ft.TextField(
        hint_text="Search metrics...",
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        on_change=_on_test_search,
        on_submit=_on_test_search,
        border_radius=8,
    )

    test_menu_panel = ft.Container(
        width=pt_scale(page, 230),
        content=ft.Column(
            [
                ft.Text("Metrics / Tests", size=16, weight="bold"),
                test_search_field,
                ft.Container(content=test_list_view, expand=True),
            ],
            expand=True,
            spacing=8,
        ),
        padding=pt_scale(page, 10),
        border=ft.Border(right=ft.BorderSide(1, OUTLINE_VARIANT))
        if hasattr(ft.Colors, "OUTLINE_VARIANT")
        else ft.Border(right=ft.BorderSide(1, ft.Colors.GREY)),
    )

    # ----------------------------
    # Right panel header
    # ----------------------------
    test_title = ft.Text("Select a metric or test", size=22, weight="bold")

    add_data_btn = ft.FilledButton(
        "Add Data",
        icon=ft.Icons.ADD,
        on_click=lambda e: open_add_lab_data(page, patient_id, on_refresh),
        disabled=False,
    )

    right_header = ft.Row(
        [
            test_title,
            ft.Container(expand=True),
            add_data_btn,
        ],
        alignment=ft.MainAxisAlignment.START,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=10,
    )

    # Initial load
    # ----------------------------
    _build_test_menu()

    if page.mrma._labs_selected_test_name:
        add_data_btn.disabled = False
        refresh_for_test(page.mrma._labs_selected_test_name)
    else:
        chart_container.content = ft.Text(
            "Select a test from the menu to view trends.",
            italic=True,
        )

    # ----------------------------
    # Layout
    # ----------------------------
    right_panel = ft.Column(
        [
            right_header,
            chart_container,
            ft.Divider(),
            ft.Text("Historical Test Table", size=16, weight="bold"),
            results_container,
        ],
        expand=True,
        scroll=True,
        spacing=10,
    )

    main_view = ft.Row(
        [
            test_menu_panel,
            ft.Container(content=right_panel, expand=True, padding=pt_scale(page, 10)),
        ],
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    def _on_tab_change(is_vitals):
        page.mrma._labs_category = "Vitals" if is_vitals else "Lab"
        page.mrma._labs_selected_test_name = None
        _build_test_menu()
        refresh_for_test(None)
        
        tab_vitals.border = ft.border.only(bottom=ft.BorderSide(3, ft.Colors.BLUE)) if is_vitals else None
        tab_labs.border = ft.border.only(bottom=ft.BorderSide(3, ft.Colors.BLUE)) if not is_vitals else None
        
        for c in tab_vitals.content.controls:
            c.color = ft.Colors.BLUE if is_vitals else ft.Colors.ON_SURFACE_VARIANT if hasattr(ft.Colors, "ON_SURFACE_VARIANT") else ft.Colors.GREY
        for c in tab_labs.content.controls:
            c.color = ft.Colors.BLUE if not is_vitals else ft.Colors.ON_SURFACE_VARIANT if hasattr(ft.Colors, "ON_SURFACE_VARIANT") else ft.Colors.GREY

        tab_vitals.update()
        tab_labs.update()
        page.update()

    is_vitals_start = (page.mrma._labs_category == "Vitals")
    base_color = ft.Colors.ON_SURFACE_VARIANT if hasattr(ft.Colors, "ON_SURFACE_VARIANT") else ft.Colors.GREY

    tab_vitals = ft.Container(
        content=ft.Row([ft.Icon(ft.Icons.MONITOR_HEART, size=18, color=ft.Colors.BLUE if is_vitals_start else base_color), ft.Text("Vitals", weight="bold", color=ft.Colors.BLUE if is_vitals_start else base_color)], alignment=ft.MainAxisAlignment.CENTER),
        expand=True,
        padding=10,
        ink=True,
        on_click=lambda _: _on_tab_change(True),
        border=ft.border.only(bottom=ft.BorderSide(3, ft.Colors.BLUE)) if is_vitals_start else None,
    )

    tab_labs = ft.Container(
        content=ft.Row([ft.Icon(ft.Icons.SCIENCE, size=18, color=ft.Colors.BLUE if not is_vitals_start else base_color), ft.Text("Clinical Labs", weight="bold", color=ft.Colors.BLUE if not is_vitals_start else base_color)], alignment=ft.MainAxisAlignment.CENTER),
        expand=True,
        padding=10,
        ink=True,
        on_click=lambda _: _on_tab_change(False),
        border=ft.border.only(bottom=ft.BorderSide(3, ft.Colors.BLUE)) if not is_vitals_start else None,
    )

    tabs_control = ft.Row([tab_vitals, tab_labs], alignment=ft.MainAxisAlignment.CENTER)

    _info_btn = make_info_button(page, "Vitals & Labs", [
        "This tab has two sub-tabs: Vitals (daily measurements like blood pressure or weight) and Clinical Labs (official test results from a lab, clinic, or hospital).",
        "Select a metric or test name from the left sidebar to see its trend chart and history table.",
        "The trend chart plots numeric values over time. Green dashed lines show the reference range (normal bounds) when available.",
        "Click a column header in the Historical Test Table to sort results. Click the Info icon on any row to see full details including notes and reference ranges.",
    ])

    return themed_panel(
        page,
        ft.Column(
            [
                ft.Row(
                    [tabs_control, ft.Container(expand=True), _info_btn],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(content=main_view, expand=True),
            ],
            expand=True,
        ),
        padding=pt_scale(page, 10),
        radius=10,
    )