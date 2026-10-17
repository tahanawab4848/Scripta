import sys
import os
import unittest
import numpy as np
from PIL import Image

# Ensure book_digitizer is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "book_digitizer")))

from config import AppConfig
from preprocessor import PagePreprocessor


class TestPagePreprocessor(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig()
        self.preprocessor = PagePreprocessor(self.config)

    def test_process_page_basic(self):
        # Create a synthetic 200x200 white image with black text-like bar
        img_array = np.full((200, 200, 3), 255, dtype=np.uint8)
        img_array[80:120, 50:150] = 0  # Black bar
        pil_img = Image.fromarray(img_array)

        processed_img, meta = self.preprocessor.process_page(pil_img, page_number=1)
        self.assertIsNotNone(processed_img)
        self.assertEqual(meta["page_number"], 1)
        self.assertEqual(meta["width"], 200)
        self.assertEqual(meta["height"], 200)

    def test_ghost_dot_removal(self):
        # Test ghost dot filter on faint noise dots
        gray = np.full((100, 100), 255, dtype=np.uint8)
        gray[50, 50] = 210  # Very faint ghost dot
        cleaned = self.preprocessor.remove_ghost_dots(gray)
        self.assertEqual(cleaned.shape, gray.shape)
        # Faint dot should be attenuated closer to white (255)
        self.assertGreaterEqual(int(cleaned[50, 50]), 210)


if __name__ == "__main__":
    unittest.main()
