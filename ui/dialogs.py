# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Centralized dialog registration and secure recovery flows.
#
# This module creates and manages long-lived dialogs (stored on page.* and
# registered in page.overlay exactly once) to ensure reliable rendering across
# the app UI shell.
#
# Provides two primary dialog workflows:
# - Recovery key “ceremony” for displaying/copying a recovery key and requiring
#   explicit user confirmation (“I saved it”) before closing
# - “Forgot password” recovery flow using a recovery key to unlock the vault,
#   set a new password, and generate a new recovery key
#
# Security/UX design goals:
# - Prevent accidental lockout: recovery-key rotation is staged and only
#   committed after the user confirms they saved the new key
# - Keep key material transient: recovery keys are stored only in-memory for
#   display and clipboard copy, never persisted by this module
# - Fail closed with clear messaging if recovery/unlock operations fail
# - Make dialogs idempotent and reusable: safe to call registration repeatedly
#   without duplicating overlay entries or leaking UI state
# -----------------------------------------------------------------------------

from __future__ import annotations
import flet as ft

from database import open_vault_with_recovery, get_profile
from crypto.keybag import set_new_password, generate_recovery_key_b64, rotate_recovery_key
from core.startup import run_self_test
from utils import run_async, copy_with_snack


def ensure_dialogs_registered(page: ft.Page, *, s, show_snack):
    """
    Create dialogs once and append to page.overlay exactly once.
    Safe to call multiple times.
    """
    if not hasattr(page, "_dialogs_registered"):
        page._dialogs_registered = False

    if page._dialogs_registered:
        return

    # ---------------------------
    # Recovery ceremony dialog
    # ---------------------------
    page._current_recovery_key = ""  # transient; read by event handler (no closure capture)

    page._recovery_key_text = ft.Text("", selectable=True, font_family="Consolas", size=s(page, 14))
    page._recovery_saved_check = ft.Checkbox(
        label="I saved this recovery key somewhere safe.",
        value=False,
    )
    page._recovery_status = ft.Text("", color="red", size=s(page, 12))

    page._recovery_pending_note = ft.Container(
        padding=s(page, 10),
        border=ft.Border.all(1, ft.Colors.ORANGE),
        border_radius=6,
        visible=False,
        content=ft.Text(
            "Rotation is NOT final until you click 'I saved it.'\n"
            "If you close this dialog before confirming, the old recovery key remains valid.",
            color=ft.Colors.ORANGE,
            size=s(page, 12),
            ),
        )

    def copy_key(_):
        async def _do():
            ok = await copy_with_snack(
                page,
                page._current_recovery_key,
                ok_message="Recovery key copied to clipboard.",
                fail_message="Could not copy to clipboard on this platform.",
            )

            # Optional: visible confirmation inside the dialog
            page._recovery_status.value = "Copied to clipboard." if ok else "Copy failed on this platform."
            page._recovery_status.color = ft.Colors.GREEN if ok else ft.Colors.ORANGE

            try:
                page._recovery_dlg.update()
            except Exception:
                pass
            page.update()

        run_async(page, _do())

    def recovery_close(_=None):
        if not page._recovery_saved_check.value:
            page._recovery_status.value = "Please confirm you saved it before closing."
            try:
                page._recovery_dlg.update()
            except Exception:
                pass
            page.update()
            return

        # If we staged a recovery-key rotation, commit it now
        pending = getattr(page, "_pending_recovery_rotation", None)
        if pending:
            try:
                committed = rotate_recovery_key(
                    pending["db_path"],
                    pending["dmk_raw"],
                    new_recovery_key_b64=pending["new_key_b64"],
                )
                page.recovery_key_first_run = committed
                page._pending_recovery_rotation = None
                show_snack(page, "Recovery key rotation committed.", ft.Colors.GREEN)
            except Exception as ex:
                page._recovery_status.value = str(ex)
                page._recovery_status.color = ft.Colors.RED
                try:
                    page._recovery_dlg.update()
                except Exception:
                    pass
                page.update()
                return

        # Close dialog first
        page._recovery_dlg.open = False
        try:
            page._recovery_dlg.update()
        except Exception:
            pass
        page.update()

        # Then run optional callback
        after = getattr(page, "_after_recovery_ceremony", None)
        page._after_recovery_ceremony = None
        if callable(after):
            after()

    page._recovery_done_btn = ft.Button(
        "I saved it",
        icon=ft.Icons.CHECK,
        on_click=recovery_close,
        disabled=True,
    )

    def on_check(_):
        page._recovery_done_btn.disabled = not bool(page._recovery_saved_check.value)
        try:
            page._recovery_dlg.update()
        except Exception:
            pass
        page.update()

    page._recovery_saved_check.on_change = on_check

    page._recovery_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Save your recovery key", size=s(page, 18), weight="bold"),
        content=ft.Column(
            [
                ft.Text(
                    "This key lets you recover the vault if you forget your password.\n"
                    "If you lose BOTH your password and this key, the vault cannot be recovered.",
                    size=s(page, 14),
                ),

                page._recovery_pending_note,  

                ft.Container(
                    padding=s(page, 10),
                    border=ft.Border.all(2, ft.Colors.GREY),
                    border_radius=8,
                    content=page._recovery_key_text,
                ),
                ft.Row([ft.Button("Copy", icon=ft.Icons.CONTENT_COPY, on_click=copy_key)]),
                page._recovery_saved_check,
                page._recovery_status,
            ],
            tight=True,
            spacing=s(page, 10),
        ),
        actions=[page._recovery_done_btn],
        on_dismiss=recovery_close,
    )
    page.overlay.append(page._recovery_dlg)

    # ---------------------------
    # Forgot password dialog
    # ---------------------------
    page._forgot_recovery_field = ft.TextField(
        label="Recovery key",
        password=True,
        can_reveal_password=True,
        width=420,
    )
    page._forgot_new_pwd_field = ft.TextField(
        label="New database password",
        password=True,
        can_reveal_password=True,
        width=420,
    )
    page._forgot_new_pwd2_field = ft.TextField(
        label="Confirm new password",
        password=True,
        can_reveal_password=True,
        width=420,
    )
    page._forgot_status = ft.Text("", color="red")

    def forgot_close(_=None):
        page._forgot_dlg.open = False
        page.update()

    def do_recover(_):
        rk = (page._forgot_recovery_field.value or "").strip()
        p1 = page._forgot_new_pwd_field.value or ""
        p2 = page._forgot_new_pwd2_field.value or ""

        if not rk:
            page._forgot_status.value = "Recovery key is required."
            page.update()
            return
        if not p1 or len(p1) < 8:
            page._forgot_status.value = "New password must be at least 8 characters."
            page.update()
            return
        if p1 != p2:
            page._forgot_status.value = "Passwords do not match."
            page.update()
            return

        conn = None
        try:
            conn, dmk_raw, db_path = open_vault_with_recovery(rk)
            set_new_password(db_path, dmk_raw, p1)
            #Stage rotation: show key now, commit only after "I saved it"
            staged_rk = generate_recovery_key_b64()
            page._pending_recovery_rotation = {
                "db_path": db_path,
                "dmk_raw": dmk_raw,
                "new_key_b64": staged_rk,
            }

            page.recovery_key_first_run = staged_rk  # for display only; not committed yet

            page._forgot_dlg.open = False
            try:
                page._forgot_dlg.update()
            except Exception:
                pass

            # Clear reset fields (good hygiene)
            page._forgot_recovery_field.value = ""
            page._forgot_new_pwd_field.value = ""
            page._forgot_new_pwd2_field.value = ""
            page._forgot_status.value = ""
            page.update()

            # After ceremony: show message and return to login (if available)
            def _after():
                go_login = getattr(page, "_go_login", None)
                if callable(go_login):
                    go_login()

                show_snack(
                    page,
                    "Password updated successfully. Please log in with your new password.",
                    ft.Colors.GREEN,
                )

            page._after_recovery_ceremony = _after

            # Show ceremony (now includes the warning)
            show_recovery_ceremony(page, staged_rk, s=s, show_snack=show_snack)

        except Exception as ex:
            page._forgot_status.value = str(ex)
            page.update()
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    page._forgot_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Recover account"),
        content=ft.Column(
            [
                ft.Text(
                    "Enter your recovery key and choose a new password.\n"
                    "After recovery, a NEW recovery key will be generated."
                ),
                page._forgot_recovery_field,
                page._forgot_new_pwd_field,
                page._forgot_new_pwd2_field,
                page._forgot_status,
            ],
            tight=True,
        ),
        actions=[
            ft.TextButton("Cancel", on_click=forgot_close),
            ft.Button("Reset password", icon=ft.Icons.LOCK_RESET, on_click=do_recover),
        ],
        on_dismiss=forgot_close,
    )
    page.overlay.append(page._forgot_dlg)

    page._dialogs_registered = True


def open_forgot_password(page: ft.Page, *, s, show_snack):
    ensure_dialogs_registered(page, s=s, show_snack=show_snack)
    page._forgot_status.value = ""
    page._forgot_dlg.open = True
    page.update()


def show_recovery_ceremony(page: ft.Page, recovery_key: str, *, s, show_snack):
    ensure_dialogs_registered(page, s=s, show_snack=show_snack)

    page._recovery_pending_note.visible = bool(getattr(page, "_pending_recovery_rotation", None))

    page._current_recovery_key = recovery_key
    page._recovery_key_text.value = recovery_key
    page._recovery_saved_check.value = False
    page._recovery_status.value = ""
    page._recovery_done_btn.disabled = True

    page._recovery_dlg.open = True
    try:
        page._recovery_dlg.update()
    except Exception:
        pass
    page.update()