# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Robust, locally-executed AI extraction pipeline that reads document text (OCR) 
# and suggests structured database additions into the AI Inbox. 
# Handles JSON formatting fallbacks (like regex and ast.literal_eval) to gracefully 
# process conversational outputs from small LLMs (like Phi-3).
#
# Returns a list of extraction suggestions. NEVER persists anything —
# the caller (UI layer) handles the approve / edit / persist flow.
#
# Provenance conflict detection:
#   If an existing user-entered field value exists and the AI extracts the
#   same field from a document that contains clinical/surgical language,
#   the suggestion is flagged as a potential conflict for the caller to surface
#   as a dialog. Self-reported data is NEVER silently overwritten.
#
# Chunking Strategy:
#   Long documents are split into ~4000-character chunks (to stay within the
#   model's context window after the prompt template is prepended). Each chunk
#   is independently extracted, then results are merged with deduplication.
#
# Public API:
#   extract_fields(conn, patient_id, text, source_file_name, llm=None)
#       -> list[Suggestion]
#
# Suggestion is a TypedDict:
#   {
#     "field_key":         str,
#     "value":             str,
#     "confidence":        float,   # 0.0–1.0
#     "source_file_name":  str,
#     "conflict":          bool,
#     "existing_value":    str | None,
#   }
# -----------------------------------------------------------------------------

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from ai.extraction_filters import explode_and_deduplicate, post_process

logger = logging.getLogger(__name__)

# Keywords that indicate a clinical/procedural source, raising confidence for
# date fields when a user-entered value already exists.
_CLINICAL_KEYWORDS = frozenset([
    "operative", "surgery", "procedure", "incision", "anesthesia",
    "post-op", "preoperative", "surgical", "operation report",
])

from ai.prompts import _EXTRACTION_PROMPT_TEMPLATE

# Maximum characters of document text per LLM call.
_CHUNK_SIZE = 4000

# Overlap between consecutive chunks so entities spanning chunk boundaries
# are captured. 400 chars ≈ 2-3 sentences.
_CHUNK_OVERLAP = 400


def _is_clinical_source(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in _CLINICAL_KEYWORDS)


def _split_into_chunks(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks, preferring to break on paragraph boundaries."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            # Try to break on a paragraph boundary (double newline)
            search_start = max(end - 200, start)
            break_pos = text.rfind("\n\n", search_start, end)
            if break_pos > start:
                end = break_pos
            else:
                # Fall back to single newline
                break_pos = text.rfind("\n", search_start, end)
                if break_pos > start:
                    end = break_pos

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # Advance with overlap
        start = end - overlap if end < len(text) else len(text)

    return chunks


def _extract_single_chunk(text_chunk: str, llm) -> list[dict]:
    """Run extraction on a single text chunk. Returns raw candidate dicts."""
    prompt = _EXTRACTION_PROMPT_TEMPLATE.format(text=text_chunk)

    try:
        raw = llm.complete(prompt).text
        raw = str(raw).strip()
        print(f"[AI-DIAG] LLM raw output ({len(text_chunk)} chars input): {raw[:500]}...")

        # Strip markdown code fences that the LLM likes to add (```json ... ```)
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip()

        # Extract everything from the true start of the object array.
        # Also accept arrays of stringified objects like '["{...}", "{...}"]'
        match = re.search(r'\[\s*[{\"]', raw)
        if not match:
            print(f"[AI-DIAG] No JSON array found in LLM output")
            return []
            
        raw = raw[match.start():]

        candidates = None

        def try_parse(json_str):
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                import ast
                safe_raw = json_str.replace("null", "None").replace("true", "True").replace("false", "False")
                try:
                    return ast.literal_eval(safe_raw)
                except Exception:
                    return None

        # 1. Try to parse directly
        bracket_end = raw.rfind("]")
        if bracket_end > 0:
            candidates = try_parse(raw[:bracket_end + 1])

        # 2. If it failed, try progressively shorter substrings
        if candidates is None:
            brace_positions = [m.start() for m in re.finditer(r'\}', raw)]
            for pos in reversed(brace_positions):
                if pos <= 0:
                    continue
                salvaged = raw[:pos + 1] + "]"
                candidates = try_parse(salvaged)
                if candidates is not None:
                    print(f"[AI-DIAG] Recovered truncated JSON ({pos + 1} chars salvaged)")
                    break

        if candidates is None:
            print(f"[AI-DIAG] JSON parse failed for chunk")
            return []

        if not isinstance(candidates, list):
            return []

        # Normalize candidates
        valid_candidates = []
        for item in candidates:
            if isinstance(item, str):
                try:
                    parsed_item = json.loads(item)
                    if isinstance(parsed_item, dict):
                        item = parsed_item
                    else:
                        continue
                except (json.JSONDecodeError, TypeError):
                    continue
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("value"), (dict, list)):
                item["value"] = json.dumps(item["value"])
            valid_candidates.append(item)

        return valid_candidates

    except Exception as exc:
        print(f"[AI-DIAG] Chunk extraction error: {exc}")
        logger.warning("Chunk extraction failed: %s", exc)
        return []


def _build_chunk_item_counts(all_candidates: list[dict]) -> dict[str, int]:
    """Count how many raw candidates mention each allergy substance, medication name, or vital name.

    Since each chunk produces its own set of candidates, the count
    approximates how many *chunks* produced a given item. This is
    used by the consensus filter to suppress single-source hallucinations.
    """
    counts: dict[str, int] = {}
    for item in all_candidates:
        if not isinstance(item, dict):
            continue
        fk = str(item.get("field_key", "")).strip()
        val = str(item.get("value", "")).strip()
        try:
            parsed = json.loads(val) if val.startswith("{") else None
        except Exception:
            parsed = None
            
        key_to_count = None
        if fk == "allergyintolerance.list" and isinstance(parsed, dict):
            key_to_count = str(parsed.get("substance", "")).strip().lower()
        elif fk == "medicationstatement.current_list":
            if isinstance(parsed, dict):
                key_to_count = str(parsed.get("name", "")).strip().lower()
            else:
                key_to_count = val.strip().lower()
        elif fk == "vitals.list" and isinstance(parsed, dict):
            key_to_count = str(parsed.get("name", "")).strip().lower()

        if key_to_count:
            counts[key_to_count] = counts.get(key_to_count, 0) + 1
    return counts


def extract_fields(
    conn,
    patient_id: int,
    text: str,
    source_file_name: str,
    llm=None,
    doc_id: int | None = None,
) -> list[dict]:
    """
    Run structured extraction on a text excerpt.

    Returns a list of suggestion dicts (see module docstring).
    Does not write to the database (except chunk cache for resumability).
    """
    if llm is None:
        from ai.backend import get_llm
        llm = get_llm()

    # Split into chunks that fit the model's context window
    chunks = _split_into_chunks(text)
    print(f"[AI-DIAG] Document split into {len(chunks)} chunks ({len(text)} total chars)")

    # Load any previously cached chunk results (for resumability)
    cached_chunks: dict[int, list[dict]] = {}
    if doc_id is not None:
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT chunk_index, candidates FROM ai_chunk_cache WHERE doc_id=?",
                (doc_id,),
            )
            for row in cur.fetchall():
                try:
                    cached_chunks[row[0]] = json.loads(row[1])
                except Exception:
                    pass
            if cached_chunks:
                print(f"[AI-DIAG] Resuming: {len(cached_chunks)} chunks already cached")
        except Exception:
            pass  # table might not exist yet on first run

    # Extract from each chunk (skip cached ones)
    all_candidates = []
    for i, chunk in enumerate(chunks):
        if i in cached_chunks:
            chunk_candidates = cached_chunks[i]
            print(f"[AI-DIAG] Chunk {i+1}/{len(chunks)} loaded from cache ({len(chunk_candidates)} candidates)")
        else:
            print(f"[AI-DIAG] Processing chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
            chunk_candidates = _extract_single_chunk(chunk, llm)
            print(f"[AI-DIAG] Chunk {i+1} produced {len(chunk_candidates)} raw candidates")

            # Cache the result immediately for resumability
            if doc_id is not None and chunk_candidates:
                try:
                    from datetime import datetime
                    conn.execute(
                        "INSERT OR REPLACE INTO ai_chunk_cache (doc_id, chunk_index, candidates, created_at) VALUES (?, ?, ?, ?)",
                        (doc_id, i, json.dumps(chunk_candidates), datetime.now().isoformat()),
                    )
                    conn.commit()
                except Exception as cache_ex:
                    logger.debug("Chunk cache write failed: %s", cache_ex)

        all_candidates.extend(chunk_candidates)

    print(f"[AI-DIAG] Total raw candidates from all chunks: {len(all_candidates)}")

    # Clean up chunk cache now that all chunks are done
    if doc_id is not None:
        try:
            conn.execute("DELETE FROM ai_chunk_cache WHERE doc_id=?", (doc_id,))
            conn.commit()
        except Exception:
            pass

    # Build chunk-level item frequency map BEFORE dedup
    chunk_item_counts = _build_chunk_item_counts(all_candidates)

    # Explode list items and deduplicate across all chunks
    candidates = explode_and_deduplicate(all_candidates)
    print(f"[AI-DIAG] After deduplication: {len(candidates)} unique candidates")

    # Post-processing: clean up misclassifications and noise
    candidates = post_process(candidates, chunk_item_counts, len(chunks))
    print(f"[AI-DIAG] After post-processing filters: {len(candidates)} candidates")

    # Fetch existing user-entered values for conflict detection
    cur = conn.cursor()
    cur.execute(
        "SELECT field_key, value_text, source FROM patient_field_values WHERE patient_id=?",
        (patient_id,),
    )
    existing = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    clinical = _is_clinical_source(text[:20000])
    suggestions = []

    for item in candidates:
        if not isinstance(item, dict):
            continue
        field_key = item.get("field_key", "").strip()
        value = str(item.get("value", "")).strip()
        confidence = float(item.get("confidence", 0.5))
        if not field_key or not value:
            continue

        # Skip generic scalars that are blank form lines or placeholders
        _lower_val = value.lower()
        if set(value) <= {"_", "-"} or _lower_val in ("none", "null", "n/a", "unknown"):
            continue
            
        if "___" in value or "---" in value:
            continue

        conflict = False
        existing_value = None
        should_drop_suggestion = False

        if field_key in existing:
            ex_val, ex_source = existing[field_key]
            
            # Silently drop suggestions that exactly match existing verified data
            if ex_val and str(ex_val).strip().lower() == value.lower():
                continue

            # Default flag for generic fields
            if ex_source == "user" and ex_val and ex_val != value:
                conflict = True
                existing_value = ex_val
                
                # Boost confidence if the document is a clinical record
                if clinical:
                    confidence = min(confidence + 0.2, 1.0)

            # SMART List Handling: Don't conflict if we are just appending a NEW item
            if (".list" in field_key or "_list" in field_key) and ex_val:
                try:
                    ex_list = json.loads(ex_val)
                    new_obj = json.loads(value)
                    
                    if isinstance(ex_list, list) and isinstance(new_obj, dict):
                        # Find the defining key depending on the list type
                        pk = "name"
                        if "allergy" in field_key: pk = "substance"
                        if "insurance" in field_key: pk = "payer"
                        if "provider" in field_key: pk = "clinic"
                        
                        new_item_name = str(new_obj.get(pk, "")).strip().lower()
                        # Fallback for providers if clinic is empty
                        if not new_item_name and "provider" in field_key:
                            new_item_name = str(new_obj.get("name", "")).strip().lower()
                        
                        if new_item_name:
                            # Search the existing list for this precise item
                            matched = None
                            for existing_item in ex_list:
                                if isinstance(existing_item, dict):
                                    existing_pk_val = str(existing_item.get(pk, "")).strip().lower()
                                    if not existing_pk_val and "provider" in field_key:
                                        existing_pk_val = str(existing_item.get("name", "")).strip().lower()
                                    
                                    if existing_pk_val == new_item_name:
                                        matched = existing_item
                                        break
                            
                            if matched:
                                # Is there new information?
                                has_new_info = False
                                for k, v in new_obj.items():
                                    if isinstance(v, bool):
                                        match_v = matched.get(k)
                                        if match_v is None or str(match_v).lower() != str(v).lower():
                                            has_new_info = True
                                            break
                                        continue
                                        
                                    v_str = str(v).strip().lower()
                                    if v_str and v_str not in ("none", "null", "n/a", "unknown", ""):
                                        match_v = matched.get(k)
                                        if not match_v or str(match_v).strip().lower() != v_str:
                                            has_new_info = True
                                            break

                                if not has_new_info:
                                    should_drop_suggestion = True
                                else:
                                    if "allergy" in field_key:
                                        conflict = False
                                        existing_value = json.dumps(matched)
                                    else:
                                        conflict = True
                                        existing_value = json.dumps(matched)
                            else:
                                conflict = False
                                existing_value = None
                except Exception:
                    pass

        if should_drop_suggestion:
            continue

        suggestions.append({
            "field_key": field_key,
            "value": value,
            "confidence": confidence,
            "source_file_name": source_file_name,
            "conflict": conflict,
            "existing_value": existing_value,
        })

    return suggestions
