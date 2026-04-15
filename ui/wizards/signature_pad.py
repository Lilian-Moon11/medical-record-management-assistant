# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Reusable signature-capture widget backed by a Flet canvas + gesture detector.
#
# Provides:
# - SignaturePad: a GestureDetector that records pen strokes as (x, y) tuples
#   separated by None (pen-lift markers) and renders live ink on screen.
# - render_signature_png(): a pure helper that converts recorded strokes into a
#   PIL Image suitable for embedding in a PDF.
# - get_signature_path(): convenience method that writes the PNG to a temp file
#   and returns the path (PyPDFForm requires a file path for images).
#
# Design notes:
# - Uses absolute local_position (Flet 0.80+) to avoid calibration drift.
# - Explicitly closes the tempfile fd before Pillow writes (Windows fix).
# - Ink and background colors adapt automatically to light/dark theme.
# -----------------------------------------------------------------------------

import logging
import os
import tempfile

import flet as ft
import flet.canvas as cv
from PIL import Image, ImageDraw

from utils.ui_helpers import pt_scale

logger = logging.getLogger(__name__)


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

        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            # Explicitly close file descriptor so Windows doesn't block Pillow's save()
            os.close(fd)

            img = render_signature_png(self.points, width=400, height=150)
            img.save(path, format="PNG")
            return path
        except Exception as e:
            logger.debug("Signature creation error: %s", e)
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
