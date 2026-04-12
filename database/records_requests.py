# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Pure data-access layer for the records_requests table.
#
# All functions are synchronous and accept a raw DB connection (SQLCipher or
# standard sqlite3 — the surface is identical).  No UI imports anywhere here.
# -----------------------------------------------------------------------------

from datetime import datetime


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ── Write operations ─────────────────────────────────────────────────────────

def create_request(
    conn,
    patient_id: int,
    provider_name: str,
    department: str | None,
    date_requested: str,
    due_date: str | None,
    due_date_source: str = "default",
    notes: str | None = None,
    source_doc_id: int | None = None,
) -> int:
    """Insert a new records request row. Returns the new row id."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO records_requests
            (patient_id, provider_name, department, date_requested,
             due_date, due_date_source, status, notes, created_at, source_doc_id)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            patient_id,
            provider_name,
            department or None,
            date_requested,
            due_date,
            due_date_source,
            notes or None,
            _now(),
            source_doc_id,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_request_status(
    conn,
    request_id: int,
    status: str,
    candidate_doc_id: int | None = None,
) -> None:
    """Set status, optionally recording the candidate document."""
    conn.execute(
        """
        UPDATE records_requests
           SET status = ?, candidate_doc_id = ?
         WHERE id = ?
        """,
        (status, candidate_doc_id, request_id),
    )
    conn.commit()


def update_due_date(
    conn,
    request_id: int,
    due_date: str,
    source: str = "manual",
) -> None:
    """Update the due date and record its provenance."""
    conn.execute(
        "UPDATE records_requests SET due_date = ?, due_date_source = ? WHERE id = ?",
        (due_date, source, request_id),
    )
    conn.commit()


def mark_complete(conn, request_id: int) -> None:
    conn.execute(
        "UPDATE records_requests SET status = 'complete' WHERE id = ?",
        (request_id,),
    )
    conn.commit()


def delete_request(conn, request_id: int) -> None:
    conn.execute("DELETE FROM records_requests WHERE id = ?", (request_id,))
    conn.commit()


def update_notes(conn, request_id: int, notes: str) -> None:
    conn.execute(
        "UPDATE records_requests SET notes = ? WHERE id = ?",
        (notes, request_id),
    )
    conn.commit()


# ── Read operations ───────────────────────────────────────────────────────────

def list_requests(conn, patient_id: int) -> list:
    """Return all requests for *patient_id*, newest first.

    Row shape:
        (id, provider_name, department, date_requested,
         due_date, due_date_source, status, candidate_doc_id, notes, created_at,
         source_doc_id)
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, provider_name, department, date_requested,
               due_date, due_date_source, status, candidate_doc_id, notes, created_at,
               source_doc_id
          FROM records_requests
         WHERE patient_id = ?
         ORDER BY created_at DESC
        """,
        (patient_id,),
    )
    return cur.fetchall()


def get_request(conn, request_id: int) -> tuple | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, provider_name, department, date_requested,
               due_date, due_date_source, status, candidate_doc_id, notes, created_at,
               source_doc_id
          FROM records_requests
         WHERE id = ?
        """,
        (request_id,),
    )
    return cur.fetchone()


def list_pending_requests(conn, patient_id: int) -> list:
    """Return only 'pending' requests (for upload-matching)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, provider_name, department
          FROM records_requests
         WHERE patient_id = ? AND status = 'pending'
        """,
        (patient_id,),
    )
    return cur.fetchall()


# ── Candidate matching ────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Lower-case and replace filename separators with spaces for loose matching."""
    return text.lower().replace("_", " ").replace("-", " ")


def check_upload_for_matches(
    conn,
    patient_id: int,
    doc_id: int,
    file_name: str,
    parsed_text: str | None = None,
) -> list[int]:
    """Fuzzy-match a newly uploaded document against pending requests.

    Matching rules (case-insensitive, separator-normalised):
    - provider_name must appear in file_name OR parsed_text.
    - If department is set, it must ALSO appear in file_name OR parsed_text.

    Returns a list of request IDs that matched (may be empty).
    """
    pending = list_pending_requests(conn, patient_id)
    if not pending:
        return []

    # Normalise the haystack once: replace _ and - with spaces so filenames
    # like "Stanford_Oncology_records.pdf" match provider "Stanford Medicine".
    haystack = _normalise(" ".join(filter(None, [file_name, parsed_text or ""])))
    matched_ids: list[int] = []

    for req_id, provider_name, department in pending:
        if not provider_name:
            continue
        if _normalise(provider_name) not in haystack:
            continue
        # If a department is specified, enforce the extra constraint
        if department and _normalise(department) not in haystack:
            continue
        matched_ids.append(req_id)
        update_request_status(conn, req_id, "candidate", candidate_doc_id=doc_id)

    return matched_ids

