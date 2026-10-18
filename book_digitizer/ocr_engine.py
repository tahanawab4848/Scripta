import base64
import io
import json
import os
import random
import re
import time
import unicodedata
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pytesseract
import requests
from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image
from config import AppConfig

# Illegal control characters in XML 1.0 specifications
_ILLEGAL_XML_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f\ufffe\uffff]")


def get_pdf_page_count(pdf_path: str, poppler_path: Optional[str] = None) -> int:
    """
    Return total pages in the PDF using Poppler's pdfinfo, with transparent fallbacks
    to pypdfium2, pypdf, or pymupdf to prevent failures when Poppler is absent.
    """
    # 1. Primary: Poppler via pdf2image
    try:
        info = pdfinfo_from_path(pdf_path, poppler_path=poppler_path)
        return int(info.get("Pages", 0))
    except Exception:
        pass

    # 2. Fallback: pypdfium2 (zero-dependency bundled Google PDFium)
    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(pdf_path)
        return len(pdf)
    except Exception:
        pass

    # 3. Fallback: pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        return len(reader.pages)
    except Exception:
        pass

    # 4. Fallback: pymupdf (fitz)
    try:
        import fitz
        doc = fitz.open(pdf_path)
        return len(doc)
    except Exception:
        pass

    raise RuntimeError(
        f"Unable to read page count from '{pdf_path}'. Please install Poppler or run: pip install pypdfium2"
    )


def render_single_pdf_page(
    pdf_path: str,
    page_number: int,
    dpi: int = 350,
    poppler_path: Optional[str] = None,
) -> Image.Image:
    """
    Render exactly one PDF page (1-indexed) at the requested DPI so memory stays flat
    even on 5,000-page books. Supports Poppler with transparent fallbacks to pypdfium2 and pymupdf.
    """
    # 1. Primary: Poppler via pdf2image
    try:
        images = convert_from_path(
            pdf_path,
            dpi=dpi,
            first_page=page_number,
            last_page=page_number,
            poppler_path=poppler_path,
            fmt="png",
            thread_count=1,
        )
        if images:
            return images[0]
    except Exception:
        pass

    # 2. Fallback: pypdfium2
    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(pdf_path)
        page = pdf.get_page(page_number - 1)
        scale = dpi / 72.0
        pil_image = page.render(scale=scale).to_pil()
        return pil_image
    except Exception:
        pass

    # 3. Fallback: pymupdf (fitz)
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc.load_page(page_number - 1)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    except Exception:
        pass

    raise RuntimeError(
        f"Could not render page {page_number} of '{pdf_path}'. "
        "Please install Poppler or install pypdfium2 via: pip install pypdfium2"
    )


class OCREngine:
    """
    Hybrid Multilingual OCR engine driving Tesseract 5 LSTM with automatic Multimodal Vision LLM
    fallback (Gemini / Claude) for difficult Nastaliq, cursive, or degraded historical lithographs.
    Features:
    - True BiDi phrase-level word sorting (preserving internal LTR order for embedded English/numbers in RTL lines).
    - Automatic Page Segmentation Mode (PSM) tuning.
    - Script analysis and per-token confidence tracking.
    """

    RTL_UNICODE_RANGES: List[Tuple[int, int]] = [
        (0x0600, 0x06FF),  # Arabic / Persian / Urdu core block
        (0x0750, 0x077F),  # Arabic Supplement
        (0x08A0, 0x08FF),  # Arabic Extended-A
        (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
        (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
    ]

    EASTERN_DIGITS = set("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹")

    def __init__(self, config: AppConfig):
        self.config = config
        if self.config.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.config.tesseract_cmd

    @classmethod
    def is_rtl_char(cls, ch: str) -> bool:
        code = ord(ch)
        for start, end in cls.RTL_UNICODE_RANGES:
            if start <= code <= end:
                return True
        bidi = unicodedata.bidirectional(ch)
        return bidi in ("R", "AL", "AN")

    @classmethod
    def analyze_token_script(cls, text: str) -> Dict[str, Any]:
        """
        Inspect a token's characters to determine its primary script and reading direction.
        """
        rtl_count = 0
        ltr_count = 0
        digit_count = 0
        diacritic_count = 0

        for ch in text:
            cat = unicodedata.category(ch)
            if cat == "Mn":
                diacritic_count += 1
            if cls.is_rtl_char(ch):
                rtl_count += 1
            elif ch.isdigit() or ch in cls.EASTERN_DIGITS:
                digit_count += 1
            elif ch.isalpha():
                ltr_count += 1

        if rtl_count > 0 and ltr_count == 0:
            direction = "RTL"
            script = "RTL_SCRIPT"
        elif ltr_count > 0 and rtl_count == 0:
            direction = "LTR"
            script = "LATIN"
        elif rtl_count > 0 and ltr_count > 0:
            direction = "RTL" if rtl_count >= ltr_count else "LTR"
            script = "MIXED"
        elif digit_count > 0:
            direction = "NEUTRAL_NUM"
            script = "NUMERIC"
        else:
            direction = "NEUTRAL"
            script = "PUNCT"

        return {
            "direction": direction,
            "script": script,
            "rtl_chars": rtl_count,
            "ltr_chars": ltr_count,
            "digits": digit_count,
            "diacritics": diacritic_count,
        }

    def auto_select_psm(self, pil_image: Image.Image) -> int:
        """
        Determine optimal Tesseract Page Segmentation Mode (PSM) from page ink layout.
        """
        if not self.config.auto_tune_psm:
            return self.config.psm

        gray = np.array(pil_image.convert("L"))
        h, w = gray.shape
        binary_inv = (gray < 180).astype(np.uint8)
        ink_ratio = float(np.mean(binary_inv))

        # Sparse page (Title page, half-title, section divider)
        if ink_ratio < 0.025:
            return 4

        # Check center vertical gutter to detect two-column layout
        mid_x = w // 2
        gutter_band = binary_inv[
            int(h * 0.15) : int(h * 0.85), max(0, mid_x - w // 40) : min(w, mid_x + w // 40)
        ]
        left_band = binary_inv[
            int(h * 0.15) : int(h * 0.85), int(w * 0.15) : int(w * 0.42)
        ]
        right_band = binary_inv[
            int(h * 0.15) : int(h * 0.85), int(w * 0.58) : int(w * 0.85)
        ]

        gutter_ink = float(np.mean(gutter_band)) if gutter_band.size else 1.0
        left_ink = float(np.mean(left_band)) if left_band.size else 0.0
        right_ink = float(np.mean(right_band)) if right_band.size else 0.0

        if gutter_ink < 0.008 and left_ink > 0.05 and right_ink > 0.05:
            return 3

        return self.config.psm

    @staticmethod
    def _overlaps_illustration(
        word_bbox: Tuple[int, int, int, int], illustrations: List[Dict[str, Any]]
    ) -> bool:
        wx, wy, ww, wh = word_bbox
        w_area = max(1, ww * wh)
        for ill in illustrations:
            ix, iy, iw, ih = ill["bbox"]
            ox = max(0, min(wx + ww, ix + iw) - max(wx, ix))
            oy = max(0, min(wy + wh, iy + ih) - max(wy, iy))
            if (ox * oy) / float(w_area) > 0.65:
                return True
        return False

    @staticmethod
    def sort_line_words_bidi(words: List[Dict[str, Any]], line_dir: str) -> List[Dict[str, Any]]:
        """
        Sort words according to Unicode Bidirectional Reading Order.
        - In an LTR line: words flow left-to-right across the page.
        - In an RTL line: text blocks flow right-to-left across the page,
          BUT any embedded Latin phrase or numeric run (e.g., 'Oxford University' or '1995')
          MUST retain its internal left-to-right word order.
        """
        if len(words) <= 1:
            return words

        spatial_ltr = sorted(words, key=lambda w: w["bbox"][0])

        if line_dir == "LTR":
            return spatial_ltr

        runs: List[List[Dict[str, Any]]] = []
        current_run: List[Dict[str, Any]] = [spatial_ltr[0]]

        def is_ltr_like(w: Dict[str, Any]) -> bool:
            return w["direction"] in ("LTR", "NEUTRAL_NUM")

        for w in spatial_ltr[1:]:
            prev_is_ltr = is_ltr_like(current_run[-1])
            curr_is_ltr = is_ltr_like(w)

            if w["direction"] == "NEUTRAL":
                current_run.append(w)
            elif prev_is_ltr == curr_is_ltr:
                current_run.append(w)
            else:
                runs.append(current_run)
                current_run = [w]
        runs.append(current_run)

        def run_x_center(run: List[Dict[str, Any]]) -> float:
            x_min = min(w["bbox"][0] for w in run)
            x_max = max(w["bbox"][0] + w["bbox"][2] for w in run)
            return (x_min + x_max) / 2.0

        runs.sort(key=run_x_center, reverse=True)

        ordered_words: List[Dict[str, Any]] = []
        for run in runs:
            run_is_ltr = any(is_ltr_like(w) for w in run)
            if run_is_ltr:
                ordered_words.extend(sorted(run, key=lambda w: w["bbox"][0]))
            else:
                ordered_words.extend(sorted(run, key=lambda w: w["bbox"][0], reverse=True))

        return ordered_words

    def run_vision_llm_ocr(self, pil_image: Image.Image, page_number: int) -> Optional[Dict[str, Any]]:
        """
        Semantic OCR via Vision LLM (e.g. Gemini 2.0 Flash) for difficult Nastaliq,
        historical lithographs, or degraded book pages.
        """
        api_key = self.config.vision_llm_api_key
        if not api_key:
            return None

        try:
            # Compress image to high-quality JPEG
            buffer = io.BytesIO()
            pil_image.save(buffer, format="JPEG", quality=90)
            img_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

            prompt = (
                f"You are a specialized precision book OCR transcription system for "
                f"{self.config.language_preset} literature. Transcribe all text from this scanned page verbatim. "
                f"Preserve exact line breaks, paragraph structure, and verse couplets. "
                f"Do not include commentary, explanations, or code blocks; return ONLY the plain extracted text."
            )

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.config.vision_llm_model}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                        ]
                    }
                ],
                "generationConfig": {"temperature": 0.0},
            }

            max_retries = max(1, getattr(self.config, "vision_llm_max_retries", 3))
            base_delay = float(getattr(self.config, "vision_llm_base_delay", 2.0))

            resp = None
            for attempt in range(max_retries + 1):
                try:
                    resp = requests.post(url, json=payload, timeout=30)
                    if resp.status_code == 200:
                        break
                    elif resp.status_code == 429:
                        # Rate limited: apply exponential backoff with jitter
                        delay = base_delay * (2 ** attempt) + random.uniform(0.2, 1.2)
                        time.sleep(delay)
                    elif resp.status_code >= 500:
                        delay = base_delay + random.uniform(0.2, 0.8)
                        time.sleep(delay)
                    else:
                        break
                except (requests.exceptions.RequestException, TimeoutError):
                    if attempt < max_retries:
                        time.sleep(base_delay * (2 ** attempt))
                    else:
                        break

            if resp is None or resp.status_code != 200:
                return None

            resp_json = resp.json()
            candidates = resp_json.get("candidates", [])
            if not candidates:
                return None

            raw_transcription = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not raw_transcription.strip():
                return None

            # Parse lines into structured output
            raw_lines = [ln.strip() for ln in raw_transcription.splitlines() if ln.strip()]
            img_w, img_h = pil_image.size
            structured_lines = []
            valid_confidences = []
            total_rtl = 0
            total_ltr = 0

            line_height = max(24, int(img_h / max(1, len(raw_lines) * 1.4)))

            for idx, ln_text in enumerate(raw_lines):
                tokens = ln_text.split()
                line_words = []
                line_y = int((idx + 1) * line_height * 1.1)

                for w_idx, tok in enumerate(tokens):
                    script_meta = self.analyze_token_script(tok)
                    total_rtl += script_meta["rtl_chars"]
                    total_ltr += script_meta["ltr_chars"]
                    valid_confidences.append(97.5)

                    line_words.append(
                        {
                            "word_index": w_idx,
                            "raw_text": tok,
                            "text": tok,
                            "confidence": 97.5,
                            "bbox": [50 + w_idx * 60, line_y, 50, line_height],
                            "direction": script_meta["direction"],
                            "script": script_meta["script"],
                            "diacritics": script_meta["diacritics"],
                            "is_low_confidence": False,
                        }
                    )

                line_dir = "RTL" if sum(1 for w in line_words if w["direction"] == "RTL") >= len(line_words) / 2 else "LTR"
                structured_lines.append(
                    {
                        "block_num": 1,
                        "par_num": 1,
                        "line_num": idx + 1,
                        "bbox": [50, line_y, img_w - 100, line_height],
                        "direction": line_dir,
                        "confidence": 97.5,
                        "words": line_words,
                    }
                )

            page_dir = "RTL" if total_rtl >= total_ltr else "LTR"
            return {
                "page_number": page_number,
                "psm_used": 6,
                "page_direction": page_dir,
                "mean_confidence": 97.5,
                "word_count": len(valid_confidences),
                "low_confidence_word_count": 0,
                "rtl_char_count": total_rtl,
                "ltr_char_count": total_ltr,
                "lines": structured_lines,
                "backend_used": "vision_llm",
            }
        except Exception:
            return None

    def probe_born_digital_page(
        self,
        pdf_path: str,
        page_number: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Probe whether a PDF page already contains authentic digital vector text.
        If extractable text density exceeds the configured threshold, extracts
        clean Unicode text and bounding boxes directly without rasterization or OCR.
        """
        if not getattr(self.config, "detect_born_digital_pdf", True):
            return None
        if not os.path.exists(pdf_path):
            return None

        threshold = getattr(self.config, "born_digital_char_threshold", 40)
        dpi = max(72, getattr(self.config, "dpi", 350))
        scale = dpi / 72.0

        # 1. Try pypdfium2 (preferred: fast and gives exact text rectangles)
        try:
            import pypdfium2
            pdf = pypdfium2.PdfDocument(pdf_path)
            if page_number - 1 >= len(pdf):
                return None
            page = pdf.get_page(page_number - 1)
            tp = page.get_textpage()
            char_count = tp.count_chars()
            if char_count < threshold:
                return None

            page_w_pt, page_h_pt = page.get_size()

            structured_lines: List[Dict[str, Any]] = []
            valid_confidences: List[float] = []
            all_page_words: List[Dict[str, Any]] = []
            total_rtl = 0
            total_ltr = 0

            # Reconstruct lines and words sequentially from the character stream
            current_line_words: List[Dict[str, Any]] = []
            current_word_chars: List[str] = []
            current_word_boxes: List[Tuple[float, float, float, float]] = []

            def _finish_word():
                nonlocal current_word_chars, current_word_boxes, current_line_words, total_rtl, total_ltr, valid_confidences, all_page_words
                if not current_word_chars:
                    return
                raw_token = "".join(current_word_chars)
                clean_token = _ILLEGAL_XML_CHARS_RE.sub("", raw_token).strip()
                if clean_token and current_word_boxes:
                    x0 = min(b[0] for b in current_word_boxes)
                    x1 = max(b[2] for b in current_word_boxes)
                    yb = min(b[1] for b in current_word_boxes)
                    yt = max(b[3] for b in current_word_boxes)

                    px_x = int(x0 * scale)
                    px_y = int((page_h_pt - yt) * scale)
                    px_w = max(1, int((x1 - x0) * scale))
                    px_h = max(1, int((yt - yb) * scale))

                    meta = self.analyze_token_script(clean_token)
                    total_rtl += meta["rtl_chars"]
                    total_ltr += meta["ltr_chars"]
                    valid_confidences.append(99.9)

                    word_obj = {
                        "word_index": len(current_line_words),
                        "raw_text": clean_token,
                        "text": clean_token,
                        "confidence": 99.9,
                        "bbox": [px_x, px_y, px_w, px_h],
                        "direction": meta["direction"],
                        "script": meta["script"],
                        "diacritics": meta["diacritics"],
                        "is_low_confidence": False,
                    }
                    current_line_words.append(word_obj)
                    all_page_words.append(word_obj)

                current_word_chars = []
                current_word_boxes = []

            def _finish_line():
                nonlocal current_line_words, structured_lines
                _finish_word()
                if not current_line_words:
                    return
                lx0 = min(w["bbox"][0] for w in current_line_words)
                ly0 = min(w["bbox"][1] for w in current_line_words)
                lx1 = max(w["bbox"][0] + w["bbox"][2] for w in current_line_words)
                ly1 = max(w["bbox"][1] + w["bbox"][3] for w in current_line_words)

                line_dir = "RTL" if sum(1 for w in current_line_words if w["direction"] == "RTL") >= len(current_line_words) / 2 else "LTR"
                sorted_words = self.sort_line_words_bidi(current_line_words, line_dir)
                line_text = " ".join(w["text"] for w in sorted_words)

                structured_lines.append({
                    "block_num": 1,
                    "par_num": 1,
                    "line_num": len(structured_lines) + 1,
                    "logical_text": line_text,
                    "bbox": [lx0, ly0, max(1, lx1 - lx0), max(1, ly1 - ly0)],
                    "direction": line_dir,
                    "confidence": 99.9,
                    "words": sorted_words,
                })
                current_line_words = []

            for i in range(char_count):
                ch = tp.get_text_range(i, 1)
                box = tp.get_charbox(i)
                if ch in ("\r", "\n"):
                    _finish_line()
                elif ch in (" ", "\t", "\xa0"):
                    _finish_word()
                else:
                    current_word_chars.append(ch)
                    current_word_boxes.append(box)
            _finish_line()

            if not structured_lines:
                return None

            page_dir = "RTL" if total_rtl >= total_ltr else "LTR"
            return {
                "page_number": page_number,
                "psm_used": 6,
                "page_direction": page_dir,
                "mean_confidence": 99.9,
                "word_count": len(all_page_words),
                "words": all_page_words,
                "low_confidence_word_count": 0,
                "rtl_char_count": total_rtl,
                "ltr_char_count": total_ltr,
                "lines": structured_lines,
                "backend_used": "born_digital_stream",
            }
        except Exception:
            pass

        # 2. Fallback: pypdf
        try:
            import pypdf
            reader = pypdf.PdfReader(pdf_path)
            if page_number - 1 >= len(reader.pages):
                return None
            p = reader.pages[page_number - 1]
            raw_text = p.extract_text() or ""
            if len(raw_text.strip()) < threshold:
                return None

            raw_lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
            if not raw_lines:
                return None

            box = p.mediabox
            w_pt = float(box.width)
            h_pt = float(box.height)
            px_w = int(w_pt * scale)
            px_h = int(h_pt * scale)
            line_h = max(24, int(px_h / max(1, len(raw_lines) * 1.5)))

            structured_lines = []
            valid_confidences = []
            total_rtl = 0
            total_ltr = 0

            for l_idx, l_str in enumerate(raw_lines):
                tokens = l_str.split()
                line_words_pdf = []
                ly = int((l_idx + 1) * line_h * 1.1)
                for w_idx, tok in enumerate(tokens):
                    meta = self.analyze_token_script(tok)
                    total_rtl += meta["rtl_chars"]
                    total_ltr += meta["ltr_chars"]
                    valid_confidences.append(99.9)
                    line_words_pdf.append({
                        "word_index": w_idx,
                        "raw_text": tok,
                        "text": tok,
                        "confidence": 99.9,
                        "bbox": [50 + w_idx * 50, ly, 45, line_h],
                        "direction": meta["direction"],
                        "script": meta["script"],
                        "diacritics": meta["diacritics"],
                        "is_low_confidence": False,
                    })
                ldir = "RTL" if sum(1 for w in line_words_pdf if w["direction"] == "RTL") >= len(line_words_pdf) / 2 else "LTR"
                structured_lines.append({
                    "block_num": 1,
                    "par_num": 1,
                    "line_num": l_idx + 1,
                    "bbox": [50, ly, px_w - 100, line_h],
                    "direction": ldir,
                    "confidence": 99.9,
                    "words": self.sort_line_words_bidi(line_words_pdf, ldir),
                })

            pdir = "RTL" if total_rtl >= total_ltr else "LTR"
            return {
                "page_number": page_number,
                "psm_used": 6,
                "page_direction": pdir,
                "mean_confidence": 99.9,
                "word_count": len(valid_confidences),
                "low_confidence_word_count": 0,
                "rtl_char_count": total_rtl,
                "ltr_char_count": total_ltr,
                "lines": structured_lines,
                "backend_used": "born_digital_stream",
            }
        except Exception:
            return None

    def run_ocr_on_page(
        self,
        pil_image: Image.Image,
        page_number: int,
        illustrations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Execute OCR on the preprocessed page image, with automatic Vision LLM fallback
        when enabled and confidence is below threshold.
        """
        illustrations = illustrations or []

        # If purely Vision LLM backend is selected
        if self.config.ocr_backend == "vision_llm" and self.config.vision_llm_api_key:
            llm_result = self.run_vision_llm_ocr(pil_image, page_number)
            if llm_result is not None:
                return llm_result

        chosen_psm = self.auto_select_psm(pil_image)

        tess_args = [
            f"--oem {self.config.oem}",
            f"--psm {chosen_psm}",
            "-c preserve_interword_spaces=1",
        ]
        if self.config.tessdata_dir:
            tess_args.append(f'--tessdata-dir "{self.config.tessdata_dir}"')
        config_str = " ".join(tess_args)

        try:
            data = pytesseract.image_to_data(
                pil_image,
                lang=self.config.languages,
                config=config_str,
                output_type=pytesseract.Output.DICT,
            )
        except Exception as exc:
            if self.config.vision_llm_api_key:
                llm_result = self.run_vision_llm_ocr(pil_image, page_number)
                if llm_result is not None:
                    return llm_result
            raise RuntimeError(
                f"Tesseract OCR execution failed: {exc}. "
                "Please install Tesseract-OCR, place portable tesseract in ./tesseract, "
                "or set a Vision LLM API Key in Settings."
            )


        num_items = len(data.get("text", []))
        lines_map: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
        valid_confidences: List[float] = []
        total_rtl_chars = 0
        total_ltr_chars = 0

        for i in range(num_items):
            raw_text = (data["text"][i] or "").strip()
            conf_raw = data["conf"][i]
            try:
                conf = float(conf_raw)
            except (ValueError, TypeError):
                conf = -1.0

            if not raw_text or conf < 0:
                continue

            left = int(data["left"][i])
            top = int(data["top"][i])
            width = int(data["width"][i])
            height = int(data["height"][i])

            if self._overlaps_illustration((left, top, width, height), illustrations):
                continue

            block_num = int(data["block_num"][i])
            par_num = int(data["par_num"][i])
            line_num = int(data["line_num"][i])
            word_num = int(data["word_num"][i])

            script_meta = self.analyze_token_script(raw_text)
            total_rtl_chars += script_meta["rtl_chars"]
            total_ltr_chars += script_meta["ltr_chars"]
            valid_confidences.append(conf)

            word_entry = {
                "word_index": word_num,
                "raw_text": raw_text,
                "text": raw_text,
                "confidence": round(conf, 2),
                "bbox": [left, top, width, height],
                "direction": script_meta["direction"],
                "script": script_meta["script"],
                "diacritics": script_meta["diacritics"],
                "is_low_confidence": conf < self.config.confidence_threshold,
            }

            line_key = (block_num, par_num, line_num)
            if line_key not in lines_map:
                lines_map[line_key] = {
                    "block_num": block_num,
                    "par_num": par_num,
                    "line_num": line_num,
                    "words": [],
                }
            lines_map[line_key]["words"].append(word_entry)

        structured_lines: List[Dict[str, Any]] = []
        for line_key, line_obj in lines_map.items():
            words = line_obj["words"]
            if not words:
                continue

            rtl_w = sum(1 for w in words if w["direction"] == "RTL")
            ltr_w = sum(1 for w in words if w["direction"] == "LTR")
            line_dir = "RTL" if rtl_w >= ltr_w and rtl_w > 0 else ("LTR" if ltr_w > 0 else "RTL")

            bidi_sorted_words = self.sort_line_words_bidi(words, line_dir)

            x0 = min(w["bbox"][0] for w in words)
            y0 = min(w["bbox"][1] for w in words)
            x1 = max(w["bbox"][0] + w["bbox"][2] for w in words)
            y1 = max(w["bbox"][1] + w["bbox"][3] for w in words)
            line_conf = float(np.mean([w["confidence"] for w in words]))

            structured_lines.append(
                {
                    "block_num": line_obj["block_num"],
                    "par_num": line_obj["par_num"],
                    "line_num": line_obj["line_num"],
                    "bbox": [x0, y0, x1 - x0, y1 - y0],
                    "direction": line_dir,
                    "confidence": round(line_conf, 2),
                    "words": bidi_sorted_words,
                }
            )

        structured_lines.sort(key=lambda ln: ln["bbox"][1])

        page_dir = "RTL" if total_rtl_chars >= total_ltr_chars else "LTR"
        mean_conf = float(np.mean(valid_confidences)) if valid_confidences else 100.0
        low_conf_count = sum(
            1 for c in valid_confidences if c < self.config.confidence_threshold
        )

        tesseract_payload = {
            "page_number": page_number,
            "psm_used": chosen_psm,
            "page_direction": page_dir,
            "mean_confidence": round(mean_conf, 2),
            "word_count": len(valid_confidences),
            "low_confidence_word_count": low_conf_count,
            "rtl_char_count": total_rtl_chars,
            "ltr_char_count": total_ltr_chars,
            "lines": structured_lines,
            "backend_used": "tesseract",
        }

        # Hybrid check: if Tesseract struggled on this page, trigger Vision LLM fallback
        if (
            self.config.ocr_backend == "hybrid"
            and mean_conf < self.config.hybrid_confidence_threshold
            and self.config.vision_llm_api_key
        ):
            llm_result = self.run_vision_llm_ocr(pil_image, page_number)
            if llm_result is not None:
                return llm_result

        return tesseract_payload

    def recognize(
        self,
        image_or_path: Any,
        page_number: int = 1,
        illustrations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Universal recognition alias. Accepts either an open PIL.Image or a file path string.
        """
        if isinstance(image_or_path, str):
            with Image.open(image_or_path) as img:
                return self.run_ocr_on_page(img.convert("RGB"), page_number, illustrations)
        return self.run_ocr_on_page(image_or_path, page_number, illustrations)
