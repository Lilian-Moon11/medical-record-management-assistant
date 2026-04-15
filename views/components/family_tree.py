import flet as ft
from utils.ui_helpers import OUTLINE_VARIANT, pt_scale
from views.components.family_helpers import _sex_indicator


# ---------------------------------------------------------------------------
# Node card
# ---------------------------------------------------------------------------
def _node_card(
    page: ft.Page,
    relation: str,
    display_name: str,
    entries: list[dict],
    on_click=None,
    is_you: bool = False,
) -> ft.Control:
    s = pt_scale(page, 1)
    is_half_sib = (relation == "Half-Sibling")

    if is_you:
        return ft.Container(
            width=110 * s,
            height=72 * s,
            border_radius=10 * s,
            bgcolor=ft.Colors.TEAL_700,
            border=ft.border.all(3 * s, ft.Colors.TEAL_300),
            alignment=ft.Alignment(x=0, y=0),
            tooltip="You — your own diagnoses live in Health Record",
            content=ft.Column(
                [ft.Text("⭐  YOU", size=13 * s, weight="bold",
                         color=ft.Colors.WHITE, text_align=ft.TextAlign.CENTER)],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    cond_count = len(entries)
    badge_text  = (f"{cond_count} condition{'s' if cond_count != 1 else ''}"
                   if cond_count else "No conditions")
    badge_color = ft.Colors.ORANGE_600 if cond_count > 0 else ft.Colors.GREY_500

    # Name line (bold) + relation subtitle
    top_label = display_name if display_name else relation
    sub_label  = relation if display_name else ""

    # Sex indicator
    glyph, g_color = _sex_indicator(entries)

    # Half-sibling via-parent chip
    shared_chip = None
    if is_half_sib and entries:
        shared = entries[0].get("shared_parent") or entries[0].get("related_to_name") or ""
        if shared:
            shared_chip = ft.Text(f"via {shared}", size=7 * s,
                                  color=ft.Colors.AMBER_300, italic=True,
                                  text_align=ft.TextAlign.CENTER)

    name_row: list[ft.Control] = []
    if glyph:
        name_row.append(ft.Text(glyph, size=10 * s, color=g_color,
                                tooltip=f"Biological sex: {entries[0].get('biological_sex','')}"))
    name_row.append(ft.Text(top_label, size=9 * s, weight="bold",
                             text_align=ft.TextAlign.CENTER, color=ft.Colors.ON_SURFACE,
                             expand=True))

    card_items: list[ft.Control] = [
        ft.Row(name_row, alignment=ft.MainAxisAlignment.CENTER,
               vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
    ]
    if sub_label:
        card_items.append(ft.Text(sub_label, size=7 * s, color=ft.Colors.ON_SURFACE_VARIANT,
                                  text_align=ft.TextAlign.CENTER))
    card_items.append(
        ft.Container(
            content=ft.Text(badge_text, size=7 * s, color=ft.Colors.WHITE,
                            text_align=ft.TextAlign.CENTER),
            bgcolor=badge_color,
            border_radius=6 * s,
            padding=ft.padding.symmetric(horizontal=4 * s, vertical=1 * s),
        )
    )
    if shared_chip:
        card_items.append(shared_chip)

    # Half-sibling: amber 2px border to signal "dashed/shared" relationship
    border_color  = ft.Colors.AMBER_500 if is_half_sib else OUTLINE_VARIANT
    border_width  = 2 * s           if is_half_sib else 1 * s
    card_bgcolor  = ft.Colors.SURFACE_CONTAINER_HIGHEST if cond_count > 0 else ft.Colors.SURFACE_CONTAINER_HIGH

    return ft.Container(
        width=100 * s,
        height=72 * s,
        border_radius=8 * s,
        bgcolor=card_bgcolor,
        border=ft.border.all(border_width, border_color),
        alignment=ft.Alignment(x=0, y=0),
        ink=on_click is not None,
        on_click=on_click,
        tooltip=f"Click to view {display_name or relation}" if on_click else None,
        content=ft.Column(
            card_items,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=2,
        ),
    )


def _empty_slot(page: ft.Page) -> ft.Control:
    s = pt_scale(page, 1)
    return ft.Container(
        width=100 * s, height=72 * s,
        border_radius=8 * s,
        bgcolor=ft.Colors.SURFACE_CONTAINER,
        border=ft.Border.all(1 * s, OUTLINE_VARIANT),
        opacity=0.2,
    )


# ---------------------------------------------------------------------------
# Tree legend
# ---------------------------------------------------------------------------
def build_legend(page: ft.Page) -> ft.Control:
    s = pt_scale(page, 1)
    def _swatch(color, border_w, label):
        return ft.Row([
            ft.Container(
                width=24 * s, height=16 * s,
                border_radius=4 * s,
                border=ft.border.all(border_w * s, color),
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            ),
            ft.Text(label, size=11 * s, color=ft.Colors.GREY_500),
        ], spacing=6)

    return ft.Row([
        _swatch(OUTLINE_VARIANT, 1, "Direct relative"),
        ft.Container(width=16 * s),
        _swatch(ft.Colors.AMBER_500, 2, "Half-sibling (shared parent)"),
        ft.Container(width=16 * s),
        ft.Row([
            ft.Container(
                width=24 * s, height=16 * s,
                border_radius=4 * s,
                bgcolor=ft.Colors.TEAL_700,
                border=ft.border.all(2 * s, ft.Colors.TEAL_300),
            ),
            ft.Text("You", size=11 * s, color=ft.Colors.GREY_500),
        ], spacing=6),
    ], spacing=0)


# ---------------------------------------------------------------------------
# Tree connector helpers
# ---------------------------------------------------------------------------
def _conn_gap_solid(s, color=None) -> ft.Control:
    """Gap-width container with a solid horizontal line through its center."""
    c = color or ft.Colors.ON_SURFACE_VARIANT
    return ft.Container(
        width=14 * s, height=72 * s,
        content=ft.Container(width=14 * s, height=2 * s, bgcolor=c),
        alignment=ft.Alignment(x=0, y=0),
    )


def _conn_gap_dotted(s, color=None) -> ft.Control:
    """Gap-width container with a dotted horizontal line through its center."""
    c = color or ft.Colors.AMBER_400
    dot, sp = 3 * s, 2 * s
    dots: list[ft.Control] = []
    for i in range(4):
        if i:
            dots.append(ft.Container(width=sp, height=2 * s))
        dots.append(ft.Container(width=dot, height=2 * s, bgcolor=c))
    return ft.Container(
        width=14 * s, height=72 * s,
        content=ft.Row(dots, spacing=0),
        alignment=ft.Alignment(x=0, y=0),
    )


def _vstem_row(s, has_parents: bool) -> ft.Control:
    """Plain gap row between parent and sibling rows."""
    return ft.Container(height=18 * s)


# ---------------------------------------------------------------------------
# Tree builder
# ---------------------------------------------------------------------------
def build_tree(
    page: ft.Page,
    by_relation: dict[str, list[tuple[str, list[dict]]]],
    on_node_click
) -> ft.Control:
    s   = pt_scale(page, 1)

    try:
        import flet.canvas as cv
        _cv_ok = True
    except Exception:
        _cv_ok = False

    CARD_W   = 100 * s
    CARD_H   = 72  * s
    GAP      = 14  * s
    ROW_GAP  = 18  * s
    CANVAS_W = int(800 * s)   # fixed width; rows center within this

    def mk_gap():
        return ft.Container(width=GAP)

    def make_node(relation, display_name, entries):
        return _node_card(
            page, relation, display_name, entries,
            on_click=lambda e, r=relation, n=display_name, ent=entries: on_node_click(r, n, ent),
        )

    def nodes_for(relation: str, max_nodes: int = 3) -> list[ft.Control]:
        people = by_relation.get(relation, [])
        if not people:
            return [_empty_slot(page)]
        return [make_node(relation, name, entries)
                for name, entries in people[:max_nodes]]

    def spaced_row(controls: list[ft.Control]) -> ft.Row:
        items: list[ft.Control] = []
        for i, c in enumerate(controls):
            if i:
                items.append(mk_gap())
            items.append(c)
        return ft.Row(items, alignment=ft.MainAxisAlignment.CENTER,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0)

    # ── Row 0: Grandparents ──
    gp_nodes = nodes_for("Grandparent", 4)
    gp_row   = spaced_row(gp_nodes)

    # ── Row 1: Parents + Parent's Siblings ──
    has_ps  = "Parent's Sibling" in by_relation
    has_par = "Parent"           in by_relation

    ps_nodes  = nodes_for("Parent's Sibling", 2)
    par_nodes = nodes_for("Parent", 2)

    p_items: list[ft.Control] = []
    for n in ps_nodes[:1]:
        p_items.append(n)
        p_items.append(_conn_gap_solid(s) if (has_ps and has_par) else mk_gap())
    p_items += par_nodes
    for n in ps_nodes[1:2]:
        p_items.append(_conn_gap_solid(s) if (has_ps and has_par) else mk_gap())
        p_items.append(n)
    parents_row = spaced_row(p_items)

    # ── Row 2: Siblings + Half-Siblings + YOU ──
    has_hs = "Half-Sibling" in by_relation

    sib_nodes      = nodes_for("Sibling", 3)
    half_sib_nodes = nodes_for("Half-Sibling", 2)
    you_node       = _node_card(page, "YOU", "", [], is_you=True)

    sib_items: list[ft.Control] = []
    for n in sib_nodes:
        sib_items += [n, mk_gap()]
    for i, n in enumerate(half_sib_nodes):
        sib_items.append(n)
        last = (i == len(half_sib_nodes) - 1)
        sib_items.append(_conn_gap_dotted(s) if (has_hs and last) else mk_gap())
    sib_items.append(you_node)
    siblings_row = spaced_row(sib_items)

    # ── Position math (all rows are centered in CANVAS_W) ──
    # Row 1 slots: [PS_left(1), par_nodes(n), PS_right(0-1)]
    n_par      = len(par_nodes)
    n_ps_right = 1 if len(ps_nodes) > 1 else 0
    n_row1     = 1 + n_par + n_ps_right
    row1_w     = n_row1 * CARD_W + (n_row1 - 1) * GAP
    row1_left  = (CANVAS_W - row1_w) / 2
    par_xs     = [row1_left + (1 + i) * (CARD_W + GAP) + CARD_W / 2
                  for i in range(n_par)]
    par_cx     = sum(par_xs) / len(par_xs)    # H-center of parent card(s)

    # Row 2 slots: [sib_nodes(n), half_sib_nodes(n), YOU(1)]
    n_sib   = len(sib_nodes)
    n_hs    = len(half_sib_nodes)
    n_row2  = n_sib + n_hs + 1
    row2_w  = n_row2 * CARD_W + (n_row2 - 1) * GAP
    row2_left = (CANVAS_W - row2_w) / 2
    you_x   = row2_left + (n_row2 - 1) * (CARD_W + GAP) + CARD_W / 2

    # Y positions (spacing=0; gaps are explicit Container heights in the column)
    y0b = CARD_H                     # gp row bottom
    y1t = CARD_H + ROW_GAP          # parent row top
    y1b = 2 * CARD_H + ROW_GAP      # parent row bottom
    y2t = 2 * CARD_H + 2 * ROW_GAP  # sibling row top

    has_ch = "Child" in by_relation
    y2b = 2 * CARD_H + 3 * ROW_GAP
    tree_h = int((3 * CARD_H + 3 * ROW_GAP) if has_ch else (y2t + CARD_H))

    LW       = max(1, int(2 * s))
    LINE_CLR = ft.Colors.with_opacity(0.55, ft.Colors.ON_SURFACE_VARIANT)

    def _lconn(cx_from: float, cx_to: float) -> ft.Control:
        """ROW_GAP-tall zone with an L-shaped line from cx_from (above) to cx_to (below)."""
        h   = int(ROW_GAP)
        mid = h // 2
        lf  = int(cx_from) - LW // 2
        lt  = int(cx_to)   - LW // 2
        lx  = min(lf, lt)
        hw  = abs(int(cx_from) - int(cx_to)) + LW
        return ft.Stack([
            ft.Container(width=CANVAS_W, height=h),
            ft.Container(left=lf, top=0,             width=LW, height=mid,     bgcolor=LINE_CLR),
            ft.Container(left=lx, top=mid - LW // 2, width=hw, height=LW,      bgcolor=LINE_CLR),
            ft.Container(left=lt, top=mid,            width=LW, height=h - mid, bgcolor=LINE_CLR),
        ])

    def _gap() -> ft.Control:
        return ft.Container(height=int(ROW_GAP))

    # ── Assemble rows ──
    rows: list[ft.Control] = [gp_row]
    rows.append(_lconn(CANVAS_W / 2, par_cx) if (has_par and "Grandparent" in by_relation) else _gap())
    rows.append(parents_row)
    rows.append(_lconn(par_cx, you_x) if has_par else _gap())
    rows.append(siblings_row)

    if has_ch:
        rows += [_gap(), spaced_row(nodes_for("Child", 4))]

    other_nodes = by_relation.get("Other", [])
    if other_nodes:
        other_cards = [make_node("Other", n, e) for n, e in other_nodes[:4]]
        rows.append(ft.Container(height=int(8 * s)))
        rows.append(ft.Row(
            [ft.Text("Other relatives:", size=11 * s,
                     color=ft.Colors.GREY_500, italic=True),
             *[ft.Container(content=c, margin=ft.margin.only(left=4 * s))
               for c in other_cards]],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
        ))

    return ft.Container(
        width=CANVAS_W,
        content=ft.Column(rows, spacing=0,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER),
    )
