# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Shared dictionary of common medical abbreviations and a normalisation
# utility that expands them to plain English.
#
# Used by:
#   - ai/extraction_filters.py  (normalize during ingestion)
#   - ai/paperwork.py           (normalize for PDF form filling)
#
# Public API:
#   MED_SHORTHAND       → dict of abbreviation → plain English
#   normalize_medical_shorthand(text) → str with abbreviations expanded
# -----------------------------------------------------------------------------

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Prescription / clinical abbreviations → plain-English equivalents
# ---------------------------------------------------------------------------

# Frequency abbreviations
_FREQUENCY = {
    "bid": "Twice daily", "b.i.d.": "Twice daily",
    "tid": "Three times daily", "t.i.d.": "Three times daily",
    "qd": "Once daily", "q.d.": "Once daily",
    "qid": "Four times daily", "q.i.d.": "Four times daily",
    "qhs": "At bedtime", "q.h.s.": "At bedtime",
    "prn": "As needed", "p.r.n.": "As needed",
    "qod": "Every other day", "q.o.d.": "Every other day",
    "ac": "Before meals", "a.c.": "Before meals",
    "pc": "After meals", "p.c.": "After meals",
    "q4h": "Every 4 hours", "q6h": "Every 6 hours",
    "q8h": "Every 8 hours", "q12h": "Every 12 hours",
    "qam": "Every morning", "qpm": "Every evening",
    "stat": "Immediately",
    "qwk": "Once weekly", "biw": "Twice weekly",
}

# Route abbreviations
_ROUTE = {
    "po": "By mouth", "p.o.": "By mouth",
    "sl": "Sublingual", "s.l.": "Sublingual",
    "im": "Intramuscular", "i.m.": "Intramuscular",
    "iv": "Intravenous", "i.v.": "Intravenous",
    "sq": "Subcutaneous", "subq": "Subcutaneous", "subcut": "Subcutaneous",
    "s.c.": "Subcutaneous",
    "p.r.": "Rectal",
    "od": "Right eye", "o.d.": "Right eye",
    "os": "Left eye", "o.s.": "Left eye",
    "ou": "Both eyes", "o.u.": "Both eyes",
    "a.d.": "Right ear",
    "a.s.": "Left ear",
    "a.u.": "Both ears",
    "inh": "Inhaled",
    "neb": "Nebulized",
    "topical": "Topical",
    "transdermal": "Transdermal",
}

# Dosage form abbreviations
_DOSAGE_FORM = {
    "tab": "Tablet", "tabs": "Tablets",
    "cap": "Capsule", "caps": "Capsules",
    "gtts": "Drops", "gtt": "Drop",
    "supp": "Suppository",
    "inj": "Injection",
    "soln": "Solution", "sol": "Solution",
    "susp": "Suspension",
    "ung": "Ointment",
    "pch": "Patch",
    "elix": "Elixir",
    "syr": "Syrup",
    "liq": "Liquid",
    "pwd": "Powder",
    "aer": "Aerosol",
    "mdi": "Metered dose inhaler",
}

# Unit abbreviations (kept as-is since they're standard)
_UNITS = {
    "mg": "mg", "mcg": "mcg", "ml": "mL", "meq": "mEq",
    "iu": "IU",
}

# Combined dictionary — public for any module that needs raw access
MED_SHORTHAND: dict[str, str] = {}
MED_SHORTHAND.update(_FREQUENCY)
MED_SHORTHAND.update(_ROUTE)
MED_SHORTHAND.update(_DOSAGE_FORM)
MED_SHORTHAND.update(_UNITS)

# Pre-compile regex patterns sorted by length (longest first to avoid
# partial matches — e.g. "subcut" before "sub")
_PATTERNS: list[tuple[re.Pattern, str]] = []
for abbr in sorted(MED_SHORTHAND, key=len, reverse=True):
    pattern = re.compile(r'\b' + re.escape(abbr) + r'\b', re.IGNORECASE)
    _PATTERNS.append((pattern, MED_SHORTHAND[abbr]))


def normalize_medical_shorthand(text: str) -> str:
    """Replace medical abbreviations anywhere in *text* with plain English.

    Case-insensitive, word-boundary aware. Safe to call on any string —
    returns the original if no abbreviations are found.
    """
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
