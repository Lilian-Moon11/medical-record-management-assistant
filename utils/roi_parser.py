# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Lightweight, regex-based due-date extractor for ROI (Release of Information)
# forms.
#
# Scans raw text for time-limit phrases such as "within 30 days", "up to 2
# weeks", "10 business days".  Returns an ISO 8601 due date and a source token
# so callers can communicate provenance to the user.
#
# Deliberately avoids any LLM / AI pipeline — this is a pure text pass so it
# is fast, local, and deterministic.
# -----------------------------------------------------------------------------

import re
from datetime import datetime, timedelta


def parse_due_date_from_text(
    text: str,
    request_date: datetime | None = None,
) -> tuple[str, str]:
    """Scan *text* for ROI turnaround language and return a due date.

    Args:
        text: Raw text extracted from the ROI PDF template.
        request_date: The date the request was submitted (defaults to today).

    Returns:
        (iso_due_date, source) where source is ``'parsed'`` or ``'default'``.
    """
    if request_date is None:
        request_date = datetime.today()

    # Ordered from most-specific to least-specific.
    # Each tuple: (regex pattern, unit)
    patterns: list[tuple[str, str]] = [
        (r"(\d+)\s*business\s+days?",  "business_days"),
        (r"(\d+)\s*working\s+days?",   "business_days"),
        (r"(\d+)\s*calendar\s+days?",  "days"),
        (r"(\d+)\s*days?",             "days"),
        (r"(\d+)\s*weeks?",            "weeks"),
        (r"(\d+)\s*months?",           "months"),
    ]

    for pattern, unit in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            # Sanity-bound: ignore implausibly large numbers (> 365 days equiv)
            if unit == "business_days" and n <= 260:
                # Approximate: 5 business days per week
                delta = timedelta(days=int(n * 7 / 5))
            elif unit == "days" and n <= 365:
                delta = timedelta(days=n)
            elif unit == "weeks" and n <= 52:
                delta = timedelta(weeks=n)
            elif unit == "months" and n <= 12:
                delta = timedelta(days=n * 30)
            else:
                continue  # out-of-range, try next pattern
            return (request_date + delta).strftime("%Y-%m-%d"), "parsed"

    # Fallback: international-safe 30-day default
    return (request_date + timedelta(days=30)).strftime("%Y-%m-%d"), "default"
