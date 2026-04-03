# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Centralized registration for long-lived Flet dialogs used by the Patient Info
# experience and recovery flows.
#
# This module mounts dialogs once (stored on `page.*` and appended to
# `page.overlay` exactly once) to keep handlers stable across rerenders and
# navigation. It exposes safe entry points (methods attached to `page`) for:
# - Sensitive Details dialog: masked-by-default DOB/SSN display with explicit
#   Reveal gating before edits, and SSN persistence to patient_field_values.
# - Patient Info management dialogs:
#   - Delete field definition (with guardrails preventing deletion of core/system keys)
#   - Add custom field (unique field_key generation + basic data-type detection)
#   - Bulk Edit Visibility switches that toggle section/list sensitivity flags
#     in field_definitions (e.g., section.demographics / section.other).
# - Account recovery dialogs:
#   - Forgot-password flow (unlock via recovery key, set new password)
#   - Recovery-key ceremony (copy/display key + require I saved it)
#   - Staged recovery-key rotation committed only after confirmation
#
# Intersections:
# - Called by view modules (e.g., patient_info) to register dialogs and use the
#   exposed `page.open_*` functions, while actual UI rendering stays in the views.
# - Writes sensitivity settings and field definitions through the database layer.
# -----------------------------------------------------------------------------

from __future__ import annotations
import flet as ft
import json

from database import (
    list_field_definitions,
    get_patient_field_map,
    ensure_field_definition,
    field_definition_exists,
    delete_field_definition,
    list_distinct_field_categories,
    update_field_definition_sensitivity,
    open_vault_with_recovery, 
    get_profile,
)
from crypto.keybag import set_new_password, generate_recovery_key_b64, rotate_recovery_key
from core.startup import run_self_test
from utils.ui_helpers import run_async, copy_with_snack, is_sensitive_flag, detect_data_type_from_label, slugify_label, clean_lbl, pt_scale, show_snack

def ensure_sensitive_dialogs_registered(page: ft.Page, *, s, show_snack):
    if getattr(page, "_sensitive_dlg", None) is not None:
        return

    page._sensitive_revealed = False

    title = ft.Text("Sensitive Details", size=pt_scale(page, 18), weight="bold")
    hint = ft.Text("Click Reveal to show DOB and SSN.", size=pt_scale(page, 12))

    dob_text = ft.Text("DOB: ****-**-**", selectable=True)
    ssn_text = ft.Text("SSN: ***-**-****", selectable=True)

    # Create once, reuse (store on page for stability)
    page._ssn_field = ft.TextField(
        label="SSN",
        password=True,
        can_reveal_password=True,
        width=420,
        disabled=True,  # enabled only after Reveal
    )

    status = ft.Text("", size=pt_scale(page, 12), color="red")

    def _load_masked():
        page._sensitive_revealed = False
        dob_text.value = "DOB: ****-**-**"
        ssn_text.value = "SSN: ***-**-****"
        page._ssn_field.value = ""
        page._ssn_field.disabled = True
        status.value = ""
        status.color = ft.Colors.RED
        try:
            page._sensitive_dlg.update()
        except Exception:
            pass
        page.update()

    def _reveal(_e=None):
        patient = getattr(page, "current_profile", None)
        if not patient:
            status.value = "No patient loaded."
            status.color = ft.Colors.RED
            page.update()
            return
        patient_id = patient[0]

        dob = patient[2] or ""

        try:
            fmap = get_patient_field_map(page.db_connection, patient_id)
            ssn = (fmap.get("patient.identifier.us-ssn", {}).get("value") or "").strip()
        except Exception as ex:
            status.value = f"Could not load: {ex}"
            status.color = ft.Colors.RED
            page.update()
            return

        page._sensitive_revealed = True
        dob_text.value = f"DOB: {dob or '(not set)'}"
        ssn_text.value = f"SSN: {ssn or '(not set)'}"
        page._ssn_field.value = ssn
        page._ssn_field.disabled = False
        status.value = ""
        status.color = ft.Colors.RED

        try:
            page._sensitive_dlg.update()
        except Exception:
            pass
        page.update()

    def _save_ssn(_e=None):
        if not getattr(page, "_sensitive_revealed", False):
            status.value = "Click Reveal first."
            status.color = ft.Colors.ORANGE
            page.update()
            return

        patient = getattr(page, "current_profile", None)
        if not patient:
            status.value = "No patient loaded."
            status.color = ft.Colors.RED
            page.update()
            return
        patient_id = patient[0]

        try:
            from database import upsert_patient_field_value
            upsert_patient_field_value(
                page.db_connection,
                patient_id,
                "patient.identifier.us-ssn",
                (page._ssn_field.value or "").strip(),
                source="user",
            )
            status.value = "Saved."
            status.color = ft.Colors.GREEN

            # Update the visible text too
            ssn_text.value = f"SSN: {(page._ssn_field.value or '').strip() or '(not set)'}"
        except Exception as ex:
            status.value = f"Could not save: {ex}"
            status.color = ft.Colors.RED

        try:
            page._sensitive_dlg.update()
        except Exception:
            pass
        page.update()

    def _close(_e=None):
        page._sensitive_dlg.open = False
        _load_masked()

    page._sensitive_dlg = ft.AlertDialog(
        modal=False,
        title=title,
        content=ft.Column(
            [
                hint,
                ft.Container(height=10),
                dob_text,
                ssn_text,
                page._ssn_field,
                ft.Container(height=10),
                status,
            ],
            tight=True,
        ),
        actions=[
            ft.OutlinedButton("Reveal", icon=ft.Icons.VISIBILITY, on_click=_reveal),
            ft.FilledButton("Save SSN", icon=ft.Icons.SAVE, on_click=_save_ssn),
            ft.TextButton("Close", on_click=_close),
        ],
        on_dismiss=_close,
    )

    if page._sensitive_dlg not in page.overlay:
        page.overlay.append(page._sensitive_dlg)

def open_sensitive_details(page: ft.Page):
    # You already pass s/show_snack around; if not, import from utils.
    from utils.ui_helpers import pt_scale, show_snack
    ensure_sensitive_dialogs_registered(page, s=pt_scale, show_snack=show_snack)
    page._sensitive_dlg.open = True
    try:
        page._sensitive_dlg.update()
    except Exception:
        pass
    page.update()

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

    page._recovery_key_text = ft.Text("", selectable=True, font_family="Consolas", size=pt_scale(page, 14))
    page._recovery_saved_check = ft.Checkbox(
        label="I saved this recovery key somewhere safe.",
        value=False,
    )
    page._recovery_status = ft.Text("", color="red", size=pt_scale(page, 12))

    page._recovery_pending_note = ft.Container(
        padding=pt_scale(page, 10),
        border=ft.Border.all(1, ft.Colors.ORANGE),
        border_radius=6,
        visible=False,
        content=ft.Text(
            "Rotation is NOT final until you click 'I saved it.'\n"
            "If you close this dialog before confirming, the old recovery key remains valid.",
            color=ft.Colors.ORANGE,
            size=pt_scale(page, 12),
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
        title=ft.Text("Save your recovery key", size=pt_scale(page, 18), weight="bold"),
        content=ft.Column(
            [
                ft.Text(
                    "This key lets you recover the vault if you forget your password.\n"
                    "If you lose BOTH your password and this key, the vault cannot be recovered.",
                    size=pt_scale(page, 14),
                ),

                page._recovery_pending_note,  

                ft.Container(
                    padding=pt_scale(page, 10),
                    border=ft.Border.all(2, ft.Colors.GREY),
                    border_radius=8,
                    content=page._recovery_key_text,
                ),
                ft.Row([ft.Button("Copy", icon=ft.Icons.CONTENT_COPY, on_click=copy_key)]),
                page._recovery_saved_check,
                page._recovery_status,
            ],
            tight=True,
            spacing=pt_scale(page, 10),
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

def _make_unique_field_key(conn, label: str, category: str) -> str:
    base = f"custom.{slugify_label(category)}.{slugify_label(label)}"
    key = base
    n = 2
    while field_definition_exists(conn, key):
        key = f"{base}.{n}"
        n += 1
    return key

def ensure_patient_info_dialogs(page: ft.Page, refresh_callback):
    """Mounts patient info dialogs safely and links their triggers to the page object."""
    if getattr(page, "_patient_info_dialogs_registered", False):
        page._patient_info_refresh_callback = refresh_callback
        # Always re-link triggers in case page was rebuilt after an import/routing change
        if not hasattr(page, "open_delete_field_dialog") or not hasattr(page, "_bulk_edit_dlg"):
            page._patient_info_dialogs_registered = False  # force full re-registration
        else:
            return
        
    if hasattr(page, "_bulk_edit_dlg") and hasattr(page, "_delete_field_dlg"):
        if hasattr(page, "_impl_open_delete") and hasattr(page, "_impl_open_bulk"):
            page.open_delete_field_dialog = page._impl_open_delete
            page.open_add_field_dialog    = page._impl_open_add
            page.open_bulk_edit_dlg       = page._impl_open_bulk
        return

    page._patient_info_refresh_callback = refresh_callback
    if not hasattr(page, "_revealed_fields"):
        page._revealed_fields = set() 

    # --- 1. Delete Field Dialog ---
    page._delete_field_label = ft.Text("")
    
    def _close_delete(_e=None):
        page._delete_field_dlg.open = False
        try: page._delete_field_dlg.update()
        except Exception: pass
        page.update()

    def _confirm_delete(_e=None):
        field_key = getattr(page._delete_field_dlg, "target_key", None)

        if not field_key or str(field_key).startswith("core.") or str(field_key).startswith("section."):
            show_snack(page, "System fields cannot be deleted.", "red")
            _close_delete()
            return
            
        try:
            delete_field_definition(page.db_connection, field_key)
            page._revealed_fields.discard(field_key)
        except Exception as ex:
            show_snack(page, f"Could not delete: {ex}", "red")
            return
            
        row_ref   = getattr(page, "_delete_inline_row",   None)
        table_ref = getattr(page, "_delete_inline_table", None)
        if row_ref is not None and table_ref is not None:
            try:
                table_ref.rows.remove(row_ref)
                table_ref.update()
            except Exception:
                pass
        page._delete_inline_row   = None
        page._delete_inline_table = None

        _close_delete()
        
        show_snack(page, "Field deleted.", "green")

    page._delete_field_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Delete field?"),
        content=ft.Column([ft.Text("This will remove the field definition and any saved values."), page._delete_field_label], tight=True),
        actions=[
            ft.TextButton("Cancel", on_click=_close_delete),
            ft.FilledButton("Delete", icon=ft.Icons.DELETE, on_click=_confirm_delete),
        ],
        on_dismiss=_close_delete,
    )
    page.overlay.append(page._delete_field_dlg)

    def open_delete_field_dialog(field_key, label):
        page._delete_field_dlg.target_key = field_key
        page._delete_field_label.value = f"Field: {label}"
        page._delete_field_dlg.open = True
        try: page._delete_field_dlg.update()
        except Exception: pass
        page.update()

    # --- 2. Add Field Dialog ---
    page._add_field_label_tf = ft.TextField(label="Field label (e.g. Dentist phone)", autofocus=True)
    page._add_field_category_dd = ft.Dropdown(label="Category", value="Other", width=360)
    page._add_field_sensitive_cb = ft.Checkbox(label="Hide data by default (Requires click to reveal)", value=False)

    def _close_add(_e=None):
        page._add_field_dlg.open = False
        try: page._add_field_dlg.update()
        except Exception: pass
        page.update()

    def _do_add(_e=None):
        label = (page._add_field_label_tf.value or "").strip()
        if not label:
            show_snack(page, "Field label is required.", "red")
            return
        category = (page._add_field_category_dd.value or "Other").strip() or "Other"
        data_type = detect_data_type_from_label(label)
        is_sensitive = 1 if bool(page._add_field_sensitive_cb.value) else 0
        key = _make_unique_field_key(page.db_connection, label, category)

        try:
            ensure_field_definition(page.db_connection, key, label, data_type=data_type, category=category, is_sensitive=is_sensitive)
        except Exception as ex:
            show_snack(page, f"Could not add field: {ex}", "red")
            return

        _close_add()
        page._patient_info_refresh_callback()
        show_snack(page, "Field added.", "green")

    page._add_field_dlg = ft.AlertDialog(
        modal=False, 
        title=ft.Text("Add New Field"),
        content=ft.Column([page._add_field_label_tf, page._add_field_category_dd, page._add_field_sensitive_cb], tight=True),
        actions=[
            ft.ElevatedButton("Cancel", on_click=_close_add),
            ft.ElevatedButton("Add", icon=ft.Icons.ADD, on_click=_do_add),
        ],
        on_dismiss=_close_add,
    )
    page.overlay.append(page._add_field_dlg)

    def open_add_field_dialog(target_category="Other"):
        page._add_field_label_tf.value = ""
        page._add_field_category_dd.value = target_category
        page._add_field_sensitive_cb.value = False
        try:
            cats = list_distinct_field_categories(page.db_connection)
        except Exception: cats = []
        common = ["Demographics", "Allergies", "Medications", "Insurance", "Providers", "Other"]
        merged = []
        for c in common + cats:
            if c and c not in merged: merged.append(c)
        page._add_field_category_dd.options = [ft.dropdown.Option(c) for c in merged]
        page._add_field_dlg.open = True
        try:
            page._add_field_category_dd.update()
            page._add_field_dlg.update()
        except Exception: pass
        page.update()
    page._patient_info_dialogs_registered = True

    # --- 3. Bulk Edit Visibility Dialog ---
    page._bulk_edit_col = ft.Column(scroll=ft.ScrollMode.AUTO, height=400, spacing=4)
    
    def _close_bulk(_e=None):
        page._bulk_edit_dlg.open = False
        try: page._bulk_edit_dlg.update()
        except Exception: pass
        page.update()

    def _save_bulk(_e=None):
        def _walk(ctrl):
            yield ctrl
            if hasattr(ctrl, "controls") and isinstance(ctrl.controls, list):
                for ch in ctrl.controls: yield from _walk(ch)
            if hasattr(ctrl, "content") and ctrl.content is not None: yield from _walk(ctrl.content)

        for ctrl in _walk(page._bulk_edit_col):
            if not isinstance(ctrl, ft.Checkbox):
                continue
            
            data = getattr(ctrl, "data", None)
            val = 1 if ctrl.value else 0

            # Only process section master switches now
            if isinstance(data, dict):
                dtype = data.get("type")
                if dtype == "list":
                    key = data.get("key")
                    if key:
                        try:
                            update_field_definition_sensitivity(page.db_connection, key, val)
                            if val == 1: page._revealed_fields.discard(key)
                        except Exception: pass

        _close_bulk()
        page._patient_info_refresh_callback()
        show_snack(page, "Sensitivity settings updated.", "green")

    # CREATE THE DIALOG ONCE
    page._bulk_edit_dlg = ft.AlertDialog(
        modal=False,
        title=ft.Text("Edit Visibility"),
        content=ft.Container(width=400, content=page._bulk_edit_col),
        actions=[
            ft.TextButton("Cancel", on_click=_close_bulk),
            ft.FilledButton("Save", icon=ft.Icons.SAVE, on_click=_save_bulk),
        ],
        on_dismiss=_close_bulk,
    )
    page.overlay.append(page._bulk_edit_dlg)

    def open_bulk_edit_dlg():
        DEMO_SECTION_KEY = "section.demographics"
        OTHER_SECTION_KEY = "section.other"

        try:
            ensure_field_definition(page.db_connection, DEMO_SECTION_KEY, "Demographics Section", data_type="json", category="System", is_sensitive=0)
            ensure_field_definition(page.db_connection, OTHER_SECTION_KEY, "Other Section", data_type="json", category="System", is_sensitive=0)
        except Exception:
            pass
        
        current_defs = list_field_definitions(page.db_connection)

        page._bulk_edit_col.controls.clear()
        page._bulk_edit_col.controls.append(
            ft.Text("Check a section to enable the visibility toggle features for those fields.", size=12, color="grey")
        )
        page._bulk_edit_col.controls.append(ft.Container(height=10))

        def _is_sens_key(key: str) -> bool:
            return is_sensitive_flag(next((d[4] for d in current_defs if d[0] == key), 0))

        # -------------------------
        # Category sections (Demographics / Other)
        # -------------------------
        def _add_category_section(cat_name: str):
            section_key = DEMO_SECTION_KEY if cat_name == "Demographics" else OTHER_SECTION_KEY

            header_cb = ft.Checkbox(
                label="",
                value=_is_sens_key(section_key) if section_key else False,
                data={"type": "list", "key": section_key} if section_key else None,
            )

            page._bulk_edit_col.controls.append(
                ft.Row(
                    [header_cb, ft.Text(cat_name, weight="bold", size=16)],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                )
            )
            page._bulk_edit_col.controls.append(ft.Container(height=8))

        # -------------------------
        # JSON list sections (Allergies / Meds / Insurance / Providers)
        # -------------------------
        def _add_json_list_section(list_key: str, list_label: str):
            hdr = ft.Checkbox(
                label="",
                value=_is_sens_key(list_key),
                data={"type": "list", "key": list_key},
            )
            page._bulk_edit_col.controls.append(
                ft.Row(
                    [hdr, ft.Text(list_label, weight="bold", size=16)],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                )
            )
            page._bulk_edit_col.controls.append(ft.Container(height=8))

        # Add Demographics
        if any(d[3] == "Demographics" or d[0] in ("core.name", "core.dob") for d in current_defs):
            _add_category_section("Demographics")

        # Add JSON lists
        for list_key, list_label in [
            ("allergyintolerance.list",          "Allergies"),
            ("medicationstatement.current_list", "Current Medications"),
            ("conditions.list", "Conditions"),
            ("procedures.list", "Surgeries"),
            ("insurance.list",                   "Insurance Plans"),
            ("providers.list",                   "Healthcare Providers"),
        ]:
            if any(d[0] == list_key for d in current_defs):
                _add_json_list_section(list_key, list_label)

        # Add Other
        _add_category_section("Other")

        page._bulk_edit_dlg.open = True
        try:
            page._bulk_edit_dlg.update()
        except Exception:
            pass
        page.update()

    # Link triggers globally to page
    page._impl_open_delete = open_delete_field_dialog
    page._impl_open_add    = open_add_field_dialog
    page._impl_open_bulk   = open_bulk_edit_dlg
    page.open_delete_field_dialog = open_delete_field_dialog
    page.open_add_field_dialog    = open_add_field_dialog
    page.open_bulk_edit_dlg       = open_bulk_edit_dlg