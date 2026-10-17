import sys
import os
import unittest

# Ensure book_digitizer is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "book_digitizer")))

from config import AppConfig
from corrector import TextCorrector


class TestTextCorrector(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig()
        self.corrector = TextCorrector(self.config)

    def test_strip_diacritics(self):
        # Arabic with fatha, damma, kasra, shadda, sukun
        text_with_harakat = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
        stripped = self.corrector.strip_diacritics(text_with_harakat)
        self.assertEqual(stripped, "بسم الله الرحمن الرحيم")

    def test_historical_orthography_whitelist(self):
        # Words in Quranic Rasm like صلوة (Salah with Waw) should be protected
        self.config.primary_language = "ara"
        self.config.enable_historical_orthography_mode = True
        
        token = "صلوة"
        cleaned = self.corrector.normalize_token(token, direction="RTL")
        self.assertEqual(cleaned, "صلوة")

        # Test another classical word from whitelist
        token2 = "ابرهيم"
        cleaned2 = self.corrector.normalize_token(token2, direction="RTL")
        self.assertEqual(cleaned2, "ابرهيم")

    def test_persian_urdu_orthography(self):
        # Keheh / Yeh replacements in Urdu mode
        self.config.primary_language = "urd"
        # Arabic kaf (\u0643) should be replaced with Urdu Keheh (\u06a9)
        arabic_style = "كتاب"
        urdu_style = self.corrector.normalize_token(arabic_style, direction="RTL")
        self.assertEqual(urdu_style, "کتاب")

    def test_arabic_stemmer_clitics(self):
        # Test Arabic clitic stripping
        stems = self.corrector.extract_arabic_stems("والقمر")
        # Should contain "القمر" or "قمر"
        self.assertTrue(len(stems) > 1)
        self.assertTrue(any("قمر" in s for s in stems))

    def test_shape_for_visual_pdf(self):
        # Test BiDi reshaping for PDF canvas
        urdu_text = "اردو ادب"
        shaped = self.corrector.shape_for_visual_pdf(urdu_text, base_direction="RTL")
        self.assertIsNotNone(shaped)
        self.assertGreater(len(shaped), 0)


if __name__ == "__main__":
    unittest.main()
