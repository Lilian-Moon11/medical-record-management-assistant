import unittest
import os
import sys
import tempfile
import sqlite3
from pathlib import Path

# Fix paths to allow importing project modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.clinical import (
    delete_document, delete_lab_report, add_document, create_lab_report, add_lab_result
)
from database.schema import _ensure_schema
from core import app_state
from ui import login
import database.core as db_core
from utils import pdf_gen


class TestPhase1And2Fixes(unittest.TestCase):

    def setUp(self):
        # Create an unencrypted SQLite DB for testing
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        _ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_delete_document_unlinks_enc_file(self):
        """Verify delete_document deletes the associated .enc file."""
        # Create dummy file
        dummy_enc = tempfile.NamedTemporaryFile(suffix=".enc", delete=False)
        dummy_enc.write(b"data")
        dummy_enc.close()
        
        # Verify it exists
        self.assertTrue(os.path.exists(dummy_enc.name))
        
        # Insert document pointing to it
        doc_id = add_document(self.conn, 1, "test_file.pdf", dummy_enc.name, "2026-01-01")
        
        # Call delete_document
        delete_document(self.conn, doc_id)
        
        # Verify the file is deleted
        self.assertFalse(os.path.exists(dummy_enc.name))

    def test_delete_lab_report_returns_combined_rowcount(self):
        """Verify delete_lab_report returns the sum of deleted reports and results."""
        patient_id = 99
        report_id = create_lab_report(self.conn, patient_id=patient_id, source_document_id=None,
                                      collected_date="2026-01-01 10:00", reported_date="2026-01-02",
                                      ordering_provider="Dr. Smith", facility="Lab", notes="")
        
        # Add 3 results
        add_lab_result(self.conn, patient_id, report_id, test_name="T1", value_text="V1")
        add_lab_result(self.conn, patient_id, report_id, test_name="T2", value_text="V2")
        add_lab_result(self.conn, patient_id, report_id, test_name="T3", value_text="V3")
        
        # Delete report
        deleted_count = delete_lab_report(self.conn, patient_id, report_id)
        
        # Should be 1 report + 3 results = 4
        self.assertEqual(deleted_count, 4)

    def test_wipe_local_data_exists(self):
        """Verify wipe_local_data is defined in core.app_state."""
        self.assertTrue(hasattr(app_state, "wipe_local_data"))

    def test_keybag_encoding(self):
        """Verify keybag.py can be deeply read as UTF-8 without raising UnicodeDecodeError."""
        keybag_path = os.path.join(os.path.dirname(__file__), "..", "crypto", "keybag.py")
        with open(keybag_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn(chr(0x92), content)

    def test_stale_files_removed(self):
        """Verify the stale dev files are removed from the root folder."""
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.assertFalse(os.path.exists(os.path.join(root_dir, "flet_tabs_help.txt")))
        self.assertFalse(os.path.exists(os.path.join(root_dir, "ft_tabs_utf8.txt")))
        self.assertFalse(os.path.exists(os.path.join(root_dir, "Medical_Summary_1.pdf")))

    def test_tempfile_mktemp_removed(self):
        """Verify tempfile.mktemp is not used in overview.py."""
        overview_path = os.path.join(os.path.dirname(__file__), "..", "views", "overview.py")
        with open(overview_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("tempfile.mktemp()", content)

    def test_resolve_boolean_field_exists(self):
        """Verify _resolve_boolean_field is defined in ai.paperwork."""
        # import programmatically because it's in a sub-module
        import ai.paperwork
        self.assertTrue(hasattr(ai.paperwork, "_resolve_boolean_field"))
        
    def test_logging_configured(self):
        """Verify logging is configured in main.py."""
        main_path = os.path.join(os.path.dirname(__file__), "..", "main.py")
        with open(main_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("logging.basicConfig(", content)
        
    def test_pdf_gen_uses_tempdir(self):
        """Verify pdf_gen doesn't write to CWD anymore."""
        pdf_gen_path = os.path.join(os.path.dirname(__file__), "..", "utils", "pdf_gen.py")
        with open(pdf_gen_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn('filename = f"Medical_Summary_{patient_id}.pdf"', content)
        # Should use either paths.export_dir or tempfile
        self.assertTrue('tempfile' in content or 'paths.export_dir' in content)
        
    def test_thread_safe_connection_exists(self):
        """Verify ThreadSafeConnection exists in database/core.py."""
        self.assertTrue(hasattr(db_core, "ThreadSafeConnection"))

    def test_recovery_key_cleared_in_login(self):
        """Verify login.py clears recovery_key from page session state."""
        login_path = os.path.join(os.path.dirname(__file__), "..", "ui", "login.py")
        with open(login_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("page.recovery_key_first_run = None", content)

if __name__ == "__main__":
    unittest.main()
