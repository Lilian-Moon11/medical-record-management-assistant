# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Tiered LLM backend — prefers a locally-running Ollama instance (zero install
# overhead for the user), falls back to a llama-cpp GGUF embedded locally.
#
# Usage:
#   from ai.backend import get_llm
#   llm = get_llm()   # returns an LLM compatible with llama-index
# -----------------------------------------------------------------------------

from __future__ import annotations

import logging

from core import paths

logger = logging.getLogger(__name__)

_OLLAMA_URL = "http://localhost:11434"
_OLLAMA_MODEL = "phi3"
_GGUF_FILENAME = "Phi-3-mini-4k-instruct-q4.gguf"


def _ollama_is_running() -> bool:
    """Return True if Ollama is reachable on localhost."""
    try:
        import requests
        resp = requests.get(f"{_OLLAMA_URL}/api/tags", timeout=1.0)
        return resp.status_code == 200
    except Exception:
        return False


def get_llm():
    """
    Return an llama-index-compatible LLM object.

    Priority:
      1. Ollama running locally (phi3)
      2. llama-cpp-python GGUF (Phi-3-mini-4k-instruct-q4.gguf)

    Raises RuntimeError if neither is available.
    """
    if _ollama_is_running():
        logger.info("AI backend: using Ollama (%s)", _OLLAMA_MODEL)
        from llama_index.llms.ollama import Ollama
        return Ollama(model=_OLLAMA_MODEL, request_timeout=120.0)

    model_path = paths.model_dir / _GGUF_FILENAME
    if model_path.exists():
        logger.info("AI backend: using llama-cpp (%s)", model_path.name)
        from llama_index.llms.llama_cpp import LlamaCPP
        return LlamaCPP(
            model_path=str(model_path),
            temperature=0.1,
            max_new_tokens=512,
            context_window=4096,
            verbose=False,
        )

    raise RuntimeError(
        "No AI backend available. Either start Ollama or download the model "
        "via ai.model_manager.ensure_model()."
    )
