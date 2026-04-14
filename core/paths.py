# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Single source of truth for all application filesystem paths.
#
# Replaces any hardcoded or CWD-relative path assumptions throughout the app.
# All paths are derived from platformdirs.user_data_dir, which resolves to:
#   Windows : %LOCALAPPDATA%\MRMA\MedicalRecordManagementAssistant\
#   macOS   : ~/Library/Application Support/MedicalRecordManagementAssistant/
#   Linux   : ~/.local/share/MedicalRecordManagementAssistant/
#
# Directories are created at import time (idempotent via exist_ok=True).
# No application logic lives here — only path constants.
# -----------------------------------------------------------------------------

from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir

# ── Root ─────────────────────────────────────────────────────────────────────

app_dir: Path = Path(user_data_dir("MedicalRecordManagementAssistant", "MRMA"))

# ── Database ──────────────────────────────────────────────────────────────────

db_path: Path     = app_dir / "medical_records_v1.db"
keybag_path: Path = app_dir / "medical_records_v1.db.keybag"

# ── AI ────────────────────────────────────────────────────────────────────────

ai_dir: Path     = app_dir / "ai"
model_dir: Path  = ai_dir  / "models"

# ── Exports ───────────────────────────────────────────────────────────────────

export_dir: Path = app_dir / "exports"

# ── Encrypted document storage ────────────────────────────────────────────────

data_dir: Path = app_dir / "data"

# ── Bootstrap ────────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    """Create all required directories on first run (idempotent)."""
    for _d in (app_dir, model_dir, export_dir, data_dir):
        _d.mkdir(parents=True, exist_ok=True)


_ensure_dirs()


# ── Document path helpers ────────────────────────────────────────────────────
# The database stores file_path as a path relative to app_dir
# (e.g. "data/1/file.pdf.enc").  These helpers convert between
# the stored relative path and a usable absolute path.

def resolve_doc_path(stored_path: str) -> Path:
    """Convert a stored file_path (relative or legacy absolute) to an absolute path.

    Handles:
      - Relative paths (preferred):  "data/1/file.enc" -> app_dir / "data/1/file.enc"
      - Legacy absolute paths:       "C:\\old\\data\\1\\file.enc" -> returned as-is
    """
    p = Path(stored_path)
    if p.is_absolute():
        return p  # legacy absolute path — returned unchanged
    return app_dir / p


def to_relative_doc_path(abs_path: str) -> str:
    """Convert an absolute path to a relative path for DB storage.

    If the path is under app_dir, returns the relative portion.
    Otherwise returns the path unchanged (safety fallback).
    """
    try:
        return str(Path(abs_path).relative_to(app_dir))
    except ValueError:
        # Not under app_dir — return as-is (shouldn't happen in normal use)
        return abs_path
