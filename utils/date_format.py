# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Pure-function utility for formatting dates according to user preference.
#
# Canonical storage format: ISO 8601 (YYYY-MM-DD)
#
# Supported display formats:
#   "MM/DD/YYYY"  → 05/12/2026   (US convention)
#   "DD/MM/YYYY"  → 12/05/2026   (International / European)
#   "YYYY-MM-DD"  → 2026-05-12   (ISO 8601)
#
# Public API:
#   FORMAT_OPTIONS  → list of (key, label) tuples for dropdown display
#   format_date(iso_date, preferred_format) → str
#   format_date_short(iso_date, preferred_format) → str  (for chart labels)
# -----------------------------------------------------------------------------

from __future__ import annotations

import re

# Dropdown options: (setting_value, display_label)
FORMAT_OPTIONS = [
    ("MM/DD/YYYY", "MM/DD/YYYY (US)"),
    ("DD/MM/YYYY", "DD/MM/YYYY (International)"),
    ("YYYY-MM-DD", "YYYY-MM-DD (ISO 8601)"),
]

DEFAULT_FORMAT = "MM/DD/YYYY"

# Pre-compiled pattern for ISO date (YYYY-MM-DD)
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def format_date(iso_date: str, preferred_format: str = DEFAULT_FORMAT) -> str:
    """Convert an ISO date string to the user's preferred display format.

    If the input is not a valid ISO date, returns it unchanged.
    Handles dates with trailing time components (e.g. "2026-05-12T10:30:00").
    """
    if not iso_date:
        return iso_date or ""

    text = str(iso_date).strip()
    m = _ISO_DATE_RE.match(text)
    if not m:
        return text  # not ISO — return as-is

    year, month, day = m.group(1), m.group(2), m.group(3)
    fmt = (preferred_format or DEFAULT_FORMAT).strip()

    if fmt == "MM/DD/YYYY":
        return f"{month}/{day}/{year}"
    elif fmt == "DD/MM/YYYY":
        return f"{day}/{month}/{year}"
    elif fmt == "YYYY-MM-DD":
        return f"{year}-{month}-{day}"
    else:
        return f"{month}/{day}/{year}"  # fallback to US


def format_date_short(iso_date: str, preferred_format: str = DEFAULT_FORMAT) -> str:
    """Short date format for chart x-axis labels.

    Uses 2-digit year to save space on charts.
    Returns formats like "05/12/26", "12/05/26", or "26-05-12".
    """
    if not iso_date:
        return iso_date or ""

    text = str(iso_date).strip()
    m = _ISO_DATE_RE.match(text)
    if not m:
        return text

    year, month, day = m.group(1), m.group(2), m.group(3)
    yy = year[2:]  # 2-digit year
    fmt = (preferred_format or DEFAULT_FORMAT).strip()

    if fmt == "MM/DD/YYYY":
        return f"{month}/{day}/{yy}"
    elif fmt == "DD/MM/YYYY":
        return f"{day}/{month}/{yy}"
    elif fmt == "YYYY-MM-DD":
        return f"{yy}-{month}-{day}"
    else:
        return f"{month}/{day}/{yy}"
