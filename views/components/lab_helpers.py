# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

from __future__ import annotations
import flet as ft
import re

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
