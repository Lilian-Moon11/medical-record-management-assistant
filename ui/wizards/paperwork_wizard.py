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

import flet as ft
import flet.canvas as cv
import os
from datetime import datetime
from PyPDFForm import PdfWrapper

from database.clinical import list_providers
from utils.ui_helpers import pt_scale, show_snack
from PIL import Image, ImageDraw
import io
from crypto.file_crypto import get_or_create_file_master_key, encrypt_bytes
from database import add_document

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
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    last = None
    for p in points:
        if p is None:
            last = None
            continue
        if last is not None:
            draw.line([last, p], fill=(0, 0, 0), width=4)
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
        self.prov_from_dropdown = ft.Dropdown(label="Records From (Saved Providers):")

        # 3. UI STRUCTURE
        self.content_area = ft.Column(
            tight=True, 
            width=pt_scale(page, 520), 
            spacing=pt_scale(page, 10) # Scaled spacing
        )
        self.next_btn = ft.FilledButton("Next", on_click=self.next_step)
        self.page.on_keyboard_event = self.on_key_event

        self.dlg = ft.AlertDialog(
            title=ft.Text("Complete Paperwork"),
            content=self.content_area,
            actions=[
                ft.TextButton("Cancel", on_click=self.close),
                self.next_btn,
            ],
            on_dismiss=self.close,
        )

        # IMPORTANT: mount dialog ONCE so open() actually shows it
        if self.dlg not in self.page.overlay:
            self.page.overlay.append(self.dlg)

    def on_key_event(self, e: ft.KeyboardEvent):
        """Triggers next_step when Enter is pressed and the dialog is open."""
        if e.key == "Enter" and self.dlg.open:
            # Accessibility: Prevent generation if they haven't reached the final step
            if self.step < 4:
                self.next_step()
            else:
                self.execute_generation()
    
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
                # If ROI not selected, skip to review
                self.next_step()

        elif self.step == 3:
            self.content_area.controls.append(
                ft.Text("Step 3: Sign the Authorization", weight="bold", size=header_size)
            )
            # Pass page to sig_pad for scaling
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

    def next_step(self, e=None):
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
            self.execute_generation()
            return

        self.render_step()

    def execute_generation(self):
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

            # Dynamic Field Mapping to avoid PyPDFForm key errors
            pdf_schema = PdfWrapper(template_data).schema
            pdf_fields = list(pdf_schema.get("properties", {}).keys()) if pdf_schema else []
            
            # Debug: print detected fields so the user can verify
            print(f"PDF FIELDS DETECTED: {pdf_fields}")
            
            # Helper to find the best matching key in the PDF
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
            
            name_key = _find_key(["patient name", "Patient Name", "name", "patient"])
            if name_key: mapping[name_key] = self.patient_name
            
            dob_key = _find_key(["birth date", "Birth Date", "dob", "date of birth", "DOB"])
            if dob_key: mapping[dob_key] = self.page.current_profile[2]
            
            date_key = _find_key(["Date", "date", "Sign Date", "today"], exclude=dob_key)
            if date_key:
                mapping[date_key] = self.sign_date.value

            sig_key = _find_key(["signature", "Signature", "sign"])
            if sig_path and sig_key:
                mapping[sig_key] = sig_path

            # --- Recipient (Send To) provider fields ---
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

                # Map recipient fields to PDF
                rn_key = _find_key(["Recipient Name", "recipient", "send to"])
                if rn_key and recip["name"]:
                    mapping[rn_key] = recip["name"]

                addr_key = _find_key(["Address", "address", "street"])
                if addr_key and recip["address"]:
                    mapping[addr_key] = recip["address"]

                ph_key = _find_key(["Phone", "phone", "telephone", "tel"])
                if ph_key and recip["phone"]:
                    mapping[ph_key] = recip["phone"]

                # Use exact match for Email_2 to avoid colliding with patient Email
                em_key = _find_key(["Email_2"])
                if em_key and recip["email"]:
                    mapping[em_key] = recip["email"]

            print(f"MAPPING APPLIED: {mapping}")

            # 5. GENERATION LOGIC: Checkboxes
            
            # --- Accessible Copy ---
            if self.check_accessible.value:
                filled_acc = PdfWrapper(self.template_path, generate_appearance_streams=True).fill(mapping)
                acc_bytes = filled_acc.read()
                acc_file = os.path.join(download_dir, f"ROI_Accessible_{timestamp}.pdf")
                with open(acc_file, "wb") as f:
                    f.write(acc_bytes)
                output_path = acc_file 

            # --- Flattened Copy ---
            if self.check_flattened.value:
                filled_flat = PdfWrapper(self.template_path, generate_appearance_streams=True).fill(mapping, flatten=True)
                flat_bytes = filled_flat.read()
                flat_file = os.path.join(download_dir, f"ROI_Flattened_{timestamp}.pdf")
                with open(flat_file, "wb") as f:
                    f.write(flat_bytes)
                if not output_path:
                    output_path = flat_file

            # 6. SECURE ARCHIVE
            if self.save_to_db_check.value:
                # Generate a filled copy for archive if neither version was created
                archive_bytes = acc_bytes or flat_bytes
                if not archive_bytes:
                    archive_bytes = PdfWrapper(
                        self.template_path, generate_appearance_streams=True
                    ).fill(mapping).read()

                try:
                    dest_dir = os.path.join(os.getcwd(), "data", str(self.patient_id))
                    os.makedirs(dest_dir, exist_ok=True)
                    
                    display_name = f"ROI_Signed_{timestamp}.pdf"
                    enc_path = os.path.join(dest_dir, display_name + ".enc")
                    
                    fmk = get_or_create_file_master_key(self.page.db_connection, dmk_raw=self.page.db_key_raw)
                    ciphertext = encrypt_bytes(fmk, archive_bytes)

                    with open(enc_path, "wb") as f:
                        f.write(ciphertext)

                    add_document(
                        self.page.db_connection,
                        self.patient_id,
                        display_name,
                        enc_path,
                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                    )
                    show_snack(self.page, "ROI securely archived.", "blue")
                except Exception as db_ex:
                    print(f"Archive Error: {db_ex}")
                    show_snack(self.page, "Archive failed, check data folder.", "orange")

            # 7. SUCCESS & CLEANUP
            if output_path:
                show_snack(self.page, f"PDF(s) generated in Downloads", "green")
                if os.name == 'nt':
                    os.startfile(output_path)
            elif self.save_to_db_check.value:
                show_snack(self.page, "ROI archived to Patient Records.", "green")
            else:
                show_snack(self.page, "No output versions selected.", "orange")
            
            self.close()

            if sig_path and os.path.exists(sig_path):
                os.remove(sig_path)

        except Exception as ex:
            print(f"GENERATION ERROR: {ex}")
            show_snack(self.page, f"Error: {ex}", "red")
            self.next_btn.disabled = False
            self.next_btn.text = "Generate & Save"
            self.page.update()