import concurrent.futures
import json
import os
import sys
import time
import traceback
from typing import Callable, Dict, Any, List, Optional, Tuple
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

        if digital_ocr is not None:
            # Born-digital: Bypass optical preprocessing & dewarping, preserve clean digital text
            preproc_meta = {
                "page_number": page_number,
                "skew_angle": 0.0,
                "dewarped": False,
                "denoised": False,
                "illustrations": [],
                "page_dimensions": pil_img.size,
                "source": "born_digital_vector",
            }
            processed_pil = pil_img
            ocr_raw = digital_ocr
        else:
            # Scanned / raster page: run full OpenCV Sauvola, deskew, 3D curl dewarping, and OCR
            preprocessor = PagePreprocessor(config)
            processed_pil, preproc_meta = preprocessor.process_page(pil_img, page_number)
            ocr_raw = ocr_engine.run_ocr_on_page(
                processed_pil,
                page_number=page_number,
                illustrations=preproc_meta.get("illustrations", []),
            )

        # Save compact, optimized JPEG preview snapshot for instant UI page inspection (saves ~97% disk space)
        preview_img_path = os.path.join(
            config.extracted_images_dir, f"page_{page_number:05d}_preview.jpg"
        )
        try:
            max_dim = getattr(config, "inspector_image_max_dim", 1600)
            orig_w, orig_h = processed_pil.size
            if max(orig_w, orig_h) > max_dim:
                scale = max_dim / float(max(orig_w, orig_h))
                new_w = int(orig_w * scale)
                new_h = int(orig_h * scale)
                thumb = processed_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
            else:
                thumb = processed_pil

            if thumb.mode not in ("RGB", "L"):
                thumb = thumb.convert("RGB")
            quality = getattr(config, "inspector_image_quality", 85)
            thumb.save(preview_img_path, format="JPEG", quality=quality, optimize=True)
        except Exception:
            preview_img_path = ""

        corrector = TextCorrector(config)
        ocr_corrected = corrector.process_ocr_page(ocr_raw)

        analyzer = LayoutAnalyzer(config)
        page_layout = analyzer.analyze_page(ocr_corrected, preproc_meta)

        elapsed = round(time.time() - start_ts, 2)
        extracted_text = extract_page_plain_text(page_layout)

        result_payload = {
            "status": "success",
            "page_number": page_number,
            "elapsed_sec": elapsed,
            "mean_confidence": page_layout["mean_confidence"],
            "word_count": page_layout["word_count"],
            "low_confidence_word_count": page_layout["low_confidence_word_count"],
            "page_direction": page_layout["page_direction"],
            "column_count": page_layout["column_count"],
            "preview_image_path": preview_img_path,
            "extracted_text": extracted_text,
            "layout": page_layout,
            "error": None,
        }

        tmp_file = checkpoint_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(result_payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, checkpoint_file)

        return result_payload

    except Exception as exc:
        elapsed = round(time.time() - start_ts, 2)
        err_msg = f"{type(exc).__name__}: {exc}"
        tb_str = traceback.format_exc()
        fail_payload = {
            "status": "failed",
            "page_number": page_number,
            "elapsed_sec": elapsed,
            "mean_confidence": 0.0,
            "word_count": 0,
            "low_confidence_word_count": 0,
            "page_direction": "N/A",
            "column_count": 0,
            "preview_image_path": "",
            "extracted_text": "",
            "layout": None,
            "error": err_msg,
            "traceback": tb_str,
        }
        try:
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(fail_payload, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return fail_payload


class BatchProcessor:
    """
    Coordinates multiprocessing across PDF pages with pause/resume support,
    abort/cancel termination, start/end page range filtering, live throughput & ETA metrics,
    worker process recycling to prevent RAM bloat, instant per-page checkpointing,
    and final book compilation.
    """

    def __init__(
        self,
        config: AppConfig,
        on_page_complete: Optional[Callable[..., None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.on_page_complete = on_page_complete
        self.on_log = on_log
        self._pause_requested = False
        self._stop_requested = False

    def log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)
        else:
            print(message)

    def request_pause(self) -> None:
        self._pause_requested = True
        self.log("Pause requested. Waiting for active worker pages to finish...")

    def request_resume(self) -> None:
        self._pause_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True
        self._pause_requested = True
        self.log("Cancellation requested by user. Terminating active worker processes...")

    def _load_existing_checkpoints(self, start_p: int, end_p: int) -> Dict[int, Dict[str, Any]]:
        completed: Dict[int, Dict[str, Any]] = {}
        if not self.config.resume_enabled or not os.path.isdir(self.config.checkpoint_dir):
            return completed

        for pnum in range(start_p, end_p + 1):
            cfile = os.path.join(self.config.checkpoint_dir, f"page_{pnum:05d}.json")
            if not os.path.exists(cfile):
                continue
            try:
                with open(cfile, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("status") == "success" or (
                    data.get("status") == "failed" and not self.config.retry_failed_on_resume
                ):
                    completed[pnum] = data
            except Exception:
                continue
        return completed

    def preview_single_page(self, page_number: int) -> Dict[str, Any]:
        """
        Instant Single-Page Test/Preview mode: renders, preprocesses, OCRs,
        and analyzes a single page in-process, returning original and processed
        image paths alongside the extracted text and layout structure.
        """
        self.config.ensure_directories()
        if not os.path.exists(self.config.input_pdf_path):
            raise FileNotFoundError(f"Input PDF not found: {self.config.input_pdf_path}")

        total_pages = get_pdf_page_count(
            self.config.input_pdf_path, poppler_path=self.config.poppler_path
        )
        if page_number < 1 or page_number > total_pages:
            raise ValueError(f"Page number {page_number} is out of bounds (1..{total_pages})")

        start_ts = time.time()
        self.log(f"Generating single-page preview for Page {page_number}...")

        # 1. Render raw PDF page
        pil_img = render_single_pdf_page(
            self.config.input_pdf_path,
            page_number=page_number,
            dpi=self.config.dpi,
            poppler_path=self.config.poppler_path,
        )

        orig_img_path = os.path.join(
            self.config.extracted_images_dir, f"preview_p{page_number:05d}_orig.jpg"
        )
        if pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")
        pil_img.save(orig_img_path, format="JPEG", quality=85, optimize=True)

        # 2. Preprocess & dewarp
        preprocessor = PagePreprocessor(self.config)
        processed_pil, preproc_meta = preprocessor.process_page(pil_img, page_number)

        proc_img_path = os.path.join(
            self.config.extracted_images_dir, f"preview_p{page_number:05d}_proc.jpg"
        )
        if processed_pil.mode not in ("RGB", "L"):
            proc_save = processed_pil.convert("RGB")
        else:
            proc_save = processed_pil
        proc_save.save(proc_img_path, format="JPEG", quality=85, optimize=True)

        # 3. OCR Engine
        ocr_engine = OCREngine(self.config)
        ocr_raw = ocr_engine.run_ocr_on_page(
            processed_pil,
            page_number=page_number,
            illustrations=preproc_meta.get("illustrations", []),
        )

        # 4. Linguistic correction & BiDi shaping
        corrector = TextCorrector(self.config)
        ocr_corrected = corrector.process_ocr_page(ocr_raw)

        # 5. Layout, poetry & table analysis
        analyzer = LayoutAnalyzer(self.config)
        page_layout = analyzer.analyze_page(ocr_corrected, preproc_meta)

        elapsed = round(time.time() - start_ts, 2)
        extracted_text = extract_page_plain_text(page_layout)

        preview_payload = {
            "status": "success",
            "page_number": page_number,
            "elapsed_sec": elapsed,
            "mean_confidence": page_layout["mean_confidence"],
            "word_count": page_layout["word_count"],
            "low_confidence_word_count": page_layout["low_confidence_word_count"],
            "page_direction": page_layout["page_direction"],
            "column_count": page_layout["column_count"],
            "orig_image_path": orig_img_path,
            "proc_image_path": proc_img_path,
            "preview_image_path": proc_img_path,
            "layout": page_layout,
            "extracted_text": extracted_text,
            "illustrations": preproc_meta.get("illustrations", []),
            "error": None,
        }

        self.log(
            f"Preview for Page {page_number} completed in {elapsed}s | "
            f"Conf: {page_layout['mean_confidence']:.1f}% | "
            f"Words: {page_layout['word_count']}"
        )
        return preview_payload

    def run(self) -> Dict[str, Any]:
        self._pause_requested = False
        self._stop_requested = False
        self.config.ensure_directories()

        if not os.path.exists(self.config.input_pdf_path):
            raise FileNotFoundError(f"Input PDF not found: {self.config.input_pdf_path}")

        total_pages = get_pdf_page_count(
            self.config.input_pdf_path, poppler_path=self.config.poppler_path
        )
        self.log(f"Detected {total_pages} total pages in '{os.path.basename(self.config.input_pdf_path)}'.")

        # Determine target page range
        start_p = max(1, self.config.start_page or 1)
        end_p = total_pages
        if self.config.end_page and self.config.end_page > 0:
            end_p = min(total_pages, self.config.end_page)
        if start_p > end_p:
            start_p = 1
            end_p = total_pages

        target_pages = list(range(start_p, end_p + 1))
        target_count = len(target_pages)
        self.log(f"Targeting pages {start_p} to {end_p} ({target_count} pages).")

        existing = self._load_existing_checkpoints(start_p, end_p)
        if existing:
            self.log(f"Resuming: found {len(existing)} already completed page checkpoints in range {start_p}-{end_p}.")
            for pnum in sorted(existing.keys()):
                if self.on_page_complete:
                    try:
                        self.on_page_complete(existing[pnum], len(existing), target_count, None)
                    except TypeError:
                        self.on_page_complete(existing[pnum], len(existing), target_count)

        pending_pages = [p for p in target_pages if p not in existing]
        all_results: Dict[int, Dict[str, Any]] = dict(existing)

        batch_start_ts = time.time()
        initial_completed = len(existing)

        if pending_pages and not self._stop_requested:
            workers = max(1, min(self.config.max_workers, len(pending_pages)))
            self.log(f"Starting OCR multiprocessing pool with {workers} workers for {len(pending_pages)} pages...")

            cfg_dict = self.config.to_dict()
            page_iter = iter(pending_pages)
            in_flight: Dict[concurrent.futures.Future, int] = {}

            pool_kwargs: Dict[str, Any] = {"max_workers": workers}
            if sys.version_info >= (3, 11):
                pool_kwargs["max_tasks_per_child"] = 25

            with concurrent.futures.ProcessPoolExecutor(**pool_kwargs) as executor:
                # Prime the worker pool
                while len(in_flight) < workers and not self._pause_requested and not self._stop_requested:
                    try:
                        next_p = next(page_iter)
                        fut = executor.submit(process_single_page_worker, (cfg_dict, next_p))
                        in_flight[fut] = next_p
                    except StopIteration:
                        break

                while in_flight and not self._stop_requested:
                    done, _ = concurrent.futures.wait(
                        in_flight.keys(),
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for fut in done:
                        pnum = in_flight.pop(fut)
                        try:
                            res = fut.result()
                        except Exception as exc:
                            res = {
                                "status": "failed",
                                "page_number": pnum,
                                "elapsed_sec": 0.0,
                                "mean_confidence": 0.0,
                                "word_count": 0,
                                "low_confidence_word_count": 0,
                                "page_direction": "N/A",
                                "column_count": 0,
                                "preview_image_path": "",
                                "extracted_text": "",
                                "layout": None,
                                "error": str(exc),
                            }
                        all_results[pnum] = res

                        # Compute real-time throughput & ETA
                        done_count = len(all_results)
                        run_done = done_count - initial_completed
                        elapsed_sec = time.time() - batch_start_ts
                        pages_per_min = (
                            (run_done / elapsed_sec * 60.0)
                            if elapsed_sec > 0.5 and run_done > 0
                            else 0.0
                        )
                        remaining = max(0, target_count - done_count)
                        eta_sec = (
                            (remaining / (run_done / elapsed_sec))
                            if run_done > 0 and elapsed_sec > 0
                            else 0.0
                        )

                        elapsed_str = time.strftime("%H:%M:%S", time.gmtime(int(elapsed_sec)))
                        eta_str = (
                            time.strftime("%H:%M:%S", time.gmtime(int(eta_sec)))
                            if eta_sec > 0
                            else "--:--:--"
                        )

                        stats = {
                            "elapsed_sec": round(elapsed_sec, 1),
                            "elapsed_str": elapsed_str,
                            "pages_per_min": round(pages_per_min, 1),
                            "eta_sec": round(eta_sec, 1),
                            "eta_str": eta_str,
                            "done_count": done_count,
                            "total_target": target_count,
                            "remaining": remaining,
                        }

                        if self.on_page_complete:
                            try:
                                self.on_page_complete(res, done_count, target_count, stats)
                            except TypeError:
                                self.on_page_complete(res, done_count, target_count)

                        if res["status"] == "success":
                            self.log(
                                f"Page {pnum}/{end_p} complete | "
                                f"Conf: {res['mean_confidence']:.1f}% | "
                                f"Speed: {pages_per_min:.1f} p/min | "
                                f"ETA: {eta_str}"
                            )
                        else:
                            self.log(f"Page {pnum}/{end_p} FAILED: {res.get('error')}")

                        if self._stop_requested:
                            break

                        if not self._pause_requested:
                            try:
                                next_p = next(page_iter)
                                new_fut = executor.submit(
                                    process_single_page_worker, (cfg_dict, next_p)
                                )
                                in_flight[new_fut] = next_p
                            except StopIteration:
                                pass

                # If abort was requested, cancel remaining active futures
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

        # Compile final outputs from all successful pages in range
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
