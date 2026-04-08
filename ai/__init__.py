# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# ai/ package — Medical Record Management Assistant AI layer
#
# Provides:
#   ai.backend        — tiered LLM (Ollama-first, llama-cpp fallback)
#   ai.model_manager  — GGUF model check + download
#   ai.ingestion      — background document ingestion pipeline
#   ai.extraction     — structured field extraction + conflict detection
