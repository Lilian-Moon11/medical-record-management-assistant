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
import flet.canvas as cv
import re
from datetime import date
from utils.ui_helpers import show_snack, themed_panel, pt_scale, make_info_button
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
    # Test-centric
    list_distinct_test_names,
    list_all_results_for_test,
    # Documents
    get_document_metadata,
)


def get_labs_view(page: ft.Page):
    patient = getattr(page, "current_profile", None)
    if not patient:
        return ft.Text("No patient loaded.")
    patient_id = patient[0]

    # ----------------------------
    # Stable state holders
    # ----------------------------
    if not hasattr(page, "_labs_selected_test_name"):
        page._labs_selected_test_name = None  # str | None
    if not hasattr(page, "_editing_lab_result_id"):
        page._editing_lab_result_id = None
    if not hasattr(page, "_editing_lab_result_report_id"):
        page._editing_lab_result_report_id = None
    if not hasattr(page, "_pending_lab_result_delete"):
        page._pending_lab_result_delete = None
    if not hasattr(page, "_labs_report_cache"):
        page._labs_report_cache = {}
    if not hasattr(page, "_labs_category"):
        page._labs_category = "Vitals"
    if not hasattr(page, "_labs_results_sort_col"):
        page._labs_results_sort_col = 0  # default: sort by Date
    if not hasattr(page, "_labs_results_sort_asc"):
        page._labs_results_sort_asc = False  # newest first

    # ----------------------------
    # Helpers
    # ----------------------------
    def _parse_value_num(value_text: str | None) -> float | None:
        """
        Extract a numeric value from user-entered lab text when it's clearly numeric.
        Keeps things like '<5', '>200', 'NEG', 'trace' as non-numeric (None).
        """
        if not value_text:
            return None
        t = value_text.strip()
        if not t:
            return None
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

    def _flag_chip(flag: str | None) -> ft.Control:
        """Return a colored chip for the flag column."""
        if not flag:
            f_upper = "N"
        else:
            f_upper = flag.strip().upper()

        label_map = {"H": "High", "L": "Low", "A": "Abnormal", "N": "Normal"}
        color_map = {"H": "red", "L": "blue", "A": "orange", "N": "green"}

        label = label_map.get(f_upper, flag or "Normal")
        bg = color_map.get(f_upper, "green")

        return ft.Container(
            content=ft.Text(label, size=11, color="white", weight="bold"),
            bgcolor=bg,
            border_radius=10,
            padding=ft.Padding(left=8, right=8, top=3, bottom=3),
        )

    def _compute_trend(results_rows) -> str:
        """Compute trend from last 3+ numeric data points."""
        nums = []
        for row in results_rows:
            vn = row[3]  # value_num
            if vn is not None:
                nums.append(vn)
        if len(nums) < 2:
            return "Insufficient Data"
        recent = nums[-3:] if len(nums) >= 3 else nums[-2:]
        diffs = [recent[i+1] - recent[i] for i in range(len(recent)-1)]
        avg_diff = sum(diffs) / len(diffs)
        if avg_diff > 0.5:
            return "Rising"
        elif avg_diff < -0.5:
            return "Falling"
        return "Stable"

    def _compute_range(results_rows) -> str:
        """Compute Normal/High/Low from the latest value vs reference range."""
        if not results_rows:
            return "No Data"
        latest = results_rows[-1]
        vn = latest[3]    # value_num
        flag = latest[9]  # abnormal_flag
        if flag:
            f = flag.strip().upper()
            if f == "H": return "High"
            if f == "L": return "Low"
            if f == "A": return "Abnormal"
            if f == "N": return "Normal"
        ref_low = latest[6]   # ref_low
        ref_high = latest[7]  # ref_high
        if vn is not None and ref_low is not None and vn < ref_low:
            return "Low"
        if vn is not None and ref_high is not None and vn > ref_high:
            return "High"
        if vn is not None:
            return "Normal"
        return "N/A"

    # ----------------------------
    # Chart builder (canvas-based)
    # ----------------------------
    CHART_H = pt_scale(page, 200)
    CHART_PAD_L = pt_scale(page, 55)   # left padding for y-axis labels
    CHART_PAD_R = pt_scale(page, 20)
    CHART_PAD_T = pt_scale(page, 15)
    CHART_PAD_B = pt_scale(page, 30)   # bottom padding for x-axis labels

    chart_container = ft.Container(expand=True, height=CHART_H)

    def _build_chart(results_rows):
        """Build a canvas-based line chart from the results rows."""
        # Track per-point data including individual reference ranges
        numeric_pts = []  # list of (index, value_num, date_label, tooltip_text, ref_low, ref_high)

        for row in results_rows:
            vn = row[3]  # value_num
            if vn is None:
                continue
            d = row[10] or row[14] or ""  # result_date or collected_date
            tip = f"{d}\n{row[2] or ''} {row[4] or ''}"
            numeric_pts.append((len(numeric_pts), vn, d, tip, row[6], row[7]))

        if not numeric_pts:
            chart_container.content = ft.Text(
                "No numeric data to chart.",
                italic=True,
                color=ft.Colors.ON_SURFACE_VARIANT
                if hasattr(ft.Colors, "ON_SURFACE_VARIANT")
                else None,
            )
            return

        values = [p[1] for p in numeric_pts]
        min_y = min(values)
        max_y = max(values)
        y_range = max_y - min_y if max_y > min_y else 10
        chart_min_y = min_y - y_range * 0.2
        chart_max_y = max_y + y_range * 0.2
        # Expand chart bounds to fit all per-point reference ranges
        for pt in numeric_pts:
            if pt[4] is not None:  # ref_low
                chart_min_y = min(chart_min_y, pt[4] - y_range * 0.1)
            if pt[5] is not None:  # ref_high
                chart_max_y = max(chart_max_y, pt[5] + y_range * 0.1)

        n = len(numeric_pts)
        draw_w = 600 - CHART_PAD_L - CHART_PAD_R  # default canvas width
        draw_h = CHART_H - CHART_PAD_T - CHART_PAD_B
        y_span = chart_max_y - chart_min_y if chart_max_y > chart_min_y else 1

        def _x(idx):
            if n <= 1:
                return CHART_PAD_L + draw_w / 2
            return CHART_PAD_L + (idx / (n - 1)) * draw_w

        def _y(val):
            return CHART_PAD_T + draw_h - ((val - chart_min_y) / y_span) * draw_h

        shapes = []
        line_paint = ft.Paint(color=ft.Colors.LIGHT_BLUE_400, stroke_width=2, style=ft.PaintingStyle.STROKE)
        dot_paint = ft.Paint(color=ft.Colors.LIGHT_BLUE_400, style=ft.PaintingStyle.FILL)
        grid_paint = ft.Paint(color=ft.Colors.with_opacity(0.15, ft.Colors.ON_SURFACE), stroke_width=1, style=ft.PaintingStyle.STROKE)
        ref_paint = ft.Paint(color=ft.Colors.with_opacity(0.5, ft.Colors.GREEN), stroke_width=1, style=ft.PaintingStyle.STROKE)
        ref_fill_paint = ft.Paint(color=ft.Colors.with_opacity(0.08, ft.Colors.GREEN), style=ft.PaintingStyle.FILL)
        text_paint = ft.Paint(color=ft.Colors.ON_SURFACE_VARIANT if hasattr(ft.Colors, "ON_SURFACE_VARIANT") else ft.Colors.GREY)

        # Grid lines (4 horizontal)
        for i in range(5):
            gy = CHART_PAD_T + (i / 4) * draw_h
            shapes.append(cv.Line(CHART_PAD_L, gy, CHART_PAD_L + draw_w, gy, paint=grid_paint))
            # Y-axis label
            val_label = chart_max_y - (i / 4) * y_span
            shapes.append(cv.Text(CHART_PAD_L - pt_scale(page, 50), gy - 5, f"{val_label:.0f}", style=ft.TextStyle(size=9, color=ft.Colors.ON_SURFACE_VARIANT if hasattr(ft.Colors, "ON_SURFACE_VARIANT") else ft.Colors.GREY)))

        # Per-point reference range bands
        # Draw segments between adjacent points where reference ranges are available.
        # Each segment uses the reference range of the left-side point, transitioning
        # to the next point's range at that point's x-position.
        def _draw_ref_segment(x_start, x_end, ref_val, paint):
            """Draw a dashed horizontal line segment for a reference bound."""
            x_pos = x_start
            while x_pos < x_end:
                seg_end = min(x_pos + 6, x_end)
                shapes.append(cv.Line(x_pos, _y(ref_val), seg_end, _y(ref_val), paint=paint))
                x_pos += 12

        def _draw_ref_fill_segment(x_start, x_end, low_val, high_val, paint):
            """Draw a filled rectangle between low and high ref bounds."""
            y_top = _y(high_val)
            y_bot = _y(low_val)
            shapes.append(cv.Rect(x=x_start, y=y_top, width=x_end - x_start, height=y_bot - y_top, paint=paint))

        if n == 1:
            # Single point — draw full-width reference lines if available
            pt = numeric_pts[0]
            rl, rh = pt[4], pt[5]
            if rl is not None and rh is not None:
                _draw_ref_fill_segment(CHART_PAD_L, CHART_PAD_L + draw_w, rl, rh, ref_fill_paint)
            if rh is not None:
                _draw_ref_segment(CHART_PAD_L, CHART_PAD_L + draw_w, rh, ref_paint)
            if rl is not None:
                _draw_ref_segment(CHART_PAD_L, CHART_PAD_L + draw_w, rl, ref_paint)
        else:
            # Multiple points — draw per-segment reference bands
            for i in range(n):
                rl, rh = numeric_pts[i][4], numeric_pts[i][5]
                if rl is None and rh is None:
                    continue
                # Determine horizontal span for this point's reference range
                if i == 0:
                    x_start = CHART_PAD_L
                else:
                    x_start = (_x(i - 1) + _x(i)) / 2  # midpoint to previous
                if i == n - 1:
                    x_end = CHART_PAD_L + draw_w
                else:
                    x_end = (_x(i) + _x(i + 1)) / 2  # midpoint to next

                if rl is not None and rh is not None:
                    _draw_ref_fill_segment(x_start, x_end, rl, rh, ref_fill_paint)
                if rh is not None:
                    _draw_ref_segment(x_start, x_end, rh, ref_paint)
                if rl is not None:
                    _draw_ref_segment(x_start, x_end, rl, ref_paint)

        # Data lines connecting points
        for i in range(1, n):
            x1, y1 = _x(i - 1), _y(numeric_pts[i - 1][1])
            x2, y2 = _x(i), _y(numeric_pts[i][1])
            shapes.append(cv.Line(x1, y1, x2, y2, paint=line_paint))

        # Data point dots
        for i, (idx, val, d, tip, _rl, _rh) in enumerate(numeric_pts):
            px, py = _x(i), _y(val)
            shapes.append(cv.Circle(px, py, 4, paint=dot_paint))

        # X-axis date labels (show ~6 max)
        label_step = max(1, n // 6)
        for i, (idx, val, d, tip, _rl, _rh) in enumerate(numeric_pts):
            if i % label_step == 0 or i == n - 1:
                short = d[5:] if len(d) >= 7 else d
                shapes.append(cv.Text(_x(i) - 15, CHART_H - CHART_PAD_B + 5, short, style=ft.TextStyle(size=9, color=ft.Colors.ON_SURFACE_VARIANT if hasattr(ft.Colors, "ON_SURFACE_VARIANT") else ft.Colors.GREY)))

        chart_canvas = cv.Canvas(
            shapes=shapes,
            width=600,
            height=CHART_H,
        )
        chart_container.content = chart_canvas

    # ----------------------------
    # Historical results table
    # ----------------------------
    _show_source = bool(getattr(page, "_show_source", False))
    _show_updated = bool(getattr(page, "_show_updated", False))

    # ----------------------------
    # Sort handler for results table
    # ----------------------------
    def _on_results_sort(e: ft.DataColumnSortEvent):
        if page._labs_results_sort_col == e.column_index:
            page._labs_results_sort_asc = not page._labs_results_sort_asc
        else:
            page._labs_results_sort_col = e.column_index
            page._labs_results_sort_asc = True
        results_table.sort_column_index = page._labs_results_sort_col
        results_table.sort_ascending = page._labs_results_sort_asc
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
        sort_column_index=page._labs_results_sort_col,
        sort_ascending=page._labs_results_sort_asc,
        column_spacing=pt_scale(page, 14),
        heading_row_height=pt_scale(page, 40),
        data_row_min_height=pt_scale(page, 40),
        data_row_max_height=pt_scale(page, 56),
        heading_row_color=ft.Colors.with_opacity(0.06, ft.Colors.ON_SURFACE),
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
        if hasattr(ft.Colors, "OUTLINE_VARIANT") else None,
        border_radius=8,
    )

    results_container = ft.Container(content=results_table, expand=True)

    # ----------------------------
    # Info dialog (coding/details with source doc)
    # ----------------------------
    def _ensure_result_info_dialog():
        if getattr(page, "_lab_result_info_dlg", None) is not None:
            return page._lab_result_info_dlg

        page._lab_result_info_title = ft.Text("Measurement Details", weight="bold")
        page._lab_result_info_body = ft.Column([], tight=True, scroll=True)

        def _close(_=None):
            page._lab_result_info_dlg.open = False
            page.update()

        page._lab_result_info_dlg = ft.AlertDialog(
            modal=True,
            title=page._lab_result_info_title,
            content=ft.Container(
                width=pt_scale(page, 520),
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

        dlg = _ensure_result_info_dialog()

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

        page._lab_result_info_title.value = test_name or f"Result #{result_id}"
        page._lab_result_info_body.controls = [
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
                                on_click=lambda e, xx=x: open_result_info(xx),
                            )
                        ),
                        ft.DataCell(
                            ft.Row([
                                ft.IconButton(
                                    icon=ft.Icons.EDIT,
                                    tooltip="Edit result",
                                    on_click=lambda e, xx=x: open_edit_result(xx),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE,
                                    tooltip="Delete result",
                                    on_click=lambda e, xid=int(result_id), xx=x: open_delete_result(
                                        xid, f"{x[1] or ''}: {x[2] or ''}"
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
        tn = test_name or page._labs_selected_test_name
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
            rows = list_all_results_for_test(page.db_connection, patient_id, tn, category=page._labs_category)
        except Exception as ex:
            show_snack(page, f"Load results failed: {ex}", "red")
            rows = []

        # Sort rows before rendering
        col = page._labs_results_sort_col
        asc = page._labs_results_sort_asc

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
        _build_chart(sorted(rows, key=lambda x: str(x[10] or x[14] or "")))

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
                page.db_connection, patient_id, search=search, category=page._labs_category
            )
        except Exception as ex:
            show_snack(page, f"Load tests failed: {ex}", "red")
            names = []

        test_list_view.controls = []
        selected = page._labs_selected_test_name

        for name in names:
            is_selected = name == selected

            def _on_click(e, n=name):
                page._labs_selected_test_name = n
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
        border=ft.Border(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT))
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
        on_click=lambda e: open_add_lab_data(),
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

    # ----------------------------
    # Dialog: Add Lab Data (add a result to an existing or new report)
    # ----------------------------
    def _ensure_result_edit_dialog():
        if getattr(page, "_lab_result_edit_dlg", None) is not None:
            return page._lab_result_edit_dlg

        page._lx_test = ft.TextField(label="Metric / Test name*", autofocus=True)
        page._lx_value_text = ft.TextField(label="Value (text)*")
        page._lx_unit = ft.TextField(label="Unit")
        page._lx_ref_range = ft.TextField(label="Reference range (e.g. 70-130)")
        page._lx_flag = ft.TextField(label="Abnormal flag (H/L/A/N)")
        page._lx_date = ft.TextField(label="Result date (YYYY-MM-DD)")
        page._lx_notes = ft.TextField(label="Notes", multiline=True, min_lines=2, max_lines=4)
        page._lx_report_selector = ft.Dropdown(
            label="Attach to report",
            width=pt_scale(page, 460),
        )

        def _close(_=None):
            page._lab_result_edit_dlg.open = False
            page.update()

        def _save(_=None):
            test_name_val = (page._lx_test.value or "").strip()
            value_text = (page._lx_value_text.value or "").strip()

            if not test_name_val:
                show_snack(page, "Test name is required.", "red")
                return
            if not value_text:
                show_snack(page, "Value is required.", "red")
                return

            try:
                # Get or create report
                report_id = None
                if page._lx_report_selector.value:
                    report_id = int(page._lx_report_selector.value)

                result_id = getattr(page, "_editing_lab_result_id", None)
                report_id_for_edit = getattr(page, "_editing_lab_result_report_id", None)

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

                unit = (page._lx_unit.value or "").strip() or None
                flag = (page._lx_flag.value or "").strip() or None
                notes = (page._lx_notes.value or "").strip() or None
                value_num = _parse_value_num(value_text)

                rdate = (page._lx_date.value or "").strip() or None
                if not rdate:
                    rdate = date.today().isoformat()

                # Parse ref range text into low/high
                ref_range_raw = (page._lx_ref_range.value or "").strip() or None
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
                        category=page._labs_category,
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
                        category=page._labs_category,
                    )
                    show_snack(
                        page,
                        "Result updated." if updated else "Result not found.",
                        "blue" if updated else "orange",
                    )

                _close()
                # Refresh test menu and current test view
                _build_test_menu(test_search_field.value or None)
                page._labs_selected_test_name = test_name_val
                refresh_for_test(test_name_val)

            except Exception as ex:
                show_snack(page, f"Save result failed: {ex}", "red")

        page._lab_result_edit_dlg = ft.AlertDialog(
            modal=False,
            title=ft.Text("Measurement / Result"),
            content=ft.Container(
                width=pt_scale(page, 520),
                content=ft.Column(
                    [
                        page._lx_test,
                        ft.Row([page._lx_value_text, page._lx_unit], wrap=True),
                        page._lx_ref_range,
                        ft.Row([page._lx_flag, page._lx_date], wrap=True),
                        page._lx_notes,
                        page._lx_report_selector,
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

    def _populate_report_dropdown():
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

        page._lx_report_selector.options = options

    def open_add_lab_data(_=None):
        selected_test = page._labs_selected_test_name or ""

        page._editing_lab_result_id = None
        page._editing_lab_result_report_id = None

        dlg = _ensure_result_edit_dialog()
        dlg.title = ft.Text("Add Lab Data")

        page._lx_test.value = selected_test
        page._lx_value_text.value = ""
        page._lx_unit.value = ""
        page._lx_ref_range.value = ""
        page._lx_flag.value = ""
        page._lx_date.value = ""
        page._lx_notes.value = ""

        _populate_report_dropdown()
        page._lx_report_selector.value = ""

        dlg.open = True
        page.update()

    def open_edit_result(result_row):
        """Open the edit dialog pre-populated with existing values."""
        (
            result_id, test_name, value_text, value_num, unit,
            ref_range_text, ref_low, ref_high, ref_unit,
            flag, result_date, notes,
            report_id, source_document_id, collected_date,
            _c, _u,
        ) = result_row

        page._editing_lab_result_id = int(result_id)
        page._editing_lab_result_report_id = int(report_id)

        dlg = _ensure_result_edit_dialog()
        dlg.title = ft.Text("Edit Result")

        page._lx_test.value = test_name or ""
        page._lx_value_text.value = value_text or ""
        page._lx_unit.value = unit or ""
        page._lx_ref_range.value = ref_range_text or ""
        page._lx_flag.value = flag or ""
        page._lx_date.value = result_date or ""
        page._lx_notes.value = notes or ""

        _populate_report_dropdown()
        page._lx_report_selector.value = str(report_id)

        dlg.open = True
        page.update()

    # ----------------------------
    # Dialog: Confirm Delete Result
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

                # Refresh current view
                _build_test_menu(test_search_field.value or None)
                refresh_for_test()

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
    # Initial load
    # ----------------------------
    _build_test_menu()

    if page._labs_selected_test_name:
        add_data_btn.disabled = False
        refresh_for_test(page._labs_selected_test_name)
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
        page._labs_category = "Vitals" if is_vitals else "Lab"
        page._labs_selected_test_name = None
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

    is_vitals_start = (page._labs_category == "Vitals")
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
        "The trend chart plots numeric values over time. A green dashed line shows the reference range (normal bounds) when available.",
        "Click a column header in the Historical Test Table to sort results.",
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