# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import unittest
import sqlite3
import base64
import os
from cryptography.fernet import Fernet
from crypto.file_crypto import (
    get_or_create_file_master_key,
    encrypt_bytes,
    decrypt_bytes,
    _dmk_to_fernet_key,
    InvalidToken,
)

class TestFileCrypto(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.cur = self.conn.cursor()
        self.cur.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
        self.dmk_raw = os.urandom(32)

    def tearDown(self):
        self.conn.close()

    def test_dmk_format_validation(self):
        with self.assertRaises(ValueError):
            _dmk_to_fernet_key(b"too_short")
        
        with self.assertRaises(ValueError):
            _dmk_to_fernet_key(os.urandom(33))
            
        with self.assertRaises(ValueError):
            _dmk_to_fernet_key(b"")

    def test_fmk_creation_and_retrieval(self):
        # First call creates the FMK
        fmk1 = get_or_create_file_master_key(self.conn, dmk_raw=self.dmk_raw)
        self.assertIsNotNone(fmk1)
        
        # Ensure it's stored in db
        self.cur.execute("SELECT value FROM app_settings WHERE key='crypto.fmk_wrapped_by_dmk_b64'")
        row = self.cur.fetchone()
        self.assertIsNotNone(row)
        
        # Second call retrieves the SAME FMK
        fmk2 = get_or_create_file_master_key(self.conn, dmk_raw=self.dmk_raw)
        self.assertEqual(fmk1, fmk2)

    def test_fmk_retrieval_fails_with_wrong_dmk(self):
        fmk1 = get_or_create_file_master_key(self.conn, dmk_raw=self.dmk_raw)
        
        wrong_dmk = os.urandom(32)
        with self.assertRaises(RuntimeError) as context:
            get_or_create_file_master_key(self.conn, dmk_raw=wrong_dmk)
        
        self.assertIn("Unable to unwrap file key", str(context.exception))

    def test_encrypt_decrypt_roundtrip(self):
        fmk = get_or_create_file_master_key(self.conn, dmk_raw=self.dmk_raw)
        
        plaintext = b"Super secret medical data"
        ciphertext = encrypt_bytes(fmk, plaintext)
        
        # Ciphertext should not be plaintext
        self.assertNotEqual(ciphertext, plaintext)
        
        # Decrypt should restore plaintext
        decrypted = decrypt_bytes(fmk, ciphertext)
        self.assertEqual(decrypted, plaintext)

    def test_decrypt_fails_with_wrong_fmk(self):
        fmk1 = get_or_create_file_master_key(self.conn, dmk_raw=self.dmk_raw)
        plaintext = b"Test 123"
        ciphertext = encrypt_bytes(fmk1, plaintext)
        
        wrong_fmk = Fernet.generate_key()
        with self.assertRaises(InvalidToken):
            decrypt_bytes(wrong_fmk, ciphertext)

    def test_decrypt_fails_on_corrupted_data(self):
        fmk = get_or_create_file_master_key(self.conn, dmk_raw=self.dmk_raw)
        plaintext = b"Important"
        ciphertext = encrypt_bytes(fmk, plaintext)
        
        # Corrupt the last byte
        corrupted = ciphertext[:-1] + (b"A" if ciphertext[-1:] != b"A" else b"B")
        with self.assertRaises(InvalidToken):
            decrypt_bytes(fmk, corrupted)

    def test_legacy_format_blocked(self):
        # Insert legacy keys
        self.cur.execute("INSERT INTO app_settings (key, value) VALUES ('crypto.fmk_wrapped_b64', 'something')")
        self.conn.commit()
        
        with self.assertRaises(RuntimeError) as context:
            get_or_create_file_master_key(self.conn, dmk_raw=self.dmk_raw)
            
        self.assertIn("This vault was created with an older encryption format", str(context.exception))

    def test_fmk_creation_fails_without_db_or_dmk(self):
        with self.assertRaises(ValueError) as context:
            get_or_create_file_master_key(None, dmk_raw=self.dmk_raw)
        self.assertIn("DB connection required", str(context.exception))

        with self.assertRaises(ValueError) as context:
            get_or_create_file_master_key(self.conn, dmk_raw=b"")
        self.assertIn("DMK required", str(context.exception))

if __name__ == "__main__":
    unittest.main()
