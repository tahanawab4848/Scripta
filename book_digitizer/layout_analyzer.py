import re
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
from config import AppConfig


class LayoutAnalyzer:
    """
    Reconstructs high-level book layout from OCR lines and page geometry:
    - Title page, Table of Contents (TOC)
    - Chapter headings and Section subheadings
    - Body paragraphs and Multi-column regions
    - Classical Poetry Couplets (Ash'ar / Hemistichs) vs Data Tables
    - Headers, Footers, and Page Numbers
    - Footnotes and Side notes (marginalia)
    - Structured multi-row Tables
    - Embedded Illustrations
    """

    PAGE_NUM_PATTERN = re.compile(
        r"^[\s\-–—\[\]()۰-۹٠-٩0-9ivxlcdmIVXLCDM]+$"
    )
    TOC_KEYWORDS = {
        "فہرست",
        "فهرست",
        "المحتويات",
        "محتويات",
        "contents",
        "table of contents",
    }
    FOOTNOTE_START_PATTERN = re.compile(
        r"^([(\[«*†‡]?[۰-۹٠-٩0-9]{1,3}[)\].»]|[*†‡])\s+"
    )

    def __init__(self, config: AppConfig):
        self.config = config

    def analyze(
        self,
        ocr_page: Dict[str, Any],
        page_number: Optional[int] = None,
        preproc_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Backward-compatible alias for analyze_page."""
        if preproc_meta is None:
            preproc_meta = {
                "page_number": page_number or ocr_page.get("page_number", 1),
                "width": ocr_page.get("page_width", ocr_page.get("width", 2400)),
                "height": ocr_page.get("page_height", ocr_page.get("height", 3400)),
                "illustrations": ocr_page.get("illustrations", []),
            }
        return self.analyze_page(ocr_page, preproc_meta)

    def analyze_page(
        self, ocr_page: Dict[str, Any], preproc_meta: Dict[str, Any]
    ) -> Dict[str, Any]:
        page_num = ocr_page["page_number"]
        page_w = max(1, int(preproc_meta.get("width", 2400)))
        page_h = max(1, int(preproc_meta.get("height", 3400)))
        page_dir = ocr_page.get("page_direction", "RTL")
        lines = ocr_page.get("lines", [])
        illustrations = preproc_meta.get("illustrations", [])

        heights = [ln["bbox"][3] for ln in lines if ln["bbox"][3] > 6]
        median_line_h = float(np.median(heights)) if heights else 36.0

        header_elements: List[Dict[str, Any]] = []
        footer_elements: List[Dict[str, Any]] = []
        page_number_text: str = ""
        side_notes: List[Dict[str, Any]] = []
        body_lines: List[Dict[str, Any]] = []

        top_zone = page_h * 0.085
        bottom_zone = page_h * 0.915
        left_margin_zone = page_w * 0.14
        right_margin_zone = page_w * 0.86

        for ln in lines:
            x, y, w, h = ln["bbox"]
            y_center = y + h / 2.0
            txt = ln.get("logical_text", "").strip()
            if not txt:
                continue

            # 1. Header & top page number detection
            if y_center <= top_zone and len(lines) > 2:
                if self.PAGE_NUM_PATTERN.match(txt) and len(txt) <= 10:
                    page_number_text = txt
                    header_elements.append(self._make_element("page_number", [ln], page_dir))
                else:
                    header_elements.append(self._make_element("header", [ln], page_dir))
                continue

            # 2. Footer & bottom page number detection
            if y_center >= bottom_zone and len(lines) > 2:
                if self.PAGE_NUM_PATTERN.match(txt) and len(txt) <= 10:
                    page_number_text = txt
                    footer_elements.append(self._make_element("page_number", [ln], page_dir))
                else:
                    footer_elements.append(self._make_element("footer", [ln], page_dir))
                continue

            # 3. Side notes / Marginalia (narrow block in outer margin)
            if w < page_w * 0.16 and (x + w < left_margin_zone or x > right_margin_zone):
                side_notes.append(self._make_element("side_note", [ln], ln["direction"]))
                continue

            body_lines.append(ln)

        # Check if entire page is a Title Page or Table of Contents
        is_title_page = self._detect_title_page(page_num, body_lines, page_h, median_line_h)
        is_toc_page = self._detect_toc_page(body_lines, page_w)

        elements: List[Dict[str, Any]] = []

        if is_title_page:
            for ln in body_lines:
                el = self._make_element("title_page_line", [ln], ln["direction"])
                elements.append(el)
        elif is_toc_page:
            elements.extend(self._build_toc_elements(body_lines, page_dir))
        else:
            # Separate footnotes at bottom of body
            main_lines, footnote_lines = self._split_footnotes(
                body_lines, page_h, median_line_h
            )

            # Check for multi-column regions
            column_groups = self._split_columns(main_lines, page_w, page_dir)

            for col_idx, col_lines in enumerate(column_groups):
                col_elements = self._segment_column_lines(
                    col_lines, page_w, median_line_h, page_dir, column_index=col_idx
                )
                elements.extend(col_elements)

            for fn_group in self._group_paragraphs(footnote_lines, median_line_h * 0.9):
                elements.append(self._make_element("footnote", fn_group, page_dir))

        # Interleave extracted illustrations at their vertical Y position
        for ill in illustrations:
            elements.append(
                {
                    "type": "illustration",
                    "bbox": ill["bbox"],
                    "direction": page_dir,
                    "image_path": ill["image_path"],
                    "width_px": ill["width_px"],
                    "height_px": ill["height_px"],
                    "logical_text": f"[Illustration: {ill['id']}]",
                    "flagged_logical_text": f"[Illustration: {ill['id']}]",
                    "visual_pdf_text": f"[Illustration: {ill['id']}]",
                    "words": [],
                }
            )

        # Interleave marginalia / side notes at their vertical Y position
        for sn in side_notes:
            elements.append(sn)

        # Sort main elements vertically (preserving column grouping order where applicable)
        elements.sort(key=lambda e: (e.get("column_index", 0), e["bbox"][1]))

        return {
            "page_number": page_num,
            "detected_page_number_label": page_number_text or str(page_num),
            "page_width": page_w,
            "page_height": page_h,
            "page_direction": page_dir,
            "is_title_page": is_title_page,
            "is_toc_page": is_toc_page,
            "column_count": len(column_groups) if not (is_title_page or is_toc_page) else 1,
            "mean_confidence": ocr_page.get("mean_confidence", 100.0),
            "word_count": ocr_page.get("word_count", 0),
            "low_confidence_word_count": ocr_page.get("low_confidence_word_count", 0),
            "flagged_words": ocr_page.get("flagged_words", []),
            "headers": header_elements,
            "footers": footer_elements,
            "side_notes": side_notes,
            "elements": elements,
        }

    def _detect_title_page(
        self,
        page_num: int,
        body_lines: List[Dict[str, Any]],
        page_h: int,
        median_h: float,
    ) -> bool:
        if page_num > 3 or not body_lines or len(body_lines) > 14:
            return False
        max_h = max(ln["bbox"][3] for ln in body_lines)
        return page_num == 1 or max_h >= median_h * 1.45

    def _detect_toc_page(
        self, body_lines: List[Dict[str, Any]], page_w: int
    ) -> bool:
        if not body_lines:
            return False
        sample_text = " ".join(
            ln.get("logical_text", "").lower() for ln in body_lines[:5]
        )
        if any(kw in sample_text for kw in self.TOC_KEYWORDS):
            return True

        # Check if >= 40% of lines end or start with numbers / leader dots
        toc_like = 0
        for ln in body_lines:
            txt = ln.get("logical_text", "").strip()
            if ".." in txt or re.search(r"(\s[۰-۹٠-٩0-9]{1,4}|^[۰-۹٠-٩0-9]{1,4}\s)", txt):
                toc_like += 1
        return len(body_lines) >= 6 and (toc_like / float(len(body_lines))) >= 0.55

    def _build_toc_elements(
        self, body_lines: List[Dict[str, Any]], page_dir: str
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for idx, ln in enumerate(body_lines):
            el_type = "chapter_heading" if idx == 0 else "toc_entry"
            results.append(self._make_element(el_type, [ln], ln.get("direction", page_dir)))
        return results

    def _split_footnotes(
        self,
        body_lines: List[Dict[str, Any]],
        page_h: int,
        median_h: float,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        if len(body_lines) < 4:
            return body_lines, []

        main_lines: List[Dict[str, Any]] = []
        footnote_lines: List[Dict[str, Any]] = []
        in_footnotes = False

        for i, ln in enumerate(body_lines):
            x, y, w, h = ln["bbox"]
            txt = ln.get("logical_text", "").strip()
            is_lower_quarter = y > page_h * 0.72
            is_smaller_font = h < median_h * 0.86
            starts_like_fn = bool(self.FOOTNOTE_START_PATTERN.match(txt))

            if not in_footnotes and is_lower_quarter and (starts_like_fn or is_smaller_font):
                # Check vertical gap from previous line
                if i > 0:
                    prev_bottom = body_lines[i - 1]["bbox"][1] + body_lines[i - 1]["bbox"][3]
                    if (y - prev_bottom) > median_h * 0.75 or starts_like_fn:
                        in_footnotes = True

            if in_footnotes:
                footnote_lines.append(ln)
            else:
                main_lines.append(ln)

        return main_lines, footnote_lines

    def _split_columns(
        self,
        lines: List[Dict[str, Any]],
        page_w: int,
        page_dir: str,
    ) -> List[List[Dict[str, Any]]]:
        """
        Adaptive column detector. Checks whether lines group into two non-overlapping
        vertical clusters separated by a central gutter, isolating spanned headers/banners.
        """
        if len(lines) < 6:
            return [lines]

        mid_x = page_w * 0.5
        left_col: List[Dict[str, Any]] = []
        right_col: List[Dict[str, Any]] = []
        spanning: List[Dict[str, Any]] = []

        for ln in lines:
            x, y, w, h = ln["bbox"]
            # Column elements cannot be excessively wide
            if w < page_w * 0.46 and (x + w) < mid_x * 1.05:
                left_col.append(ln)
            elif w < page_w * 0.46 and x > mid_x * 0.95:
                right_col.append(ln)
            else:
                spanning.append(ln)

        # Ensure genuine two-column distribution
        if len(left_col) >= 3 and len(right_col) >= 3 and len(spanning) <= len(lines) * 0.35:
            col_min_y = min(
                min(ln["bbox"][1] for ln in left_col),
                min(ln["bbox"][1] for ln in right_col),
            )
            col_max_y = max(
                max(ln["bbox"][1] + ln["bbox"][3] for ln in left_col),
                max(ln["bbox"][1] + ln["bbox"][3] for ln in right_col),
            )

            top_spanning = [ln for ln in spanning if ln["bbox"][1] < col_min_y]
            bottom_spanning = [ln for ln in spanning if ln["bbox"][1] >= col_max_y]
            mid_spanning = [ln for ln in spanning if col_min_y <= ln["bbox"][1] < col_max_y]

            # In RTL: Right column is read first, then Left column
            primary_col = right_col if page_dir == "RTL" else left_col
            secondary_col = left_col if page_dir == "RTL" else right_col

            if mid_spanning:
                primary_col = mid_spanning + primary_col

            groups = []
            if top_spanning:
                groups.append(top_spanning)
            groups.append(primary_col)
            groups.append(secondary_col)
            if bottom_spanning:
                groups.append(bottom_spanning)
            return groups

        return [lines]


    def _is_poetry_couplet(
        self, line: Dict[str, Any], page_w: int
    ) -> Tuple[bool, List[str]]:
        """
        Distinguishes poetry couplets (Ash'ar / Hemistichs) from data tables:
        - Exactly two hemistichs (misras) separated by a central gap.
        - Balanced word counts (typically 2-10 words per hemistich).
        - Free from numeric/currency tabular columns.
        """
        if not self.config.enable_poetry_detection:
            return False, []

        is_split, cells = self._is_table_row(line, page_w)
        if not is_split or len(cells) != 2:
            return False, []

        w1 = cells[0].split()
        w2 = cells[1].split()
        max_words = self.config.poetry_max_hemistich_words

        if not (2 <= len(w1) <= max_words and 2 <= len(w2) <= max_words):
            return False, []

        # Avoid table rows with purely numbers / serials
        for c in (cells[0], cells[1]):
            if re.match(r"^[\s\d۰-۹٠-٩.,:\-–]+$", c.strip()):
                return False, []

        return True, cells

    def _consume_poetry_lines(
        self, lines: List[Dict[str, Any]], start_idx: int, page_w: int
    ) -> Tuple[List[Tuple[Dict[str, Any], List[str]]], int]:
        collected: List[Tuple[Dict[str, Any], List[str]]] = []
        idx = start_idx
        while idx < len(lines):
            is_poem, hemistichs = self._is_poetry_couplet(lines[idx], page_w)
            if not is_poem:
                break
            collected.append((lines[idx], hemistichs))
            idx += 1
        return collected, idx

    def _build_poetry_element(
        self,
        poetry_rows: List[Tuple[Dict[str, Any], List[str]]],
        page_dir: str,
        column_index: int,
    ) -> Dict[str, Any]:
        raw_lines = [r[0] for r in poetry_rows]
        couplets = [
            {"misra_1": r[1][0], "misra_2": r[1][1]} for r in poetry_rows
        ]
        el = self._make_element("poetry", raw_lines, page_dir, column_index)
        el["couplets"] = couplets
        # For plain text / fallback
        el["flagged_logical_text"] = "\n".join(
            f"{c['misra_1']}   ...   {c['misra_2']}" for c in couplets
        )
        return el

    def _segment_column_lines(
        self,
        lines: List[Dict[str, Any]],
        page_w: int,
        median_h: float,
        page_dir: str,
        column_index: int = 0,
    ) -> List[Dict[str, Any]]:
        if not lines:
            return []

        elements: List[Dict[str, Any]] = []
        idx = 0
        n = len(lines)

        while idx < n:
            ln = lines[idx]
            x, y, w, h = ln["bbox"]

            # 1. Poetry Couplets Check (takes precedence over table if 2 balanced hemistichs)
            poetry_lines, next_p_idx = self._consume_poetry_lines(lines, idx, page_w)
            if len(poetry_lines) >= 1:
                poetry_el = self._build_poetry_element(poetry_lines, page_dir, column_index)
                elements.append(poetry_el)
                idx = next_p_idx
                continue

            # 2. Check if current line starts a genuine Table block (3+ columns or tabular numbers)
            table_lines, next_t_idx = self._consume_table_lines(lines, idx, page_w)
            if len(table_lines) >= 3:
                table_el = self._build_table_element(table_lines, page_dir, column_index)
                elements.append(table_el)
                idx = next_t_idx
                continue

            # 3. Interlinear Translation Pair (Arabic verse with tight smaller-font Urdu/Persian gloss)
            if (
                getattr(self.config, "enable_interlinear_detection", True)
                and idx + 1 < n
            ):
                nxt = lines[idx + 1]
                nx, ny, nw, nh = nxt["bbox"]
                v_gap = ny - (y + h)
                x_overlap = max(0, min(x + w, nx + nw) - max(x, nx))
                is_sub_line = (
                    nh <= h * 0.74
                    and v_gap <= median_h * 0.60
                    and x_overlap >= min(w, nw) * 0.45
                )
                if is_sub_line:
                    el = self._make_element("interlinear_pair", [ln, nxt], ln["direction"], column_index)
                    el["primary_text"] = ln.get("logical_text", "")
                    el["gloss_text"] = nxt.get("logical_text", "")
                    el["flagged_logical_text"] = f"{el['primary_text']}\n   ↳ [ترجمہ / Gloss]: {el['gloss_text']}"
                    elements.append(el)
                    idx += 2
                    continue

            # 4. Check for Chapter or Section Heading based on font height & isolation
            if h >= median_h * 1.38 and len(ln.get("words", [])) <= 16:
                el = self._make_element("chapter_heading", [ln], ln["direction"], column_index)
                elements.append(el)
                idx += 1
                continue
            elif h >= median_h * 1.18 and w < page_w * 0.65 and len(ln.get("words", [])) <= 14:
                el = self._make_element("section_heading", [ln], ln["direction"], column_index)
                elements.append(el)
                idx += 1
                continue

            # 5. Group consecutive body lines into paragraphs
            para_lines = [ln]
            idx += 1


            while idx < n:
                nxt = lines[idx]
                nx, ny, nw, nh = nxt["bbox"]
                prev = para_lines[-1]
                px, py, pw, ph = prev["bbox"]

                v_gap = ny - (py + ph)
                is_heading_size = nh >= median_h * 1.22
                is_table_candidate, _ = self._is_table_row(nxt, page_w)
                is_poetry_candidate, _ = self._is_poetry_couplet(nxt, page_w)

                if v_gap > median_h * 0.85 or is_heading_size or is_table_candidate or is_poetry_candidate:
                    break

                para_lines.append(nxt)
                idx += 1

            elements.append(
                self._make_element("paragraph", para_lines, page_dir, column_index)
            )

        return elements

    def _is_table_row(
        self, line: Dict[str, Any], page_w: int
    ) -> Tuple[bool, List[str]]:
        """
        Detect whether a line has multiple wide horizontal gaps between word clusters,
        characteristic of tabular columns (both bordered and whitespace-aligned borderless tables).
        """
        words = sorted(line.get("words", []), key=lambda w: w["bbox"][0])
        if len(words) < 2:
            return False, []

        gaps = [
            curr_w["bbox"][0] - (prev_w["bbox"][0] + prev_w["bbox"][2])
            for prev_w, curr_w in zip(words, words[1:])
        ]
        positive_gaps = [g for g in gaps if g > 0]
        median_gap = float(np.median(positive_gaps)) if positive_gaps else 15.0

        # Adaptive threshold: column gap is significantly wider than standard inter-word spacing
        min_col_gap = max(26, int(median_gap * 3.2), int(page_w * 0.024))

        cells: List[List[Dict[str, Any]]] = [[words[0]]]
        for prev_w, curr_w in zip(words, words[1:]):
            gap = curr_w["bbox"][0] - (prev_w["bbox"][0] + prev_w["bbox"][2])
            if gap >= min_col_gap:
                cells.append([curr_w])
            else:
                cells[-1].append(curr_w)

        if len(cells) < 2:
            return False, []

        if line.get("direction", "RTL") == "RTL":
            cells.reverse()

        cell_strings = [
            " ".join(w.get("flagged_text", w["text"]) for w in cell) for cell in cells
        ]
        return True, cell_strings

    def _consume_table_lines(
        self, lines: List[Dict[str, Any]], start_idx: int, page_w: int
    ) -> Tuple[List[Tuple[Dict[str, Any], List[str]]], int]:
        collected: List[Tuple[Dict[str, Any], List[str]]] = []
        idx = start_idx
        while idx < len(lines):
            # If this is poetry, stop table consumption
            is_poem, _ = self._is_poetry_couplet(lines[idx], page_w)
            if is_poem:
                break

            is_row, cells = self._is_table_row(lines[idx], page_w)
            if not is_row:
                break
            collected.append((lines[idx], cells))
            idx += 1
        return collected, idx

    def _build_table_element(
        self,
        table_rows: List[Tuple[Dict[str, Any], List[str]]],
        page_dir: str,
        column_index: int,
    ) -> Dict[str, Any]:
        raw_lines = [r[0] for r in table_rows]
        max_cols = max(len(r[1]) for r in table_rows)
        grid: List[List[str]] = []
        for _, row_cells in table_rows:
            padded = row_cells + [""] * (max_cols - len(row_cells))
            grid.append(padded)

        el = self._make_element("table", raw_lines, page_dir, column_index)
        el["table_rows"] = grid
        return el

    def _group_paragraphs(
        self, lines: List[Dict[str, Any]], gap_threshold: float
    ) -> List[List[Dict[str, Any]]]:
        if not lines:
            return []
        groups: List[List[Dict[str, Any]]] = [[lines[0]]]
        for prev, curr in zip(lines, lines[1:]):
            gap = curr["bbox"][1] - (prev["bbox"][1] + prev["bbox"][3])
            if gap > gap_threshold:
                groups.append([curr])
            else:
                groups[-1].append(curr)
        return groups

    @staticmethod
    def _make_element(
        el_type: str,
        lines: List[Dict[str, Any]],
        direction: str,
        column_index: int = 0,
    ) -> Dict[str, Any]:
        x0 = min(ln["bbox"][0] for ln in lines)
        y0 = min(ln["bbox"][1] for ln in lines)
        x1 = max(ln["bbox"][0] + ln["bbox"][2] for ln in lines)
        y1 = max(ln["bbox"][1] + ln["bbox"][3] for ln in lines)

        all_words = [w for ln in lines for w in ln.get("words", [])]
        rtl_w = sum(1 for w in all_words if w.get("direction") == "RTL")
        ltr_w = sum(1 for w in all_words if w.get("direction") == "LTR")
        resolved_dir = "RTL" if rtl_w >= ltr_w and rtl_w > 0 else (
            "LTR" if ltr_w > 0 else direction
        )

        logical_text = " ".join(ln.get("logical_text", "") for ln in lines).strip()
        flagged_text = " ".join(ln.get("flagged_logical_text", "") for ln in lines).strip()
        clean_pdf_lines = [ln.get("clean_visual_pdf_text", ln.get("visual_pdf_text", "")) for ln in lines]
        visual_pdf_lines = [ln.get("visual_pdf_text", "") for ln in lines]

        ends_with_terminal = bool(re.search(r"[۔.!؟:;\n]\s*$", logical_text))
        starts_with_fn_marker = bool(re.match(r"^([(\[«*†‡]?[۰-۹٠-٩0-9]{1,3}[)\].»]|[*†‡])\s+", logical_text))

        return {
            "type": el_type,
            "bbox": [x0, y0, x1 - x0, y1 - y0],
            "direction": resolved_dir,
            "column_index": column_index,
            "logical_text": logical_text,
            "flagged_logical_text": flagged_text,
            "clean_visual_pdf_lines": clean_pdf_lines,
            "visual_pdf_lines": visual_pdf_lines,
            "words": all_words,
            "ends_with_terminal": ends_with_terminal,
            "starts_with_fn_marker": starts_with_fn_marker,
        }
