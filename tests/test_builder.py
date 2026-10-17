import sys
import os
import unittest
import tempfile
import xml.etree.ElementTree as ET

# Ensure book_digitizer is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "book_digitizer")))

from config import AppConfig
from builder import BookBuilder


class TestBookBuilder(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig()
        self.builder = BookBuilder(self.config)
        self.test_dir = tempfile.mkdtemp()

        # Synthetic page data structure
        self.sample_pages = [
            {
                "page_number": 1,
                "label": "1",
                "width": 800,
                "height": 1200,
                "reading_direction": "RTL",
                "elements": [
                    {
                        "type": "paragraph",
                        "direction": "RTL",
                        "logical_text": "کتاب خوب",
                        "bbox": [50, 100, 700, 200],
                        "words": [
                            {
                                "text": "کتاب",
                                "bbox": [50, 100, 60, 30],
                                "confidence": 98.0,
                            },
                            {
                                "text": "خوب",
                                "bbox": [120, 100, 50, 30],
                                "confidence": 95.0,
                            },
                        ],
                    }
                ],
            }
        ]

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_build_txt(self):
        out_file = os.path.join(self.test_dir, "output.txt")
        self.builder.build_txt(self.sample_pages, out_file)
        self.assertTrue(os.path.isfile(out_file))
        with open(out_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("کتاب خوب", content)

    def test_build_html_replica(self):
        out_file = os.path.join(self.test_dir, "output.html")
        self.builder.build_html_replica(self.sample_pages, out_file)
        self.assertTrue(os.path.isfile(out_file))
        with open(out_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("کتاب", content)

    def test_build_alto_xml(self):
        out_file = os.path.join(self.test_dir, "output_alto.xml")
        self.builder.build_alto_xml(self.sample_pages, out_file)
        self.assertTrue(os.path.isfile(out_file))

        # Parse and verify XML validity
        tree = ET.parse(out_file)
        root = tree.getroot()
        self.assertTrue(root.tag.endswith("alto") or "alto" in root.tag)

    def test_build_page_xml(self):
        pagexml_dir = os.path.join(self.test_dir, "pagexml")
        created = self.builder.build_page_xml(self.sample_pages, pagexml_dir)
        self.assertTrue(len(created) > 0)
        self.assertTrue(os.path.isfile(created[0]))

        # Parse and verify XML validity
        tree = ET.parse(created[0])
        root = tree.getroot()
        self.assertTrue(root.tag.endswith("PcGts") or "PcGts" in root.tag)


if __name__ == "__main__":
    unittest.main()
