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
#   Windows : %LOCALAPPDATA%\LPA\LocalPatientAdvocate\
#   macOS   : ~/Library/Application Support/LocalPatientAdvocate/
#   Linux   : ~/.local/share/LocalPatientAdvocate/
#
# Directories are created at import time (idempotent via exist_ok=True).
# No application logic lives here — only path constants.
# -----------------------------------------------------------------------------

from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir

# ── Root ─────────────────────────────────────────────────────────────────────

app_dir: Path = Path(user_data_dir("LocalPatientAdvocate", "LPA"))

# ── Database ──────────────────────────────────────────────────────────────────

db_path: Path     = app_dir / "medical_records_v1.db"
keybag_path: Path = app_dir / "medical_records_v1.db.keybag"

# ── AI ────────────────────────────────────────────────────────────────────────

ai_dir: Path     = app_dir / "ai"
model_dir: Path  = ai_dir  / "models"
chroma_dir: Path = ai_dir  / "embeddings"

# ── Exports ───────────────────────────────────────────────────────────────────

export_dir: Path = app_dir / "exports"

# ── Encrypted document storage ────────────────────────────────────────────────

data_dir: Path = app_dir / "data"

# ── Bootstrap ────────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    """Create all required directories on first run (idempotent)."""
    for _d in (app_dir, model_dir, chroma_dir, export_dir, data_dir):
        _d.mkdir(parents=True, exist_ok=True)


_ensure_dirs()
