# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Settings screen and preference orchestration for the app.
#
# Provides the UI and wiring for app-wide preferences (theme, high contrast,
# large text), persisting changes to the database and immediately re-applying
# them via a callback into main.py for instant visual feedback.
#
# Also includes a secure recovery-key rotation flow:
# - Requires the current database password for verification
# - Confirms intent before rotating (destructive to the old key)
# - Generates and displays the new recovery key with copy support
#
# Emphasizes safety and clarity: settings changes are durable (DB-backed),
# UI updates are immediate, and key-management actions are guarded with
# explicit checks and user messaging.
# -----------------------------------------------------------------------------

import os
import flet as ft
from database import get_setting, set_setting
from utils.ui_helpers import pt_scale, show_snack, run_async, copy_with_snack
from utils.airlock import export_profile
from crypto.keybag import verify_password, rotate_recovery_key, generate_recovery_key_b64


def get_settings_view(page: ft.Page, apply_settings_callback):
    """
    Args:
        page: The Flet page object.
        apply_settings_callback: A function passed from main.py that re-runs 
                                 'apply_settings()' to refresh the theme instantly.
    """

    # 1. Load current values from DB (or defaults)
    current_theme = get_setting(page.db_connection, "ui.theme", "system")
    is_high_contrast = get_setting(page.db_connection, "ui.high_contrast", "0") == "1"
    is_large_text = get_setting(page.db_connection, "ui.large_text", "0") == "1"
    is_show_source = get_setting(page.db_connection, "ui.show_source", "0") == "1"
    is_show_updated = get_setting(page.db_connection, "ui.show_updated", "0") == "1"

    # â”€â”€ Export My Data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    export_status = ft.Text("", size=pt_scale(page, 14))
    export_progress = ft.ProgressRing(visible=False, width=20, height=20)

    async def export_click(e):
        result = await ft.FilePicker().save_file(
            dialog_title="Save profile backup",
            file_name="my_medical_profile.zip",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["zip"],
        )
        if not result:
            return
        dest = result if isinstance(result, str) else getattr(result, 'path', str(result))
        if not dest:
            return
        if not dest.lower().endswith(".zip"):
            dest += ".zip"
        export_status.value = "Exporting\u2026"
        export_status.color = ft.Colors.GREY
        export_progress.visible = True
        page.update()
        try:
            data_dir = os.path.join(os.path.dirname(page.db_path), "data")
            export_profile(
                conn=page.db_connection,
                dmk_raw=page.db_key_raw,
                data_dir=data_dir,
                dest_path=dest,
                zip_password=page.db_password,
            )
            export_status.value = f"Exported to {os.path.basename(dest)}"
            export_status.color = ft.Colors.GREEN
            show_snack(page, "Profile exported successfully.", ft.Colors.GREEN)
        except Exception as ex:
            export_status.value = f"Export failed: {ex}"
            export_status.color = ft.Colors.RED
            show_snack(page, f"Export failed: {ex}", ft.Colors.RED)
        finally:
            export_progress.visible = False
            page.update()

    # 2. Define Controls
    theme_dd = ft.Dropdown(
        label="Theme",
        width=300,
        options=[
            ft.dropdown.Option("system", "System default"),
            ft.dropdown.Option("light", "Light"),
            ft.dropdown.Option("dark", "Dark"),
        ],
        value=current_theme,
    )

    hc_switch = ft.Switch(label="High contrast", value=is_high_contrast)
    lt_switch = ft.Switch(label="Large text", value=is_large_text)

    def _auto_save_source(e):
        set_setting(page.db_connection, "ui.show_source", "1" if source_cb.value else "0")
        apply_settings_callback()

    def _auto_save_updated(e):
        set_setting(page.db_connection, "ui.show_updated", "1" if updated_cb.value else "0")
        apply_settings_callback()

    source_cb = ft.Checkbox(label="Show source of information", value=is_show_source, on_change=_auto_save_source)
    updated_cb = ft.Checkbox(label="Show updated date", value=is_show_updated, on_change=_auto_save_updated)

    # 3. Logic: Save and Apply
    def save_settings(e):
        # Save to DB
        set_setting(page.db_connection, "ui.theme", theme_dd.value)
        set_setting(page.db_connection, "ui.high_contrast", "1" if hc_switch.value else "0")
        set_setting(page.db_connection, "ui.large_text", "1" if lt_switch.value else "0")
        
        # Trigger the visual update in main.py
        apply_settings_callback()
        
        show_snack(page, "Settings saved.", ft.Colors.GREEN)

    def reset_settings(e):
        # Reset DB
        set_setting(page.db_connection, "ui.theme", "system")
        set_setting(page.db_connection, "ui.high_contrast", "0")
        set_setting(page.db_connection, "ui.large_text", "0")
        set_setting(page.db_connection, "ui.show_source", "0")
        set_setting(page.db_connection, "ui.show_updated", "0")

        # Reset Controls
        theme_dd.value = "system"
        hc_switch.value = False
        lt_switch.value = False
        source_cb.value = False
        updated_cb.value = False
        page.update()

        # Trigger visual update
        apply_settings_callback()
        show_snack(page, "Defaults restored.", ft.Colors.BLUE)

    # Rotate Recovery Key
    current_pwd_field = ft.TextField(
        label="Current database password (required to rotate recovery key)",
        password=True,
        can_reveal_password=True,
        width=420,
    )

    def show_new_recovery_key_ceremony(new_key):
        # Create dialog once and reuse it
        if not hasattr(page, "_new_rk_dlg") or page._new_rk_dlg is None:
            page._new_rk_value = ""

            page._new_rk_text = ft.Text(
                "",
                selectable=True,
                font_family="Consolas",
            )

            page._new_rk_saved_check = ft.Checkbox(
                label="I saved this recovery key somewhere safe.",
                value=False,
            )

            page._new_rk_status = ft.Text("", color="red")

            page._new_rk_done_btn = ft.Button(
                "I saved it",
                icon=ft.Icons.CHECK,
                disabled=True,
            )

            def copy_key(_):
                # Immediate UI proof (no console needed)
                page._new_rk_status.value = "Copying..."
                page._new_rk_status.color = ft.Colors.GREY
                try:
                    page._new_rk_dlg.update()
                except Exception:
                    pass
                page.update()

                async def _do():
                    ok = await copy_with_snack(
                        page,
                        page._new_rk_value,
                        ok_message="Recovery key copied to clipboard.",
                        fail_message="Could not copy to clipboard on this platform.",
                    )

                    page._new_rk_status.value = "Copied to clipboard." if ok else "Copy failed on this platform."
                    page._new_rk_status.color = ft.Colors.GREEN if ok else ft.Colors.ORANGE

                    try:
                        page._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()

                run_async(page, _do())

            def close(_=None):
                if not page._new_rk_saved_check.value:
                    page._new_rk_status.value = "Please confirm you saved it before closing."
                    try:
                        page._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()
                    return

                page._new_rk_dlg.open = False
                try:
                    page._new_rk_dlg.update()
                except Exception:
                    pass
                page.update()

            def on_check(_):
                page._new_rk_done_btn.disabled = not bool(page._new_rk_saved_check.value)
                try:
                    page._new_rk_dlg.update()
                except Exception:
                    pass
                page.update()

            page._new_rk_saved_check.on_change = on_check
            def commit_and_close(_=None):
                # Don't allow closing without checkbox
                if not page._new_rk_saved_check.value:
                    page._new_rk_status.value = "Please confirm you saved it before closing."
                    try:
                        page._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()
                    return

                staged = getattr(page, "_pending_recovery_rotation_key", None)
                if not staged:
                    page._new_rk_status.value = "No pending rotation found."
                    page._new_rk_status.color = ft.Colors.RED
                    page.update()
                    return

                try:
                    # Commit rotation now using the already-shown key
                    committed = rotate_recovery_key(page.db_path, page.db_key_raw, new_recovery_key_b64=staged)

                    # Sanity: committed should match staged (it will)
                    page.recovery_key_first_run = committed

                    # Now clear the password field (safe after commit)
                    current_pwd_field.value = ""
                    try:
                        current_pwd_field.update()
                    except Exception:
                        pass

                    # Clear pending staged state
                    page._pending_recovery_rotation_key = None

                    show_snack(page, "Recovery key rotated.", ft.Colors.GREEN)

                    # Close dialog
                    page._new_rk_dlg.open = False
                    try:
                        page._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()

                except Exception as ex:
                    page._new_rk_status.value = str(ex)
                    page._new_rk_status.color = ft.Colors.RED
                    try:
                        page._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()

            page._new_rk_done_btn.on_click = commit_and_close

            page._new_rk_dlg = ft.AlertDialog(
                modal=False,
                title=ft.Text("New Recovery Key"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Save this recovery key somewhere safe.\n"
                            "If you lose both your password and this key, the vault cannot be recovered."
                        ),
                        ft.Container(
                            padding=10,
                            border=ft.Border.all(1, ft.Colors.ORANGE),
                            border_radius=6,
                            content=ft.Text(
                                "Rotation is NOT final until you click 'I saved it.'\n"
                                "If you close this dialog before confirming, the old recovery key remains valid.",
                                color=ft.Colors.ORANGE,
                            ),
                        ),          
                        ft.Container(
                            padding=10,
                            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
                            if hasattr(ft.Colors, "OUTLINE_VARIANT") else None,
                            content=page._new_rk_text,
                        ),
                        ft.Row(
                            [
                                ft.Button("Copy", icon=ft.Icons.CONTENT_COPY, on_click=copy_key),
                            ]
                        ),
                        page._new_rk_saved_check,
                        page._new_rk_status,
                    ],
                    tight=True,
                ),
                actions=[page._new_rk_done_btn],
                on_dismiss=close,
            )
            page.overlay.append(page._new_rk_dlg)

        # Update dialog content for THIS key
        page._new_rk_value = new_key
        page._new_rk_text.value = new_key
        page._new_rk_saved_check.value = False
        page._new_rk_done_btn.disabled = True
        page._new_rk_status.value = ""

        page._new_rk_dlg.open = True
        try:
            page._new_rk_dlg.update()
        except Exception:
            pass
        page.update()

    def close_dialog(dlg: ft.AlertDialog):
        dlg.open = False
        try:
            dlg.update()
        except Exception:
            pass
        page.update()

    def rotate_recovery_click(e):
        # Guardrails
        if not getattr(page, "db_path", None) or not getattr(page, "db_key_raw", None):
            show_snack(page, "Vault not loaded; cannot rotate recovery key.", ft.Colors.RED)
            return

        pwd = (current_pwd_field.value or "").strip()
        if not pwd:
            show_snack(page, "Enter your current database password to rotate the recovery key.", ft.Colors.ORANGE)
            return

        # Create dialog once and reuse it
        if not hasattr(page, "_rotate_rk_dlg") or page._rotate_rk_dlg is None:
            page._rotate_rk_status = ft.Text("", color="red")

            def close_rotate(_=None):
                page._rotate_rk_dlg.open = False
                page._rotate_rk_status.value = ""
                try:
                    page._rotate_rk_dlg.update()
                except Exception:
                    pass
                page.update()

            def do_rotate(_=None):
                try:
                    pwd_now = (current_pwd_field.value or "").strip()
                    if not pwd_now:
                        raise RuntimeError("Enter your current database password.")

                    if not verify_password(page.db_path, pwd_now):
                        raise RuntimeError("Incorrect password.")

                    staged_key = generate_recovery_key_b64()
                    page._pending_recovery_rotation_key = staged_key

                    # Do NOT clear password yet; commit happens after "I saved it"
                    close_rotate()
                    show_new_recovery_key_ceremony(staged_key)

                except Exception as ex:
                    page._rotate_rk_status.value = str(ex)
                    try:
                        page._rotate_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()

            page._rotate_rk_dlg = ft.AlertDialog(
                modal=False,
                title=ft.Text("Rotate recovery key?"),
                content=ft.Column(
                    [
                        ft.Text(
                            "This will generate a NEW recovery key and invalidate the previous one.\n"
                            "Make sure you save the new key immediately."
                        ),
                        page._rotate_rk_status,
                    ],
                    tight=True,
                ),
                actions=[
                    ft.Button("Cancel", on_click=close_rotate),
                    ft.Button("Rotate", icon=ft.Icons.REPLAY, on_click=do_rotate),
                ],
                on_dismiss=close_rotate,
            )
            page.overlay.append(page._rotate_rk_dlg)

        # Open it
        page._rotate_rk_status.value = ""
        page._rotate_rk_dlg.open = True
        page.update()

    # 4. Return Layout
    return ft.Container(
        padding=pt_scale(page, 20),
        content=ft.Column(
            [
                ft.Text("Settings", size=pt_scale(page, 24), weight="bold"),
                ft.Divider(),
                theme_dd,
                hc_switch,
                lt_switch,
                ft.Row([
                    ft.Button("Save Settings", icon=ft.Icons.SAVE, on_click=save_settings),
                    ft.Button("Reset Defaults", icon=ft.Icons.RESTART_ALT, on_click=reset_settings),
                ]),
                ft.Divider(),
                source_cb,
                updated_cb,
                ft.Divider(),
                ft.Text("Recovery Key", size=pt_scale(page, 18), weight="bold"),
                ft.Text(
                    "Rotate your recovery key if you think it may be exposed, or just periodically.",
                    size=pt_scale(page, 14),
                    color=ft.Colors.GREY,
                ),
                current_pwd_field,
                ft.Row(
                    [
                        ft.Button(
                            "Rotate Recovery Key",
                            icon=ft.Icons.VPN_KEY,
                            on_click=rotate_recovery_click,
                        ),
                    ]
                ),
                ft.Divider(),
                ft.Text("Export My Data", size=pt_scale(page, 18), weight="bold"),
                ft.Text(
                    "Save all your medical records to a portable file you can "
                    "move to another computer.  The file is encrypted with your "
                    "database password.",
                    size=pt_scale(page, 14),
                    color=ft.Colors.GREY,
                ),
                ft.Row([
                    ft.Button(
                        "Export My Data",
                        icon=ft.Icons.DOWNLOAD,
                        on_click=export_click,
                    ),
                    export_progress,
                ]),
                export_status,
            ]
        )
    )