# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import os
import sys
import tempfile
import unittest
import sqlite3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.schema import _ensure_schema
from utils.airlock import export_profile, import_profile
from crypto.file_crypto import encrypt_bytes, get_or_create_file_master_key

class TestAirlock(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp_dir.name, "data")
        os.makedirs(self.data_dir)
        
        self.db1_path = os.path.join(self.tmp_dir.name, "db1.db")
        self.conn1 = sqlite3.connect(self.db1_path)
        _ensure_schema(self.conn1)
        
        self.db2_path = os.path.join(self.tmp_dir.name, "db2.db")
        self.conn2 = sqlite3.connect(self.db2_path)
        _ensure_schema(self.conn2)
        
        self.dmk1 = os.urandom(32)
        self.dmk2 = os.urandom(32)

    def tearDown(self):
        self.conn1.close()
        self.conn2.close()
        self.tmp_dir.cleanup()

    def test_roundtrip(self):
        cur1 = self.conn1.cursor()
        
        # 1. Populate DB1 with test data
        cur1.execute("INSERT INTO patients (name, dob) VALUES (?, ?)", ("Test Patient", "1990-01-01"))
        patient_id = cur1.lastrowid
        
        cur1.execute(
            "INSERT INTO providers (patient_id, name, specialty) VALUES (?, ?, ?)",
            (patient_id, "Dr. Smith", "Cardio")
        )
        
        # Add a dummy file and encrypt it for DB1
        fmk1 = get_or_create_file_master_key(self.conn1, dmk_raw=self.dmk1)
        dummy_content = b"health secrets here"
        enc_bytes = encrypt_bytes(fmk1, dummy_content)
        
        patient_dir = os.path.join(self.data_dir, str(patient_id))
        os.makedirs(patient_dir, exist_ok=True)
        doc_path = os.path.join(patient_dir, "report.pdf.enc")
        with open(doc_path, "wb") as f:
            f.write(enc_bytes)
            
        cur1.execute(
            "INSERT INTO documents (patient_id, file_name, file_path) VALUES (?, ?, ?)",
            (patient_id, "report.pdf", doc_path)  # using absolute path here works due to resolve_doc_path handling
        )
        self.conn1.commit()
        
        # 2. Export Profile
        zip_path = os.path.join(self.tmp_dir.name, "export.zip")
        export_profile(self.conn1, self.dmk1, self.data_dir, zip_path, "mypassword")
        
        self.assertTrue(os.path.exists(zip_path))
        
        # 3. Import Profile into DB2
        # Use a new data dir for db2 to ensure imports create new files
        data_dir2 = os.path.join(self.tmp_dir.name, "data2")
        os.makedirs(data_dir2)
        
        counts = import_profile(self.conn2, self.dmk2, data_dir2, zip_path, "mypassword")
        
        # Verify component counts
        self.assertEqual(counts["patients"], 1)
        self.assertEqual(counts["providers"], 1)
        self.assertEqual(counts["documents"], 1)
        self.assertEqual(counts["files"], 1)
        
        # Verify db2 patient contents
        cur2 = self.conn2.cursor()
        cur2.execute("SELECT name, dob FROM patients")
        p = cur2.fetchone()
        self.assertEqual(p[0], "Test Patient")
        
        # Verify db2 document contents and that the file was re-encrypted properly
        cur2.execute("SELECT file_path FROM documents")
        new_doc_path = cur2.fetchone()[0]
        
        from core.paths import resolve_doc_path
        # When import_profile writes it, it uses 'data/{patient_id}/report.pdf.enc'
        # Since we passed `data_dir2` to import_profile, the actual file goes into data_dir2
        # But the DB relative path needs to be resolved. In our mock `data_dir2` is used for the base directory during file write.
        # Let's read the file directly off the disk based on `data_dir2`:
        
        # In airlock.py:
        # patient_dir = os.path.join(data_dir, str(new_pid))
        # dest_enc = os.path.join(patient_dir, enc_name)
        
        cur2.execute("SELECT id FROM patients")
        new_pid = cur2.fetchone()[0]
        written_enc_path = os.path.join(data_dir2, str(new_pid), "report.pdf.enc")
        
        self.assertTrue(os.path.exists(written_enc_path))
        
        with open(written_enc_path, "rb") as f:
            written_enc_bytes = f.read()
            
        fmk2 = get_or_create_file_master_key(self.conn2, dmk_raw=self.dmk2)
        from crypto.file_crypto import decrypt_bytes
        decrypted_content = decrypt_bytes(fmk2, written_enc_bytes)
        self.assertEqual(decrypted_content, dummy_content)

if __name__ == "__main__":
    unittest.main()
