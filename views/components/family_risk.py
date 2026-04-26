# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import flet as ft
from utils.ui_helpers import pt_scale
from views.components.family_helpers import _degree_label

# ---------------------------------------------------------------------------
# Family history summary - grouped by degree (1st / 2nd / extended)
# ---------------------------------------------------------------------------
def build_risk_summary(page: ft.Page, items: list[dict],
                       on_node_click=None) -> ft.Control:
    """
    Build the degree-based family history summary.

    on_node_click(relation, display_name, entries) is called when the user
    clicks a condition row to edit that family member.
    """
    s = pt_scale(page, 1)

    first: list[dict]    = []
    second: list[dict]   = []
    extended: list[dict] = []

    for it in items:
        rel  = (it.get("relation") or "").strip()
        cond = (it.get("condition") or "").strip()
        if not cond:
            continue
        entry = {**it, "relation": rel, "condition": cond}
        deg   = _degree_label(rel)
        if deg == "1st":
            first.append(entry)
        elif deg == "2nd":
            second.append(entry)
        else:
            extended.append(entry)

    def _get_person_entries(rel: str, name: str) -> list[dict]:
        """Collect all entries for a specific person across all items."""
        return [it for it in items
                if (it.get("relation") or "").strip() == rel
                and (it.get("name") or "").strip() == name]

    def _cond_row(e: dict) -> ft.Control:
        rel  = e.get("relation", "")
        name = (e.get("name") or "").strip()
        cond = e.get("condition", "")

        # Format: "(nickname, relationship)" or "(relationship)" if no nickname
        if name:
            who_label = f"({name}, {rel})"
        else:
            who_label = f"({rel})"

        row_c: list[ft.Control] = [
            ft.Icon(ft.Icons.CIRCLE, size=8 * s, color=ft.Colors.TEAL_400),
            ft.Text(cond, size=12 * s, expand=True),
            ft.Text(who_label, size=11 * s,
                    color=ft.Colors.GREY_500, italic=True),
        ]

        if on_node_click:
            row_c.append(
                ft.IconButton(
                    icon=ft.Icons.EDIT,
                    icon_size=14 * s,
                    tooltip="Edit family member",
                    icon_color=ft.Colors.GREY_500,
                    on_click=lambda e, r=rel, n=name: on_node_click(
                        r, n, _get_person_entries(r, n)),
                )
            )

        return ft.Row(row_c, spacing=6)

    def _make_col(title: str, subtitle: str, entries: list[dict]) -> ft.Control:
        rows: list[ft.Control] = [
            ft.Text(title, size=14 * s, weight="bold"),
            ft.Text(subtitle, size=11 * s, italic=True, color=ft.Colors.GREY_500),
            ft.Divider(height=8 * s),
        ]
        if entries:
            seen: set[tuple] = set()
            for e in entries:
                key = (e["relation"], e.get("name", ""), e["condition"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(_cond_row(e))
        else:
            rows.append(ft.Text("None recorded", italic=True,
                                color=ft.Colors.GREY_400, size=12 * s))
        return ft.Column(rows, spacing=4, expand=True)

    degree_row = ft.Row(
        [
            _make_col("1st Degree Relatives",
                      "Parents, siblings, children, half-siblings", first),
            ft.VerticalDivider(width=1),
            _make_col("2nd Degree Relatives",
                      "Grandparents, parents' siblings", second),
        ],
        vertical_alignment=ft.CrossAxisAlignment.START,
        expand=True,
        spacing=16 * s,
    )

    parts: list[ft.Control] = [degree_row]

    if extended:
        parts.append(ft.Divider())
        parts.append(ft.Text("Extended / Other Relatives", size=13 * s, weight="bold"))
        seen_ext: set[tuple] = set()
        for e in extended:
            key = (e["relation"], e.get("name", ""), e["condition"])
            if key in seen_ext:
                continue
            seen_ext.add(key)
            parts.append(_cond_row(e))

    return ft.Column(parts, spacing=4 * s)
