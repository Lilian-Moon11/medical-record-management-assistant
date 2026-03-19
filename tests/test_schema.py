# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import os
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.schema import _ensure_schema


class TestSchema(unittest.TestCase):
    """Verify schema migrations against a plain (unencrypted) SQLite DB."""

    def setUp(self):
        # Use a plain SQLite connection so the test has no SQLCipher dependency
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        _ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def _table_exists(self, name: str) -> bool:
        cur = self.conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return cur.fetchone()[0] == 1

    def _column_exists(self, table: str, column: str) -> bool:
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cur.fetchall())

    # ── Core tables ──────────────────────────────────────────────────────────

    def test_patients_table(self):
        self.assertTrue(self._table_exists("patients"))

    def test_documents_table(self):
        self.assertTrue(self._table_exists("documents"))

    def test_patient_field_values_table(self):
        self.assertTrue(self._table_exists("patient_field_values"))

    # ── Phase 5.0 additions ──────────────────────────────────────────────────

    def test_document_chunks_table_exists(self):
        self.assertTrue(self._table_exists("document_chunks"),
                        "document_chunks table is missing")

    def test_document_chunks_columns(self):
        for col in ("id", "doc_id", "patient_id", "page_number",
                    "source_file_name", "chunk_text", "chunk_index", "created_at"):
            self.assertTrue(
                self._column_exists("document_chunks", col),
                f"document_chunks is missing column: {col}"
            )

    def test_patient_field_values_source_doc_id(self):
        self.assertTrue(
            self._column_exists("patient_field_values", "source_doc_id"),
            "patient_field_values is missing source_doc_id column"
        )

    def test_patient_field_values_ai_confidence(self):
        self.assertTrue(
            self._column_exists("patient_field_values", "ai_confidence"),
            "patient_field_values is missing ai_confidence column"
        )

    def test_schema_idempotent(self):
        """Running _ensure_schema twice must not raise."""
        _ensure_schema(self.conn)


if __name__ == "__main__":
    unittest.main()
