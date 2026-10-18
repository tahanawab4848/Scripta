import concurrent.futures
import json
import os
import sys
import time
import traceback
from typing import Callable, Dict, Any, List, Optional, Tuple

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from config import AppConfig
from preprocessor import PagePreprocessor
from ocr_engine import OCREngine, get_pdf_page_count, render_single_pdf_page
from corrector import TextCorrector
from layout_analyzer import LayoutAnalyzer
from builder import BookBuilder


def extract_page_plain_text(page_layout: Optional[Dict[str, Any]]) -> str:
    """Reconstruct clean readable plain text from analyzed page layout."""
    if not page_layout:
        return ""
    lines: List[str] = []
    for h in page_layout.get("headers", []):
        t = h.get("logical_text", "").strip()
        if t:
            lines.append(f"--- Header: {t} ---")

    for el in page_layout.get("elements", []):
        el_type = el.get("type", "paragraph")
        if el_type == "poetry":
            couplet_lines = []
            for c in el.get("couplets", []):
                m1 = c.get("misra_1", "").strip()
                m2 = c.get("misra_2", "").strip()
                couplet_lines.append(f"  {m1}  —  {m2}")
            if couplet_lines:
                lines.append("\n".join(couplet_lines))
        elif el_type == "table":
            table_lines = []
            for r in el.get("table_rows", []):
                table_lines.append(" | ".join(str(cell).strip() for cell in r))
            if table_lines:
                lines.append("[Table]\n" + "\n".join(table_lines))
        elif el_type == "interlinear_pair":
            p_text = el.get("primary_text", "").strip()
            g_text = el.get("gloss_text", "").strip()
            lines.append(f"{p_text}\n   ↳ [ترجمہ / Gloss]: {g_text}")
        elif el_type == "illustration":
            img_p = el.get("image_path", "")
            lines.append(f"[Illustration: {os.path.basename(img_p)}]")
        elif el_type == "footnote":
            lines.append(f"[Footnote: {el.get('logical_text', '').strip()}]")
        elif el_type in ("title_page_line", "chapter_heading", "section_heading"):
            lines.append(f"## {el.get('logical_text', '').strip()}")
        else:
            t = el.get("logical_text", "").strip()
            if t:
                lines.append(t)

    for f in page_layout.get("footers", []):
        t = f.get("logical_text", "").strip()
        if t:
            lines.append(f"--- Footer: {t} ---")

    return "\n\n".join(lines)


def process_single_page_worker(args: Tuple[Dict[str, Any], int]) -> Dict[str, Any]:
    """
    Top-level multiprocessing worker function. Renders, preprocesses, OCRs, corrects,
    and analyzes a single page, then writes an atomic checkpoint JSON file to disk.
    Also saves a preview snapshot of the preprocessed page for instant UI inspection.
    """
    config_dict, page_number = args
    config = AppConfig.from_dict(config_dict)
    checkpoint_file = os.path.join(
        config.checkpoint_dir, f"page_{page_number:05d}.json"
    )

    start_ts = time.time()
    try:
        # Check if page is born-digital before heavy image processing
        ocr_engine = OCREngine(config)
        digital_ocr = None
        if getattr(config, "detect_born_digital_pdf", True) and os.path.exists(config.input_pdf_path):
            digital_ocr = ocr_engine.probe_born_digital_page(config.input_pdf_path, page_number)

        pil_img = render_single_pdf_page(
            config.input_pdf_path,
            page_number=page_number,
            dpi=config.dpi,
            poppler_path=config.poppler_path,
        )

        os.makedirs(config.extracted_images_dir, exist_ok=True)
        preview_path = os.path.join(
            config.extracted_images_dir, f"page_{page_number:05d}_preview.png"
        )

        if digital_ocr is not None:
            # Born-digital: Bypass optical preprocessing & dewarping, preserve clean digital text
            raw_ocr = digital_ocr
            try:
                pil_img.save(preview_path, format="PNG")
                preprocessed_path = preview_path
            except Exception:
                preprocessed_path = ""
            pre_metrics: Dict[str, Any] = {
                "page_number": page_number,
                "width": pil_img.width,
                "height": pil_img.height,
                "deskew_angle": 0.0,
                "dewarp_applied": False,
                "spine_shadow_removed": False,
                "bleed_through_reduced": False,
                "is_born_digital": True,
                "illustrations": [],
            }
        else:
            # Scanned book: Run physical restoration pipeline
            preprocessor = PagePreprocessor(config)
            processed_pil, pre_metrics = preprocessor.process_page(
                pil_img, page_number=page_number
            )
            try:
                processed_pil.save(preview_path, format="PNG")
                preprocessed_path = preview_path
            except Exception:
                preprocessed_path = ""
            raw_ocr = ocr_engine.run_ocr_on_page(
                processed_pil,
                page_number=page_number,
                illustrations=pre_metrics.get("illustrations", []),
            )

        # 3. Post-OCR Linguistic Correction & Classical Normalization
        corrector = TextCorrector(config)
        corrected_ocr = corrector.process_ocr_page(raw_ocr)

        # 4. Layout & Structural Semantic Analysis
        analyzer = LayoutAnalyzer(config)
        layout = analyzer.analyze_page(corrected_ocr, pre_metrics)

        # Re-inject manually edited text if previously modified
        plain_text = extract_page_plain_text(layout)

        result_payload: Dict[str, Any] = {
            "page_number": page_number,
            "input_pdf_path": os.path.abspath(config.input_pdf_path),
            "status": "success",
            "mean_confidence": round(corrected_ocr.get("mean_confidence", 0.0), 2),
            "word_count": len(corrected_ocr.get("words", [])),
            "low_confidence_word_count": sum(
                1 for w in corrected_ocr.get("words", []) if w.get("confidence", 100) < config.confidence_threshold
            ),
            "page_direction": layout.get("page_direction", layout.get("dominant_direction", "LTR")),
            "column_count": layout.get("column_count", 1),
            "preview_image_path": preprocessed_path,
            "layout": layout,
            "extracted_text": plain_text,
            "preprocessing_metrics": pre_metrics,
            "duration_sec": round(time.time() - start_ts, 2),
            "timestamp": time.time(),
        }

        # Write atomic checkpoint to disk
        os.makedirs(config.checkpoint_dir, exist_ok=True)
        tmp_chk = checkpoint_file + ".tmp"
        with open(tmp_chk, "w", encoding="utf-8") as f:
            json.dump(result_payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_chk, checkpoint_file)

        return result_payload

    except Exception as exc:
        err_payload: Dict[str, Any] = {
            "page_number": page_number,
            "status": "failed",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "mean_confidence": 0.0,
            "word_count": 0,
            "low_confidence_word_count": 0,
            "page_direction": "LTR",
            "column_count": 1,
            "duration_sec": round(time.time() - start_ts, 2),
            "timestamp": time.time(),
        }
        try:
            os.makedirs(config.checkpoint_dir, exist_ok=True)
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(err_payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return err_payload


class BatchProcessor:
    """
    Multiprocessing Batch Digitizer with atomic checkpoints, pause/resume,
    live telemetry, and on-demand book re-assembly.
    """

    def __init__(
        self,
        config: AppConfig,
        on_page_complete: Optional[Callable[[Dict[str, Any], int, int, Optional[Dict[str, Any]]], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.on_page_complete = on_page_complete
        self.on_log = on_log

        self._pause_requested = False
        self._stop_requested = False

        self.start_time: float = 0.0
        self.processed_count: int = 0
        self.total_target_pages: int = 0

    def log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)
        else:
            print(f"[BatchProcessor] {message}")

    def request_pause(self) -> None:
        self._pause_requested = True
        self.log("Pause requested. Active worker processes will complete their current page.")

    def request_stop(self) -> None:
        self._stop_requested = True
        self.log("Cancellation requested. Terminating background worker pool...")

    def preview_single_page(self, page_number: int) -> Dict[str, Any]:
        """Runs the entire pipeline on a single page immediately."""
        self.log(f"Running single-page inspection on Page #{page_number}...")
        return process_single_page_worker((self.config.to_dict(), page_number))

    def reassemble_books_from_checkpoints(self) -> Dict[str, Any]:
        """
        Assembles DOCX, PDF, EPUB, TXT, and HTML replicas directly from existing checkpoints
        without re-running OCR. Incorporates any manual user text edits committed to checkpoints.
        """
        chk_dir = self.config.checkpoint_dir
        if not os.path.exists(chk_dir):
            raise FileNotFoundError(f"Checkpoints directory not found at: {chk_dir}")

        cfiles = sorted([f for f in os.listdir(chk_dir) if f.startswith("page_") and f.endswith(".json")])
        if not cfiles:
            raise FileNotFoundError("No page checkpoint files found to assemble.")

        self.log(f"Loading {len(cfiles)} page checkpoint(s) for digital book re-assembly...")
        all_results: Dict[int, Dict[str, Any]] = {}
        successful_layouts: List[Dict[str, Any]] = []

        for f in cfiles:
            p = os.path.join(chk_dir, f)
            try:
                with open(p, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                pnum = data.get("page_number")
                if pnum:
                    all_results[pnum] = data
                    if data.get("status") == "success" and data.get("layout"):
                        # If manually edited text exists, update layout paragraph text accordingly
                        layout = data["layout"]
                        if data.get("manually_edited") and data.get("extracted_text"):
                            self._apply_manual_text_to_layout(layout, data["extracted_text"])
                        successful_layouts.append(layout)
            except Exception as exc:
                self.log(f"Warning: could not read checkpoint {f}: {exc}")

        self.log("Re-compiling DOCX, Searchable PDF, Reflow PDF, EPUB3, HTML5 & TXT...")
        builder = BookBuilder(self.config)
        generated_files = builder.build_all(successful_layouts)

        report = self.generate_quality_report(all_results, len(all_results), generated_files)
        self.log(f"Book re-assembly complete! Output saved to: {self.config.output_dir}")
        return report

    def _apply_manual_text_to_layout(self, layout: Dict[str, Any], manual_text: str) -> None:
        """Helper to ensure manual text corrections feed into book builder layout elements."""
        paragraphs = [p for p in manual_text.split("\n\n") if p.strip()]
        if not paragraphs:
            return
        
        # Replace or update primary layout elements with corrected paragraph text
        elements = layout.get("elements", [])
        if elements and len(elements) == len(paragraphs):
            for i, p_str in enumerate(paragraphs):
                elements[i]["logical_text"] = p_str
        elif elements:
            # Overwrite first element and append remaining
            elements[0]["logical_text"] = "\n\n".join(paragraphs)

    def run(self) -> Dict[str, Any]:
        """Main batch conversion execution loop."""
        if not os.path.exists(self.config.input_pdf_path):
            raise FileNotFoundError(f"Input PDF not found: {self.config.input_pdf_path}")

        total_pdf_pages = get_pdf_page_count(self.config.input_pdf_path)
        start_p = self.config.start_page if self.config.start_page else 1
        end_p = min(self.config.end_page if self.config.end_page else total_pdf_pages, total_pdf_pages)
        target_pages = list(range(start_p, end_p + 1))
        target_count = len(target_pages)

        self.total_target_pages = target_count
        self.start_time = time.time()
        self.processed_count = 0

        self.log(
            f"Starting digitization run for '{os.path.basename(self.config.input_pdf_path)}' "
            f"({target_count} pages in scope: {start_p} to {end_p}) using {self.config.max_workers} worker processes..."
        )

        os.makedirs(self.config.checkpoint_dir, exist_ok=True)
        os.makedirs(self.config.extracted_images_dir, exist_ok=True)
        os.makedirs(self.config.output_dir, exist_ok=True)

        all_results: Dict[int, Dict[str, Any]] = {}
        pending_pages: List[int] = []

        # Check existing checkpoints to support seamless resume
        for p in target_pages:
            chk_file = os.path.join(self.config.checkpoint_dir, f"page_{p:05d}.json")
            if os.path.exists(chk_file):
                try:
                    with open(chk_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    chk_pdf = data.get("input_pdf_path")
                    curr_pdf = os.path.abspath(self.config.input_pdf_path)
                    if data.get("status") == "success" and (not chk_pdf or chk_pdf == curr_pdf):
                        all_results[p] = data
                        self.processed_count += 1
                        if self.on_page_complete:
                            self.on_page_complete(data, self.processed_count, target_count, self._calc_stats())
                    else:
                        # Re-process previously failed or mismatched pages
                        pending_pages.append(p)
                except Exception:
                    pending_pages.append(p)
            else:
                pending_pages.append(p)

        if not pending_pages:
            self.log(f"All {target_count} pages already completed in checkpoints. Proceeding directly to compilation...")
        else:
            self.log(f"{len(pending_pages)} pages remaining to be processed...")

            cfg_dict = self.config.to_dict()
            worker_tasks = [(cfg_dict, p) for p in pending_pages]

            with concurrent.futures.ProcessPoolExecutor(max_workers=self.config.max_workers) as executor:
                page_iter = iter(worker_tasks)
                in_flight: Dict[concurrent.futures.Future, int] = {}

                # Fill initial worker pool
                for _ in range(min(self.config.max_workers * 2, len(worker_tasks))):
                    try:
                        task = next(page_iter)
                        fut = executor.submit(process_single_page_worker, task)
                        in_flight[fut] = task[1]
                    except StopIteration:
                        break

                while in_flight:
                    if self._stop_requested:
                        break

                    done_futs, _ = concurrent.futures.wait(
                        in_flight.keys(), return_when=concurrent.futures.FIRST_COMPLETED
                    )

                    for fut in done_futs:
                        pnum = in_flight.pop(fut)
                        try:
                            res = fut.result()
                        except Exception as exc:
                            res = {
                                "page_number": pnum,
                                "status": "failed",
                                "error": str(exc),
                                "mean_confidence": 0.0,
                                "word_count": 0,
                                "low_confidence_word_count": 0,
                                "page_direction": "LTR",
                                "column_count": 1,
                            }

                        all_results[pnum] = res
                        self.processed_count += 1
                        stats = self._calc_stats()

                        if self.on_page_complete:
                            self.on_page_complete(res, self.processed_count, target_count, stats)

                        # Submit next pending job if not paused or stopped
                        if not self._pause_requested and not self._stop_requested:
                            try:
                                next_task = next(page_iter)
                                new_fut = executor.submit(process_single_page_worker, next_task)
                                in_flight[new_fut] = next_task[1]
                            except StopIteration:
                                pass

                if self._stop_requested:
                    self.log("Batch stopped by user. Cancelling outstanding worker jobs...")
                    for fut in list(in_flight.keys()):
                        fut.cancel()

        if self._stop_requested:
            self.log(f"Pipeline stopped by user. {len(all_results)}/{target_count} pages processed.")
            return {
                "stopped": True,
                "paused": False,
                "completed_pages": len(all_results),
                "total_pages": target_count,
            }

        if self._pause_requested and len(all_results) < target_count:
            self.log(
                f"Run paused safely at {len(all_results)}/{target_count} pages. "
                "Click Resume at any time to continue."
            )
            return {
                "stopped": False,
                "paused": True,
                "completed_pages": len(all_results),
                "total_pages": target_count,
            }

        # Compile final outputs
        successful_layouts = [
            r["layout"]
            for p in sorted(all_results.keys())
            for r in [all_results[p]]
            if r["status"] == "success" and r.get("layout") is not None
        ]

        self.log("Compiling digital book replicas with cross-page footnote stitching (DOCX / PDF / EPUB / TXT / HTML)...")
        builder = BookBuilder(self.config)
        generated_files = builder.build_all(successful_layouts)

        report = self.generate_quality_report(all_results, target_count, generated_files)
        self.log(f"Conversion complete! Quality report saved to: {report['report_txt_path']}")
        return report

    def _calc_stats(self) -> Dict[str, Any]:
        elapsed = max(0.001, time.time() - self.start_time)
        pages_per_sec = self.processed_count / elapsed
        pages_per_min = pages_per_sec * 60.0

        rem_pages = max(0, self.total_target_pages - self.processed_count)
        eta_sec = (rem_pages / pages_per_sec) if pages_per_sec > 0 else 0

        return {
            "elapsed_sec": elapsed,
            "elapsed_str": time.strftime("%H:%M:%S", time.gmtime(elapsed)),
            "eta_sec": eta_sec,
            "eta_str": time.strftime("%H:%M:%S", time.gmtime(eta_sec)) if eta_sec > 0 else "--:--:--",
            "pages_per_min": round(pages_per_min, 1),
        }

    def generate_quality_report(
        self,
        all_results: Dict[int, Dict[str, Any]],
        total_pages: int,
        generated_files: Dict[str, str],
    ) -> Dict[str, Any]:
        success_pages = [r for r in all_results.values() if r["status"] == "success"]
        failed_pages = [r for r in all_results.values() if r["status"] == "failed"]
        low_conf_pages = [
            r
            for r in success_pages
            if r["mean_confidence"] < self.config.confidence_threshold
            or r["low_confidence_word_count"] > 0
        ]

        avg_conf = (
            round(
                sum(r["mean_confidence"] for r in success_pages) / float(len(success_pages)),
                2,
            )
            if success_pages
            else 0.0
        )

        per_page_scores = [
            {
                "page_number": pnum,
                "status": all_results[pnum]["status"],
                "mean_confidence": all_results[pnum]["mean_confidence"],
                "word_count": all_results[pnum]["word_count"],
                "low_confidence_word_count": all_results[pnum]["low_confidence_word_count"],
                "direction": all_results[pnum]["page_direction"],
                "error": all_results[pnum].get("error"),
            }
            for pnum in sorted(all_results.keys())
        ]

        report_data = {
            "paused": False,
            "stopped": False,
            "total_pages": total_pages,
            "successful_pages": len(success_pages),
            "pages_with_low_confidence_flags": len(low_conf_pages),
            "failed_pages_count": len(failed_pages),
            "failed_page_numbers": [r["page_number"] for r in failed_pages],
            "overall_average_confidence": avg_conf,
            "confidence_threshold_used": self.config.confidence_threshold,
            "generated_files": generated_files,
            "per_page_breakdown": per_page_scores,
        }

        json_path = os.path.join(self.config.output_dir, "quality_report.json")
        txt_path = os.path.join(self.config.output_dir, "quality_report.txt")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("====================================================================\n")
            f.write("                 BOOK DIGITIZATION QUALITY REPORT                   \n")
            f.write("====================================================================\n")
            f.write(f"Input PDF                  : {self.config.input_pdf_path}\n")
            f.write(f"Languages                  : {self.config.languages}\n")
            f.write(f"Target Pages               : {total_pages}\n")
            f.write(f"Successful Pages           : {len(success_pages)}\n")
            f.write(f"Pages Flagged (Low-Conf)   : {len(low_conf_pages)}\n")
            f.write(f"Failed Pages               : {len(failed_pages)}\n")
            f.write(f"Overall Mean Confidence    : {avg_conf:.2f}%\n")
            f.write(f"Confidence Threshold       : {self.config.confidence_threshold:.1f}%\n\n")
            f.write("Generated Replicas:\n")
            for fmt, fpath in generated_files.items():
                f.write(f"  - {fmt.upper():5s}: {fpath}\n")
            f.write("\nPer-Page Breakdown:\n")
            f.write("Page  | Status  | Confidence | Words | Flagged Words | Direction\n")
            f.write("------+---------+------------+-------+---------------+----------\n")
            for row in per_page_scores:
                f.write(
                    f"{row['page_number']:5d} | "
                    f"{row['status']:7s} | "
                    f"{row['mean_confidence']:9.2f}% | "
                    f"{row['word_count']:5d} | "
                    f"{row['low_confidence_word_count']:13d} | "
                    f"{row['direction']}\n"
                )

        report_data["report_json_path"] = json_path
        report_data["report_txt_path"] = txt_path
        return report_data
