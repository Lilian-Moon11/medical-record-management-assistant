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
# - Signature capture via a canvas-backed gesture detector, exported to PNG bytes
#   for PDF injection
# - Optional “archive to Patient Records” toggle for saving the generated PDF
#   into the app’s local record store (intended to be encrypted + recorded in DB)
#
# Design goals:
# - Keep the flow approachable for non-technical users (clear steps + guardrails)
# - Use stable, reusable overlay dialogs to avoid event-handler flakiness
# - Support accessibility preferences via scale-safe sizing (pt_scale(page, ...))
# - Fail safely with clear snackbar messages when inputs or generation fail
# -----------------------------------------------------------------------------

import asyncio
import flet as ft
import flet.canvas as cv
import os
import random
from datetime import datetime
from PyPDFForm import PdfWrapper

from ai.paperwork import map_pdf_fields
from ai.paperwork_overlay import fill_static_pdf
from database.clinical import list_providers
from database.records_requests import create_request as create_records_request
from utils.roi_parser import parse_due_date_from_text
from utils.ui_helpers import pt_scale, show_snack
from PIL import Image, ImageDraw
import io
from crypto.file_crypto import get_or_create_file_master_key, encrypt_bytes
from database import add_document
from utils.open_file import open_file_cross_platform

# ---------------------------------------------------------------------------
# Loading-screen tips — one random tip is shown per generation session,
# similar to video-game loading screens.
# ---------------------------------------------------------------------------
_LOADING_TIPS = [
    # Overview
    "Overview tab: Question marks in the top right of each tab offer information, suggestions, and a little appreciation as you navigate.",
    "Overview tab: The Notes space is great for action items, things to bring up at your next appointment, or self affirmations. These notes can also be included in your summary PDF.",
    "Overview tab: The Records Requests panel tracks your ROI follow-ups. A task is created automatically when you complete an ROI form. Click the due date to edit it inline.",
    'Overview tab: The orange "Review Suggestions" button appears when new data has been extracted from an uploaded document. Click it to accept or dismiss each suggestion.',
    'Overview tab: Use "Generate Summary" to export a customizable PDF of your health record to share with providers.',
    # Health Record
    'Health Record tab: The "Edit Visibility" button lets you mark sections as sensitive, adding eye icons that can be used to hide or reveal information.',
    # Vitals & Labs
    "Vitals & Labs tab: There are two sub-tabs: Vitals for daily measurements like blood pressure or weight, and Clinical Labs for official test results.",
    "Vitals & Labs tab: Select a metric or test name from the left sidebar to see its trend chart and full history table.",
    "Vitals & Labs tab: The trend chart plots numeric values over time. A green dashed line shows the normal reference range when available.",
    "Vitals & Labs tab: Click a column header in the Historical Test Table to sort results.",
    # Documents
    'Medical Records tab: Upload any medical document (PDF, image, etc.) using the "Upload Document" button.',
    'Medical Records tab: After uploading, AI extraction runs in the background. An orange "Review Suggestions" button will appear on the Overview tab when it is ready.',
    "Medical Records tab: Click a column header (Upload Date, Visit Date, Specialty) to sort the table. Click again to reverse the order.",
    "Medical Records tab: Documents are encrypted on your device. The Open button decrypts a secure temporary copy for viewing.",
    # Providers
    "Provider Directory tab: Primarily used for release of information forms, but you can use it to track any provider you choose.",
    # Family History
    "Family History tab: Only 1st and 2nd degree relatives are listed here since those are what science agrees matter for hereditary risk, but you can add more if you want to.",
    "Family History tab: Your own diagnoses live in the Health Record tab, not here.",
    # Settings
    "Settings tab: Your recovery key lets you restore your vault if you ever forget your password.",
    "Settings tab: Rotating the recovery key will invalidate your old key and generate a fresh one.",
]

class SignaturePad(ft.GestureDetector):
    @staticmethod
    def _sig_bg(page: ft.Page) -> str:
        return "#2B2B2B" if page.theme_mode == ft.ThemeMode.DARK else "#F2F2F2"

    @staticmethod
    def _sig_border(page: ft.Page) -> str:
        return "#6E6E6E" if page.theme_mode == ft.ThemeMode.DARK else "#B0B0B0"

    @staticmethod
    def _ink(page: ft.Page) -> str:
        # High-contrast ink color for visibility
        return "#FFFFFF" if page.theme_mode == ft.ThemeMode.DARK else "#000000"

    def __init__(self, page: ft.Page):
        super().__init__()
        self.pg = page
        self.points = []  # list[tuple[float,float] | None] (None separates strokes)
        self._cur_x = 0.0
        self._cur_y = 0.0

        self.path = cv.Path(
            elements=[],
            paint=ft.Paint(
                stroke_width=3,
                style=ft.PaintingStyle.STROKE,
                stroke_join=ft.StrokeJoin.ROUND,
                stroke_cap=ft.StrokeCap.ROUND,
                color=SignaturePad._ink(self.pg),
            ),
        )

        self.canvas = cv.Canvas(
            shapes=[self.path],
            width=pt_scale(self.pg, 400),
            height=pt_scale(self.pg, 150),
        )

        self.content = ft.Container(
            self.canvas,
            bgcolor=SignaturePad._sig_bg(self.pg),
            border=ft.border.all(1, SignaturePad._sig_border(self.pg)),
            border_radius=pt_scale(self.pg, 4),
        )

        self.on_tap_down = self.tap_down
        self.on_pan_start = self.pan_start
        self.on_pan_update = self.pan_update
        self.on_pan_end = self.pan_end

    def tap_down(self, e):
        # TapEvent has local_position with actual coordinates
        pos = getattr(e, "local_position", None)
        if pos:
            self._cur_x = pos.x
            self._cur_y = pos.y

    def pan_start(self, e: ft.DragStartEvent):
        # Mark that the next pan_update should emit MoveTo (not LineTo).
        # DragStartEvent has no position data, and tap_down may not have
        # fired yet, so _cur_x/_cur_y could still be (0, 0).
        self._need_move = True

    def pan_update(self, e: ft.DragUpdateEvent):
        # Prefer absolute local_position (Flet 0.80+) over delta accumulation
        # to avoid calibration drift between cursor and drawn ink.
        pos = getattr(e, "local_position", None)
        if pos:
            self._cur_x = pos.x
            self._cur_y = pos.y
        else:
            delta = getattr(e, "local_delta", None)
            if not delta:
                return
            self._cur_x += delta.x
            self._cur_y += delta.y

        if getattr(self, "_need_move", False):
            self.path.elements.append(cv.Path.MoveTo(self._cur_x, self._cur_y))
            self._need_move = False
        else:
            self.path.elements.append(cv.Path.LineTo(self._cur_x, self._cur_y))
        self.points.append((self._cur_x, self._cur_y))
        self.canvas.update()

    def pan_end(self, e: ft.DragEndEvent):
        # Separate strokes so exported PNG doesn't connect lines across pen lifts
        self.points.append(None)

    def clear(self, e=None):
        self.points = []
        self._cur_x = 0.0
        self._cur_y = 0.0
        self.path.elements = []
        self.canvas.update()

    def get_signature_path(self):
        """Saves signature to a temp file. PyPDFForm requires a file path for images."""
        if not any(isinstance(p, tuple) for p in self.points):
            return None

        import tempfile
        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            # Explicitly close file descriptor so Windows doesn't block Pillow's save()
            os.close(fd)
            
            img = render_signature_png(self.points, width=400, height=150)
            img.save(path, format="PNG")
            return path
        except Exception as e:
            print(f"Sig error: {e}")
            return None


def render_signature_png(points, width: int, height: int) -> Image.Image:
    """Pure helper: render signature strokes to a PIL Image (easy to unit test)."""
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    last = None
    for p in points:
        if p is None:
            last = None
            continue
        if last is not None:
            draw.line([last, p], fill=(0, 0, 0, 255), width=4)
        last = p

    return img


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
            self.page.overlay.append(self.dlg)

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
            self.page.overlay.append(self.dlg)

        self.render_step()

        self.dlg.open = True
        self.page.update()

    def close(self, e=None):
        self.dlg.open = False
        self.dlg.update()
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
            output_path = None
            acc_bytes = None
            flat_bytes = None

            # Keep a bytes copy only for schema detection; fill uses file path
            # so PyPDFForm can generate proper appearance streams.
            with open(self.template_path, "rb") as f:
                template_data = f.read()

            # --- 1. Detect all blank fields in the PDF template ---
            pdf_schema = PdfWrapper(template_data).schema
            schema_props = pdf_schema.get("properties", {}) if pdf_schema else {}
            pdf_fields = list(schema_props.keys())


            # Guard: if the PDF has no AcroForm fields, it is a static/flat PDF.
            # Show an explanatory dialog and let the user decide whether to
            # continue (manual fill) or go back and try a fillable version.
            if not pdf_fields:
                import asyncio as _asyncio
                choice_future: _asyncio.Future = _asyncio.get_event_loop().create_future()

                def _on_continue(_e=None):
                    if not choice_future.done():
                        choice_future.set_result("continue")

                def _on_cancel(_e=None):
                    if not choice_future.done():
                        choice_future.set_result("cancel")

                # Reuse the existing wizard dialog (which we know opens/closes
                # reliably) by swapping its content to show the warning.
                self.dlg.title = ft.Row([
                    ft.Icon(ft.Icons.INFO_OUTLINE, color="orange"),
                    ft.Text("  Fillable Form Recommended", weight="bold"),
                ])
                self.dlg.modal = True
                self.content_area.controls.clear()
                self.content_area.controls.extend([
                    ft.Text(
                        "This PDF does not have fillable fields. An accessible copy cannot be generated for this document.The app can still "
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
                    if sig_path and os.path.exists(sig_path):
                        os.remove(sig_path)
                    return

                # User chose to continue with static PDF.
                # Show a non-modal loading dialog the user can dismiss.
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
                    self._make_tip_card(),
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

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                form_prefix = "Intake" if self.selected_type == "intake" else "ROI"

                if not fill_items:
                    # Nothing was placed — save the template as a blank draft
                    # so the user at least has a clean copy to fill manually.
                    download_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                    static_out = os.path.join(download_dir, f"{form_prefix}_Draft_{timestamp}.pdf")
                    with open(static_out, "wb") as f:
                        f.write(static_bytes)
                    show_snack(self.page, "No fields matched. Blank draft saved to Downloads.", "orange")
                    open_file_cross_platform(static_out)
                    self.close()
                    if sig_path and os.path.exists(sig_path):
                        os.remove(sig_path)
                    return

                # Close the loading dialog before opening placement review
                self.close()

                # execute_generation returns here; all further work happens
                # inside the on_confirm callback once the user clicks Save Final.

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
                        try:
                            dest_dir = os.path.join(os.getcwd(), "data", str(self.patient_id))
                            os.makedirs(dest_dir, exist_ok=True)
                            display_name = f"{form_prefix}_Draft_{timestamp}.pdf"
                            enc_path = os.path.join(dest_dir, display_name + ".enc")
                            fmk = get_or_create_file_master_key(
                                self.page.db_connection, dmk_raw=self.page.db_key_raw
                            )
                            ciphertext = encrypt_bytes(fmk, final_bytes)
                            with open(enc_path, "wb") as f:
                                f.write(ciphertext)
                            doc_id = add_document(
                                self.page.db_connection,
                                self.patient_id,
                                display_name,
                                enc_path,
                                datetime.now().strftime("%Y-%m-%d %H:%M"),
                            )
                            # Flag as processed so background AI extraction ignores it
                            self.page.db_connection.execute(
                                "INSERT OR IGNORE INTO ai_extraction_inbox (patient_id, doc_id, field_key, suggested_value, confidence, source_file_name, status) VALUES (?, ?, 'system.processed', ?, 1.0, ?, 'system')",
                                (self.patient_id, doc_id, str(doc_id), display_name)
                            )
                            self.page.db_connection.commit()
                            self._last_archived_doc_id = doc_id
                        except Exception as arc_ex:
                            print(f"Static archive error: {arc_ex}")

                    show_snack(
                        self.page,
                        "Draft saved to Downloads. Please review before submitting.",
                        "orange",
                    )
                    open_file_cross_platform(static_out)

                    # Records Request Tracker hook (ROI only)
                    if self.selected_type == "roi":
                        self._create_roi_records_request(template_data, self._last_archived_doc_id)

                    self.close()
                    if sig_path and os.path.exists(sig_path):
                        os.remove(sig_path)

                from ui.wizards.placement_review import open_placement_review
                open_placement_review(
                    page=self.page,
                    merged_pdf_bytes=static_bytes,
                    fill_items=fill_items,
                    template_path=self.template_path,
                    on_confirm=_on_placement_confirmed,
                )
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
                print(f"PDF FIELD LIMITS: {field_limits}")

            # Pass the full per-field schema props to ai/paperwork.py so it can
            # distinguish boolean (checkbox) and enum (radio/select) fields from text.
            field_schema = {
                field: props
                for field, props in schema_props.items()
                if isinstance(props, dict) and props.get("type") in ("boolean", "string")
            }

            # --- 2. Build the UI-sourced (hardcoded) mapping first ---
            # These come directly from Wizard inputs and always take priority.
            # Helper to find the best matching key in the PDF schema.
            def _find_key(possible_matches, exclude=None):
                for p in possible_matches:
                    for f in pdf_fields:
                        if exclude and f == exclude:
                            continue
                        if p.lower() == f.lower():
                            return f  # exact match first
                for p in possible_matches:
                    for f in pdf_fields:
                        if exclude and f == exclude:
                            continue
                        if p.lower() in f.lower():
                            return f  # substring match
                return None

            mapping = {}

            # Fields present in BOTH intake and ROI forms
            name_key = _find_key(["patient name", "Patient Name", "name", "patient"])
            if name_key:
                mapping[name_key] = self.patient_name

            dob_key = _find_key(["birth date", "Birth Date", "dob", "date of birth", "DOB"])
            if dob_key:
                mapping[dob_key] = self.page.current_profile[2]

            date_key = _find_key(["Date", "date", "Sign Date", "today"], exclude=dob_key)
            if date_key:
                mapping[date_key] = self.sign_date.value

            sig_key = _find_key(["signature", "Signature", "sign"])
            if sig_path and sig_key:
                mapping[sig_key] = sig_path

            # ROI-specific: recipient provider fields selected in the Wizard UI
            if self.selected_type == "roi" and self.prov_to_dropdown.value:
                to_key = self.prov_to_dropdown.value
                recip = {"name": "", "address": "", "phone": "", "email": ""}

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

                rn_key = _find_key(["Recipient Name", "recipient", "send to"])
                if rn_key and recip["name"]:
                    mapping[rn_key] = recip["name"]

                addr_key = _find_key(["Address", "address", "street"])
                if addr_key and recip["address"]:
                    mapping[addr_key] = recip["address"]

                ph_key = _find_key(["Phone", "phone", "telephone", "tel"])
                if ph_key and recip["phone"]:
                    mapping[ph_key] = recip["phone"]

                # Exact match for Email_2 to avoid colliding with patient Email field
                em_key = _find_key(["Email_2"])
                if em_key and recip["email"]:
                    mapping[em_key] = recip["email"]

                # ROI purpose and expiry
                purpose_key = _find_key(["purpose", "Purpose", "reason", "Reason"])
                if purpose_key and hasattr(self, "roi_purpose") and self.roi_purpose.value:
                    mapping[purpose_key] = self.roi_purpose.value

                expiry_key = _find_key(["expir", "Expir", "expiration", "Expiration", "expires"])
                if expiry_key and hasattr(self, "roi_expiry") and self.roi_expiry.value:
                    mapping[expiry_key] = self.roi_expiry.value

            print(f"UI MAPPING APPLIED: {mapping}")

            # --- 3. AI Mapping Phase ---
            # Filter to only fields NOT already handled by the UI hardcoding above.
            ui_mapped_keys = set(mapping.keys())
            remaining_fields = [f for f in pdf_fields if f not in ui_mapped_keys]

            if remaining_fields:
                # Show the loading indicator with an expansion panel
                self._show_ai_loading_ui()

                # Run the blocking LLM call off the UI thread.
                # Pass field_schema so booleans (checkboxes) and enums (radio buttons)
                # are handled correctly, and field_limits for character truncation.
                ai_mapping = await asyncio.to_thread(
                    map_pdf_fields,
                    self.page.db_connection,
                    self.patient_id,
                    remaining_fields,
                    field_schema,
                    field_limits,
                )

                # Merge AI results — UI-sourced mapping always wins on collision
                mapping.update(ai_mapping)
                print(f"FINAL MERGED MAPPING: {mapping}")

            # --- 4. PDF Generation ---
            self.next_btn.text = "Saving..."
            self.page.update()

            # Derive output filename prefix from form type for clarity
            form_prefix = "Intake" if self.selected_type == "intake" else "ROI"

            # --- Accessible Copy ---
            if self.check_accessible.value:
                filled_acc = PdfWrapper(self.template_path, generate_appearance_streams=True).fill(mapping)
                acc_bytes = filled_acc.read()
                acc_file = os.path.join(download_dir, f"{form_prefix}_Accessible_{timestamp}.pdf")
                with open(acc_file, "wb") as f:
                    f.write(acc_bytes)
                output_path = acc_file

            # --- Flattened Copy ---
            if self.check_flattened.value:
                filled_flat = PdfWrapper(self.template_path, generate_appearance_streams=True).fill(mapping, flatten=True)
                flat_bytes = filled_flat.read()
                flat_file = os.path.join(download_dir, f"{form_prefix}_Flattened_{timestamp}.pdf")
                with open(flat_file, "wb") as f:
                    f.write(flat_bytes)
                if not output_path:
                    output_path = flat_file

            # --- 5. Secure Archive ---
            if self.save_to_db_check.value:
                archive_bytes = acc_bytes or flat_bytes
                if not archive_bytes:
                    archive_bytes = PdfWrapper(
                        self.template_path, generate_appearance_streams=True
                    ).fill(mapping).read()

                try:
                    dest_dir = os.path.join(os.getcwd(), "data", str(self.patient_id))
                    os.makedirs(dest_dir, exist_ok=True)

                    display_name = f"{form_prefix}_Signed_{timestamp}.pdf"
                    enc_path = os.path.join(dest_dir, display_name + ".enc")

                    fmk = get_or_create_file_master_key(self.page.db_connection, dmk_raw=self.page.db_key_raw)
                    ciphertext = encrypt_bytes(fmk, archive_bytes)

                    with open(enc_path, "wb") as f:
                        f.write(ciphertext)

                    doc_id = add_document(
                        self.page.db_connection,
                        self.patient_id,
                        display_name,
                        enc_path,
                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                    )
                    # Flag as processed so background AI extraction ignores it
                    self.page.db_connection.execute(
                        "INSERT OR IGNORE INTO ai_extraction_inbox (patient_id, doc_id, field_key, suggested_value, confidence, source_file_name, status) VALUES (?, ?, 'system.processed', ?, 1.0, ?, 'system')",
                        (self.patient_id, doc_id, str(doc_id), display_name)
                    )
                    self.page.db_connection.commit()
                    self._last_archived_doc_id = doc_id
                    show_snack(self.page, "Form securely archived.", "blue")
                except Exception as db_ex:
                    print(f"Archive Error: {db_ex}")
                    show_snack(self.page, "Archive failed, check data folder.", "orange")

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
                self._create_roi_records_request(template_data, self._last_archived_doc_id)

            self.close()

            if sig_path and os.path.exists(sig_path):
                os.remove(sig_path)

        except Exception as ex:
            print(f"GENERATION ERROR: {ex}")
            show_snack(self.page, f"Error: {ex}", "red")
            self.next_btn.disabled = False
            self.next_btn.text = "Generate & Save"
            self.page.update()

    def _create_roi_records_request(self, template_bytes: bytes, source_doc_id: int | None = None) -> None:
        """Create a pending records request after a successful ROI completion.

        Extracts the provider name from the 'Records From' dropdown selection,
        parses a due date from the template text (or falls back to 30 days),
        and inserts a row into records_requests.
        """
        try:
            # Resolve provider name from the wizard dropdown
            provider_name = ""
            department: str | None = None
            from_key = self.prov_from_dropdown.value
            if from_key:
                provs = list_providers(self.page.db_connection, self.patient_id)
                prov = next((p for p in provs if str(p[0]) == from_key), None)
                if prov:
                    # provider tuple: (id, name, specialty, clinic, phone, fax, email, address, ...)
                    provider_name = (prov[1] or "").strip()
                    department = (prov[3] or "").strip() or None  # clinic as department

            if not provider_name:
                provider_name = "Unknown Provider"

            # Parse due date from template text (lightweight regex, no LLM)
            try:
                import pdfplumber
                text = ""
                import io as _io
                with pdfplumber.open(_io.BytesIO(template_bytes)) as pdf:
                    for pg in pdf.pages:
                        text += (pg.extract_text() or "")
            except Exception:
                text = ""

            today = datetime.today()
            due_date, due_source = parse_due_date_from_text(text, request_date=today)
            date_requested = today.strftime("%Y-%m-%d")

            create_records_request(
                self.page.db_connection,
                self.patient_id,
                provider_name,
                department,
                date_requested,
                due_date,
                due_source,
                notes=None,
                source_doc_id=source_doc_id,
            )

            # Refresh the Overview panel if it is currently visible
            if hasattr(self.page, "_refresh_requests_panel"):
                try:
                    self.page._refresh_requests_panel()
                except Exception:
                    pass
        except Exception as ex:
            # Non-critical: log but don't surface to the user
            print(f"Records request hook error: {ex}")

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
            self._make_tip_card(),
        ])

        if self.dlg.open:
            self.dlg.update()
        self.page.update()

    def _make_tip_card(self) -> ft.Container:
        """Returns a styled card showing one randomly chosen loading tip."""
        tip = random.choice(_LOADING_TIPS)
        return ft.Container(
            padding=ft.padding.symmetric(
                horizontal=pt_scale(self.page, 12),
                vertical=pt_scale(self.page, 10),
            ),
            border_radius=pt_scale(self.page, 8),
            bgcolor=ft.Colors.with_opacity(0.07, ft.Colors.PRIMARY),
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.LIGHTBULB_OUTLINE,
                        color=ft.Colors.YELLOW,
                        size=pt_scale(self.page, 18),
                    ),
                    ft.Text(
                        tip,
                        expand=True,
                        size=pt_scale(self.page, 12),
                        color=ft.Colors.SECONDARY,
                        italic=True,
                    ),
                ],
                spacing=pt_scale(self.page, 8),
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        )