# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Pure-function utility for converting vital-sign measurements between
# Imperial and Metric unit systems.
#
# Canonical storage units (metric):
#   Weight      → kg
#   Height      → cm
#   Temperature → °C
#
# Public API:
#   detect_unit_system(unit_str)  → "imperial" | "metric" | None
#   convert_weight(value, from_unit, to_unit) → float
#   convert_height(value, from_unit, to_unit) → float
#   convert_temperature(value, from_unit, to_unit) → float
#   normalize_vital_to_metric(parsed_vital) → dict
#   format_vital_for_display(parsed_vital, preferred_system) → (str, str)
#   cm_to_feet_inches(cm_value) → str   (e.g. "5' 7\"")
#   feet_inches_to_cm(text) → float | None
# -----------------------------------------------------------------------------

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LBS_PER_KG = 2.20462
KG_PER_LB = 1 / LBS_PER_KG
CM_PER_INCH = 2.54
INCHES_PER_FOOT = 12

# Unit string aliases → canonical unit name
_IMPERIAL_WEIGHT = frozenset(["lb", "lbs", "pound", "pounds"])
_METRIC_WEIGHT = frozenset(["kg", "kgs", "kilogram", "kilograms"])
_IMPERIAL_HEIGHT = frozenset(["in", "ins", "inch", "inches", "ft", "feet", "foot"])
_METRIC_HEIGHT = frozenset(["cm", "centimeter", "centimeters", "m", "meter", "meters"])
_IMPERIAL_TEMP = frozenset(["f", "°f", "fahrenheit", "deg f"])
_METRIC_TEMP = frozenset(["c", "°c", "celsius", "deg c", "centigrade"])


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_unit_system(unit_str: str) -> str | None:
    """Detect whether a unit string is 'imperial' or 'metric'.

    Returns 'imperial', 'metric', or None if unrecognized.
    """
    u = (unit_str or "").strip().lower().rstrip(".")
    if u in _IMPERIAL_WEIGHT | _IMPERIAL_HEIGHT | _IMPERIAL_TEMP:
        return "imperial"
    if u in _METRIC_WEIGHT | _METRIC_HEIGHT | _METRIC_TEMP:
        return "metric"
    # Check for feet-inches pattern like 5'7"
    if re.match(r"^\d+'\s*\d+\"?$", u):
        return "imperial"
    return None


# ---------------------------------------------------------------------------
# Conversion functions
# ---------------------------------------------------------------------------

def convert_weight(value: float, from_unit: str, to_unit: str) -> float:
    """Convert weight between lbs and kg.

    from_unit / to_unit: 'lbs' or 'kg'
    """
    f = from_unit.strip().lower()
    t = to_unit.strip().lower()
    if f == t:
        return value
    if f in _IMPERIAL_WEIGHT and t in _METRIC_WEIGHT:
        return round(value * KG_PER_LB, 2)
    if f in _METRIC_WEIGHT and t in _IMPERIAL_WEIGHT:
        return round(value * LBS_PER_KG, 2)
    return value  # unrecognized — return as-is


def convert_height(value: float, from_unit: str, to_unit: str) -> float:
    """Convert height between inches and cm.

    Accepts inches (in) or cm. For feet-inches input, use feet_inches_to_cm().
    """
    f = from_unit.strip().lower()
    t = to_unit.strip().lower()
    if f == t:
        return value
    if f in _IMPERIAL_HEIGHT and t in _METRIC_HEIGHT:
        return round(value * CM_PER_INCH, 2)
    if f in _METRIC_HEIGHT and t in _IMPERIAL_HEIGHT:
        return round(value / CM_PER_INCH, 2)
    return value


def convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    """Convert temperature between °F and °C."""
    f = from_unit.strip().lower()
    t = to_unit.strip().lower()
    if f == t:
        return value
    if f in _IMPERIAL_TEMP and t in _METRIC_TEMP:
        return round((value - 32) * 5 / 9, 2)
    if f in _METRIC_TEMP and t in _IMPERIAL_TEMP:
        return round(value * 9 / 5 + 32, 2)
    return value


# ---------------------------------------------------------------------------
# Height formatting helpers
# ---------------------------------------------------------------------------

def cm_to_feet_inches(cm_value: float) -> str:
    """Convert cm to a formatted string like 5' 7\"."""
    total_inches = cm_value / CM_PER_INCH
    feet = int(total_inches // INCHES_PER_FOOT)
    inches = round(total_inches % INCHES_PER_FOOT)
    # Handle rounding edge case: 12 inches → next foot
    if inches == 12:
        feet += 1
        inches = 0
    return f"{feet}' {inches}\""


def feet_inches_to_cm(text: str) -> float | None:
    """Parse a feet-inches string like 5'7\" or 5' 7\" and return cm.

    Returns None if the text doesn't match the expected pattern.
    """
    m = re.match(r"(\d+)'\s*(\d+)\"?", text.strip())
    if m:
        feet = int(m.group(1))
        inches = int(m.group(2))
        total_inches = feet * INCHES_PER_FOOT + inches
        return round(total_inches * CM_PER_INCH, 2)
    return None


# ---------------------------------------------------------------------------
# Ingestion-time normalization (to metric)
# ---------------------------------------------------------------------------

def normalize_vital_to_metric(parsed: dict) -> dict:
    """Normalize a parsed vital dict to canonical metric units.

    Modifies the 'value' and 'unit' fields in-place and returns the dict.
    Only converts weight, height, and temperature.
    """
    name = str(parsed.get("name", "")).strip().lower()
    value_str = str(parsed.get("value", parsed.get("value_text", ""))).strip()
    unit = str(parsed.get("unit", "")).strip().lower()

    if not value_str or not name:
        return parsed

    # --- Height: handle feet-inches notation (e.g. "5'7\"") ---
    if name == "height":
        cm_val = feet_inches_to_cm(value_str)
        if cm_val is not None:
            parsed["value"] = str(cm_val)
            parsed["unit"] = "cm"
            return parsed
        # Also handle "5 ft 7 in" or "67 in" or "67 inches"
        ft_in_match = re.match(
            r"(\d+)\s*(?:ft|feet|foot)\s*(\d+)?\s*(?:in|inch|inches)?",
            value_str, re.IGNORECASE
        )
        if ft_in_match:
            feet = int(ft_in_match.group(1))
            inches = int(ft_in_match.group(2) or 0)
            total_inches = feet * INCHES_PER_FOOT + inches
            parsed["value"] = str(round(total_inches * CM_PER_INCH, 2))
            parsed["unit"] = "cm"
            return parsed

    # --- Numeric conversion for weight, height (inches), temperature ---
    try:
        num_val = float(value_str)
    except (ValueError, TypeError):
        return parsed  # non-numeric — return as-is

    if name == "weight" and unit in _IMPERIAL_WEIGHT:
        parsed["value"] = str(convert_weight(num_val, "lbs", "kg"))
        parsed["unit"] = "kg"
    elif name == "height" and unit in _IMPERIAL_HEIGHT:
        parsed["value"] = str(convert_height(num_val, "in", "cm"))
        parsed["unit"] = "cm"
    elif name == "temperature" and unit in _IMPERIAL_TEMP:
        parsed["value"] = str(convert_temperature(num_val, "f", "c"))
        parsed["unit"] = "°C"
    elif name == "weight" and not unit:
        # Heuristic: values > 100 are likely lbs (no kg adult is > 100 typically)
        # but this is risky — only apply if no unit at all
        pass  # leave as-is if no unit provided; user can correct
    elif name == "temperature" and not unit:
        # Heuristic: values > 50 are likely °F
        if num_val > 50:
            parsed["value"] = str(convert_temperature(num_val, "f", "c"))
            parsed["unit"] = "°C"

    return parsed


# All known unit tokens for stripping from value strings
_ALL_UNIT_TOKENS = sorted(
    list(_IMPERIAL_WEIGHT) + list(_METRIC_WEIGHT) +
    list(_IMPERIAL_HEIGHT) + list(_METRIC_HEIGHT) +
    list(_IMPERIAL_TEMP) + list(_METRIC_TEMP),
    key=len, reverse=True,       # longest first so "kilograms" matches before "kg"
)

def _strip_unit_from_value(value_str: str) -> tuple[str, str]:
    """Strip a trailing unit token from a value string.

    Returns (numeric_part, detected_unit).
    If no unit is found, returns (value_str, "").
    """
    v = value_str.strip()
    v_lower = v.lower()
    for token in _ALL_UNIT_TOKENS:
        if v_lower.endswith(token):
            numeric_part = v[:len(v) - len(token)].strip()
            if numeric_part:
                return numeric_part, token
    return v, ""


def format_vital_for_display(
    name: str,
    value_str: str,
    unit: str,
    preferred_system: str,
) -> tuple[str, str]:
    """Convert a vital to the user's preferred display format.

    Handles both normalized metric data and legacy imperial data that
    may not have been normalized yet. Also strips unit suffixes that
    may be baked into the value string (e.g. "170.2 cm").

    Returns (display_value, display_unit).
    """
    name_lower = (name or "").strip().lower()
    preferred = (preferred_system or "imperial").strip().lower()
    unit_lower = (unit or "").strip().lower()

    # --- Step 1: Try to extract a clean numeric value ---
    clean_val = (value_str or "").strip()
    effective_unit = unit_lower

    # Handle feet-inches notation (e.g. "5'7\"", "5' 7\"")
    if name_lower == "height":
        cm_parsed = feet_inches_to_cm(clean_val)
        if cm_parsed is not None:
            # Value is in feet-inches format → we know it's imperial
            if preferred == "imperial":
                # Re-format consistently as X' Y"
                return cm_to_feet_inches(cm_parsed), ""
            else:
                return str(cm_parsed), "cm"

    # Strip baked-in unit suffix from value (e.g. "170.2 cm" → "170.2", "cm")
    stripped_val, stripped_unit = _strip_unit_from_value(clean_val)
    if stripped_unit:
        clean_val = stripped_val
        # Use the stripped unit if the explicit unit column is empty or matches
        if not effective_unit or effective_unit == stripped_unit:
            effective_unit = stripped_unit

    # Try parsing as numeric
    try:
        num_val = float(clean_val)
    except (ValueError, TypeError):
        return value_str, unit  # can't parse — return as-is

    # --- Step 2: Detect what system the stored value is in ---
    stored_system = detect_unit_system(effective_unit)

    # --- Step 3: Convert to preferred system ---
    if name_lower == "weight":
        if stored_system == "metric" and preferred == "imperial":
            return str(round(num_val * LBS_PER_KG, 1)), "lbs"
        elif stored_system == "imperial" and preferred == "metric":
            return str(round(num_val * KG_PER_LB, 1)), "kg"
        elif stored_system == "imperial" and preferred == "imperial":
            return str(round(num_val, 1)), "lbs"
        elif stored_system == "metric" and preferred == "metric":
            return str(round(num_val, 1)), "kg"

    elif name_lower == "height":
        if stored_system == "metric" and preferred == "imperial":
            return cm_to_feet_inches(num_val), ""
        elif stored_system == "imperial" and preferred == "metric":
            # inches → cm
            return str(round(num_val * CM_PER_INCH, 1)), "cm"
        elif stored_system == "metric" and preferred == "metric":
            return str(round(num_val, 1)), "cm"
        elif stored_system == "imperial" and preferred == "imperial":
            # inches → formatted feet-inches
            return cm_to_feet_inches(num_val * CM_PER_INCH), ""

    elif name_lower == "temperature":
        if stored_system == "metric" and preferred == "imperial":
            return str(convert_temperature(num_val, "c", "f")), "°F"
        elif stored_system == "imperial" and preferred == "metric":
            return str(convert_temperature(num_val, "f", "c")), "°C"
        elif stored_system == "imperial" and preferred == "imperial":
            return str(round(num_val, 1)), "°F"
        elif stored_system == "metric" and preferred == "metric":
            return str(round(num_val, 1)), "°C"

    # Unrecognized unit system — return cleaned value
    return str(round(num_val, 1)) if num_val == int(num_val) or num_val != num_val else clean_val, unit

