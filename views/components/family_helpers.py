# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import json
from collections import defaultdict
import flet as ft
from database import get_patient_field_map, upsert_patient_field_value
from utils.ui_helpers import show_snack

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FIELD_KEY = "family_history.list"
_SOCIAL_FIELD_KEY = "social_history.list"

RELATION_LIST = [
    "Parent",
    "Grandparent",
    "Sibling",
    "Half-Sibling",
    "Parent's Sibling",
    "Child",
    "Other",
]

# Map gendered/specific terms to generic relation categories
_RELATION_NORMALIZE: dict[str, str] = {
    "father":        "Parent",
    "mother":        "Parent",
    "dad":           "Parent",
    "mom":           "Parent",
    "brother":       "Sibling",
    "sister":        "Sibling",
    "half-brother":  "Half-Sibling",
    "half brother":  "Half-Sibling",
    "half-sister":   "Half-Sibling",
    "half sister":   "Half-Sibling",
    "grandfather":   "Grandparent",
    "grandmother":   "Grandparent",
    "grandpa":       "Grandparent",
    "grandma":       "Grandparent",
    "uncle":         "Parent's Sibling",
    "aunt":          "Parent's Sibling",
    "son":           "Child",
    "daughter":      "Child",
    "cousin":        "Other",
    "nephew":        "Other",
    "niece":         "Other",
    "spouse":        "Other",
    "husband":       "Other",
    "wife":          "Other",
    "partner":       "Other",
}


def normalize_relation(raw: str) -> str:
    """Normalize a gendered/specific relation term to the generic category."""
    stripped = raw.strip()
    return _RELATION_NORMALIZE.get(stripped.lower(), stripped)


FIRST_DEGREE = {"Parent", "Sibling", "Half-Sibling", "Child"}
SECOND_DEGREE = {"Grandparent", "Parent's Sibling"}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _load(page: ft.Page, patient_id: int) -> list[dict]:
    try:
        vm = get_patient_field_map(page.db_connection, patient_id)
        raw = (vm.get(_FIELD_KEY) or {}).get("value")
        items = json.loads(raw or "[]")
        return [x for x in items if isinstance(x, dict)]
    except Exception:
        return []


def _save_items(page: ft.Page, patient_id: int, items: list[dict]):
    try:
        upsert_patient_field_value(
            page.db_connection, patient_id, _FIELD_KEY, json.dumps(items), "user"
        )
    except Exception as ex:
        show_snack(page, f"Save failed: {ex}", ft.Colors.RED)


def _group_by_relation(items: list[dict]) -> dict[str, list[tuple[str, list[dict]]]]:
    """
    Returns {relation: [(name, [entries_for_person]), ...]}

    People with the same (relation, name) are grouped as one person.
    Unnamed entries within the same relation are each their own person slot
    (name key = auto-index string so they stay separate).
    """
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    unnamed_idx: dict[str, int] = defaultdict(int)

    for it in items:
        raw_rel = (it.get("relation") or "Other").strip()
        rel = normalize_relation(raw_rel)
        # Use original specific term (e.g. "Father") as name when no explicit name is given
        name = (it.get("name") or "").strip()
        if not name and raw_rel.lower() != rel.lower():
            name = raw_rel.title()  # "Father", "Mother", etc.
        if not name:
            idx  = unnamed_idx[rel]
            unnamed_idx[rel] += 1
            key  = (rel, f"__unnamed_{idx}")
        else:
            key = (rel, name)
        buckets[key].append(it)

    by_rel: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for (rel, name_key), entries in buckets.items():
        display_name = "" if name_key.startswith("__unnamed_") else name_key
        by_rel[rel].append((display_name, entries))

    return dict(by_rel)


def _degree_label(relation: str) -> str:
    if relation in FIRST_DEGREE:
        return "1st"
    if relation in SECOND_DEGREE:
        return "2nd"
    return "ext"


# ---------------------------------------------------------------------------
# Social history load / save
# ---------------------------------------------------------------------------
def _load_social(page: ft.Page, patient_id: int) -> list[dict]:
    try:
        vm = get_patient_field_map(page.db_connection, patient_id)
        raw = (vm.get(_SOCIAL_FIELD_KEY) or {}).get("value")
        items = json.loads(raw or "[]")
        return [x for x in items if isinstance(x, dict)]
    except Exception:
        return []


def _save_social(page: ft.Page, patient_id: int, items: list[dict]):
    try:
        upsert_patient_field_value(
            page.db_connection, patient_id, _SOCIAL_FIELD_KEY,
            json.dumps(items), "user",
        )
    except Exception as ex:
        show_snack(page, f"Save failed: {ex}", ft.Colors.RED)
