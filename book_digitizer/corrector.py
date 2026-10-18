import os
import re
import unicodedata
from typing import Dict, Any, List, Optional, Set, Tuple
import arabic_reshaper
from bidi.algorithm import get_display
from config import AppConfig


class TextCorrector:
    """
    Post-OCR linguistic and orthographic normalizer for English, Arabic, Persian, and Urdu.
    - Arabic: Preserves full tashkeel (harakat/shadda), enforces standard Kaf (ك) and Yeh (ي).
    - Persian: Enforces Keheh (ک) and Farsi Yeh (ی), repairs Zero-Width Non-Joiner (ZWNJ / نیم‌فاصله)
      for prefixes (می‌/نمی‌) and plural/comparative suffixes (ها/تر/ترین).
    - Urdu: Enforces Urdu ligatures (اللہ, ﷺ), preserves do-chashmi heh (ھ) vs goal heh (ہ),
      and preserves bari ye (ے).
    - English: Handles soft hyphen de-breaking, punctuation quotes, and optional LanguageTool grammar.
    - Morphological Clitic Stemmer: Strips Arabic/Persian prefixes and suffixes to verify inflected words.
    """

    # Arabic/Persian/Urdu combining diacritics range (Fatha, Damma, Kasra, Shadda, Sukun, Tanwin)
    DIACRITIC_CODEPOINTS: Set[int] = set(range(0x064B, 0x0660)) | {0x0670} | set(
        range(0x06D6, 0x06EE)
    )

    # Universal safe orthographic normalizations
    SAFE_CHAR_MAP: Dict[str, str] = {
        "\u0640": "",        # Remove stray Arabic Tatweel (kashida) noise from OCR
        "\ufdfd": "ﷺ",       # Preserve ligature
        "٬": "،",            # Normalize thousands separator confused with Arabic comma
    }

    # Urdu-specific deterministic ligature/encoding repairs
    URDU_FIXES: Dict[str, str] = {
        "اﷲ": "اللہ",
        "اللّه": "اللہ",
        "رحمه": "رحمہ",
    }

    # Historical Orthography & Classical Rasm Whitelist (Protected from modern over-correction)
    HISTORICAL_ORTHOGRAPHY_WHITELIST: Set[str] = {
        # Quranic Rasm & Classical Arabic spellings
        "صلوة", "زكوة", "حيوة", "مشكوة", "ربوا", "الغدوة", "مرضاة", "نجوة", "بينة",
        "ابرهيم", "اسمعيل", "اسحق", "يعقوب", "هرون", "سليمن", "داود", "طغوت", "تورية",
        # Classical Persian & Ottoman Archaic spellings
        "طهران", "طوس", "اصفهان", "طوطی", "خورشید", "خویش", "خواب", "خواست", "خواهش",
        "خویشتن", "خویشان", "قسطنطنیه", "شست", "استاد", "اوستاد", "پیل", "چوگان",
        # Classical Islamic Honorifics & Ligatures
        "علیه", "علیها", "علیهم", "رحمة", "رضی", "قدس", "سره", "عزوجل",
        # Alif Maqsura Loanwords (Must retain authentic terminal \u0649)
        "علی", "حتی", "الی", "موسی", "عیسی", "فتوی", "معنی", "تقوی", "دعوی", "کبری", "صغری", "اولی",
    }


    def __init__(self, config: AppConfig):
        self.config = config
        self.lexicons: Dict[str, Set[str]] = {
            "rtl": set(),
            "ltr": set(),
        }
        self._load_custom_dictionaries()
        self._lang_tool = None
        if self.config.enable_english_languagetool:
            self._init_language_tool()

        self.reshaper = arabic_reshaper.ArabicReshaper(
            configuration={
                "delete_harakat": not self.config.preserve_diacritics,
                "support_ligatures": True,
            }
        )

    def _init_language_tool(self) -> None:
        try:
            import language_tool_python
            self._lang_tool = language_tool_python.LanguageTool("en-US")
        except Exception:
            self._lang_tool = None

    def _load_custom_dictionaries(self) -> None:
        """
        Load optional newline-delimited word lists (`urd.txt`, `ara.txt`, `fas.txt`, `eng.txt`)
        from `config.custom_dictionary_dir` if provided.
        """
        if not self.config.custom_dictionary_dir or not os.path.isdir(
            self.config.custom_dictionary_dir
        ):
            return

        for fname in os.listdir(self.config.custom_dictionary_dir):
            if not fname.endswith(".txt"):
                continue
            fpath = os.path.join(self.config.custom_dictionary_dir, fname)
            bucket = "ltr" if fname.lower().startswith("eng") else "rtl"
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        word = line.strip()
                        if word:
                            self.lexicons[bucket].add(self.strip_diacritics(word))
            except OSError:
                continue

    @classmethod
    def strip_diacritics(cls, text: str) -> str:
        return "".join(
            ch for ch in text if ord(ch) not in cls.DIACRITIC_CODEPOINTS
        )

    PROTECTED_ROOT_PREFIXES: Set[str] = {
        "ورد", "وقت", "وقف", "وطن", "وفا", "وصل", "وزیر", "وضع", "وحید", "وجود",
        "وعدہ", "واقع", "واضح", "والی", "وارث", "واحد", "فکر", "فخر", "فوج", "فصل",
        "فتح", "فرمان", "فضا", "فائدہ", "فوری", "فرصت", "فرزند", "بلند", "بیدار", "بہار",
        "بازار", "بیمار", "بہتر", "بزرگ", "بنیاد", "برکت", "بیان", "باطن", "ظاہر",
    }

    @classmethod
    def extract_arabic_stems(cls, word: str) -> List[str]:
        """Strip Arabic clitic prefixes and suffixes to find candidate dictionary root/stem."""
        candidates = [word]
        proclitics = ["وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ك", "ل"]
        enclitics = ["هما", "هم", "هن", "کم", "کن", "نا", "ها", "ک", "ي", "ه"]

        current = word
        for p in proclitics:
            if current.startswith(p) and len(current) > len(p) + 2:
                # Prevent stripping if the single-letter prefix belongs to a protected root
                if len(p) == 1 and any(current.startswith(root) for root in cls.PROTECTED_ROOT_PREFIXES):
                    continue
                current = current[len(p) :]
                candidates.append(current)
                break

        for e in enclitics:
            if current.endswith(e) and len(current) > len(e) + 2:
                stem = current[: -len(e)]
                candidates.append(stem)
                break
        return candidates

    @classmethod
    def extract_persian_stems(cls, word: str) -> List[str]:
        """Strip Persian prefixes and suffixes to find candidate dictionary stem."""
        candidates = [word]
        prefixes = ["می‌", "نمی‌", "بی‌", "می\u200c", "نمی\u200c", "بی\u200c", "می", "نمی"]
        suffixes = ["ها", "های", "تر", "ترین", "ام", "ات", "اش", "مان", "تان", "شان"]

        current = word
        for p in prefixes:
            if current.startswith(p) and len(current) > len(p) + 2:
                current = current[len(p) :].lstrip("\u200c")
                candidates.append(current)
                break

        for s in suffixes:
            if current.endswith(s) and len(current) > len(s) + 2:
                stem = current[: -len(s)].rstrip("\u200c")
                candidates.append(stem)
                break
        return candidates

    def matches_lexicon(self, token: str, bucket: str) -> bool:
        """Check if token or its morphological stem exists in the custom dictionary."""
        lex = self.lexicons.get(bucket)
        if not lex:
            return False

        bare = self.strip_diacritics(token).strip("۔،؟!():«»\"'")
        if bare in lex:
            return True

        if not self.config.enable_clitic_morphology or bucket != "rtl":
            return False

        if self.config.primary_language == "ara":
            return any(stem in lex for stem in self.extract_arabic_stems(bare))
        elif self.config.primary_language == "fas":
            return any(stem in lex for stem in self.extract_persian_stems(bare))

        return False

    def normalize_token(self, token: str, direction: str) -> str:
        """
        Apply NFC Unicode normalization with language-specific orthography rules.
        """
        norm = unicodedata.normalize("NFC", token)

        for src, dst in self.SAFE_CHAR_MAP.items():
            norm = norm.replace(src, dst)

        lang = self.config.primary_language

        if direction == "RTL":
            # Fix question mark, comma, and semicolon attached to RTL words
            if norm.endswith("?"):
                norm = norm[:-1] + "؟"
            elif norm.endswith(","):
                norm = norm[:-1] + "،"
            elif norm.endswith(";"):
                norm = norm[:-1] + "؛"

            # Historical Orthography Protection
            bare = self.strip_diacritics(norm).strip("۔،؟!():«»\"'")
            if getattr(self.config, "enable_historical_orthography_mode", True) and bare in self.HISTORICAL_ORTHOGRAPHY_WHITELIST:
                return norm

            # Check if token is explicitly Arabic (Quranic superscript alef, shadda + vowels, or dense diacritics)
            diacritic_count = sum(1 for ch in norm if ord(ch) in self.DIACRITIC_CODEPOINTS)
            is_arabic_token = diacritic_count >= 2 or "\u0670" in norm or "\u0651" in norm

            # 1. Arabic-specific orthography (or authentic Arabic passage in multilingual text)
            if lang == "ara" or is_arabic_token:
                norm = norm.replace("\u06a9", "\u0643").replace("\u06cc", "\u064a")

            # 2. Persian-specific orthography
            elif lang == "fas":
                norm = norm.replace("\u0643", "\u06a9").replace("\u064a", "\u06cc")
                norm = norm.replace("٤", "۴").replace("٥", "۵").replace("٦", "۶")

            # 3. Urdu-specific orthography
            elif lang == "urd":
                norm = norm.replace("\u0643", "\u06a9").replace("\u064a", "\u06cc")
                if norm in self.URDU_FIXES:
                    norm = self.URDU_FIXES[norm]


        else:
            norm = norm.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")

        return norm

    def format_low_confidence_token(self, text: str, confidence: float) -> str:
        if not self.config.flag_low_confidence:
            return text
        if self.config.clean_bidi_tagging:
            return f"\u200E[{self.config.low_conf_prefix}:{confidence:.0f}%: {text}]\u200E"
        return f"{self.config.low_conf_prefix}:{confidence:.0f}%: {text}]"

    def shape_for_visual_pdf(self, logical_text: str, base_direction: str = "RTL") -> str:
        """
        Convert logical Unicode text into shaped visual glyph order for ReportLab PDF
        canvas rendering (which does not natively run HarfBuzz/BiDi).
        Note: DOCX, EPUB, and UTF-8 TXT always use logical order (`logical_text`).
        """
        if not self.config.enable_rtl_reshaping_for_pdf or not logical_text or base_direction == "LTR":
            return logical_text
        try:
            reshaped = self.reshaper.reshape(logical_text)
            bidi_dir = "R" if base_direction == "RTL" else "L"
            return get_display(reshaped, base_dir=bidi_dir)
        except Exception:
            return logical_text

    def process_ocr_page(self, ocr_page: Dict[str, Any]) -> Dict[str, Any]:
        """
        Walk every line and word on the page, normalize orthography, preserve diacritics,
        apply mixed BiDi ordering, and flag any word below `confidence_threshold`.
        """
        flagged_words_summary: List[Dict[str, Any]] = []

        for line in ocr_page.get("lines", []):
            line_dir = line.get("direction", ocr_page.get("page_direction", "RTL"))
            logical_tokens: List[str] = []
            flagged_logical_tokens: List[str] = []

            for word in line.get("words", []):
                raw = word.get("raw_text") or word.get("text", "")
                conf = float(word.get("confidence", 99.0))
                w_dir = word.get("direction", line_dir)

                cleaned = self.normalize_token(raw, w_dir)
                word["text"] = cleaned

                is_low = conf < self.config.confidence_threshold
                bucket = "ltr" if w_dir == "LTR" else "rtl"

                # Check if word or clitic-stripped root matches lexicon
                if is_low and self.matches_lexicon(cleaned, bucket) and conf >= (
                    self.config.confidence_threshold - 14.0
                ):
                    is_low = False

                word["is_low_confidence"] = is_low
                logical_tokens.append(cleaned)

                if is_low:
                    flagged_str = self.format_low_confidence_token(cleaned, conf)
                    word["flagged_text"] = flagged_str
                    flagged_logical_tokens.append(flagged_str)
                    flagged_words_summary.append(
                        {
                            "page": ocr_page["page_number"],
                            "word": cleaned,
                            "confidence": conf,
                            "bbox": word.get("bbox", [0, 0, 10, 10]),
                        }
                    )
                else:
                    word["flagged_text"] = cleaned
                    flagged_logical_tokens.append(cleaned)

            logical_line = self._join_bidi_tokens(logical_tokens, line_dir, self.config.primary_language)
            flagged_line = self._join_bidi_tokens(flagged_logical_tokens, line_dir, self.config.primary_language)

            if (
                line_dir == "LTR"
                and self._lang_tool is not None
                and not any(w["is_low_confidence"] for w in line.get("words", []))
            ):
                try:
                    logical_line = self._lang_tool.correct(logical_line)
                    flagged_line = logical_line
                except Exception:
                    pass

            line["logical_text"] = logical_line
            line["flagged_logical_text"] = flagged_line

            line["clean_visual_pdf_text"] = self.shape_for_visual_pdf(
                logical_line, base_direction=line_dir
            )
            line["visual_pdf_text"] = self.shape_for_visual_pdf(
                flagged_line, base_direction=line_dir
            )

        all_page_words = [w for ln in ocr_page.get("lines", []) for w in ln.get("words", [])]
        ocr_page["words"] = all_page_words
        ocr_page["word_count"] = len(all_page_words)
        ocr_page["flagged_words"] = flagged_words_summary
        return ocr_page

    def correct(self, ocr_page: Dict[str, Any]) -> Dict[str, Any]:
        """Alias for process_ocr_page."""
        return self.process_ocr_page(ocr_page)

    @staticmethod
    def _join_bidi_tokens(tokens: List[str], line_dir: str, primary_language: str = "urd") -> str:
        """
        Join tokens in reading order, clean punctuation spaces, and apply Persian ZWNJ repairs.
        """
        text = " ".join(tokens)
        text = re.sub(r"\s+([۔،؟؛!:,.;)])", r"\1", text)
        text = re.sub(r"([(«])\s+", r"\1", text)

        if primary_language == "fas" and line_dir == "RTL":
            text = re.sub(r"(^|\s)(ن?می)\s+(\S+)", r"\1\2\u200c\3", text)
            text = re.sub(
                r"(\S+)\s+(های?|ترین?|تر|ام|ات|اش|ایم|اید|اند)($|[\s۔،؛!؟])",
                r"\1\u200c\2\3",
                text,
            )

        return text.strip()
