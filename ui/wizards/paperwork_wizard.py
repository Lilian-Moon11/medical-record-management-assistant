# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Paperwork Wizard for generating patient forms (ROI / Intake) from templates.
#
# This module provides a guided, step-based UI for completing common paperwork
# using a user-supplied blank PDF template. It collects required inputs,
# supports capturing a handwritten signature, and fills the PDF fields using a
# mapping dictionary.
#
# Includes:
# - Multi-step dialog workflow (select form type → choose template → enter ROI
#   details → capture signature → final review → generate)
# - FilePicker-based template selection (matches documents.py inline async pattern)
# - Provider lookup for "Send Records To / Records From" using saved Provider
#   Directory entries (patient-scoped)
# - Optional "archive to Patient Records" toggle for saving the generated PDF
#   into the app's local record store (intended to be encrypted + recorded in DB)
#
# Design goals:
# - Keep the flow approachable for non-technical users (clear steps + guardrails)
# - Use stable, reusable overlay dialogs to avoid event-handler flakiness
# - Support accessibility preferences via scale-safe sizing (pt_scale(page, ...))
# - Fail safely with clear snackbar messages when inputs or generation fail
#
# NOTE: The heavy-lifting logic has been extracted into sibling modules:
#   - signature_pad.py   → SignaturePad widget + render_signature_png()
#   - loading_tips.py    → _LOADING_TIPS data + make_tip_card()
#   - pdf_fill.py        → _find_key(), build_ui_mapping(), fill_acroform_pdf()
#   - archive.py         → archive_to_records(), create_roi_records_request()
# -----------------------------------------------------------------------------

import asyncio
import logging
import os
from datetime import datetime

import flet as ft
from PyPDFForm import PdfWrapper

from ai.paperwork import map_pdf_fields
from ai.paperwork_overlay import fill_static_pdf
from database.clinical import list_providers
from utils.ui_helpers import append_dialog, pt_scale, show_snack
from utils.open_file import open_file_cross_platform

from ui.wizards.signature_pad import SignaturePad, render_signature_png  # noqa: F401 — re-export
from ui.wizards.loading_tips import make_tip_card
from ui.wizards.pdf_fill import build_ui_mapping, fill_acroform_pdf
from ui.wizards.archive import archive_to_records, create_roi_records_request

logger = logging.getLogger(__name__)


class PaperworkWizard:
    def __init__(self, page: ft.Page):
        self.page = page
        self.patient_id = page.current_profile[0]
        self.patient_name = page.current_profile[1]
        self.save_to_db_check = ft.Checkbox(
            label="Encrypt and save copy to Patient Records",
            value=True, # Default to checked for convenience
        )

        self.check_accessible = ft.Checkbox(label="Accessible Copy (Editable)", value=True)
        self.check_flattened = ft.Checkbox(label="Flattened Copy (Print/Fax)", value=False)
        self._last_archived_doc_id: int | None = None  # set when form is saved to records

        # 1. SIGN DATE & MANUAL INPUTS
        self.step = 1
        self.selected_type = None
        self.template_path = None
        self.sign_date = ft.TextField(
            label="Date of Signature",
            value=datetime.now().strftime("%Y-%m-%d"),
            on_submit=self.next_step,
        )
        self.roi_purpose = ft.TextField(
            label="Purpose of Release",
            value="At the request of the individual",
            on_submit=self.next_step,
        )
        self.roi_expiry = ft.TextField(
            label="Expiration Date", 
            hint_text="YYYY-MM-DD",
            on_submit=self.next_step
        )

        # 2. DROPDOWNS
        self.prov_to_dropdown = ft.Dropdown(label="Send Records To:")
        self.prov_to_dropdown.on_select = self._on_recipient_change
        self.prov_from_dropdown = ft.Dropdown(label="Records From (Saved Providers):")

        # 3. UI STRUCTURE
        self.content_area = ft.Column(
            tight=True, 
            width=pt_scale(page, 520), 
            spacing=pt_scale(page, 10) # Scaled spacing
        )
        self.next_btn = ft.FilledButton("Next", on_click=self.next_step)
        self._cancel_btn = ft.TextButton("Cancel", on_click=self.close)
        self._prev_keyboard_handler = getattr(self.page, "on_keyboard_event", None)
        self.page.on_keyboard_event = self.on_key_event

        self.dlg = ft.AlertDialog(
            title=ft.Text("Complete Paperwork"),
            content=self.content_area,
            actions=[
                self._cancel_btn,
                self.next_btn,
            ],
            on_dismiss=self.close,
        )

        # IMPORTANT: mount dialog ONCE so open() actually shows it
        if self.dlg not in self.page.overlay:
            append_dialog(self.page, self.dlg)

    async def on_key_event(self, e: ft.KeyboardEvent):
        """Triggers next_step when Enter is pressed and the dialog is open."""
        if e.key == "Enter" and self.dlg.open:
            # Accessibility: Prevent generation if they haven't reached the final step
            if self.step < 4:
                await self.next_step()
            else:
                await self.execute_generation()
    
    # --- FilePicker: match documents.py pattern (inline async pick) ---
    async def pick_template_click(self, e: ft.ControlEvent):
        files = await ft.FilePicker().pick_files(
            allow_multiple=False,
            dialog_title="Select Blank PDF Template",
            allowed_extensions=["pdf"],
        )
        if not files:
            return

        picked = files[0]
        src_path = getattr(picked, "path", None) or getattr(picked, "file_path", None)
        if not src_path:
            show_snack(self.page, "Picker returned no local path.", "red")
            return

        self.template_path = src_path
        show_snack(self.page, f"Template Loaded: {picked.name}", "blue")

        # Persist current radio selection across re-render
        if hasattr(self, "form_radio") and self.form_radio:
            self.selected_type = self.form_radio.value or self.selected_type

        self.render_step()

    def on_form_change(self, e: ft.ControlEvent):
        self.selected_type = e.control.value

    def _on_recipient_change(self, e):
        """Enable Next once a recipient is selected."""
        has_selection = bool(getattr(e, 'data', None) or self.prov_to_dropdown.value)
        self.next_btn.disabled = not has_selection
        self.next_btn.update()
        
    def open(self):
        if self.dlg not in self.page.overlay:
            append_dialog(self.page, self.dlg)

        self.render_step()

        self.dlg.open = True
        self.page.update()

    def close(self, e=None):
        self.dlg.open = False
        self.page.on_keyboard_event = getattr(self, "_prev_keyboard_handler", None)
        try:
            self.dlg.update()
        except Exception:
            pass
        self.page.update()

    def render_step(self):
        self.content_area.controls.clear()
        header_size = pt_scale(self.page, 16)

        if self.step == 1:
            self.content_area.controls.append(
                ft.Text("Step 1: Select Form Type", weight="bold", size=header_size)
            )

            self.form_radio = ft.RadioGroup(
                value=self.selected_type,
                on_change=self.on_form_change,
                content=ft.Column(
                    [
                        ft.Radio(value="intake", label="Patient Intake Form"),
                        ft.Radio(value="roi", label="Release of Information (ROI)"),
                    ]
                ),
            )

            upload_btn = ft.Button(
                "Upload Blank PDF Template",
                icon=ft.Icons.UPLOAD_FILE,
                on_click=self.pick_template_click,
            )

            status_color = "blue" if self.template_path else "red"
            status_text = ft.Text(
                f"Selected: {os.path.basename(self.template_path)}"
                if self.template_path
                else "No template selected",
                italic=True,
                size=12,
                color=status_color,
            )

            self.content_area.controls.extend(
                [self.form_radio, ft.Divider(), upload_btn, status_text]
            )

        elif self.step == 2:
            if self.selected_type == "roi":
                self.content_area.controls.append(
                    ft.Text("Release of Information (ROI) Details", weight="bold")
                )
                provs = list_providers(self.page.db_connection, self.patient_id)

                # Setup Recipient (To)
                to_opts = [
                    ft.dropdown.Option(
                        text=f"Myself ({self.patient_name})",
                        key="patient",
                    )
                ]
                to_opts.extend(
                    [ft.dropdown.Option(text=f"{p[1]}", key=str(p[0])) for p in provs]
                )
                self.prov_to_dropdown.options = to_opts

                # Setup Sender (From)
                from_opts = [
                    ft.dropdown.Option(text=f"{p[1]}", key=str(p[0])) for p in provs
                ]
                self.prov_from_dropdown.options = from_opts

                # Disable Next until a recipient is selected
                self.next_btn.disabled = not self.prov_to_dropdown.value

                self.content_area.controls.extend(
                    [
                        self.prov_from_dropdown,
                        ft.Divider(),
                        self.prov_to_dropdown,
                        ft.Divider(),
                        self.roi_purpose,
                        self.roi_expiry,
                        self.sign_date,
                    ]
                )
            else:
                # Non-ROI: skip step 2 by directly advancing to step 3.
                # Cannot call await self.next_step() here because render_step
                # is a synchronous function. Direct step mutation is safe.
                self.next_btn.disabled = False
                self.step = 3
                self.render_step()
                return

        elif self.step == 3:
            self.next_btn.disabled = False
            self.content_area.controls.append(
                ft.Text("Step 3: Provide Your Signature", weight="bold", size=header_size)
            )
            self.content_area.controls.append(
                ft.Text(
                    "Your signature will be applied to the form if it includes a designated "
                    "signature area. It is not stored by the application and is only used "
                    "to generate this document.",
                    size=pt_scale(self.page, 12),
                    color=ft.Colors.SECONDARY,
                )
            )
            self.sig_pad = SignaturePad(self.page)
            self.content_area.controls.extend([
                self.sig_pad,
                ft.TextButton("Clear Signature", on_click=self.sig_pad.clear)
            ])
            self.next_btn.text = "Review & Generate"

        elif self.step == 4:
            self.content_area.controls.append(
                ft.Text("Step 4: Output Preferences", weight="bold", size=header_size)
            )
            self.content_area.controls.extend([
                ft.Text("Select which versions to generate:", size=pt_scale(self.page, 14)),
                self.check_accessible,
                self.check_flattened,
                ft.Divider(),
                self.save_to_db_check
            ])
            self.next_btn.text = "Generate & Save"

        if self.dlg.open:
            self.dlg.update()
        self.page.update()

    async def next_step(self, e=None):
        if self.step == 1:
            if hasattr(self, "form_radio") and self.form_radio:
                self.selected_type = self.form_radio.value or self.selected_type

            if not self.selected_type:
                show_snack(self.page, "Select a form type.", "orange")
                return
            if not self.template_path:
                show_snack(self.page, "Please upload a blank PDF template.", "orange")
                return

            self.step = 2

        elif self.step == 2:
            # Move from details to signature
            self.step = 3

        elif self.step == 3:
            # Move from signature to final review
            self.step = 4

        elif self.step == 4:
            await self.execute_generation()
            return

        self.render_step()

    # --------------------------------------------------------------------- #
    #  Generation orchestrator                                                #
    # --------------------------------------------------------------------- #

    async def execute_generation(self):
        self.next_btn.disabled = True
        self.next_btn.text = "Generating..."
        self.page.update()

        sig_path = None
        try:
            # Capture the path to the temp image file
            sig_path = self.sig_pad.get_signature_path()

            # Prepare Metadata & Read Template Once
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            download_dir = os.path.join(os.path.expanduser("~"), "Downloads")

            # Keep a bytes copy only for schema detection; fill uses file path
            # so PyPDFForm can generate proper appearance streams.
            with open(self.template_path, "rb") as f:
                template_data = f.read()

            # --- 1. Detect all blank fields in the PDF template ---
            pdf_schema = PdfWrapper(template_data).schema
            schema_props = pdf_schema.get("properties", {}) if pdf_schema else {}
            pdf_fields = list(schema_props.keys())

            # Guard: if the PDF has no AcroForm fields, it is a static/flat PDF.
            if not pdf_fields:
                await self._handle_static_pdf(template_data, sig_path, timestamp)
                return

            # Extract per-field character limits from the PDF's /MaxLen attribute.
            # PyPDFForm exposes this as 'maxLength' in the schema properties.
            # Fields without a declared limit are left out (fallback handled in ai/paperwork.py).
            field_limits = {
                field: props["maxLength"]
                for field, props in schema_props.items()
                if isinstance(props, dict) and isinstance(props.get("maxLength"), int)
            }
            if field_limits:
                logger.debug("PDF field limits: %s", field_limits)

            # Pass the full per-field schema props to ai/paperwork.py so it can
            # distinguish boolean (checkbox) and enum (radio/select) fields from text.
            field_schema = {
                field: props
                for field, props in schema_props.items()
                if isinstance(props, dict) and props.get("type") in ("boolean", "string")
            }

            # --- 2. Build the UI-sourced (hardcoded) mapping first ---
            roi_details = None
            if self.selected_type == "roi" and self.prov_to_dropdown.value:
                recip = self._resolve_recipient()
                roi_details = {
                    "recipient": recip,
                    "purpose": self.roi_purpose.value if hasattr(self, "roi_purpose") else "",
                    "expiry": self.roi_expiry.value if hasattr(self, "roi_expiry") else "",
                }

            mapping = build_ui_mapping(
                pdf_fields=pdf_fields,
                patient_name=self.patient_name,
                patient_dob=self.page.current_profile[2],
                sign_date=self.sign_date.value,
                sig_path=sig_path,
                form_type=self.selected_type,
                roi_details=roi_details,
            )

            # --- 3. AI Mapping Phase ---
            ui_mapped_keys = set(mapping.keys())
            remaining_fields = [f for f in pdf_fields if f not in ui_mapped_keys]

            if remaining_fields:
                self._show_ai_loading_ui()

                ai_mapping = await asyncio.to_thread(
                    map_pdf_fields,
                    self.page.db_connection,
                    self.patient_id,
                    remaining_fields,
                    field_schema,
                    field_limits,
                )

                mapping.update(ai_mapping)
                logger.debug("Final merged mapping (keys: %s)", list(mapping.keys()))

            # --- 4. PDF Generation ---
            self.next_btn.text = "Saving..."
            self.page.update()

            form_prefix = "Intake" if self.selected_type == "intake" else "ROI"

            output_path, acc_bytes, flat_bytes = fill_acroform_pdf(
                template_path=self.template_path,
                mapping=mapping,
                form_prefix=form_prefix,
                timestamp=timestamp,
                download_dir=download_dir,
                want_accessible=self.check_accessible.value,
                want_flattened=self.check_flattened.value,
            )

            # --- 5. Secure Archive ---
            if self.save_to_db_check.value:
                archive_bytes = acc_bytes or flat_bytes
                if not archive_bytes:
                    archive_bytes = PdfWrapper(
                        self.template_path, generate_appearance_streams=True
                    ).fill(mapping).read()

                doc_id = archive_to_records(
                    self.page, self.patient_id, archive_bytes, form_prefix, timestamp,
                )
                if doc_id is not None:
                    self._last_archived_doc_id = doc_id

            # --- 6. Success & Cleanup ---
            if output_path:
                show_snack(self.page, "PDF(s) generated in Downloads.", "green")
                open_file_cross_platform(output_path)
            elif self.save_to_db_check.value:
                show_snack(self.page, "Form archived to Patient Records.", "green")
            else:
                show_snack(self.page, "No output versions selected.", "orange")

            # --- 7. Records Request Tracker hook (ROI only) ---
            if self.selected_type == "roi":
                create_roi_records_request(
                    self.page,
                    self.patient_id,
                    self.prov_from_dropdown.value,
                    template_data,
                    self._last_archived_doc_id,
                )

            self.close()
            self._cleanup_sig(sig_path)

        except Exception as ex:
            logger.error("PDF generation error: %s", ex, exc_info=True)
            show_snack(self.page, f"Error: {ex}", "red")
            self.next_btn.disabled = False
            self.next_btn.text = "Generate & Save"
            self.page.update()

    # --------------------------------------------------------------------- #
    #  Static-PDF fallback                                                    #
    # --------------------------------------------------------------------- #

    async def _handle_static_pdf(self, template_data: bytes, sig_path: str | None, timestamp: str):
        """Handle PDFs without AcroForm fields via the overlay-based fill path."""
        choice_future: asyncio.Future = asyncio.get_event_loop().create_future()

        def _on_continue(_e=None):
            if not choice_future.done():
                choice_future.set_result("continue")

        def _on_cancel(_e=None):
            if not choice_future.done():
                choice_future.set_result("cancel")

        # Reuse the existing wizard dialog by swapping its content to show the warning.
        self.dlg.title = ft.Row([
            ft.Icon(ft.Icons.INFO_OUTLINE, color="orange"),
            ft.Text("  Fillable Form Recommended", weight="bold"),
        ])
        self.dlg.modal = True
        self.content_area.controls.clear()
        self.content_area.controls.extend([
            ft.Text(
                "This PDF does not have fillable fields. An accessible copy cannot be generated for this document."
                "The app can still "
                "try to place your information by reading the visible labels, "
                "but the result may need manual corrections.",
                size=pt_scale(self.page, 13),
            ),
            ft.Container(
                bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.ORANGE),
                border_radius=pt_scale(self.page, 6),
                padding=ft.padding.all(pt_scale(self.page, 8)),
                content=ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="orange", size=pt_scale(self.page, 16)),
                    ft.Container(
                        expand=True,
                        content=ft.Text(
                            "Please review the output carefully before submitting.",
                            size=pt_scale(self.page, 12),
                            color="orange",
                        ),
                    ),
                ], spacing=pt_scale(self.page, 6)),
            ),
            ft.ExpansionTile(
                title=ft.Text("What is a fillable PDF?", size=pt_scale(self.page, 12)),
                expanded=False,
                controls=[
                    ft.Container(
                        padding=ft.padding.only(left=pt_scale(self.page, 8), bottom=pt_scale(self.page, 8)),
                        content=ft.Text(
                            "A fillable PDF has embedded form fields that the app "
                            "can automatically populate. The file you selected has "
                            "blanks drawn as lines or underscores instead, "
                            "which makes it harder to process.",
                            size=pt_scale(self.page, 11),
                            color=ft.Colors.SECONDARY,
                        ),
                    ),
                ],
            ),
            ft.ExpansionTile(
                title=ft.Text("Where to find a fillable version:", size=pt_scale(self.page, 12)),
                expanded=False,
                controls=[
                    ft.Container(
                        alignment=ft.alignment.Alignment(-1.0, -1.0),
                        padding=ft.padding.only(left=pt_scale(self.page, 24), bottom=pt_scale(self.page, 8)),
                        content=ft.Text(
                            "\u2022 Check the provider's website or patient portal\n"
                            "\u2022 Call the front desk and ask for their \"fillable PDF\"\n"
                            "\u2022 Open the form in Adobe Acrobat to see if it has clickable fields",
                            size=pt_scale(self.page, 11),
                            color=ft.Colors.SECONDARY,
                        ),
                    ),
                ],
            ),
        ])
        self.dlg.actions = [
            ft.TextButton("Cancel", on_click=_on_cancel),
            ft.FilledButton("Continue Anyway", icon=ft.Icons.ARROW_FORWARD, on_click=_on_continue),
        ]
        self.dlg.update()
        self.page.update()

        user_choice = await choice_future

        if user_choice == "cancel":
            self.close()
            self._cleanup_sig(sig_path)
            return

        # User chose to continue with static PDF — show loading UI.
        self.dlg.title = ft.Text("Completing Paperwork")
        self.dlg.modal = False
        self.dlg.actions = []
        self.next_btn.disabled = True
        self.next_btn.text = "Generating..."
        self.content_area.controls.clear()
        self.content_area.controls.extend([
            ft.Text(
                "Reading form labels and matching your information...",
                weight="bold",
                size=pt_scale(self.page, 15),
            ),
            ft.ProgressBar(width=pt_scale(self.page, 440)),
            ft.Text(
                "This may take a minute. Processing time depends on your computer's capacity because the AI runs entirely locally on your device.",
                size=pt_scale(self.page, 12),
                color=ft.Colors.SECONDARY,
                italic=True,
            ),
            ft.Container(height=pt_scale(self.page, 6)),
            ft.Text(
                "You can click away from this and keep using the app. "
                "The completed form will appear when ready.",
                size=pt_scale(self.page, 12),
                color=ft.Colors.PRIMARY,
            ),
            ft.Container(height=pt_scale(self.page, 8)),
            make_tip_card(self.page),
        ])
        self.dlg.open = True
        self.page.update()

        static_bytes, fill_items = await asyncio.to_thread(
            fill_static_pdf,
            self.template_path,
            self.page.db_connection,
            self.patient_id,
            sig_path=sig_path,
        )

        form_prefix = "Intake" if self.selected_type == "intake" else "ROI"

        if not fill_items:
            download_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            static_out = os.path.join(download_dir, f"{form_prefix}_Draft_{timestamp}.pdf")
            with open(static_out, "wb") as f:
                f.write(static_bytes)
            show_snack(self.page, "No fields matched. Blank draft saved to Downloads.", "orange")
            open_file_cross_platform(static_out)
            self.close()
            self._cleanup_sig(sig_path)
            return

        # Close the loading dialog before opening placement review
        self.close()

        def _on_placement_confirmed(final_bytes: bytes):
            download_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            static_out = os.path.join(
                download_dir, f"{form_prefix}_Draft_{timestamp}.pdf"
            )
            try:
                with open(static_out, "wb") as f:
                    f.write(final_bytes)
            except Exception as write_ex:
                show_snack(self.page, f"Save error: {write_ex}", "red")
                return

            if self.save_to_db_check.value:
                doc_id = archive_to_records(
                    self.page, self.patient_id, final_bytes, form_prefix, timestamp,
                )
                if doc_id is not None:
                    self._last_archived_doc_id = doc_id

            show_snack(
                self.page,
                "Draft saved to Downloads. Please review before submitting.",
                "orange",
            )
            open_file_cross_platform(static_out)

            # Records Request Tracker hook (ROI only)
            if self.selected_type == "roi":
                create_roi_records_request(
                    self.page,
                    self.patient_id,
                    self.prov_from_dropdown.value,
                    template_data,
                    self._last_archived_doc_id,
                )

            self.close()
            self._cleanup_sig(sig_path)

        from ui.wizards.placement_review import open_placement_review
        open_placement_review(
            page=self.page,
            merged_pdf_bytes=static_bytes,
            fill_items=fill_items,
            template_path=self.template_path,
            on_confirm=_on_placement_confirmed,
        )

    # --------------------------------------------------------------------- #
    #  Helpers                                                                #
    # --------------------------------------------------------------------- #

    def _resolve_recipient(self) -> dict:
        """Build a recipient dict from the 'Send Records To' dropdown."""
        recip = {"name": "", "address": "", "phone": "", "email": ""}
        to_key = self.prov_to_dropdown.value

        if to_key == "patient":
            recip["name"] = self.patient_name
        else:
            provs = list_providers(self.page.db_connection, self.patient_id)
            prov = next((p for p in provs if str(p[0]) == to_key), None)
            # provider tuple: (id, name, specialty, clinic, phone, fax, email, address, ...)
            if prov:
                recip["name"] = prov[1] or ""
                recip["address"] = prov[7] or ""
                recip["phone"] = prov[4] or ""
                recip["email"] = prov[6] or ""

        return recip

    def _show_ai_loading_ui(self):
        """Swap the dialog content for an AI-loading state with an expansion panel."""
        self.next_btn.text = "Please wait while your information is matched to the form fields"
        self.next_btn.disabled = True

        explanation = ft.ExpansionTile(
            title=ft.Text("What is happening?", size=pt_scale(self.page, 13), italic=True),
            initially_expanded=False,
            controls=[
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=pt_scale(self.page, 8), vertical=pt_scale(self.page, 4)),
                    content=ft.Text(
                        "The AI is reading your saved medical records, including your allergies, "
                        "medications, and medical conditions, and matching them to the blank fields "
                        "in this form.\n\nNothing is sent online. This runs entirely on your device "
                        "using a local AI model, the same one used for your health record extraction.",
                        size=pt_scale(self.page, 12),
                        color=ft.Colors.SECONDARY,
                        selectable=True,
                    ),
                )
            ],
        )

        self.content_area.controls.clear()
        self.content_area.controls.extend([
            ft.Text(
                "Filling form fields...",
                weight="bold",
                size=pt_scale(self.page, 15),
            ),
            ft.ProgressBar(width=pt_scale(self.page, 440)),
            explanation,
            ft.Container(height=pt_scale(self.page, 8)),
            make_tip_card(self.page),
        ])

        if self.dlg.open:
            self.dlg.update()
        self.page.update()

    @staticmethod
    def _cleanup_sig(sig_path: str | None):
        """Remove the temporary signature PNG if it exists."""
        if sig_path and os.path.exists(sig_path):
            os.remove(sig_path)