from __future__ import annotations
import flet as ft
import flet.canvas as cv
from utils.ui_helpers import pt_scale

def build_lab_chart(page: ft.Page, results_rows: list, chart_container: ft.Container):
    """Build a canvas-based line chart from the results rows and set it as chart_container's content."""
    CHART_H = pt_scale(page, 200)
    CHART_PAD_L = pt_scale(page, 55)   # left padding for y-axis labels
    CHART_PAD_R = pt_scale(page, 20)
    CHART_PAD_T = pt_scale(page, 15)
    CHART_PAD_B = pt_scale(page, 30)   # bottom padding for x-axis labels

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
