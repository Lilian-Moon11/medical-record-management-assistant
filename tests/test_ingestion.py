# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import os
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from crypto.file_crypto import encrypt_bytes, get_or_create_file_master_key
from database.schema import _ensure_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PLAINTEXT = b"Patient has a known allergy to penicillin. " * 30  # ~1 KB of repetitive text


def _make_conn():
    """Return an in-memory SQLite connection with the full schema applied."""
    conn = sqlite3.connect(":memory:")
    _ensure_schema(conn)
    return conn


def _seed_patient(conn) -> int:
    cur = conn.cursor()
    cur.execute("INSERT INTO patients (name, dob) VALUES (?, ?)", ("Test Patient", "1980-01-01"))
    conn.commit()
    return cur.lastrowid


def _seed_document(conn, patient_id: int, file_path: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO documents (patient_id, file_name, file_path, upload_date) VALUES (?, ?, ?, ?)",
        (patient_id, "test_record.txt", file_path, "2026-01-01 00:00"),
    )
    conn.commit()
    return cur.lastrowid



# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIngestion(unittest.TestCase):

    def setUp(self):
        # Set up a real (but plaintext) DB in memory
        self.conn = _make_conn()
        self.patient_id = _seed_patient(self.conn)

        # Generate a fake DMK (32 random bytes)
        self.dmk_raw = os.urandom(32)

        # Create FMK and encrypt a fixture file
        self.fmk = get_or_create_file_master_key(self.conn, dmk_raw=self.dmk_raw)
        self.ciphertext = encrypt_bytes(self.fmk, _PLAINTEXT)

        # Write encrypted fixture to a temp file (named .txt so the ingestion
        # plain-text passthrough is used instead of pypdf/OCR paths)
        self.tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        self.tmp.write(self.ciphertext)
        self.tmp.close()

        self.data_dir = os.path.dirname(self.tmp.name)

        # Seed the document row pointing at the fixture file
        self.doc_id = _seed_document(self.conn, self.patient_id, self.tmp.name)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    # -- helpers --

    def _chunk_count(self) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT count(*) FROM document_chunks WHERE patient_id=?", (self.patient_id,))
        return cur.fetchone()[0]

    def _run_ingestion(self, stop_event=None):
        from ai.ingestion import run_ingestion
        run_ingestion(
            self.conn,
            self.dmk_raw,
            self.patient_id,
            self.data_dir,
            stop_event=stop_event,
        )

    # -- tests --

    def test_chunks_inserted(self):
        """Pipeline should produce at least one chunk for a non-empty document."""
        self._run_ingestion()
        self.assertGreater(self._chunk_count(), 0, "No chunks were inserted")

    def test_chunk_fields(self):
        """Every chunk must have the correct doc_id, patient_id, and non-empty text."""
        self._run_ingestion()
        cur = self.conn.cursor()
        cur.execute(
            "SELECT doc_id, patient_id, chunk_text, chunk_index, source_file_name "
            "FROM document_chunks WHERE patient_id=?",
            (self.patient_id,),
        )
        rows = cur.fetchall()
        self.assertTrue(rows, "No chunk rows found")
        for doc_id, patient_id, chunk_text, chunk_index, source_file_name in rows:
            self.assertEqual(doc_id, self.doc_id)
            self.assertEqual(patient_id, self.patient_id)
            self.assertTrue(chunk_text.strip(), "Empty chunk_text")
            self.assertIsNotNone(chunk_index)
            self.assertEqual(source_file_name, "test_record.txt")

    def test_idempotent_no_reindex(self):
        """Running ingestion twice should not double-insert chunks."""
        self._run_ingestion()
        count_after_first = self._chunk_count()
        self._run_ingestion()
        count_after_second = self._chunk_count()
        self.assertEqual(
            count_after_first, count_after_second,
            "Ingestion re-indexed an already-indexed document"
        )

    def test_stop_event_halts_processing(self):
        """Setting stop_event before run should result in zero chunks inserted."""
        stop = threading.Event()
        stop.set()  # signal stop immediately
        self._run_ingestion(stop_event=stop)
        # With stop pre-set, the loop should bail before inserting anything
        self.assertEqual(self._chunk_count(), 0, "Chunks were inserted despite stop_event being set")

    def test_progress_callback_called(self):
        """Progress callback should be called at least once with (completed, total)."""
        calls = []
        from ai.ingestion import run_ingestion
        run_ingestion(
            self.conn,
            self.dmk_raw,
            self.patient_id,
            self.data_dir,
            progress_cb=lambda done, total: calls.append((done, total)),
        )
        self.assertTrue(calls, "progress_cb was never called")
        last_done, last_total = calls[-1]
        self.assertEqual(last_total, 1)   # 1 document seeded
        self.assertEqual(last_done, 1)


if __name__ == "__main__":
    unittest.main()
