# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import paths


class TestPaths(unittest.TestCase):

    def test_app_dir_is_path(self):
        self.assertIsInstance(paths.app_dir, Path)

    def test_all_paths_are_absolute(self):
        for attr in ("app_dir", "db_path", "keybag_path", "ai_dir", "model_dir",
                     "export_dir", "data_dir"):
            p = getattr(paths, attr)
            self.assertTrue(p.is_absolute(), f"{attr} is not absolute: {p}")

    def test_db_path_under_app_dir(self):
        self.assertEqual(paths.db_path.parent, paths.app_dir)

    def test_keybag_path_under_app_dir(self):
        self.assertEqual(paths.keybag_path.parent, paths.app_dir)

    def test_export_dir_under_app_dir(self):
        self.assertTrue(str(paths.export_dir).startswith(str(paths.app_dir)))

    def test_model_dir_under_app_dir(self):
        self.assertTrue(str(paths.model_dir).startswith(str(paths.app_dir)))

    def test_directories_created(self):
        """Importing paths should have created all required directories."""
        for attr in ("app_dir", "model_dir", "export_dir", "data_dir"):
            p = getattr(paths, attr)
            self.assertTrue(p.exists(), f"{attr} directory was not created: {p}")
            self.assertTrue(p.is_dir(), f"{attr} is not a directory: {p}")

    def test_db_filename(self):
        self.assertEqual(paths.db_path.name, "medical_records_v1.db")

    def test_keybag_filename(self):
        self.assertEqual(paths.keybag_path.name, "medical_records_v1.db.keybag")


if __name__ == "__main__":
    unittest.main()
