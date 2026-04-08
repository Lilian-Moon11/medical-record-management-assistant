# Copyright (C) 2026 Lilian-Moon11
# Placement Review — interactive drag-to-correct dialog for static PDF overlays.
#
# Shows the AI-merged PDF as a scrollable background image. Each placed text
# value appears as a draggable chip the user can slide to its correct position.
# Clicking "Save Final" rebuilds the overlay with updated coordinates.

import os
import logging
import flet as ft
from typing import Callable

logger = logging.getLogger(__name__)


def open_placement_review(
    page: ft.Page,
    merged_pdf_bytes: bytes,
    fill_items: list[dict],
    template_path: str,
    on_confirm: Callable[[bytes], None],
):
    """
    Open the placement review dialog.

    Parameters
    ----------
    page               Flet page reference.
    merged_pdf_bytes   The already-merged PDF (overlay + template) to display
                       as the background — user sees current placement and drags
                       chips to fix any misaligned items.
    fill_items         List of dicts from fill_static_pdf, each containing:
                       label, value, page, x_pt, y_pt, page_height, page_width.
    template_path      Path to the original (blank) template PDF, used to
                       rebuild the overlay with corrected coordinates on save.
    on_confirm         Callback receiving the final (corrected) PDF bytes.
    """
    from ai.paperwork_overlay import render_page_images, rebuild_overlay

    # Render the MERGED PDF so the user sees the current (approximate) placement.
    img_paths = render_page_images(merged_pdf_bytes, dpi=150)

    if not img_paths:
        # pypdfium2 failed or not available — skip review and confirm immediately.
        logger.warning("placement_review: page render unavailable, skipping review")
        on_confirm(merged_pdf_bytes)
        return

    # Mutable copies of fill_items — drag updates modify these in-place.
    # Keep items that have either a text value OR a sig_path image.
    items = [dict(f) for f in fill_items if f.get("value") or f.get("sig_path")]

    # ------------------------------------------------------------------ #
    # Compute display sizing from actual window dimensions.
    # Use 88% of the window width, leaving room for dialog padding.
    # ------------------------------------------------------------------ #
    win_w = page.width or 1000
    win_h = page.height or 800

    # Page display width: as large as fits comfortably in the dialog
    page_display_w = max(480, int(win_w * 0.82))

    # ------------------------------------------------------------------ #
    # Build one ft.Stack per page, all inside a scrollable Column.
    # ------------------------------------------------------------------ #
    page_sections: list[ft.Control] = []

    for page_idx, img_path in enumerate(img_paths):
        page_items = [it for it in items if it["page"] == page_idx]

        # Use first item's geometry, or Letter defaults.
        if page_items:
            ph = page_items[0]["page_height"]
            pw = page_items[0]["page_width"]
        else:
            # Fall back to first item on any page, or Letter
            any_item = next((it for it in items), None)
            ph = any_item["page_height"] if any_item else 792.0
            pw = any_item["page_width"]  if any_item else 612.0

        display_scale = page_display_w / pw
        display_height = ph * display_scale

        stack_controls: list[ft.Control] = [
            ft.Image(
                src=img_path,
                width=page_display_w,
                height=display_height,
                fit="fill",
            )
        ]

        for item in page_items:
            chip = _make_chip(page, item, display_scale, display_height,
                              page_display_w, items)
            stack_controls.append(chip)

        page_sections.append(
            ft.Container(
                content=ft.Stack(
                    controls=stack_controls,
                    width=page_display_w,
                    height=display_height,
                ),
                border=ft.border.all(1, ft.Colors.OUTLINE),
                margin=ft.margin.only(bottom=16),
            )
        )

    # ------------------------------------------------------------------ #
    # Dialog actions
    # ------------------------------------------------------------------ #
    def _cleanup():
        for p in img_paths:
            try:
                os.remove(p)
            except Exception:
                pass

    def _on_save(_e):
        dlg.open = False
        page.update()
        _cleanup()

        try:
            final_bytes = rebuild_overlay(template_path, items)
        except Exception as exc:
            logger.error("placement_review: rebuild failed: %s", exc)
            final_bytes = merged_pdf_bytes
        on_confirm(final_bytes)

    def _on_cancel(_e):
        dlg.open = False
        page.update()
        _cleanup()
        # Confirm with the original merged result so the wizard can still save.
        on_confirm(merged_pdf_bytes)

    # ------------------------------------------------------------------ #
    # Assemble dialog — use almost the full window
    # ------------------------------------------------------------------ #
    dialog_content_w = page_display_w + 32   # chip + scroll bar clearance
    dialog_content_h = int(win_h * 0.88)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Icon(ft.Icons.DRAG_INDICATOR, color=ft.Colors.PRIMARY),
            ft.Text(
                "  Review & Adjust Placement",
                weight="bold",
                size=17,
            ),
        ]),
        content=ft.Container(
            width=dialog_content_w,
            height=dialog_content_h,
            content=ft.Column(
                controls=[
                    ft.Container(
                        bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.PRIMARY),
                        border_radius=6,
                        padding=ft.padding.symmetric(horizontal=12, vertical=8),
                        content=ft.Text(
                            "Drag any chip to reposition it on the form, "
                            "then click  Save Final  to write the corrected PDF.",
                            size=13,
                            color=ft.Colors.ON_SURFACE,
                        ),
                    ),
                    ft.Divider(height=10),
                    *page_sections,
                ],
                scroll=ft.ScrollMode.AUTO,
                spacing=0,
            ),
        ),
        actions=[
            ft.TextButton("Cancel (keep original)", on_click=_on_cancel),
            ft.FilledButton(
                "Save Final",
                icon=ft.Icons.SAVE_ALT,
                on_click=_on_save,
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        # Remove default dialog max-width so our wider content can show
        inset_padding=ft.padding.symmetric(horizontal=10, vertical=16),
    )

    if dlg not in page.overlay:
        page.overlay.append(dlg)
    dlg.open = True
    page.update()


# ------------------------------------------------------------------ #
# Chip builder
# ------------------------------------------------------------------ #

def _make_chip(
    page: ft.Page,
    item: dict,
    display_scale: float,
    display_height: float,
    page_display_w: int,
    items_list: list[dict],
) -> ft.Container:
    """
    Build a draggable chip positioned at the item's current PDF coordinates.

    The chip is a ft.Container with left/top set for absolute Stack positioning,
    wrapping a ft.GestureDetector that tracks pan deltas.
    """
    is_sig = bool(item.get("sig_path"))
    value = str(item.get("value", ""))
    if is_sig:
        display_value = "✍  Signature"
    else:
        display_value = value[:42] + "…" if len(value) > 42 else value

    # Convert PDF coords → display pixels
    # PDF y=0 is bottom; display y=0 is top → flip y
    initial_left = item["x_pt"] * display_scale
    initial_top  = (item["page_height"] - item["y_pt"]) * display_scale - 20

    # Single-row chip: roughly 160px wide, 32px tall
    CHIP_W = 160
    CHIP_H = 32
    initial_left = max(0.0, min(initial_left, page_display_w - CHIP_W))
    initial_top  = max(0.0, min(initial_top,  display_height - CHIP_H))

    # Signature chips get a distinct teal accent so they stand out.
    chip_border_color = ft.Colors.TEAL_400 if is_sig else ft.Colors.PRIMARY

    def _delete_chip(e):
        if item in items_list:
            items_list.remove(item)
        outer.visible = False
        page.update()

    row_controls = [
        ft.Text(
            display_value,
            size=12,
            color=ft.Colors.ON_SURFACE,
            weight=ft.FontWeight.W_500 if is_sig else None,
            no_wrap=True,
        ),
        ft.IconButton(
            icon=ft.Icons.CLOSE,
            icon_size=14,
            width=24,
            height=24,
            padding=0,
            tooltip="Delete",
            on_click=_delete_chip,
        )
    ]

    chip_visual = ft.Container(
        bgcolor=ft.Colors.with_opacity(0.97, ft.Colors.SURFACE),
        border=ft.border.all(2, chip_border_color),
        border_radius=5,
        padding=ft.padding.only(left=8, right=2, top=2, bottom=2),
        content=ft.Row(row_controls, alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=4),
        shadow=ft.BoxShadow(
            blur_radius=8,
            spread_radius=1,
            color=ft.Colors.with_opacity(0.35, ft.Colors.SHADOW),
            offset=ft.Offset(1, 2),
        ),
    )

    # Current display-space position (mutable via closure).
    _pos = {"left": initial_left, "top": initial_top}

    def _on_pan_update(e: ft.DragUpdateEvent):
        # Flet 0.80+ uses e.local_delta (an Offset) instead of e.delta_x/delta_y.
        delta = e.local_delta
        if delta is None:
            return
        _pos["left"] = max(0.0, min(_pos["left"] + delta.x, page_display_w - 4))
        _pos["top"]  = max(0.0, min(_pos["top"]  + delta.y, display_height - 4))
        outer.left = _pos["left"]
        outer.top  = _pos["top"]
        # Write back to PDF coordinate space (inverse of the initial_top formula).
        item["x_pt"] = _pos["left"] / display_scale
        item["y_pt"] = item["page_height"] - (_pos["top"] + 20) / display_scale
        # page.update() is required — control.update() doesn't reliably propagate
        # through a Stack that lives inside an AlertDialog overlay in Flet.
        page.update()

    gesture = ft.GestureDetector(
        content=chip_visual,
        on_pan_update=_on_pan_update,
        drag_interval=16,
        mouse_cursor=ft.MouseCursor.MOVE,
    )

    outer = ft.Container(
        left=initial_left,
        top=initial_top,
        content=gesture,
    )

    return outer
