# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Shared UI and data-entry utilities for consistent, accessible rendering
# across the app.
#
# This module centralizes small helpers used throughout views to keep UI
# behavior consistent, reduce duplication, and support accessibility features.
#
# Includes:
# - Scale-safe sizing helpers that respect the users UI scale preference
# - Centralized snackbar messaging (create-once, reuse) with defensive handling
# - Async helpers for running coroutines from sync event handlers
# - Clipboard copy utilities with user feedback (snackbar success/failure)
# - Theme-aware panel/container helpers that adapt to light/dark mode and
#   enforce high-contrast accessibility when enabled
# - Lightweight field/label utilities for dynamic forms:
#   - data-type detection from human labels
#   - slug generation for stable field keys
#   - label cleanup and sensitive-flag parsing
# - Small UI affordances (e.g., reveal/hide eye icon button)
# -----------------------------------------------------------------------------

import flet as ft
import re

def is_sensitive_flag(v) -> bool:
    try:
        return int(v or 0) == 1
    except Exception:
        return False

def detect_data_type_from_label(label: str) -> str:
    l = (label or "").strip().lower()
    if "email" in l: return "email"
    if any(k in l for k in ["phone", "mobile", "cell", "tel"]): return "phone"
    if any(k in l for k in ["dob", "birth", "birthday", "date"]): return "date"
    if any(k in l for k in ["allerg", "medication", "meds", "rx", "immun", "vaccine", "list"]): return "json"
    return "text"

def slugify_label(label: str) -> str:
    s = (label or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", ".", s)
    s = re.sub(r"\.+", ".", s).strip(".")
    return s or "field"

def clean_lbl(lbl: str) -> str:
    """Removes lingering JSON text from labels fetched from the DB"""
    s = str(lbl or "")
    s = s.replace("(FHIR-lite JSON list)", "")
    s = s.replace("(JSON)", "")
    return s.strip()

def pt_scale(page: ft.Page, px: int) -> int:
    """Scale-safe sizing helper."""
    scale = getattr(page, "ui_scale", 1.0)
    if scale is None:
        scale = 1.0
    return int(px * scale)

def show_snack(page: ft.Page, message: str, color=ft.Colors.GREEN):
    """Displays a snackbar message (create once, reuse forever). Accepts ft.Colors.* or 'green'/'red' strings."""
    try:
        # Allow legacy string colors
        if isinstance(color, str):
            color_map = {
                "green": ft.Colors.GREEN,
                "red": ft.Colors.RED,
                "orange": ft.Colors.ORANGE,
                "blue": ft.Colors.BLUE,
                "yellow": ft.Colors.YELLOW,
            }
            color = color_map.get(color.lower(), ft.Colors.GREEN)

        if not hasattr(page, "_snack_text") or page._snack_text is None:
            page._snack_text = ft.Text("")
            page.snack_bar = ft.SnackBar(content=page._snack_text, bgcolor=ft.Colors.GREEN)

        page._snack_text.value = message
        page.snack_bar.bgcolor = color
        page.snack_bar.open = True
        page.update()
    except Exception as ex:
        print("SNACK ERROR:", ex, "| message:", message)

def run_async(page: ft.Page, coro):
    """Run a coroutine reliably from a sync event handler."""
    try:
        if hasattr(page, "run_task"):
            page.run_task(coro)
            return
    except Exception:
        pass

    import asyncio
    asyncio.create_task(coro)

async def copy_with_snack(
    page: ft.Page,
    text: str,
    ok_message: str = "Copied to clipboard.",
    fail_message: str = "Could not copy to clipboard on this platform.",
    ok_color=ft.Colors.GREEN,
    fail_color=ft.Colors.ORANGE,
) -> bool:
    """
    Clipboard copy + snackbar.
    Uses ft.Clipboard().set() (works for you in Settings).
    """
    text = text or ""
    try:
        await ft.Clipboard().set(text)
        show_snack(page, ok_message, ok_color)
        return True
    except Exception as ex:
        print("CLIPBOARD FAIL:", ex)
        show_snack(page, fail_message, fail_color)
        return False

def themed_panel(page: ft.Page, content, padding=None, radius=6):
    """
    A theme-safe container that looks good in light/dark,
    and enforces high-contrast when enabled.
    """
    hc = getattr(page, "is_high_contrast", False)

    if padding is None:
        padding = pt_scale(page, 15)

    if hc:
        if isinstance(content, ft.Text) and content.color is None:
            content.color = ft.Colors.YELLOW

        return ft.Container(
            content=content,
            padding=padding,
            bgcolor=ft.Colors.BLACK,
            border=ft.Border.all(2, ft.Colors.YELLOW),
            border_radius=radius,
        )

    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=None,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
        if hasattr(ft.Colors, "OUTLINE_VARIANT") else None,
        border_radius=radius,
    )

def make_eye_btn(page: ft.Page, revealed: bool, visible: bool = True) -> "ft.IconButton":
    return ft.IconButton(
        icon=ft.Icons.VISIBILITY_OFF if revealed else ft.Icons.VISIBILITY,
        tooltip="Hide" if revealed else "Reveal",
        visible=visible,
    )