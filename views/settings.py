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
from utils.ui_helpers import OUTLINE_VARIANT, append_dialog, pt_scale, show_snack, run_async, copy_with_snack, make_info_button
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
    # Large text: float scale value 1.0–1.5 (legacy "0"/"1" treated as 1.0/1.25)
    _lt_raw = get_setting(page.db_connection, "ui.large_text", "1.0")
    try:
        _lt_scale_val = float(_lt_raw)
        if _lt_scale_val == 0.0:
            _lt_scale_val = 1.0
        elif _lt_scale_val == 1.0 and _lt_raw == "1":
            _lt_scale_val = 1.25
    except ValueError:
        _lt_scale_val = 1.0

    is_show_source = get_setting(page.db_connection, "ui.show_source", "0") == "1"

    is_show_updated = get_setting(page.db_connection, "ui.show_updated", "0") == "1"

    # â”€â”€ Export My Data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    export_status = ft.Text("", size=pt_scale(page, 14))

    export_progress = ft.ProgressRing(visible=False, width=20, height=20)

    export_unencrypted_cb = ft.Checkbox(label="Save unencrypted data \u26A0\uFE0F", value=False, label_style=ft.TextStyle(color=ft.Colors.RED))

    async def export_click(e):
        if export_unencrypted_cb.value:
            def _close_dlg(ev=None):
                page.mrma._unenc_dlg.open = False
                page.update()

            opts = {
                "overview": ft.Checkbox(label="Overview & Requests", value=True),
                "health_record": ft.Checkbox(label="Health Record", value=True),
                "providers": ft.Checkbox(label="Providers", value=True),
                "labs": ft.Checkbox(label="Labs", value=True),
                "documents": ft.Checkbox(label="Documents (includes decrypted PDFs)", value=True),
                "immunizations": ft.Checkbox(label="Immunizations / Immunizations", value=True),
                "family_history": ft.Checkbox(label="Family History", value=True),
            }
            
            password_field = ft.TextField(
                label="Re-enter vault password to confirm",
                password=True,
                can_reveal_password=True,
                border_color=ft.Colors.RED,
                on_submit=lambda e: page.run_task(_do_unenc_export),
            )
            password_error = ft.Text("", color=ft.Colors.RED, size=pt_scale(page, 12), visible=False)

            async def _do_unenc_export(ev=None):
                # Verify password before allowing unencrypted export
                entered = (password_field.value or "").strip()
                if not entered:
                    password_error.value = "Password is required."
                    password_error.visible = True
                    page.update()
                    return
                try:
                    from crypto.keybag import unlock_db_key_with_password
                    verified_key = unlock_db_key_with_password(page.db_path, entered)
                    if verified_key != page.db_key_raw:
                        raise ValueError("Key mismatch")
                except Exception:
                    password_error.value = "Incorrect password."
                    password_error.visible = True
                    page.update()
                    return

                _close_dlg(ev)
                tabs = {k: v.value for k, v in opts.items()}
                result = await ft.FilePicker().save_file(
                    dialog_title="Save unencrypted backup",
                    file_name="unencrypted_medical_records.zip",
                    file_type=ft.FilePickerFileType.CUSTOM,
                    allowed_extensions=["zip"],
                )
                if not result: return
                dest = result if isinstance(result, str) else getattr(result, 'path', str(result))
                if not dest: return
                if not dest.lower().endswith(".zip"): dest += ".zip"
                
                export_status.value = "Exporting Unencrypted Data..."
                export_status.color = ft.Colors.RED
                export_progress.visible = True
                page.update()
                
                try:
                    data_dir = os.path.join(os.path.dirname(page.db_path), "data")
                    from utils.unencrypted_export import export_unencrypted_profile
                    export_unencrypted_profile(page.db_connection, page.db_key_raw, data_dir, dest, tabs)
                    export_status.value = f"Exported to {os.path.basename(dest)}"
                    export_status.color = ft.Colors.GREEN
                    show_snack(page, "Unencrypted profile exported.", ft.Colors.GREEN)
                except Exception as ex:
                    export_status.value = f"Export failed: {ex}"
                    export_status.color = ft.Colors.RED
                    show_snack(page, f"Export failed: {ex}", ft.Colors.RED)
                finally:
                    export_progress.visible = False
                    page.update()

            page.mrma._unenc_dlg = ft.AlertDialog(
                title=ft.Text("Unencrypted Export WARNING", color=ft.Colors.RED, weight="bold"),
                content=ft.Column([
                    ft.Text("You are about to export your medical records in plain text.", weight="bold"),
                    ft.Text("Anyone who accesses this file will be able to read all of your medical data!"),
                    ft.Divider(),
                    ft.Text("Select which data to include:", size=pt_scale(page, 13)),
                    opts["overview"], opts["health_record"], opts["providers"], opts["labs"], opts["documents"], opts["immunizations"], opts["family_history"],
                    ft.Divider(),
                    password_field,
                    password_error,
                ], tight=True),
                actions=[
                    ft.TextButton("Cancel", on_click=_close_dlg),
                    ft.FilledButton("Understand & Proceed", color=ft.Colors.RED, icon=ft.Icons.WARNING, on_click=_do_unenc_export),
                ]
            )
            from utils.ui_helpers import OUTLINE_VARIANT, append_dialog
            append_dialog(page, page.mrma._unenc_dlg)
            page.mrma._unenc_dlg.open = True
            page.update()
            return
            
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

    # ── Per-control auto-save handlers ───────────────────────────────────────
    # Defined BEFORE each control so on_change is passed in the constructor
    # (Flet post-assignment of on_change is unreliable in some versions).

    def _save_theme(e):
        val = getattr(e, 'data', None) or theme_dd.value or "system"
        set_setting(page.db_connection, "ui.theme", val)
        page.theme_mode = {
            "dark": ft.ThemeMode.DARK,
            "light": ft.ThemeMode.LIGHT,
            "system": ft.ThemeMode.SYSTEM,
        }.get(val, ft.ThemeMode.SYSTEM)
        apply_settings_callback()
        show_snack(page, "Theme saved.", ft.Colors.GREEN)
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
    theme_dd.on_select = _save_theme
    def _save_hc(e):
        val = e.control.value
        set_setting(page.db_connection, "ui.high_contrast", "1" if val else "0")
        apply_settings_callback()

    hc_switch = ft.Switch(label="High contrast", value=is_high_contrast, on_change=_save_hc)

    # ── Text scale slider ────────────────────────────────────────────────────
    _scale_label = ft.Text(
        f"Text Scale: {_lt_scale_val:.0%}",
        size=pt_scale(page, 14),
    )

    def _save_scale(e):
        val = e.control.value
        set_setting(page.db_connection, "ui.large_text", str(round(val, 2)))
        _scale_label.value = f"Text Scale: {val:.0%}"
        try:
            _scale_label.update()
        except Exception:
            pass
        apply_settings_callback()

    # on_change already wired above via _save_theme / _save_hc

    lt_slider = ft.Slider(
        min=1.0,
        max=1.5,
        divisions=10,
        value=_lt_scale_val,
        label="{value:.2f}x",
        width=300,
        on_change=_save_scale,
    )

    def _auto_save_source(e):
        set_setting(page.db_connection, "ui.show_source", "1" if e.control.value else "0")
        apply_settings_callback()

    source_cb = ft.Checkbox(label="Show source of information", value=is_show_source, on_change=_auto_save_source)

    def _auto_save_updated(e):
        set_setting(page.db_connection, "ui.show_updated", "1" if e.control.value else "0")
        apply_settings_callback()

    updated_cb = ft.Checkbox(label="Show updated date", value=is_show_updated, on_change=_auto_save_updated)

    current_auto_lock = get_setting(page.db_connection, "ui.auto_lock_minutes", "15")
    def _save_auto_lock(e):
        val = e.control.value.strip()
        if not val:
            val = "0"
        try:
            int(val)
            set_setting(page.db_connection, "ui.auto_lock_minutes", val)
            if hasattr(page, "mrma"):
                import time
                page.mrma._last_activity = time.time()
            show_snack(page, "Auto-lock timeout saved.", ft.Colors.GREEN)
        except ValueError:
            show_snack(page, "Invalid timeout value. Must be a number.", ft.Colors.RED)
            
    auto_lock_field = ft.TextField(
        label="Auto-Lock (Inactivity Timeout in minutes, 0 or empty for Off)",
        value=current_auto_lock,
        width=400,
        keyboard_type=ft.KeyboardType.NUMBER,
        on_blur=_save_auto_lock,
        on_submit=_save_auto_lock,
    )

    # 3. Logic: Restore defaults only

    def reset_settings(e):

        # Reset DB

        set_setting(page.db_connection, "ui.theme", "system")

        set_setting(page.db_connection, "ui.high_contrast", "0")

        set_setting(page.db_connection, "ui.large_text", "1.0")

        set_setting(page.db_connection, "ui.show_source", "0")

        set_setting(page.db_connection, "ui.show_updated", "0")
        set_setting(page.db_connection, "ui.auto_lock_minutes", "15")

        # Reset Controls

        theme_dd.value = "system"

        hc_switch.value = False

        lt_slider.value = 1.0
        _scale_label.value = "Text Scale: 100%"
        source_cb.value = False
        updated_cb.value = False
        auto_lock_field.value = "15"
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
        if not hasattr(page.mrma, "_new_rk_dlg") or page.mrma._new_rk_dlg is None:
            page.mrma._new_rk_value = ""

            page.mrma._new_rk_text = ft.Text(
                "",
                selectable=True,
                font_family="Consolas",
            )

            page.mrma._new_rk_saved_check = ft.Checkbox(
                label="I saved this recovery key somewhere safe.",
                value=False,
            )

            page.mrma._new_rk_status = ft.Text("", color="red")

            page.mrma._new_rk_done_btn = ft.Button(
                "I saved it",
                icon=ft.Icons.CHECK,
                disabled=True,
            )

            def copy_key(_):
                # Immediate UI proof (no console needed)
                page.mrma._new_rk_status.value = "Copying..."
                page.mrma._new_rk_status.color = ft.Colors.GREY
                try:
                    page.mrma._new_rk_dlg.update()
                except Exception:
                    pass
                page.update()

                async def _do():
                    ok = await copy_with_snack(
                        page,
                        page.mrma._new_rk_value,
                        ok_message="Recovery key copied to clipboard.",
                        fail_message="Could not copy to clipboard on this platform.",
                    )

                    page.mrma._new_rk_status.value = "Copied to clipboard." if ok else "Copy failed on this platform."
                    page.mrma._new_rk_status.color = ft.Colors.GREEN if ok else ft.Colors.ORANGE

                    try:
                        page.mrma._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()

                run_async(page, _do())

            def close(_=None):
                if not page.mrma._new_rk_saved_check.value:
                    page.mrma._new_rk_status.value = "Please confirm you saved it before closing."
                    try:
                        page.mrma._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()
                    return

                page.mrma._new_rk_dlg.open = False
                try:
                    page.mrma._new_rk_dlg.update()
                except Exception:
                    pass
                page.update()

            def on_check(_):
                page.mrma._new_rk_done_btn.disabled = not bool(page.mrma._new_rk_saved_check.value)
                try:
                    page.mrma._new_rk_dlg.update()
                except Exception:
                    pass
                page.update()

            page.mrma._new_rk_saved_check.on_change = on_check
            def commit_and_close(_=None):
                # Don't allow closing without checkbox
                if not page.mrma._new_rk_saved_check.value:
                    page.mrma._new_rk_status.value = "Please confirm you saved it before closing."
                    try:
                        page.mrma._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()
                    return

                staged = getattr(page.mrma, "_pending_recovery_rotation_key", None)
                if not staged:
                    page.mrma._new_rk_status.value = "No pending rotation found."
                    page.mrma._new_rk_status.color = ft.Colors.RED
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
                    page.mrma._pending_recovery_rotation_key = None

                    show_snack(page, "Recovery key rotated.", ft.Colors.GREEN)

                    # Close dialog
                    page.mrma._new_rk_dlg.open = False
                    try:
                        page.mrma._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()

                except Exception as ex:
                    page.mrma._new_rk_status.value = str(ex)
                    page.mrma._new_rk_status.color = ft.Colors.RED
                    try:
                        page.mrma._new_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()

            page.mrma._new_rk_done_btn.on_click = commit_and_close

            page.mrma._new_rk_dlg = ft.AlertDialog(
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
                            border=ft.Border.all(1, OUTLINE_VARIANT),
                            content=page.mrma._new_rk_text,
                        ),
                        ft.Row(
                            [
                                ft.Button("Copy", icon=ft.Icons.CONTENT_COPY, on_click=copy_key),
                            ]
                        ),
                        page.mrma._new_rk_saved_check,
                        page.mrma._new_rk_status,
                    ],
                    tight=True,
                ),
                actions=[page.mrma._new_rk_done_btn],
                on_dismiss=close,
            )
            append_dialog(page, page.mrma._new_rk_dlg)

        # Update dialog content for THIS key
        page.mrma._new_rk_value = new_key
        page.mrma._new_rk_text.value = new_key
        page.mrma._new_rk_saved_check.value = False
        page.mrma._new_rk_done_btn.disabled = True
        page.mrma._new_rk_status.value = ""

        page.mrma._new_rk_dlg.open = True
        try:
            page.mrma._new_rk_dlg.update()
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
        if not hasattr(page.mrma, "_rotate_rk_dlg") or page.mrma._rotate_rk_dlg is None:
            page.mrma._rotate_rk_status = ft.Text("", color="red")

            def close_rotate(_=None):
                page.mrma._rotate_rk_dlg.open = False
                page.mrma._rotate_rk_status.value = ""
                try:
                    page.mrma._rotate_rk_dlg.update()
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
                    page.mrma._pending_recovery_rotation_key = staged_key

                    # Do NOT clear password yet; commit happens after "I saved it"
                    close_rotate()
                    show_new_recovery_key_ceremony(staged_key)

                except Exception as ex:
                    page.mrma._rotate_rk_status.value = str(ex)
                    try:
                        page.mrma._rotate_rk_dlg.update()
                    except Exception:
                        pass
                    page.update()

            page.mrma._rotate_rk_dlg = ft.AlertDialog(
                modal=False,
                title=ft.Text("Rotate recovery key?"),
                content=ft.Column(
                    [
                        ft.Text(
                            "This will generate a NEW recovery key and invalidate the previous one.\n"
                            "Make sure you save the new key immediately."
                        ),
                        page.mrma._rotate_rk_status,
                    ],
                    tight=True,
                ),
                actions=[
                    ft.Button("Cancel", on_click=close_rotate),
                    ft.Button("Rotate", icon=ft.Icons.REPLAY, on_click=do_rotate),
                ],
                on_dismiss=close_rotate,
            )
            append_dialog(page, page.mrma._rotate_rk_dlg)

        # Open it
        page.mrma._rotate_rk_status.value = ""
        page.mrma._rotate_rk_dlg.open = True
        page.update()

    def _show_wipe_prompt():
        if not hasattr(page.mrma, "_wipe_dlg") or page.mrma._wipe_dlg is None:
            pwd_field = ft.TextField(
                label="Current database password",
                password=True,
                can_reveal_password=True,
                width=350,
            )
            error_txt = ft.Text("", color=ft.Colors.RED)

            def do_wipe_and_exit(_):
                pwd = (pwd_field.value or "").strip()
                if not pwd:
                    error_txt.value = "Password is required."
                    try:
                        error_txt.update()
                    except Exception as ex: pass
                    return
                from crypto.keybag import verify_password
                if not verify_password(page.db_path, pwd):
                    error_txt.value = "Incorrect password."
                    try:
                        error_txt.update()
                    except Exception as ex: pass
                    return
                
                from core.app_state import wipe_local_data
                wipe_local_data(page)
                try:
                    page.window.destroy()
                except Exception:
                    import os
                    os._exit(0)

            def close_wipe(_):
                page.mrma._wipe_dlg.open = False
                pwd_field.value = ""
                error_txt.value = ""
                try:
                    page.mrma._wipe_dlg.update()
                except Exception:
                    pass
                page.update()

            page.mrma._wipe_dlg = ft.AlertDialog(
                modal=True,
                title=ft.Text("Shared Device Cleanup", color=ft.Colors.RED),
                content=ft.Column(
                    [
                        ft.Text("Please confirm you have exported a backup to your USB drive before doing this!"),
                        ft.Text("This will securely erase the local database and clear temporary files."),
                        pwd_field,
                        error_txt,
                    ],
                    tight=True,
                ),
                actions=[
                    ft.Button("Cancel", on_click=close_wipe),
                    ft.Button("Wipe & Exit App", icon=ft.Icons.WARNING, color=ft.Colors.RED, on_click=do_wipe_and_exit),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            append_dialog(page, page.mrma._wipe_dlg)
        
        # Reset fields when reopening
        if hasattr(page.mrma._wipe_dlg.content, 'controls'):
            for c in page.mrma._wipe_dlg.content.controls:
                if isinstance(c, ft.TextField): c.value = ""
                elif isinstance(c, ft.Text) and c.color == ft.Colors.RED: c.value = ""
                
        page.mrma._wipe_dlg.open = True
        page.update()
    _info_btn = make_info_button(page, "Settings", [
        "\"Show source of information\" reveals which document or action produced each health record entry, with a hyperlink to the source document where available.",
        "The Auto-Lock timeout will lock your vault after a period of inactivity, requiring you to re-enter your password. Set to 0 to disable.",
        "\"Export My Data\" creates an encrypted backup zip protected by your vault password. You can import this on another device using \"Upload Existing Profile\" on the login screen.",
        "Check \"Save unencrypted data\" to export a plain-text ZIP with a readable PDF summary and your raw documents. This requires password confirmation for safety.",
        "The Recovery Key section lets you rotate to a new key, which will make your old key invalid. A use case for this would be if you suspect your key has been seen by someone else, or if you have lost your key but remember your password. Always save the new key before closing the dialog or the old key stays valid.",
        "\"Wipe Session & Exit\" is for shared/public computers to securely erases your local database and temporary files so no one can access your data after you leave.",
    ])

    # 4. Return Layout

    return ft.Container(
        expand=True,
        padding=pt_scale(page, 20),
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("Settings", size=pt_scale(page, 24), weight="bold"),
                        ft.Container(expand=True),
                        _info_btn,
                    ]
                ),
                ft.Divider(),
                theme_dd,
                hc_switch,
                _scale_label,
                lt_slider,
                ft.Row([
                    source_cb,
                    updated_cb,
                ]),
                ft.Container(height=pt_scale(page, 10)),
                auto_lock_field,
                ft.Container(height=pt_scale(page, 10)),
                ft.Row([
                    ft.Button("Restore Defaults", icon=ft.Icons.RESTORE, on_click=reset_settings),
                ]),
                ft.Divider(),
                ft.Text("Recovery Key", size=pt_scale(page, 18), weight="bold"),
                ft.Text(
                    "Your recovery key allows you to restore your vault if you forget your password. "
                    "Rotating the recovery key will invalidate your old key and generate a new one.",
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
                    "Save all your medical records to a portable zip file you can "
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
                    export_unencrypted_cb,
                    export_progress,
                ]),
                export_status,
                ft.Divider(),
                ft.Text("Library / Shared Device Mode", size=pt_scale(page, 18), weight="bold", color=ft.Colors.RED),
                ft.Text(
                    "Are you using a public computer or someone else's device? "
                    "Use this to securely erase your local database and temporary files before exiting. "
                    "Always save a backup of your data before using this feature.",
                    size=pt_scale(page, 14),
                    color=ft.Colors.GREY,
                ),
                ft.Row([
                    ft.Button(
                        "Wipe Session & Exit",
                        icon=ft.Icons.CLEANING_SERVICES,
                        color=ft.Colors.RED,
                        on_click=lambda e: _show_wipe_prompt(),
                    ),
                ]),
            ],
            expand=True,
            scroll=ft.ScrollMode.AUTO
        )
    )
