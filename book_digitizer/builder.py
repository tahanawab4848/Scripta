import html
import os
import re
from typing import Dict, Any, List, Tuple
import docx
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

_ILLEGAL_XML_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f\ufffe\uffff]"
)


def _clean_xml(text: Any) -> str:
    """Strip XML-incompatible control characters that crash python-docx and lxml."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return _ILLEGAL_XML_CHARS_RE.sub("", text)

from ebooklib import epub
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph as RLParagraph,
    SimpleDocTemplate,
    Spacer,
    Table as RLTable,
    TableStyle,
)
from config import AppConfig


class BookBuilder:
    """
    Reconstructs the complete book from analyzed page structures into:
    1. Formatted DOCX (with OpenXML <w:bidi/> RTL support, contiguous run grouping
       for cursive ligatures, yellow-highlighted low-conf spans, poetry blocks, tables, and images).
    2. Searchable Unicode PDF (via ReportLab with reshaped BiDi RTL glyphs).
    3. Reflowable EPUB3 (with RTL CSS, poetry formatting, and navigable chapters).
    4. Clean UTF-8 .txt replica.
    """

    def __init__(self, config: AppConfig):
        self.config = config

    def _stitch_cross_page_footnotes(self, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stitch broken footnotes that start on page N and continue onto page N+1."""
        if not self.config.enable_cross_page_footnote_stitching or len(pages) < 2:
            return pages

        for i in range(len(pages) - 1):
            curr_p = pages[i]
            next_p = pages[i + 1]

            curr_fn = [e for e in curr_p.get("elements", []) if e.get("type") == "footnote"]
            next_fn = [e for e in next_p.get("elements", []) if e.get("type") == "footnote"]

            if not curr_fn or not next_fn:
                continue

            last_fn = curr_fn[-1]
            first_next_fn = next_fn[0]

            if not last_fn.get("ends_with_terminal", True) and not first_next_fn.get("starts_with_fn_marker", False):
                last_fn["logical_text"] += " " + first_next_fn.get("logical_text", "")
                last_fn["flagged_logical_text"] += " " + first_next_fn.get("flagged_logical_text", "")
                last_fn["words"].extend(first_next_fn.get("words", []))
                next_p["elements"] = [e for e in next_p["elements"] if e is not first_next_fn]

        return pages

    def build_all(self, pages_layout: List[Dict[str, Any]]) -> Dict[str, str]:
        """Generate all enabled output formats and return a dictionary of output file paths."""
        pages_sorted = sorted(pages_layout, key=lambda p: p["page_number"])
        pages_stitched = self._stitch_cross_page_footnotes(pages_sorted)
        outputs: Dict[str, str] = {}
        import shutil

        book_base = os.path.splitext(os.path.basename(self.config.input_pdf_path))[0] if self.config.input_pdf_path else "digitized_book"
        safe_prefix = re.sub(r'[\\/*?:"<>|]', "", book_base).strip() or "digitized_book"

        # 1. Searchable Facsimile ('Sandwich') PDF: Scan image + invisible selectable OCR text
        if getattr(self.config, "export_sandwich_pdf", True):
            sandwich_path = os.path.join(self.config.output_dir, f"{safe_prefix}_sandwich.pdf")
            self.build_sandwich_pdf(pages_stitched, sandwich_path)
            std_path = os.path.join(self.config.output_dir, "digitized_book_sandwich.pdf")
            if sandwich_path != std_path:
                try:
                    shutil.copyfile(sandwich_path, std_path)
                except Exception:
                    pass
            outputs["sandwich_pdf"] = sandwich_path

        # 2. Formatted DOCX
        if self.config.export_docx:
            docx_path = os.path.join(self.config.output_dir, f"{safe_prefix}.docx")
            self.build_docx(pages_stitched, docx_path)
            std_docx = os.path.join(self.config.output_dir, "digitized_book.docx")
            if docx_path != std_docx:
                try:
                    shutil.copyfile(docx_path, std_docx)
                except Exception:
                    pass
            outputs["docx"] = docx_path

        # 3. Reflowable Searchable PDF
        if self.config.export_pdf:
            pdf_path = os.path.join(self.config.output_dir, f"{safe_prefix}_reflowable.pdf")
            self.build_pdf(pages_stitched, pdf_path)
            std_pdf = os.path.join(self.config.output_dir, "digitized_book_reflowable.pdf")
            if pdf_path != std_pdf:
                try:
                    shutil.copyfile(pdf_path, std_pdf)
                except Exception:
                    pass
            outputs["pdf"] = pdf_path

        # 4. EPUB3 eBook
        if self.config.export_epub:
            epub_path = os.path.join(self.config.output_dir, f"{safe_prefix}.epub")
            self.build_epub(pages_stitched, epub_path)
            std_epub = os.path.join(self.config.output_dir, "digitized_book.epub")
            if epub_path != std_epub:
                try:
                    shutil.copyfile(epub_path, std_epub)
                except Exception:
                    pass
            outputs["epub"] = epub_path

        # 5. Clean UTF-8 TXT
        if self.config.export_txt:
            txt_path = os.path.join(self.config.output_dir, f"{safe_prefix}.txt")
            self.build_txt(pages_stitched, txt_path)
            std_txt = os.path.join(self.config.output_dir, "digitized_book.txt")
            if txt_path != std_txt:
                try:
                    shutil.copyfile(txt_path, std_txt)
                except Exception:
                    pass
            outputs["txt"] = txt_path

        # 6. HTML5 Print Replica
        if self.config.export_html_weasyprint:
            html_path = os.path.join(self.config.output_dir, f"{safe_prefix}_replica.html")
            self.build_html_replica(pages_stitched, html_path)
            std_html = os.path.join(self.config.output_dir, "digitized_book_replica.html")
            if html_path != std_html:
                try:
                    shutil.copyfile(html_path, std_html)
                except Exception:
                    pass
            outputs["html_replica"] = html_path

        # 7. Library of Congress ALTO XML 4.2 (Archival preservation standard)
        if getattr(self.config, "export_alto_xml", True):
            alto_path = os.path.join(self.config.output_dir, f"{safe_prefix}_alto.xml")
            self.build_alto_xml(pages_stitched, alto_path)
            std_alto = os.path.join(self.config.output_dir, "digitized_book_alto.xml")
            if alto_path != std_alto:
                try:
                    shutil.copyfile(alto_path, std_alto)
                except Exception:
                    pass
            outputs["alto_xml"] = alto_path

        # 8. PRImA PageXML 2019-07-15 (Digital Humanities & Institutional Library standard)
        if getattr(self.config, "export_page_xml", True):
            pagexml_dir = os.path.join(self.config.output_dir, "page_xml")
            self.build_page_xml(pages_stitched, pagexml_dir)
            outputs["page_xml"] = pagexml_dir

        return outputs


    # ------------------------------------------------------------------
    # 1. DOCX BUILDER (python-docx + OpenXML BiDi & Run Grouping)
    # ------------------------------------------------------------------
    @staticmethod
    def _set_docx_paragraph_bidi(paragraph: docx.text.paragraph.Paragraph, is_rtl: bool) -> None:
        pPr = paragraph._p.get_or_add_pPr()
        bidi = OxmlElement("w:bidi")
        bidi.set(qn("w:val"), "1" if is_rtl else "0")
        pPr.append(bidi)
        paragraph.alignment = (
            WD_ALIGN_PARAGRAPH.RIGHT if is_rtl else WD_ALIGN_PARAGRAPH.LEFT
        )

    def _style_docx_run(
        self,
        run: docx.text.run.Run,
        is_rtl: bool,
        font_size_pt: float,
        bold: bool = False,
        is_low_conf: bool = False,
    ) -> None:
        run.bold = bold
        run.font.size = Pt(font_size_pt)
        font_name = self.config.rtl_font_name if is_rtl else self.config.ltr_font_name
        run.font.name = font_name

        rPr = run._r.get_or_add_rPr()
        rFonts = rPr.get_or_add_rFonts()
        rFonts.set(qn("w:cs"), self.config.rtl_font_name)
        rFonts.set(qn("w:ascii"), self.config.ltr_font_name)
        rFonts.set(qn("w:hAnsi"), self.config.ltr_font_name)

        if is_rtl:
            rtl_el = OxmlElement("w:rtl")
            rtl_el.set(qn("w:val"), "1")
            rPr.append(rtl_el)

        if is_low_conf:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW

    def _populate_docx_paragraph_words(
        self,
        paragraph: docx.text.paragraph.Paragraph,
        element: Dict[str, Any],
        font_size_pt: float,
        bold: bool = False,
    ) -> None:
        is_para_rtl = element.get("direction", "RTL") == "RTL"
        self._set_docx_paragraph_bidi(paragraph, is_para_rtl)

        words = element.get("words", [])
        if not words:
            run = paragraph.add_run(element.get("flagged_logical_text", ""))
            self._style_docx_run(run, is_para_rtl, font_size_pt, bold=bold)
            return

        if not self.config.docx_group_runs:
            for idx, w in enumerate(words):
                token_str = w.get("flagged_text", w.get("text", ""))
                if idx < len(words) - 1:
                    token_str += " "
                w_rtl = w.get("direction", element.get("direction", "RTL")) == "RTL"
                run = paragraph.add_run(token_str)
                self._style_docx_run(
                    run,
                    is_rtl=w_rtl,
                    font_size_pt=font_size_pt,
                    bold=bold,
                    is_low_conf=bool(w.get("is_low_confidence", False)),
                )
            return

        # Optimization: Group adjacent words with identical styling properties into contiguous runs.
        # This preserves native OpenType cursive attachment (e.g. Nastaliq/Arabic) in Microsoft Word.
        chunks: List[Tuple[str, bool, bool]] = []
        for idx, w in enumerate(words):
            token_str = w.get("flagged_text", w.get("text", ""))
            if idx < len(words) - 1:
                token_str += " "
            w_rtl = w.get("direction", element.get("direction", "RTL")) == "RTL"
            is_low = bool(w.get("is_low_confidence", False))

            if chunks and chunks[-1][1] == w_rtl and chunks[-1][2] == is_low:
                # Merge into previous run
                prev_text, prev_rtl, prev_low = chunks.pop()
                chunks.append((prev_text + token_str, prev_rtl, prev_low))
            else:
                chunks.append((token_str, w_rtl, is_low))

        for chunk_text, chunk_rtl, chunk_low in chunks:
            run = paragraph.add_run(_clean_xml(chunk_text))
            self._style_docx_run(
                run,
                is_rtl=chunk_rtl,
                font_size_pt=font_size_pt,
                bold=bold,
                is_low_conf=chunk_low,
            )

    def build_docx(self, pages: List[Dict[str, Any]], output_path: str) -> None:
        doc = Document()

        for section in doc.sections:
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)
            section.left_margin = Inches(0.85)
            section.right_margin = Inches(0.85)

        for p_idx, page in enumerate(pages):
            page_dir = page.get("page_direction", "RTL")
            page_label = page.get("detected_page_number_label", str(page["page_number"]))

            # Render page header metadata if present
            if page.get("headers"):
                hdr_text = " | ".join(h["logical_text"] for h in page["headers"] if h.get("logical_text"))
                if hdr_text:
                    hp = doc.add_paragraph()
                    self._set_docx_paragraph_bidi(hp, page_dir == "RTL")
                    hrun = hp.add_run(_clean_xml(hdr_text))
                    self._style_docx_run(hrun, page_dir == "RTL", font_size_pt=9.5)
                    hrun.font.color.rgb = RGBColor(110, 110, 110)

            for el in page.get("elements", []):
                el_type = el["type"]

                if el_type == "illustration":
                    img_path = el.get("image_path", "")
                    if img_path and os.path.exists(img_path):
                        pic_p = doc.add_paragraph()
                        pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        max_w_in = min(5.5, max(1.5, el.get("width_px", 600) / float(self.config.dpi)))
                        pic_p.add_run().add_picture(img_path, width=Inches(max_w_in))
                    continue

                if el_type == "poetry":
                    # Format authentic poetry couplets (Ash'ar)
                    couplets = el.get("couplets", [])
                    for c in couplets:
                        p_poem = doc.add_paragraph()
                        p_poem.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        p_poem.paragraph_format.space_before = Pt(3)
                        p_poem.paragraph_format.space_after = Pt(3)
                        self._set_docx_paragraph_bidi(p_poem, page_dir == "RTL")
                        verse_text = f"{c.get('misra_1', '')}   —   {c.get('misra_2', '')}"
                        v_run = p_poem.add_run(_clean_xml(verse_text))
                        self._style_docx_run(v_run, page_dir == "RTL", font_size_pt=13.0, bold=False)
                    continue

                if el_type == "interlinear_pair":
                    p_p = doc.add_paragraph()
                    self._set_docx_paragraph_bidi(p_p, page_dir == "RTL")
                    p_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if page_dir == "RTL" else WD_ALIGN_PARAGRAPH.LEFT
                    run_top = p_p.add_run(_clean_xml(el.get("primary_text", "")))
                    self._style_docx_run(run_top, page_dir == "RTL", font_size_pt=13.5, bold=True)

                    p_g = doc.add_paragraph()
                    self._set_docx_paragraph_bidi(p_g, page_dir == "RTL")
                    p_g.alignment = WD_ALIGN_PARAGRAPH.RIGHT if page_dir == "RTL" else WD_ALIGN_PARAGRAPH.LEFT
                    p_g.paragraph_format.space_after = Pt(6)
                    run_sub = p_g.add_run(_clean_xml(f"↳ {el.get('gloss_text', '')}"))
                    self._style_docx_run(run_sub, page_dir == "RTL", font_size_pt=9.5, italic=True)
                    run_sub.font.color.rgb = RGBColor(90, 90, 90)
                    continue

                if el_type == "side_note":

                    sp = doc.add_paragraph()
                    self._set_docx_paragraph_bidi(sp, page_dir == "RTL")
                    sp.paragraph_format.left_indent = Inches(0.4)
                    sp.paragraph_format.right_indent = Inches(0.4)
                    s_run = sp.add_run(_clean_xml(f"[Note: {el.get('logical_text', '')}]"))
                    self._style_docx_run(s_run, page_dir == "RTL", font_size_pt=10.0)
                    s_run.font.italic = True
                    s_run.font.color.rgb = RGBColor(100, 100, 100)
                    continue

                if el_type == "table":
                    rows_data = el.get("table_rows", [])
                    if rows_data:
                        num_cols = max(len(r) for r in rows_data)
                        tbl = doc.add_table(rows=len(rows_data), cols=num_cols)
                        tbl.style = "Table Grid"
                        for r_i, row in enumerate(rows_data):
                            for c_i, cell_val in enumerate(row):
                                cell_p = tbl.cell(r_i, c_i).paragraphs[0]
                                is_rtl = el.get("direction", "RTL") == "RTL"
                                self._set_docx_paragraph_bidi(cell_p, is_rtl)
                                run = cell_p.add_run(_clean_xml(cell_val))
                                self._style_docx_run(run, is_rtl, font_size_pt=11.0)
                    continue

                if el_type == "title_page_line":
                    p = doc.add_paragraph()
                    self._populate_docx_paragraph_words(p, el, font_size_pt=20.0, bold=True)
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif el_type == "chapter_heading":
                    p = doc.add_paragraph()
                    p.paragraph_format.space_before = Pt(14)
                    p.paragraph_format.space_after = Pt(6)
                    self._populate_docx_paragraph_words(p, el, font_size_pt=16.5, bold=True)
                elif el_type == "section_heading":
                    p = doc.add_paragraph()
                    p.paragraph_format.space_before = Pt(10)
                    p.paragraph_format.space_after = Pt(4)
                    self._populate_docx_paragraph_words(p, el, font_size_pt=14.0, bold=True)
                elif el_type == "toc_entry":
                    p = doc.add_paragraph()
                    self._populate_docx_paragraph_words(p, el, font_size_pt=12.0)
                elif el_type == "footnote":
                    p = doc.add_paragraph()
                    p.paragraph_format.space_before = Pt(4)
                    self._populate_docx_paragraph_words(p, el, font_size_pt=9.5)
                else:
                    p = doc.add_paragraph()
                    p.paragraph_format.space_after = Pt(6)
                    p.paragraph_format.line_spacing = 1.35
                    self._populate_docx_paragraph_words(p, el, font_size_pt=12.5)

            # Side notes
            for sn in page.get("side_notes", []):
                sp = doc.add_paragraph()
                self._populate_docx_paragraph_words(sp, sn, font_size_pt=9.5)

            # Footer & Original Scan Page Number
            footer_parts = [
                f["logical_text"]
                for f in page.get("footers", [])
                if f.get("type") != "page_number" and f.get("logical_text")
            ]
            footer_parts.append(f"— Page {page_label} (Scan #{page['page_number']}) —")
            fp = doc.add_paragraph()
            fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            frun = fp.add_run(_clean_xml(" | ".join(footer_parts)))
            self._style_docx_run(frun, page_dir == "RTL", font_size_pt=9.0)
            frun.font.color.rgb = RGBColor(120, 120, 120)

            if p_idx < len(pages) - 1:
                doc.add_page_break()

        doc.save(output_path)

    # ------------------------------------------------------------------
    # 2. SEARCHABLE PDF BUILDER (ReportLab Platypus + Visual BiDi RTL)
    # ------------------------------------------------------------------
    def _register_pdf_font(self) -> str:
        # 1. Configured font path
        font_path = self.config.pdf_ttf_font_path
        if font_path and os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont("BookUnicodeFont", font_path))
                return "BookUnicodeFont"
            except Exception:
                pass

        # 2. Bundled project fonts (guaranteed authentic RTL Nastaliq / Naskh)
        module_dir = os.path.dirname(os.path.abspath(__file__))
        fonts_dir = os.path.join(module_dir, "fonts")
        candidates = [
            os.path.join(fonts_dir, "NotoNastaliqUrdu-Regular.ttf"),
            os.path.join(fonts_dir, "Amiri-Regular.ttf"),
            os.path.join(fonts_dir, "NotoNaskhArabic-Regular.ttf"),
            os.path.join(fonts_dir, "Vazirmatn-Regular.ttf"),
        ]
        for cand in candidates:
            if os.path.exists(cand):
                try:
                    pdfmetrics.registerFont(TTFont("BookUnicodeFont", cand))
                    return "BookUnicodeFont"
                except Exception:
                    continue

        return "Helvetica"


    def build_pdf(self, pages: List[Dict[str, Any]], output_path: str) -> None:
        font_name = self._register_pdf_font()
        styles = getSampleStyleSheet()

        rtl_body = ParagraphStyle(
            "RTLBody",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=12,
            leading=18,
            alignment=2,  # Right align
            spaceAfter=8,
        )
        ltr_body = ParagraphStyle(
            "LTRBody",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=12,
            leading=16,
            alignment=0,  # Left align
            spaceAfter=8,
        )
        rtl_heading = ParagraphStyle(
            "RTLHeading",
            parent=rtl_body,
            fontSize=16,
            leading=22,
            spaceBefore=12,
            spaceAfter=8,
        )
        ltr_heading = ParagraphStyle(
            "LTRHeading",
            parent=ltr_body,
            fontSize=16,
            leading=22,
            spaceBefore=12,
            spaceAfter=8,
        )
        center_poetry = ParagraphStyle(
            "CenterPoetry",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=12.5,
            leading=19,
            alignment=1,  # Center align
            spaceBefore=4,
            spaceAfter=4,
        )
        center_meta = ParagraphStyle(
            "CenterMeta",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=12,
            alignment=1,
            textColor=colors.grey,
        )

        story: List[Any] = []
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=0.75 * inch,
            rightMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
            title=self.config.book_title,
            author=self.config.book_author,
        )

        for p_idx, page in enumerate(pages):
            for el in page.get("elements", []):
                el_type = el["type"]
                is_rtl = el.get("direction", "RTL") == "RTL"

                if el_type == "illustration":
                    img_path = el.get("image_path", "")
                    if img_path and os.path.exists(img_path):
                        w_pt = min(400.0, el.get("width_px", 400) * 72.0 / self.config.dpi)
                        h_pt = min(500.0, el.get("height_px", 300) * 72.0 / self.config.dpi)
                        story.append(RLImage(img_path, width=w_pt, height=h_pt))
                        story.append(Spacer(1, 8))
                    continue

                if el_type == "poetry":
                    for c in el.get("couplets", []):
                        m1 = html.escape(c.get("misra_1", ""))
                        m2 = html.escape(c.get("misra_2", ""))
                        poem_line = f"{m1} &nbsp;&nbsp;—&nbsp;&nbsp; {m2}"
                        story.append(RLParagraph(poem_line, center_poetry))
                    story.append(Spacer(1, 6))
                    continue

                if el_type == "table":
                    rows = el.get("table_rows", [])
                    if rows:
                        rl_tbl = RLTable(rows)
                        rl_tbl.setStyle(
                            TableStyle(
                                [
                                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                    ("PADDING", (0, 0), (-1, -1), 5),
                                ]
                            )
                        )
                        story.append(rl_tbl)
                        story.append(Spacer(1, 8))
                    continue

                # Use clean visual lines to prevent BiDi inversion artifacts
                visual_lines = el.get("clean_visual_pdf_lines") or el.get("visual_pdf_lines") or [
                    el.get("logical_text", "")
                ]
                escaped_lines = [html.escape(line) for line in visual_lines if line]
                joined_html = "<br/>".join(escaped_lines)

                if el_type in ("title_page_line", "chapter_heading", "section_heading"):
                    style = rtl_heading if is_rtl else ltr_heading
                else:
                    style = rtl_body if is_rtl else ltr_body

                story.append(RLParagraph(joined_html, style))

            page_label = html.escape(
                str(page.get("detected_page_number_label", page["page_number"]))
            )
            story.append(Spacer(1, 14))
            story.append(
                RLParagraph(
                    f"— Page {page_label} (Scan #{page['page_number']}) —", center_meta
                )
            )
            if p_idx < len(pages) - 1:
                story.append(PageBreak())

        doc.build(story)

    def _render_sandwich_chunk(
        self,
        pages: List[Dict[str, Any]],
        chunk_path: str,
        font_name: str,
        dpi: int,
    ) -> None:
        """Render a single batch/chunk of sandwich facsimile pages."""
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(chunk_path)

        for page in pages:
            pnum = page["page_number"]
            page_w_px = page.get("page_width", 2400)
            page_h_px = page.get("page_height", 3400)

            # Target page size in PDF points
            w_pt = page_w_px * 72.0 / dpi
            h_pt = page_h_px * 72.0 / dpi
            c.setPageSize((w_pt, h_pt))

            # Locate scan image preview
            img_candidates = [
                os.path.join(self.config.extracted_images_dir, f"page_{pnum:05d}_preview.jpg"),
                os.path.join(self.config.extracted_images_dir, f"page_{pnum:05d}_preview.png"),
                os.path.join(self.config.extracted_images_dir, f"preview_p{pnum:05d}_proc.jpg"),
                os.path.join(self.config.extracted_images_dir, f"preview_p{pnum:05d}_proc.png"),
            ]
            img_path = next((p for p in img_candidates if os.path.exists(p)), "")

            # 1. Draw background scan image
            if img_path:
                try:
                    c.drawImage(img_path, 0, 0, width=w_pt, height=h_pt, preserveAspectRatio=True)
                except Exception:
                    pass

            # 2. Draw invisible text overlay at exact coordinates
            text_obj = c.beginText()
            text_obj.setTextRenderMode(3)  # Mode 3: invisible selectable text

            for el in page.get("elements", []):
                for w in el.get("words", []):
                    txt = w.get("text", "").strip()
                    if not txt:
                        continue
                    bx, by, bw, bh = w["bbox"]
                    x_pt = bx * 72.0 / dpi
                    # Invert Y coordinate because PDF origin (0, 0) is bottom-left, image is top-left
                    y_pt = h_pt - ((by + bh) * 72.0 / dpi)
                    font_size = max(5.0, bh * 72.0 / dpi * 0.95)

                    try:
                        text_obj.setFont(font_name, font_size)
                        text_obj.setTextOrigin(x_pt, y_pt)
                        text_obj.textOut(txt + " ")
                    except Exception:
                        pass

            c.drawText(text_obj)
            c.showPage()

        c.save()

    def build_sandwich_pdf(self, pages: List[Dict[str, Any]], output_path: str) -> None:
        """
        Generate an archival Searchable Facsimile ('Sandwich') PDF:
        Displays the original high-resolution scan image while layering invisible,
        selectable, searchable OCR text at exact word coordinates underneath.
        Supports chunked streaming compilation to prevent memory exhaustion on 1,000+ page books.
        """
        font_name = self._register_pdf_font()
        dpi = max(72, self.config.dpi)
        chunk_size = max(10, getattr(self.config, "pdf_compilation_chunk_size", 50))
        use_chunking = (
            getattr(self.config, "chunked_pdf_compilation", True)
            and len(pages) > chunk_size
        )

        if not use_chunking:
            self._render_sandwich_chunk(pages, output_path, font_name, dpi)
            return

        import gc
        temp_chunks: List[str] = []
        try:
            import pypdf
            for c_idx in range(0, len(pages), chunk_size):
                chunk_slice = pages[c_idx : c_idx + chunk_size]
                chunk_file = f"{output_path}.chunk_{c_idx // chunk_size}.pdf"
                self._render_sandwich_chunk(chunk_slice, chunk_file, font_name, dpi)
                temp_chunks.append(chunk_file)
                gc.collect()

            writer = pypdf.PdfWriter()
            for cf in temp_chunks:
                reader = pypdf.PdfReader(cf)
                for p in reader.pages:
                    writer.add_page(p)

            with open(output_path, "wb") as f_out:
                writer.write(f_out)
        except Exception:
            # Fallback to direct rendering if chunk merging encountered any issue
            self._render_sandwich_chunk(pages, output_path, font_name, dpi)
        finally:
            for cf in temp_chunks:
                try:
                    if os.path.exists(cf):
                        os.remove(cf)
                except Exception:
                    pass
            gc.collect()


    # ------------------------------------------------------------------
    # 3. EPUB3 BUILDER (ebooklib)
    # ------------------------------------------------------------------
    def build_epub(self, pages: List[Dict[str, Any]], output_path: str) -> None:
        book = epub.EpubBook()
        book.set_identifier("urn:uuid:multilingual-book-replica-001")
        book.set_title(self.config.book_title)
        book.set_language("ur")
        book.add_author(self.config.book_author)

        style_css = """
        body { font-family: 'Noto Nastaliq Urdu', 'Amiri', 'Arial Unicode MS', serif; line-height: 1.8; margin: 5%; }
        .rtl { direction: rtl; unicode-bidi: embed; text-align: right; }
        .ltr { direction: ltr; unicode-bidi: embed; text-align: left; }
        .low-conf { background-color: #fff380; border-bottom: 1px dashed #c00; padding: 0 2px; }
        .page-marker { text-align: center; color: #666; font-size: 0.85em; margin: 1.5em 0; border-top: 1px solid #ddd; padding-top: 0.5em; }
        .footnote { font-size: 0.88em; color: #333; border-top: 1px dotted #aaa; padding-top: 0.4em; }
        .poetry { margin: 1.5em 0; text-align: center; }
        .couplet { margin: 0.4em 0; font-size: 1.05em; }
        .misra { display: inline-block; padding: 0 1em; }
        table { width: 100%; border-collapse: collapse; margin: 1em 0; }
        td, th { border: 1px solid #888; padding: 6px; }
        .figure-box { text-align: center; margin: 1em 0; }
        .figure-box img { max-width: 92%; height: auto; }
        """
        nav_css = epub.EpubItem(
            uid="style_nav",
            file_name="style/nav.css",
            media_type="text/css",
            content=style_css.encode("utf-8"),
        )
        book.add_item(nav_css)

        chapters: List[epub.EpubHtml] = []
        for page in pages:
            pnum = page["page_number"]
            page_dir = page.get("page_direction", "RTL")
            chap = epub.EpubHtml(
                title=f"Page {page.get('detected_page_number_label', pnum)}",
                file_name=f"page_{pnum:05d}.xhtml",
                lang="ur" if page_dir == "RTL" else "en",
            )
            chap.add_item(nav_css)

            html_parts = [f'<div class="{"rtl" if page_dir == "RTL" else "ltr"}">']

            for el in page.get("elements", []):
                el_type = el["type"]
                dir_cls = "rtl" if el.get("direction", "RTL") == "RTL" else "ltr"

                if el_type == "illustration":
                    img_path = el.get("image_path", "")
                    if img_path and os.path.exists(img_path):
                        img_name = os.path.basename(img_path)
                        with open(img_path, "rb") as img_f:
                            img_item = epub.EpubItem(
                                uid=f"img_{pnum}_{img_name}",
                                file_name=f"images/{img_name}",
                                media_type="image/png",
                                content=img_f.read(),
                            )
                        book.add_item(img_item)
                        html_parts.append(
                            f'<div class="figure-box"><img src="images/{img_name}" alt="Figure"/></div>'
                        )
                    continue

                if el_type == "poetry":
                    html_parts.append(f'<div class="poetry {dir_cls}">')
                    for c in el.get("couplets", []):
                        m1 = html.escape(c.get("misra_1", ""))
                        m2 = html.escape(c.get("misra_2", ""))
                        html_parts.append(
                            f'<div class="couplet"><span class="misra">{m1}</span> &nbsp;—&nbsp; <span class="misra">{m2}</span></div>'
                        )
                    html_parts.append('</div>')
                    continue

                if el_type == "table":
                    html_parts.append(f'<table class="{dir_cls}">')
                    for row in el.get("table_rows", []):
                        html_parts.append("<tr>")
                        for cell in row:
                            html_parts.append(f"<td>{html.escape(cell)}</td>")
                        html_parts.append("</tr>")
                    html_parts.append("</table>")
                    continue

                inner_html = self._words_to_html(el)
                if el_type in ("title_page_line", "chapter_heading"):
                    html_parts.append(f'<h2 class="{dir_cls}">{inner_html}</h2>')
                elif el_type == "section_heading":
                    html_parts.append(f'<h3 class="{dir_cls}">{inner_html}</h3>')
                elif el_type == "footnote":
                    html_parts.append(f'<p class="footnote {dir_cls}">{inner_html}</p>')
                else:
                    html_parts.append(f'<p class="{dir_cls}">{inner_html}</p>')

            html_parts.append(
                f'<div class="page-marker">Page {html.escape(str(page.get("detected_page_number_label", pnum)))} (Scan #{pnum})</div>'
            )
            html_parts.append("</div>")

            chap.content = "\n".join(html_parts).encode("utf-8")
            book.add_item(chap)
            chapters.append(chap)

        book.toc = tuple(chapters)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav"] + chapters
        epub.write_epub(output_path, book, {})

    @staticmethod
    def _words_to_html(element: Dict[str, Any]) -> str:
        words = element.get("words", [])
        if not words:
            return html.escape(element.get("logical_text", ""))
        spans: List[str] = []
        for w in words:
            escaped = html.escape(w.get("text", ""))
            if w.get("is_low_confidence"):
                spans.append(f'<span class="low-conf">{escaped}</span>')
            else:
                spans.append(escaped)
        return " ".join(spans)

    # ------------------------------------------------------------------
    # 4. CLEAN UTF-8 TXT BACKUP BUILDER
    # ------------------------------------------------------------------
    def build_txt(self, pages: List[Dict[str, Any]], output_path: str) -> None:
        with open(output_path, "w", encoding="utf-8") as f:
            for page in pages:
                pnum = page["page_number"]
                label = page.get("detected_page_number_label", str(pnum))
                f.write(f"==================== PAGE {label} (SCAN #{pnum}) ====================\n")

                for hdr in page.get("headers", []):
                    if hdr.get("logical_text"):
                        f.write(f"[HEADER] {hdr['logical_text']}\n")

                for el in page.get("elements", []):
                    el_type = el["type"]
                    if el_type == "poetry":
                        for c in el.get("couplets", []):
                            f.write(f"  [POETRY] {c.get('misra_1', '')}   —   {c.get('misra_2', '')}\n")
                        f.write("\n")
                    elif el_type == "table":
                        for row in el.get("table_rows", []):
                            f.write(" | ".join(row) + "\n")
                        f.write("\n")
                    elif el_type in ("chapter_heading", "section_heading", "title_page_line"):
                        f.write(f"\n## {el.get('logical_text', '')}\n\n")
                    elif el_type == "footnote":
                        f.write(f"  [FOOTNOTE] {el.get('logical_text', '')}\n")
                    else:
                        f.write(f"{el.get('logical_text', '')}\n\n")

                for sn in page.get("side_notes", []):
                    f.write(f"  [SIDE NOTE] {sn.get('logical_text', '')}\n")

                for ftr in page.get("footers", []):
                    if ftr.get("logical_text"):
                        f.write(f"[FOOTER] {ftr['logical_text']}\n")

                f.write("\n")

    # ------------------------------------------------------------------
    # 5. WEASYPRINT-READY / PRINT-PERFECT HTML REPLICA BUILDER
    # ------------------------------------------------------------------
    def build_html_replica(self, pages: List[Dict[str, Any]], output_path: str) -> None:
        """
        Build an HTML5 print replica. Uses native HarfBuzz / browser OpenType engines,
        guaranteeing 100% accurate copy-paste logical Unicode and proper cursive shaping.
        """
        font_family = f"'{self.config.rtl_font_name}', '{self.config.rtl_fallback_font}', 'Amiri', 'Arial', serif"

        html_out = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8"/>',
            f'<title>{html.escape(self.config.book_title)}</title>',
            "<style>",
            f"@page {{ size: A4; margin: 2.2cm; }}",
            f"body {{ font-family: {font_family}; font-size: 13pt; line-height: 1.85; color: #111; margin: 0 auto; max-width: 850px; padding: 2em; }}",
            ".rtl { direction: rtl; unicode-bidi: embed; text-align: right; }",
            ".ltr { direction: ltr; unicode-bidi: embed; text-align: left; }",
            ".page-block { page-break-after: always; margin-bottom: 3em; position: relative; }",
            ".page-marker { text-align: center; color: #777; font-size: 0.82em; margin: 2em 0 1em 0; border-top: 1px solid #e0e0e0; padding-top: 0.6em; }",
            ".header-meta { font-size: 0.85em; color: #666; margin-bottom: 1.2em; border-bottom: 1px solid #eee; padding-bottom: 0.3em; }",
            ".footnote { font-size: 0.88em; color: #444; border-top: 1px dotted #ccc; padding-top: 0.5em; margin-top: 1.5em; }",
            ".poetry { text-align: center; margin: 1.6em 0; }",
            ".couplet { margin: 0.35em 0; font-size: 1.08em; }",
            ".misra { display: inline-block; padding: 0 1.2em; }",
            "table { width: 100%; border-collapse: collapse; margin: 1.4em 0; }",
            "td, th { border: 1px solid #999; padding: 8px 12px; }",
            ".figure-box { text-align: center; margin: 1.5em 0; }",
            ".figure-box img { max-width: 90%; height: auto; border: 1px solid #ddd; }",
            ".low-conf { background-color: #fff9a6; border-bottom: 1px dashed #d9383a; }",
            ".side-note { margin: 0.8em 2em; padding: 6px 12px; background: #fffde7; border-left: 3px solid #fbc02d; font-size: 0.9em; font-style: italic; }",
            "</style>",
            "</head>",
            "<body>",
        ]

        for page in pages:
            pnum = page["page_number"]
            page_dir = page.get("page_direction", "RTL")
            dir_cls = "rtl" if page_dir == "RTL" else "ltr"
            label = page.get("detected_page_number_label", str(pnum))

            html_out.append(f'<div class="page-block {dir_cls}">')

            if page.get("headers"):
                hdr_text = " | ".join(h["logical_text"] for h in page["headers"] if h["logical_text"])
                if hdr_text:
                    html_out.append(f'<div class="header-meta">{html.escape(hdr_text)}</div>')

            for el in page.get("elements", []):
                el_type = el["type"]
                el_dir_cls = "rtl" if el.get("direction", "RTL") == "RTL" else "ltr"

                if el_type == "illustration":
                    img_path = el.get("image_path", "")
                    if img_path and os.path.exists(img_path):
                        rel_img = os.path.relpath(img_path, os.path.dirname(output_path)).replace("\\", "/")
                        html_out.append(f'<div class="figure-box"><img src="{rel_img}" alt="Scan Figure"/></div>')
                    continue

                if el_type == "poetry":
                    html_out.append(f'<div class="poetry {el_dir_cls}">')
                    for c in el.get("couplets", []):
                        m1 = html.escape(c.get("misra_1", ""))
                        m2 = html.escape(c.get("misra_2", ""))
                        html_out.append(f'<div class="couplet"><span class="misra">{m1}</span> — <span class="misra">{m2}</span></div>')
                    html_out.append('</div>')
                    continue

                if el_type == "table":
                    html_out.append(f'<table class="{el_dir_cls}">')
                    for row in el.get("table_rows", []):
                        html_out.append("<tr>")
                        for cell in row:
                            html_out.append(f"<td>{html.escape(cell)}</td>")
                        html_out.append("</tr>")
                    html_out.append("</table>")
                    continue

                if el_type == "interlinear_pair":
                    p_txt = html.escape(el.get("primary_text", ""))
                    g_txt = html.escape(el.get("gloss_text", ""))
                    html_out.append(f'<div class="interlinear {el_dir_cls}"><div class="primary-verse">{p_txt}</div><div class="gloss-line">↳ {g_txt}</div></div>')
                    continue

                inner_html = self._words_to_html(el)

                if el_type in ("title_page_line", "chapter_heading"):
                    html_out.append(f'<h2 class="{el_dir_cls}">{inner_html}</h2>')
                elif el_type == "section_heading":
                    html_out.append(f'<h3 class="{el_dir_cls}">{inner_html}</h3>')
                elif el_type == "footnote":
                    html_out.append(f'<div class="footnote {el_dir_cls}">[Footnote] {inner_html}</div>')
                elif el_type == "side_note":
                    html_out.append(f'<div class="side-note {el_dir_cls}">[Margin Note] {inner_html}</div>')
                else:
                    html_out.append(f'<p class="{el_dir_cls}">{inner_html}</p>')

            html_out.append(f'<div class="page-marker">— Page {html.escape(label)} (Scan #{pnum}) —</div>')
            html_out.append("</div>")

        html_out.extend(["</body>", "</html>"])

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(html_out))

    # ------------------------------------------------------------------
    # 7. LIBRARY OF CONGRESS ALTO XML 4.2 BUILDER
    # ------------------------------------------------------------------
    def build_alto_xml(self, pages: List[Dict[str, Any]], output_path: str) -> None:
        """
        Generate Library of Congress ALTO XML 4.2 archival metadata file,
        preserving word coordinates, lines, text blocks, and recognition confidence scores.
        """
        import xml.etree.ElementTree as ET
        from xml.dom import minidom

        alto = ET.Element(
            "alto",
            {
                "xmlns": "http://www.loc.gov/standards/alto/ns-v4#",
                "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "xsi:schemaLocation": "http://www.loc.gov/standards/alto/ns-v4# http://www.loc.gov/standards/alto/v4/alto-4-2.xsd",
            },
        )

        desc = ET.SubElement(alto, "Description")
        ET.SubElement(desc, "MeasurementUnit").text = "pixel"
        ocr_proc = ET.SubElement(desc, "OCRProcessing", {"ID": "OCR_0"})
        proc_step = ET.SubElement(ocr_proc, "ocrProcessingStep")
        ET.SubElement(proc_step, "processingSoftware").text = "Multilingual Scanned Book Digitizer 2.0"

        layout = ET.SubElement(alto, "Layout")

        for page in pages:
            pnum = page["page_number"]
            pw = str(page.get("page_width", 2400))
            ph = str(page.get("page_height", 3400))
            page_el = ET.SubElement(
                layout,
                "Page",
                {
                    "ID": f"page_{pnum}",
                    "PHYSICAL_IMG_NR": str(pnum),
                    "WIDTH": pw,
                    "HEIGHT": ph,
                },
            )
            pspace = ET.SubElement(
                page_el,
                "PrintSpace",
                {"HPOS": "0", "VPOS": "0", "WIDTH": pw, "HEIGHT": ph},
            )

            for b_idx, el in enumerate(page.get("elements", [])):
                bx, by, bw, bh = el.get("bbox", [0, 0, 100, 50])
                tblock = ET.SubElement(
                    pspace,
                    "TextBlock",
                    {
                        "ID": f"b_{pnum}_{b_idx}",
                        "HPOS": str(bx),
                        "VPOS": str(by),
                        "WIDTH": str(bw),
                        "HEIGHT": str(bh),
                    },
                )
                words = el.get("words", [])
                if not words:
                    continue

                line_groups: Dict[int, List[Dict[str, Any]]] = {}
                for w in words:
                    ly = w["bbox"][1] // 25
                    line_groups.setdefault(ly, []).append(w)

                for l_idx, (ly, line_words) in enumerate(sorted(line_groups.items())):
                    lx = min(w["bbox"][0] for w in line_words)
                    ly_val = min(w["bbox"][1] for w in line_words)
                    lw = max(w["bbox"][0] + w["bbox"][2] for w in line_words) - lx
                    lh = max(w["bbox"][1] + w["bbox"][3] for w in line_words) - ly_val

                    tline = ET.SubElement(
                        tblock,
                        "TextLine",
                        {
                            "ID": f"l_{pnum}_{b_idx}_{l_idx}",
                            "HPOS": str(lx),
                            "VPOS": str(ly_val),
                            "WIDTH": str(lw),
                            "HEIGHT": str(lh),
                        },
                    )

                    for w_idx, w in enumerate(line_words):
                        wx, wy, ww, wh = w["bbox"]
                        conf_val = round(float(w.get("confidence", 95.0)) / 100.0, 2)
                        ET.SubElement(
                            tline,
                            "String",
                            {
                                "ID": f"w_{pnum}_{b_idx}_{l_idx}_{w_idx}",
                                "CONTENT": _clean_xml(w.get("text", "")),
                                "HPOS": str(wx),
                                "VPOS": str(wy),
                                "WIDTH": str(ww),
                                "HEIGHT": str(wh),
                                "WC": str(conf_val),
                            },
                        )
                        if w_idx < len(line_words) - 1:
                            ET.SubElement(tline, "SP")

        raw_str = ET.tostring(alto, encoding="utf-8")
        parsed = minidom.parseString(raw_str)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(parsed.toprettyxml(indent="  "))

    # ------------------------------------------------------------------
    # 8. PRIMA PAGEXML 2019-07-15 DIGITAL LIBRARY BUILDER
    # ------------------------------------------------------------------
    def build_page_xml(self, pages: List[Dict[str, Any]], output_dir: str) -> List[str]:
        """
        Generate PRImA PageXML (2019-07-15) XML documents for each page in the book.
        Global standard for European and international digital humanities research and archives.
        """
        import xml.etree.ElementTree as ET
        from xml.dom import minidom
        from datetime import datetime

        os.makedirs(output_dir, exist_ok=True)
        created_files: List[str] = []
        now_iso = datetime.now().isoformat()

        for page in pages:
            pnum = page["page_number"]
            pw = page.get("page_width", 2400)
            ph = page.get("page_height", 3400)
            xml_path = os.path.join(output_dir, f"page_{pnum:05d}.xml")

            pcgts = ET.Element(
                "PcGts",
                {
                    "xmlns": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15",
                    "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                    "xsi:schemaLocation": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15 http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15/pagecontent.xsd",
                },
            )

            meta = ET.SubElement(pcgts, "Metadata")
            ET.SubElement(meta, "Creator").text = "Multilingual Scanned Book Digitizer 2.0"
            ET.SubElement(meta, "Created").text = now_iso

            img_name = f"page_{pnum:05d}_preview.jpg"
            p_elem = ET.SubElement(
                pcgts,
                "Page",
                {
                    "imageFilename": img_name,
                    "imageWidth": str(pw),
                    "imageHeight": str(ph),
                },
            )

            for b_idx, el in enumerate(page.get("elements", [])):
                bx, by, bw, bh = el.get("bbox", [0, 0, 100, 50])
                el_type = el.get("type", "paragraph")
                region_type = "heading" if "heading" in el_type else ("paragraph" if el_type != "table" else "table")
                region = ET.SubElement(
                    p_elem,
                    "TextRegion",
                    {"id": f"r_{b_idx}", "type": region_type},
                )
                coords_str = f"{bx},{by} {bx+bw},{by} {bx+bw},{by+bh} {bx},{by+bh}"
                ET.SubElement(region, "Coords", {"points": coords_str})

                words = el.get("words", [])
                if words:
                    line_groups: Dict[int, List[Dict[str, Any]]] = {}
                    for w in words:
                        ly = w["bbox"][1] // 25
                        line_groups.setdefault(ly, []).append(w)

                    for l_idx, (ly, line_words) in enumerate(sorted(line_groups.items())):
                        lx = min(w["bbox"][0] for w in line_words)
                        ly_val = min(w["bbox"][1] for w in line_words)
                        lw = max(w["bbox"][0] + w["bbox"][2] for w in line_words) - lx
                        lh = max(w["bbox"][1] + w["bbox"][3] for w in line_words) - ly_val

                        line_el = ET.SubElement(region, "TextLine", {"id": f"l_{b_idx}_{l_idx}"})
                        l_coords = f"{lx},{ly_val} {lx+lw},{ly_val} {lx+lw},{ly_val+lh} {lx},{ly_val+lh}"
                        ET.SubElement(line_el, "Coords", {"points": l_coords})
                        base_y = ly_val + int(lh * 0.85)
                        ET.SubElement(line_el, "Baseline", {"points": f"{lx},{base_y} {lx+lw},{base_y}"})

                        line_txt = _clean_xml(" ".join(w.get("text", "") for w in line_words))
                        te = ET.SubElement(line_el, "TextEquiv")
                        ET.SubElement(te, "Unicode").text = line_txt

                te_reg = ET.SubElement(region, "TextEquiv")
                ET.SubElement(te_reg, "Unicode").text = _clean_xml(el.get("logical_text", ""))

            raw_str = ET.tostring(pcgts, encoding="utf-8")
            parsed = minidom.parseString(raw_str)
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(parsed.toprettyxml(indent="  "))
            created_files.append(xml_path)

        return created_files

