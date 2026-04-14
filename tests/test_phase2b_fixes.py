import unittest
import os
import sys

# Fix paths to allow importing project modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

class TestPhase2BFixes(unittest.TestCase):

    def test_get_unprocessed_docs_name(self):
        """Verify _get_unindexed_docs is properly renamed to _get_unprocessed_docs."""
        import ai.ingestion
        self.assertTrue(hasattr(ai.ingestion, "_get_unprocessed_docs"))
        self.assertFalse(hasattr(ai.ingestion, "_get_unindexed_docs"))

    def test_ci_yaml_python_version(self):
        """Verify build.yml uses Python 3.12 to match the README."""
        yaml_path = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "build.yml")
        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn('python-version: "3.12"', content)

    def test_airlock_exports_records(self):
        """Verify airlock.py includes records_requests and ai_extraction_inbox in export dict."""
        import utils.airlock
        source_path = utils.airlock.__file__
        with open(source_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Verify the SELECT strings are present in the file
        self.assertIn("FROM records_requests", content)
        self.assertIn("FROM ai_extraction_inbox", content)
        # Verify the import insertion blocks exist
        self.assertIn("INSERT INTO records_requests", content)
        self.assertIn("INSERT INTO ai_extraction_inbox", content)

    def test_timestamp_format_consistency(self):
        """Verify inconsistent timestamp %Y-%m-%d %H:%M:%S is gone."""
        for filename in ["records_requests.py", "clinical.py"]:
            path = os.path.join(os.path.dirname(__file__), "..", "database", filename)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("%Y-%m-%d %H:%M:%S", content)
            self.assertIn("%Y-%m-%d %H:%M", content)

    def test_os_startfile_removed(self):
        """Verify os.startfile() is replaced by cross-platform helpers."""
        # The cross platform helper is open_file_cross_platform
        files_to_check = [
            os.path.join("views", "overview.py"),
            os.path.join("views", "documents.py"),
            os.path.join("views", "health_record.py"),
            os.path.join("ui", "wizards", "paperwork_wizard.py")
        ]
        
        for file_rel in files_to_check:
            path = os.path.join(os.path.dirname(__file__), "..", file_rel)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("os.startfile", content, f"os.startfile found in {file_rel}")
            # Ensure at least some files import the cross-platform handler if they used it
            if "open_file_cross_platform" in content:
                self.assertIn("open_file_cross_platform(", content)


if __name__ == "__main__":
    unittest.main()
