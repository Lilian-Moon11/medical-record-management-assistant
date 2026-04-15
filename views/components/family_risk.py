import flet as ft
from utils.ui_helpers import pt_scale
from views.components.family_helpers import _degree_label, _SEX_ICON, _SEX_COLOR

# ---------------------------------------------------------------------------
# Risk summary
# ---------------------------------------------------------------------------
def build_risk_summary(page: ft.Page, items: list[dict]) -> ft.Control:
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

    def _cond_row(e: dict) -> ft.Control:
        rel   = e.get("relation", "")
        name  = (e.get("name") or "").strip()
        cond  = e.get("condition", "")
        bs    = (e.get("biological_sex") or "").strip()
        glyph = _SEX_ICON.get(bs, "") if bs not in ("Unknown", "Prefer not to say", "") else ""
        g_col = _SEX_COLOR.get(bs, ft.Colors.GREY_400)
        who   = name if name else rel

        row_c: list[ft.Control] = [
            ft.Icon(ft.Icons.WARNING_AMBER, size=13 * s, color=ft.Colors.ORANGE_400),
            ft.Text(cond, size=12 * s, expand=True),
        ]
        if glyph:
            row_c.append(ft.Text(glyph, size=12 * s, color=g_col,
                                  tooltip=f"Biological sex: {bs}"))
        row_c.append(ft.Text(f"({who})", size=11 * s,
                              color=ft.Colors.GREY_500, italic=True))
        return ft.Row(row_c, spacing=4)

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

    disclaimer = ft.Container(
        bgcolor=ft.Colors.BLUE_50,
        border_radius=8 * s,
        border=ft.border.all(1 * s, ft.Colors.BLUE_200),
        padding=ft.padding.symmetric(horizontal=12 * s, vertical=8 * s),
        content=ft.Row([
            ft.Icon(ft.Icons.INFO_OUTLINE, color=ft.Colors.BLUE_600, size=16 * s),
            ft.Column([
                ft.Text("These conditions appear in your family history. "
                        "They are NOT your personal diagnoses.",
                        size=12 * s, color=ft.Colors.BLUE_700, italic=True),
                ft.Text("♀ ♂ ⚧  indicators show biological sex where relevant "
                        "to sex-linked conditions.",
                        size=11 * s, color=ft.Colors.BLUE_500),
            ], spacing=2, expand=True),
        ], spacing=8),
    )

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

    parts: list[ft.Control] = [disclaimer, ft.Container(height=8 * s), degree_row]

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

    return ft.Column(parts, spacing=8 * s)
