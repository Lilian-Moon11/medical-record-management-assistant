# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Model manager — checks for the local GGUF model and downloads it if absent.
#
# Public API:
#   check_model()          -> (exists: bool, path: Path)
#   ensure_model(cb=None)  -> Path   (downloads if needed; cb(done, total))
#
# Designed for accessibility: all messages are plain English, no jargon.
# -----------------------------------------------------------------------------

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable, Optional

from core import paths

logger = logging.getLogger(__name__)

_GGUF_FILENAME = "Qwen2.5-3B-Instruct-Q4_K_M.gguf"
_HF_URL = (
    "https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF"
    "/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
)
_MIN_FREE_BYTES = 3 * 1024 ** 3  # 3 GB


def check_model() -> tuple[bool, Path]:
    """Return (exists, path). Does not download anything."""
    path = paths.model_dir / _GGUF_FILENAME
    return path.exists(), path


def _check_disk_space() -> None:
    """Warn (log) if less than 3 GB free. Does not raise."""
    usage = shutil.disk_usage(paths.model_dir)
    if usage.free < _MIN_FREE_BYTES:
        free_gb = usage.free / 1024 ** 3
        logger.warning(
            "Low disk space: only %.1f GB free. The AI model requires about 2.5 GB. "
            "You may want to free up space before continuing.", free_gb
        )


def ensure_model(progress_cb: Optional[Callable[[int, int], None]] = None) -> Path:
    """
    Ensure the GGUF model is present, downloading it if needed.

    progress_cb(downloaded_bytes, total_bytes) is called during download
    so callers can update a progress bar.

    Returns the local path to the model file.
    """
    exists, path = check_model()
    if exists:
        logger.info("Model already present: %s", path)
        return path

    _check_disk_space()

    logger.info(
        "Downloading AI model. This is a one-time download of about 2.5 GB. "
        "The app will work without AI features until the download finishes."
    )

    import requests

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".gguf.tmp")

    try:
        with requests.get(_HF_URL, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb(downloaded, total)

        tmp_path.rename(path)
        logger.info("Model download complete: %s", path)
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Download failed: {exc}. "
            "Please check your internet connection and try again."
        ) from exc

    return path
