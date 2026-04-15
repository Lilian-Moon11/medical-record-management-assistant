import unittest
import os
import tempfile
from PIL import Image

# Import the core logic (assuming utils or ui modules are reachable)
import sys
# Add parent dir to path if running directly from tests/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ui.wizards.signature_pad import render_signature_png

class TestPaperworkWizard(unittest.TestCase):
    def test_render_signature_png(self):
        # Create a mock drawing trace
        points = [
            (10, 10), (20, 20), (30, 30), None,
            (40, 40), (50, 50)
        ]
        img = render_signature_png(points, 400, 150)
        
        self.assertIsInstance(img, Image.Image)
        self.assertEqual(img.size, (400, 150))
        
        # Test saving to a temp file (like the wizard does)
        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            # We must explicitly CLOSE the file descriptor on Windows before using path in Pillow!
            os.close(fd)
            # The bug in the wizard was that it kept fd open while img.save(path) tried to write it
            img.save(path, format="PNG")
            
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 0)
        finally:
            if os.path.exists(path):
                os.remove(path)

if __name__ == "__main__":
    unittest.main()
