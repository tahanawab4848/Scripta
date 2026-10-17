import html
import json
import multiprocessing
import os
import platform
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Any, Optional, List
from PIL import Image, ImageTk

from config import DEFAULT_CONFIG, AppConfig, LANGUAGE_PRESETS, check_system_health
from batch_processor import BatchProcessor, extract_page_plain_text
from ocr_engine import get_pdf_page_count


class BookDigitizerGUI:
    """
    Production-grade desktop GUI for digitizing scanned multilingual PDF books
    (English, Arabic, Persian, Urdu) into formatted DOCX, searchable PDF,
    reflowable EPUB3, print-ready HTML replicas, and clean UTF-8 text.

    Features:
    - Page Range selector (start/end page filter).
    - Abort / Cancel button with clean multiprocessing worker pool termination.
    - Single-Page Test / Preview mode with instant preprocessing and OCR inspection.
    - Interactive dual-pane Page Inspector (image preview + formatted text side-by-side).
    - Real-time throughput (pages/min), elapsed time, and ETA calculations.
    - Quick 1-click output file launchers (DOCX, PDF, EPUB, HTML, Report).
    - Modern ttk card styling with confidence-coded status tags.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Multilingual Book Digitizer — English / Arabic / Persian / Urdu")
        self.root.geometry("1340x920")
        self.root.minsize(1100, 780)

        self.config: AppConfig = AppConfig.from_dict(DEFAULT_CONFIG.to_dict())
        self.processor: Optional[BatchProcessor] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        # Cache of page results for instant inspector lookup
        self.page_results_cache: Dict[int, Dict[str, Any]] = {}
        self._current_inspector_page: int = 1
        self._current_photo_image: Optional[ImageTk.PhotoImage] = None
        self._last_canvas_w: int = 0
        self._last_canvas_h: int = 0

        self._init_tk_vars()
        self._setup_styles()
        self._build_layout()
        self.root.after(100, self._poll_queue)

    def _init_tk_vars(self) -> None:
        self.input_pdf_var = tk.StringVar(value=self.config.input_pdf_path)
        self.output_dir_var = tk.StringVar(value=self.config.output_dir)
        self.preset_var = tk.StringVar(value=self.config.language_preset)
        self.lang_var = tk.StringVar(value=self.config.languages)
        self.dpi_var = tk.IntVar(value=self.config.dpi)
        self.conf_var = tk.DoubleVar(value=self.config.confidence_threshold)

        # Page Range & Preview Vars
        self.start_page_var = tk.StringVar(value="1")
        self.end_page_var = tk.StringVar(value="")
        self.preview_page_var = tk.StringVar(value="1")

        # Output Formats
        self.docx_var = tk.BooleanVar(value=self.config.export_docx)
        self.sandwich_pdf_var = tk.BooleanVar(value=getattr(self.config, "export_sandwich_pdf", True))
        self.pdf_var = tk.BooleanVar(value=self.config.export_pdf)
        self.epub_var = tk.BooleanVar(value=self.config.export_epub)
        self.txt_var = tk.BooleanVar(value=self.config.export_txt)
        self.html_var = tk.BooleanVar(value=self.config.export_html_weasyprint)
        self.alto_var = tk.BooleanVar(value=getattr(self.config, "export_alto_xml", True))
        self.pagexml_var = tk.BooleanVar(value=getattr(self.config, "export_page_xml", True))
        self.historical_ortho_var = tk.BooleanVar(
            value=getattr(self.config, "enable_historical_orthography_mode", True)
        )


        # Preprocessing & Dewarping
        self.deskew_var = tk.BooleanVar(value=self.config.enable_deskew)
        self.dewarp_var = tk.BooleanVar(value=self.config.enable_polynomial_dewarping)
        self.spine_var = tk.BooleanVar(value=self.config.enable_spine_shadow_removal)
        self.bleed_var = tk.BooleanVar(value=self.config.enable_bleed_through_reduction)
        self.denoise_var = tk.BooleanVar(value=self.config.enable_denoise)
        self.contrast_var = tk.BooleanVar(value=self.config.enable_contrast_normalization)
        self.thresh_var = tk.BooleanVar(value=self.config.enable_adaptive_threshold)
        self.border_var = tk.BooleanVar(value=self.config.enable_border_removal)
        self.poetry_var = tk.BooleanVar(value=self.config.enable_poetry_detection)
        self.morphology_var = tk.BooleanVar(value=self.config.enable_clitic_morphology)

        # OCR Backend & Vision LLM
        self.backend_var = tk.StringVar(value=self.config.ocr_backend)
        self.api_key_var = tk.StringVar(value=self.config.vision_llm_api_key or "")
        self.born_digital_var = tk.BooleanVar(
            value=getattr(self.config, "detect_born_digital_pdf", True)
        )

        # Real-time Metrics & Status
        self.status_var = tk.StringVar(value="Ready. Select a scanned PDF book and click Start Conversion.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.speed_var = tk.StringVar(value="Speed: 0.0 p/min")
        self.elapsed_var = tk.StringVar(value="Elapsed: 00:00:00")
        self.eta_var = tk.StringVar(value="ETA: --:--:--")
        self.progress_count_var = tk.StringVar(value="Pages: 0 / 0")
        self.mean_conf_var = tk.StringVar(value="Mean Conf: 0.0%")

        # Inspector View Vars
        self.inspector_page_var = tk.StringVar(value="1")
        self.inspector_view_mode_var = tk.StringVar(value="processed")  # "processed" or "original"
        self.inspector_meta_var = tk.StringVar(value="No page loaded. Select a page from the table or enter a page number.")

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            if "vista" in style.theme_names():
                style.theme_use("vista")
            elif "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        # Card & LabelFrame styling
        style.configure("TLabelframe", font=("Segoe UI", 9, "bold"))
        style.configure("TLabelframe.Label", font=("Segoe UI", 9, "bold"))
        style.configure("Metrics.TLabel", font=("Consolas", 10, "bold"), foreground="#0d47a1")
        style.configure("Bold.TLabel", font=("Segoe UI", 9, "bold"))
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#555555")

    def _build_layout(self) -> None:
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Top Title Header
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(
            header_frame,
            text="Multilingual Scanned Book Digitizer",
            style="Header.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Label(
            header_frame,
            text=" |  English / Arabic (العربية) / Persian (فارسی) / Urdu (اردو)  —  DOCX, PDF, EPUB, HTML Replicas",
            style="SubHeader.TLabel",
        ).pack(side=tk.LEFT, padx=(4, 0), pady=(3, 0))

        # 1. Config Cards Row
        cards_container = ttk.Frame(main_frame)
        cards_container.pack(fill=tk.X, pady=(0, 6))

        self._build_paths_section(cards_container)
        self._build_engine_section(cards_container)
        self._build_options_section(cards_container)

        # 2. Action Controls & Quick Launch Toolbar
        self._build_controls_bar(main_frame)

        # 3. Live Throughput & ETA Metrics Bar
        self._build_metrics_bar(main_frame)

        # 4. Central Notebook: Dashboard vs Page Inspector
        self._build_notebook_tabs(main_frame)

    def _build_paths_section(self, parent: ttk.Frame) -> None:
        paths_group = ttk.LabelFrame(parent, text="1. Scanned PDF & Page Selection", padding=8)
        paths_group.pack(fill=tk.X, pady=(0, 4))

        # Row 0: Input PDF
        ttk.Label(paths_group, text="Input PDF:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        ttk.Entry(paths_group, textvariable=self.input_pdf_var, width=54).grid(
            row=0, column=1, sticky=tk.EW, padx=4, pady=2
        )
        ttk.Button(paths_group, text="Browse PDF...", command=self._pick_pdf).grid(
            row=0, column=2, padx=4, pady=2
        )

        # Row 1: Output Folder
        ttk.Label(paths_group, text="Output Dir:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        ttk.Entry(paths_group, textvariable=self.output_dir_var, width=54).grid(
            row=1, column=1, sticky=tk.EW, padx=4, pady=2
        )
        ttk.Button(paths_group, text="Browse Dir...", command=self._pick_output_dir).grid(
            row=1, column=2, padx=4, pady=2
        )

        # Row 2: Page Range & Single-Page Preview Controls
        range_frame = ttk.Frame(paths_group)
        range_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, padx=4, pady=(4, 0))

        ttk.Label(range_frame, text="Pages: From:").pack(side=tk.LEFT, padx=(0, 2))
        ttk.Spinbox(range_frame, from_=1, to=99999, textvariable=self.start_page_var, width=5).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Label(range_frame, text="To:").pack(side=tk.LEFT, padx=(4, 2))
        ttk.Entry(range_frame, textvariable=self.end_page_var, width=6).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Label(range_frame, text="(blank = all)", font=("Segoe UI", 8), foreground="#666").pack(
            side=tk.LEFT, padx=(0, 16)
        )

        ttk.Separator(range_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(range_frame, text="Test Single Page #:").pack(side=tk.LEFT, padx=(4, 2))
        ttk.Spinbox(range_frame, from_=1, to=99999, textvariable=self.preview_page_var, width=5).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self.preview_btn = ttk.Button(
            range_frame, text="🔍 Test / Preview Page", command=self._test_preview_page
        )
        self.preview_btn.pack(side=tk.LEFT, padx=2)

        paths_group.columnconfigure(1, weight=1)

    def _build_engine_section(self, parent: ttk.Frame) -> None:
        engine_group = ttk.LabelFrame(parent, text="2. Language Profile & OCR Engine Backend", padding=8)
        engine_group.pack(fill=tk.X, pady=(0, 4))

        row1 = ttk.Frame(engine_group)
        row1.pack(fill=tk.X, pady=1)

        ttk.Label(row1, text="Language Profile:").pack(side=tk.LEFT, padx=(4, 2))
        preset_names = list(LANGUAGE_PRESETS.keys())
        self.preset_combo = ttk.Combobox(
            row1,
            textvariable=self.preset_var,
            values=preset_names,
            state="readonly",
            width=20,
        )
        self.preset_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_change)

        ttk.Label(row1, text="Tesseract Codes:").pack(side=tk.LEFT, padx=(4, 2))
        ttk.Entry(row1, textvariable=self.lang_var, width=18).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(row1, text="OCR Backend:").pack(side=tk.LEFT, padx=(4, 2))
        self.backend_combo = ttk.Combobox(
            row1,
            textvariable=self.backend_var,
            values=["tesseract", "hybrid", "vision_llm"],
            state="readonly",
            width=12,
        )
        self.backend_combo.pack(side=tk.LEFT, padx=(0, 8))

        row2 = ttk.Frame(engine_group)
        row2.pack(fill=tk.X, pady=2)

        ttk.Label(row2, text="Vision API Key (Optional):").pack(side=tk.LEFT, padx=(4, 2))
        ttk.Entry(row2, textvariable=self.api_key_var, show="*", width=32).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Label(
            row2,
            text="(For Hybrid / Vision LLM fallback)",
            font=("Segoe UI", 8),
            foreground="#555",
        ).pack(side=tk.LEFT)

        ttk.Checkbutton(
            row2,
            text="⚡ Auto-Extract Born-Digital Vector Text (10x Faster)",
            variable=self.born_digital_var,
        ).pack(side=tk.LEFT, padx=(16, 4))


    def _build_options_section(self, parent: ttk.Frame) -> None:
        opts_row = ttk.Frame(parent)
        opts_row.pack(fill=tk.X, pady=(0, 4))

        # Formats
        fmt_group = ttk.LabelFrame(opts_row, text="3. Output Formats & Archival Metadata", padding=6)
        fmt_group.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 4))
        ttk.Checkbutton(fmt_group, text="DOCX", variable=self.docx_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(fmt_group, text="Sandwich PDF", variable=self.sandwich_pdf_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(fmt_group, text="Reflowable PDF", variable=self.pdf_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(fmt_group, text="EPUB3", variable=self.epub_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(fmt_group, text="HTML5", variable=self.html_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(fmt_group, text="TXT", variable=self.txt_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(fmt_group, text="ALTO XML", variable=self.alto_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(fmt_group, text="PageXML", variable=self.pagexml_var).pack(side=tk.LEFT, padx=2)

        # Preprocessing & Dewarping
        pre_group = ttk.LabelFrame(opts_row, text="4. Physical Scan Correction & Layout", padding=6)
        pre_group.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Checkbutton(pre_group, text="Deskew", variable=self.deskew_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(pre_group, text="3D Dewarp", variable=self.dewarp_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(pre_group, text="Spine Shadow", variable=self.spine_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(pre_group, text="Bleed-Thru", variable=self.bleed_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(pre_group, text="Poetry", variable=self.poetry_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(pre_group, text="Clitics", variable=self.morphology_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(pre_group, text="Classical Ortho", variable=self.historical_ortho_var).pack(side=tk.LEFT, padx=2)


        ttk.Label(pre_group, text="DPI:").pack(side=tk.LEFT, padx=(6, 2))
        ttk.Spinbox(pre_group, from_=200, to=600, increment=50, textvariable=self.dpi_var, width=4).pack(
            side=tk.LEFT
        )
        ttk.Label(pre_group, text="Min Conf:").pack(side=tk.LEFT, padx=(6, 2))
        ttk.Spinbox(pre_group, from_=50, to=95, increment=1, textvariable=self.conf_var, width=3).pack(
            side=tk.LEFT
        )

    def _build_controls_bar(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=(2, 4))

        # Batch Run Controls
        run_box = ttk.Frame(toolbar)
        run_box.pack(side=tk.LEFT)

        self.start_btn = ttk.Button(
            run_box, text="▶ Start Conversion", command=self._start_run
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.pause_btn = ttk.Button(
            run_box, text="⏸ Pause", command=self._pause_run, state=tk.DISABLED
        )
        self.pause_btn.pack(side=tk.LEFT, padx=4)

        self.resume_btn = ttk.Button(
            run_box, text="⏯ Resume", command=self._resume_run, state=tk.DISABLED
        )
        self.resume_btn.pack(side=tk.LEFT, padx=4)

        self.stop_btn = ttk.Button(
            run_box, text="⏹ Cancel / Stop", command=self._stop_run, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        self.health_btn = ttk.Button(
            run_box, text="🩺 System Health", command=self._show_system_health
        )
        self.health_btn.pack(side=tk.LEFT, padx=4)

        # Quick Output Launchers
        out_box = ttk.Frame(toolbar)
        out_box.pack(side=tk.RIGHT)

        ttk.Label(out_box, text="Quick Open Output:", font=("Segoe UI", 9, "bold")).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(out_box, text="📄 DOCX", command=lambda: self._launch_file("docx")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(out_box, text="📑 Sandwich PDF", command=lambda: self._launch_file("sandwich_pdf")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(out_box, text="📑 Reflow PDF", command=lambda: self._launch_file("pdf")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(out_box, text="📖 EPUB", command=lambda: self._launch_file("epub")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(out_box, text="🌐 HTML", command=lambda: self._launch_file("html")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(out_box, text="📝 Report", command=lambda: self._launch_file("report")).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(out_box, text="📁 Folder", command=self._open_output_folder).pack(
            side=tk.LEFT, padx=(2, 0)
        )

    def _build_metrics_bar(self, parent: ttk.Frame) -> None:
        metrics_card = ttk.Frame(parent, padding=(4, 2))
        metrics_card.pack(fill=tk.X, pady=(0, 4))

        # Real-time Metrics Pills
        pills_frame = ttk.Frame(metrics_card)
        pills_frame.pack(fill=tk.X, pady=(0, 2))

        ttk.Label(pills_frame, textvariable=self.speed_var, style="Metrics.TLabel").pack(side=tk.LEFT, padx=(0, 14))
        ttk.Label(pills_frame, textvariable=self.elapsed_var, style="Metrics.TLabel").pack(side=tk.LEFT, padx=14)
        ttk.Label(pills_frame, textvariable=self.eta_var, style="Metrics.TLabel").pack(side=tk.LEFT, padx=14)
        ttk.Label(pills_frame, textvariable=self.progress_count_var, style="Metrics.TLabel").pack(side=tk.LEFT, padx=14)
        ttk.Label(pills_frame, textvariable=self.mean_conf_var, style="Metrics.TLabel").pack(side=tk.LEFT, padx=14)

        # Progress bar & Status text
        self.progress_bar = ttk.Progressbar(
            metrics_card, variable=self.progress_var, maximum=100.0, mode="determinate"
        )
        self.progress_bar.pack(fill=tk.X, pady=(2, 2))

        ttk.Label(metrics_card, textvariable=self.status_var, font=("Segoe UI", 9)).pack(anchor=tk.W)

    def _build_notebook_tabs(self, parent: ttk.Frame) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.dashboard_tab = ttk.Frame(self.notebook, padding=6)
        self.inspector_tab = ttk.Frame(self.notebook, padding=6)

        self.notebook.add(self.dashboard_tab, text="  📋 Batch Dashboard & Progress  ")
        self.notebook.add(self.inspector_tab, text="  🔎 Interactive Page Inspector & Proofreader  ")

        self._build_dashboard_tab()
        self._build_inspector_tab()

    def _build_dashboard_tab(self) -> None:
        paned = ttk.PanedWindow(self.dashboard_tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left: Live Log
        log_frame = ttk.LabelFrame(paned, text="Live Pipeline Execution Log", padding=6)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=18, font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        log_actions = ttk.Frame(log_frame)
        log_actions.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(log_actions, text="Clear Log", command=self._clear_log).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_actions, text="Save Log...", command=self._save_log).pack(side=tk.LEFT, padx=2)

        paned.add(log_frame, weight=1)

        # Right: Per-Page Confidence & Status Table
        conf_frame = ttk.LabelFrame(paned, text="Per-Page Confidence & Layout Table", padding=6)
        columns = ("page", "status", "conf", "words", "flagged", "dir", "cols")
        self.tree = ttk.Treeview(conf_frame, columns=columns, show="headings", height=18)
        self.tree.heading("page", text="Page #")
        self.tree.heading("status", text="Status")
        self.tree.heading("conf", text="Confidence")
        self.tree.heading("words", text="Words")
        self.tree.heading("flagged", text="Flagged Low-Conf")
        self.tree.heading("dir", text="Script Dir")
        self.tree.heading("cols", text="Columns")

        self.tree.column("page", width=65, anchor=tk.CENTER)
        self.tree.column("status", width=85, anchor=tk.CENTER)
        self.tree.column("conf", width=95, anchor=tk.CENTER)
        self.tree.column("words", width=75, anchor=tk.CENTER)
        self.tree.column("flagged", width=115, anchor=tk.CENTER)
        self.tree.column("dir", width=80, anchor=tk.CENTER)
        self.tree.column("cols", width=70, anchor=tk.CENTER)

        # Confidence color tags
        self.tree.tag_configure("high_conf", background="#e8f5e9", foreground="#1b5e20")
        self.tree.tag_configure("med_conf", background="#fffde7", foreground="#e65100")
        self.tree.tag_configure("low_conf", background="#ffebee", foreground="#b71c1c")
        self.tree.tag_configure("failed", background="#ffcdd2", foreground="#b71c1c")

        tree_scroll = ttk.Scrollbar(conf_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        tree_actions = ttk.Frame(conf_frame)
        tree_actions.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(
            tree_actions,
            text="🔍 Open Selected Page in Inspector",
            command=self._open_selected_in_inspector,
        ).pack(side=tk.LEFT, padx=2)

        paned.add(conf_frame, weight=1)

    def _build_inspector_tab(self) -> None:
        # Top Inspector Navigation & Toolbar
        insp_bar = ttk.Frame(self.inspector_tab, padding=(2, 4))
        insp_bar.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(insp_bar, text="◀ Previous Page", command=self._inspector_prev_page).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Label(insp_bar, text="Page:").pack(side=tk.LEFT, padx=(6, 2))
        ttk.Spinbox(
            insp_bar,
            from_=1,
            to=99999,
            textvariable=self.inspector_page_var,
            width=5,
            command=self._inspector_jump_page,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(insp_bar, text="Go", command=self._inspector_jump_page).pack(side=tk.LEFT, padx=2)
        ttk.Button(insp_bar, text="Next Page ▶", command=self._inspector_next_page).pack(
            side=tk.LEFT, padx=4
        )

        ttk.Separator(insp_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Button(
            insp_bar, text="🔄 Re-render / Test Page", command=self._reprocess_current_inspector_page
        ).pack(side=tk.LEFT, padx=4)

        ttk.Separator(insp_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(insp_bar, text="View:").pack(side=tk.LEFT, padx=(2, 2))
        ttk.Radiobutton(
            insp_bar,
            text="Preprocessed Scan",
            variable=self.inspector_view_mode_var,
            value="processed",
            command=self._render_current_inspector_image,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(
            insp_bar,
            text="Original Scan",
            variable=self.inspector_view_mode_var,
            value="original",
            command=self._render_current_inspector_image,
        ).pack(side=tk.LEFT, padx=2)

        # Right aligned export actions
        ttk.Button(insp_bar, text="💾 Save Page (.txt)", command=self._save_inspector_text).pack(
            side=tk.RIGHT, padx=2
        )
        ttk.Button(insp_bar, text="📋 Copy Text", command=self._copy_inspector_text).pack(
            side=tk.RIGHT, padx=2
        )

        # Page Metadata Badge
        meta_frame = ttk.Frame(self.inspector_tab, padding=(4, 2))
        meta_frame.pack(fill=tk.X, pady=(0, 4))
        self.inspector_meta_label = ttk.Label(
            meta_frame,
            textvariable=self.inspector_meta_var,
            font=("Segoe UI", 9, "bold"),
            foreground="#1565c0",
        )
        self.inspector_meta_label.pack(side=tk.LEFT)

        # Dual Pane: Scan Image (Left) & Extracted Text Proofreader (Right)
        paned = ttk.PanedWindow(self.inspector_tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left Pane: Image Viewer Canvas
        img_frame = ttk.LabelFrame(paned, text="Page Scan Viewer (Auto-Fit to View)", padding=4)
        self.image_canvas = tk.Canvas(img_frame, background="#2c2c2c", highlightthickness=0)
        self.image_canvas.pack(fill=tk.BOTH, expand=True)
        self.image_canvas.bind("<Configure>", self._on_canvas_configure)
        paned.add(img_frame, weight=1)

        # Right Pane: Formatted Extracted Text Proofreader
        text_frame = ttk.LabelFrame(
            paned, text="Extracted Text & Elements Proofreader (Unicode / RTL / Couplets)", padding=4
        )
        self.inspector_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=("Tahoma", 12),
            background="#ffffff",
            foreground="#111111",
            padx=12,
            pady=12,
        )
        text_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.inspector_text.yview)
        self.inspector_text.configure(yscrollcommand=text_scroll.set)
        self.inspector_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Syntax tags for Proofreader
        self.inspector_text.tag_configure("rtl_text", justify=tk.RIGHT)
        self.inspector_text.tag_configure("ltr_text", justify=tk.LEFT)
        self.inspector_text.tag_configure(
            "low_conf_highlight",
            background="#fff59d",
            foreground="#b71c1c",
            font=("Tahoma", 12, "bold"),
        )
        self.inspector_text.tag_configure("header_tag", font=("Tahoma", 13, "bold"), foreground="#0d47a1")
        self.inspector_text.tag_configure("poetry_tag", foreground="#4a148c", font=("Tahoma", 12, "italic"))

        paned.add(text_frame, weight=1)

    # -------------------------------------------------------------------------
    # Preset & File Picker Handlers
    # -------------------------------------------------------------------------
    def _on_preset_change(self, event=None) -> None:
        chosen = self.preset_var.get()
        self.config.apply_preset(chosen)
        self.lang_var.set(self.config.languages)

    def _pick_pdf(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Scanned PDF Book",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")],
        )
        if selected:
            self.input_pdf_var.set(selected)

    def _pick_output_dir(self) -> None:
        selected = filedialog.askdirectory(title="Select Output Directory")
        if selected:
            self.output_dir_var.set(selected)

    def _sync_config_from_ui(self) -> None:
        self.config.input_pdf_path = self.input_pdf_var.get().strip()
        self.config.output_dir = self.output_dir_var.get().strip()
        self.config.languages = self.lang_var.get().strip()
        self.config.dpi = int(self.dpi_var.get())
        self.config.confidence_threshold = float(self.conf_var.get())

        # Sync Start Page
        try:
            s_val = int(self.start_page_var.get().strip())
            self.config.start_page = max(1, s_val)
        except (ValueError, TypeError):
            self.config.start_page = 1
            self.start_page_var.set("1")

        # Sync End Page
        e_str = self.end_page_var.get().strip()
        if e_str and e_str.lower() not in ("all", "0", "-1"):
            try:
                self.config.end_page = max(self.config.start_page, int(e_str))
            except (ValueError, TypeError):
                self.config.end_page = None
                self.end_page_var.set("")
        else:
            self.config.end_page = None

        self.config.export_docx = bool(self.docx_var.get())
        self.config.export_sandwich_pdf = bool(self.sandwich_pdf_var.get())
        self.config.export_pdf = bool(self.pdf_var.get())
        self.config.export_epub = bool(self.epub_var.get())
        self.config.export_txt = bool(self.txt_var.get())
        self.config.export_html_weasyprint = bool(self.html_var.get())
        self.config.export_alto_xml = bool(self.alto_var.get())
        self.config.export_page_xml = bool(self.pagexml_var.get())

        self.config.enable_deskew = bool(self.deskew_var.get())
        self.config.enable_polynomial_dewarping = bool(self.dewarp_var.get())
        self.config.enable_denoise = bool(self.denoise_var.get())
        self.config.enable_contrast_normalization = bool(self.contrast_var.get())
        self.config.enable_adaptive_threshold = bool(self.thresh_var.get())
        self.config.enable_border_removal = bool(self.border_var.get())
        self.config.enable_spine_shadow_removal = bool(self.spine_var.get())
        self.config.enable_bleed_through_reduction = bool(self.bleed_var.get())
        self.config.enable_poetry_detection = bool(self.poetry_var.get())
        self.config.enable_clitic_morphology = bool(self.morphology_var.get())
        self.config.enable_historical_orthography_mode = bool(self.historical_ortho_var.get())


        self.config.ocr_backend = self.backend_var.get().strip()
        self.config.detect_born_digital_pdf = bool(self.born_digital_var.get())
        key_input = self.api_key_var.get().strip()
        if key_input:
            self.config.vision_llm_api_key = key_input


    # -------------------------------------------------------------------------
    # Batch Run & Cancellation Handlers
    # -------------------------------------------------------------------------
    def _start_run(self) -> None:
        self._sync_config_from_ui()
        if not os.path.exists(self.config.input_pdf_path):
            messagebox.showerror(
                "Missing Input PDF", f"Scanned PDF file not found:\n{self.config.input_pdf_path}"
            )
            return

        self.tree.delete(*self.tree.get_children())
        self.page_results_cache.clear()
        self.start_btn.configure(state=tk.DISABLED)
        self.pause_btn.configure(state=tk.NORMAL)
        self.resume_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.preview_btn.configure(state=tk.DISABLED)

        self._launch_worker()

    def _pause_run(self) -> None:
        if self.processor:
            self.processor.request_pause()
            self.pause_btn.configure(state=tk.DISABLED)
            self.resume_btn.configure(state=tk.NORMAL)

    def _resume_run(self) -> None:
        self._sync_config_from_ui()
        self.start_btn.configure(state=tk.DISABLED)
        self.pause_btn.configure(state=tk.NORMAL)
        self.resume_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.preview_btn.configure(state=tk.DISABLED)
        self._launch_worker()

    def _stop_run(self) -> None:
        if not self.processor:
            return
        confirm = messagebox.askyesno(
            "Confirm Abort",
            "Are you sure you want to stop the digitization run?\n"
            "Active worker processes will be cleanly terminated, and all completed page checkpoints will be preserved.",
        )
        if not confirm:
            return

        self.stop_btn.configure(state=tk.DISABLED)
        self.status_var.set("Stopping pipeline and terminating worker pool...")
        self.processor.request_stop()

    def _launch_worker(self) -> None:
        def on_page(
            res: Dict[str, Any], done_count: int, total_count: int, stats: Optional[Dict[str, Any]] = None
        ) -> None:
            self.ui_queue.put(
                {
                    "type": "page",
                    "result": res,
                    "done": done_count,
                    "total": total_count,
                    "stats": stats,
                }
            )

        def on_log(msg: str) -> None:
            self.ui_queue.put({"type": "log", "message": msg})

        def run_thread() -> None:
            try:
                self.processor = BatchProcessor(
                    self.config, on_page_complete=on_page, on_log=on_log
                )
                summary = self.processor.run()
                self.ui_queue.put({"type": "finished", "summary": summary})
            except Exception as exc:
                self.ui_queue.put({"type": "error", "message": str(exc)})

        self.worker_thread = threading.Thread(target=run_thread, daemon=True)
        self.worker_thread.start()

    # -------------------------------------------------------------------------
    # Single-Page Test / Preview Mode
    # -------------------------------------------------------------------------
    def _test_preview_page(self) -> None:
        self._sync_config_from_ui()
        if not os.path.exists(self.config.input_pdf_path):
            messagebox.showerror(
                "Missing Input PDF", f"File not found:\n{self.config.input_pdf_path}"
            )
            return

        try:
            pnum = int(self.preview_page_var.get().strip())
            if pnum < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid Page Number", "Please enter a valid positive page number."
            )
            return

        self.preview_btn.configure(state=tk.DISABLED)
        self.status_var.set(f"Generating instant preview for Page #{pnum}...")

        def run_preview() -> None:
            try:
                temp_proc = BatchProcessor(
                    self.config, on_log=lambda m: self.ui_queue.put({"type": "log", "message": m})
                )
                result = temp_proc.preview_single_page(pnum)
                self.ui_queue.put({"type": "preview_ready", "result": result})
            except Exception as exc:
                self.ui_queue.put({"type": "preview_error", "message": str(exc)})

        threading.Thread(target=run_preview, daemon=True).start()

    def _reprocess_current_inspector_page(self) -> None:
        self.preview_page_var.set(str(self._current_inspector_page))
        self._test_preview_page()

    # -------------------------------------------------------------------------
    # Queue Polling & Event Loop
    # -------------------------------------------------------------------------
    def _poll_queue(self) -> None:
        while True:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            etype = event.get("type")
            if etype == "log":
                self.log_text.insert(tk.END, event["message"] + "\n")
                self.log_text.see(tk.END)

            elif etype == "page":
                res = event["result"]
                done = event["done"]
                total = max(1, event["total"])
                stats = event.get("stats")

                pnum = res["page_number"]
                self.page_results_cache[pnum] = res

                pct = (done / float(total)) * 100.0
                self.progress_var.set(pct)

                if stats:
                    self.speed_var.set(f"Speed: {stats['pages_per_min']:.1f} p/min")
                    self.elapsed_var.set(f"Elapsed: {stats['elapsed_str']}")
                    self.eta_var.set(f"ETA: {stats['eta_str']}")
                    self.progress_count_var.set(f"Pages: {done} / {total}")

                self.status_var.set(
                    f"Processed {done}/{total} pages ({pct:.1f}%) — Page #{pnum} Conf: {res['mean_confidence']:.1f}%"
                )

                # Insert / Update row in Treeview with confidence coloring
                item_id = f"p_{pnum}"
                row_vals = (
                    pnum,
                    res["status"].upper(),
                    f"{res['mean_confidence']:.1f}%",
                    res["word_count"],
                    res["low_confidence_word_count"],
                    res["page_direction"],
                    res["column_count"],
                )

                conf = float(res.get("mean_confidence", 0.0))
                low_cnt = int(res.get("low_confidence_word_count", 0))
                if res["status"] != "success":
                    row_tag = "failed"
                elif conf >= 80.0 and low_cnt == 0:
                    row_tag = "high_conf"
                elif conf >= 65.0:
                    row_tag = "med_conf"
                else:
                    row_tag = "low_conf"

                if self.tree.exists(item_id):
                    self.tree.item(item_id, values=row_vals, tags=(row_tag,))
                else:
                    self.tree.insert("", tk.END, iid=item_id, values=row_vals, tags=(row_tag,))
                self.tree.see(item_id)

            elif etype == "preview_ready":
                res = event["result"]
                pnum = res["page_number"]
                self.page_results_cache[pnum] = res
                self.preview_btn.configure(state=tk.NORMAL)
                self.status_var.set(
                    f"Preview ready for Page #{pnum} (Mean Confidence: {res['mean_confidence']:.1f}%)"
                )

                # Also insert/update in Treeview
                item_id = f"p_{pnum}"
                row_vals = (
                    pnum,
                    res["status"].upper(),
                    f"{res['mean_confidence']:.1f}%",
                    res["word_count"],
                    res["low_confidence_word_count"],
                    res["page_direction"],
                    res["column_count"],
                )
                conf = float(res.get("mean_confidence", 0.0))
                low_cnt = int(res.get("low_confidence_word_count", 0))
                row_tag = "high_conf" if conf >= 80.0 and low_cnt == 0 else ("med_conf" if conf >= 65.0 else "low_conf")

                if self.tree.exists(item_id):
                    self.tree.item(item_id, values=row_vals, tags=(row_tag,))
                else:
                    self.tree.insert("", tk.END, iid=item_id, values=row_vals, tags=(row_tag,))

                # Switch directly to Inspector tab and show the page!
                self.notebook.select(self.inspector_tab)
                self._load_page_into_inspector(pnum)

            elif etype == "preview_error":
                self.preview_btn.configure(state=tk.NORMAL)
                self.status_var.set(f"Preview error: {event['message']}")
                messagebox.showerror("Preview Failed", f"Could not generate page preview:\n{event['message']}")

            elif etype == "finished":
                summary = event["summary"]
                self.preview_btn.configure(state=tk.NORMAL)

                if summary.get("stopped"):
                    self.status_var.set(
                        f"Conversion stopped by user at {summary['completed_pages']}/{summary['total_pages']} pages."
                    )
                    self.start_btn.configure(state=tk.NORMAL)
                    self.pause_btn.configure(state=tk.DISABLED)
                    self.resume_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    messagebox.showinfo(
                        "Run Stopped",
                        f"Digitization process was cancelled.\n\n"
                        f"Completed Pages: {summary['completed_pages']}/{summary['total_pages']}\n"
                        "Checkpoints were saved. You can resume at any time.",
                    )

                elif summary.get("paused"):
                    self.status_var.set(
                        f"Paused at {summary['completed_pages']}/{summary['total_pages']} pages."
                    )
                    self.start_btn.configure(state=tk.NORMAL)
                    self.pause_btn.configure(state=tk.DISABLED)
                    self.resume_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)

                else:
                    self.progress_var.set(100.0)
                    avg_c = summary.get("overall_average_confidence", 0.0)
                    self.mean_conf_var.set(f"Mean Conf: {avg_c:.1f}%")
                    self.status_var.set(
                        f"Finished! Processed {summary['successful_pages']}/{summary['total_pages']} pages | "
                        f"Mean Conf: {avg_c:.1f}% | Low-Conf Pages: {summary['pages_with_low_confidence_flags']}"
                    )
                    self.start_btn.configure(state=tk.NORMAL)
                    self.pause_btn.configure(state=tk.DISABLED)
                    self.resume_btn.configure(state=tk.DISABLED)
                    self.stop_btn.configure(state=tk.DISABLED)
                    messagebox.showinfo(
                        "Conversion Complete",
                        f"Scanned book successfully digitized!\n\n"
                        f"Pages Processed: {summary['successful_pages']}/{summary['total_pages']}\n"
                        f"Overall Confidence: {avg_c:.2f}%\n"
                        f"Output Directory: {self.config.output_dir}\n\n"
                        "Use the quick buttons at the top to launch your DOCX, PDF, EPUB, or HTML replicas.",
                    )

            elif etype == "error":
                self.status_var.set(f"Pipeline Error: {event['message']}")
                self.start_btn.configure(state=tk.NORMAL)
                self.pause_btn.configure(state=tk.DISABLED)
                self.resume_btn.configure(state=tk.DISABLED)
                self.stop_btn.configure(state=tk.DISABLED)
                self.preview_btn.configure(state=tk.NORMAL)
                messagebox.showerror("Pipeline Error", event["message"])

        self.root.after(100, self._poll_queue)

    # -------------------------------------------------------------------------
    # Treeview & Inspector Interaction
    # -------------------------------------------------------------------------
    def _on_tree_select(self, event=None) -> None:
        sel = self.tree.selection()
        if sel:
            try:
                pnum = int(self.tree.item(sel[0], "values")[0])
                self._load_page_into_inspector(pnum)
            except Exception:
                pass

    def _on_tree_double_click(self, event=None) -> None:
        sel = self.tree.selection()
        if sel:
            try:
                pnum = int(self.tree.item(sel[0], "values")[0])
                self.notebook.select(self.inspector_tab)
                self._load_page_into_inspector(pnum)
            except Exception:
                pass

    def _open_selected_in_inspector(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select Page", "Please select a page row in the table first.")
            return
        pnum = int(self.tree.item(sel[0], "values")[0])
        self.notebook.select(self.inspector_tab)
        self._load_page_into_inspector(pnum)

    def _load_page_into_inspector(self, pnum: int) -> None:
        self._current_inspector_page = pnum
        self.inspector_page_var.set(str(pnum))

        # Check in memory cache first
        data = self.page_results_cache.get(pnum)

        # Fallback to checkpoint file on disk
        if not data:
            cfile = os.path.join(self.config.checkpoint_dir, f"page_{pnum:05d}.json")
            if os.path.exists(cfile):
                try:
                    with open(cfile, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.page_results_cache[pnum] = data
                except Exception:
                    data = None

        if not data:
            self.inspector_meta_var.set(
                f"Page #{pnum}: Not processed yet. Click 'Re-render / Test Page' above to process."
            )
            self._display_empty_canvas(f"Page #{pnum} has not been processed yet.")
            self.inspector_text.delete("1.0", tk.END)
            self.inspector_text.insert(
                tk.END,
                f"[Page #{pnum} is not in checkpoints]\n\n"
                "Click 'Re-render / Test Page' in the toolbar to run preprocessing and OCR on this page immediately.",
            )
            return

        # Update metadata banner
        direction = data.get("page_direction", "RTL")
        conf = data.get("mean_confidence", 0.0)
        words = data.get("word_count", 0)
        low_cnt = data.get("low_confidence_word_count", 0)
        cols = data.get("column_count", 1)
        layout = data.get("layout") or {}
        couplets = sum(1 for el in layout.get("elements", []) if el.get("type") == "poetry")

        self.inspector_meta_var.set(
            f"Page #{pnum:04d}  |  Script Direction: {direction}  |  Mean Conf: {conf:.1f}%  |  "
            f"Words: {words}  |  Flagged Low-Conf Words: {low_cnt}  |  Columns: {cols}  |  Poetry Blocks: {couplets}"
        )

        # Render Scan Image
        self._render_current_inspector_image()

        # Render Extracted Text
        text = data.get("extracted_text") or extract_page_plain_text(data.get("layout"))
        self.inspector_text.delete("1.0", tk.END)

        is_rtl = direction == "RTL" or self.config.primary_language in ("urd", "ara", "fas")
        tag_name = "rtl_text" if is_rtl else "ltr_text"

        self.inspector_text.insert(tk.END, text, (tag_name,))

        # Highlight low confidence markers if present
        start = "1.0"
        while True:
            pos = self.inspector_text.search("[LOW_CONF", start, stopindex=tk.END)
            if not pos:
                break
            end_bracket = self.inspector_text.search("]", pos, stopindex=tk.END)
            if end_bracket:
                end_pos = f"{end_bracket}+1c"
                self.inspector_text.tag_add("low_conf_highlight", pos, end_pos)
                start = end_pos
            else:
                start = f"{pos}+9c"

    def _render_current_inspector_image(self) -> None:
        pnum = self._current_inspector_page
        mode = self.inspector_view_mode_var.get()
        data = self.page_results_cache.get(pnum)

        img_path = ""
        if mode == "original":
            orig_candidates = [
                os.path.join(self.config.extracted_images_dir, f"preview_p{pnum:05d}_orig.jpg"),
                os.path.join(self.config.extracted_images_dir, f"preview_p{pnum:05d}_orig.png"),
            ]
            img_path = next((p for p in orig_candidates if os.path.exists(p)), "")
        else:
            if data and data.get("preview_image_path") and os.path.exists(data["preview_image_path"]):
                img_path = data["preview_image_path"]
            else:
                proc_candidates = [
                    os.path.join(self.config.extracted_images_dir, f"page_{pnum:05d}_preview.jpg"),
                    os.path.join(self.config.extracted_images_dir, f"page_{pnum:05d}_preview.png"),
                    os.path.join(self.config.extracted_images_dir, f"preview_p{pnum:05d}_proc.jpg"),
                    os.path.join(self.config.extracted_images_dir, f"preview_p{pnum:05d}_proc.png"),
                ]
                img_path = next((p for p in proc_candidates if os.path.exists(p)), "")

        if not img_path or not os.path.exists(img_path):
            self._display_empty_canvas(
                f"No image preview saved for Page #{pnum} ({mode.capitalize()} mode).\n"
                "Click 'Re-render / Test Page' above to generate it."
            )
            return

        self._display_image_file(img_path)

    def _display_empty_canvas(self, message: str) -> None:
        self.image_canvas.delete("all")
        c_w = max(200, self.image_canvas.winfo_width())
        c_h = max(200, self.image_canvas.winfo_height())
        self.image_canvas.create_text(
            c_w // 2,
            c_h // 2,
            text=message,
            fill="#aaaaaa",
            font=("Segoe UI", 10),
            justify=tk.CENTER,
        )

    def _display_image_file(self, img_path: str) -> None:
        try:
            pil_img = Image.open(img_path)
            c_w = max(100, self.image_canvas.winfo_width() - 10)
            c_h = max(100, self.image_canvas.winfo_height() - 10)

            orig_w, orig_h = pil_img.size
            if orig_w <= 0 or orig_h <= 0:
                return

            scale = min(c_w / float(orig_w), c_h / float(orig_h))
            scale = min(scale, 1.25)  # Avoid over-blurring tiny crops

            new_w = max(50, int(orig_w * scale))
            new_h = max(50, int(orig_h * scale))

            resized_pil = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(resized_pil)
            self._current_photo_image = photo

            self.image_canvas.delete("all")
            self.image_canvas.create_image(
                self.image_canvas.winfo_width() // 2,
                self.image_canvas.winfo_height() // 2,
                anchor=tk.CENTER,
                image=photo,
            )
        except Exception as exc:
            self._display_empty_canvas(f"Error rendering image:\n{exc}")

    def _on_canvas_configure(self, event) -> None:
        if abs(event.width - self._last_canvas_w) > 15 or abs(event.height - self._last_canvas_h) > 15:
            self._last_canvas_w = event.width
            self._last_canvas_h = event.height
            self._render_current_inspector_image()

    def _inspector_prev_page(self) -> None:
        if self._current_inspector_page > 1:
            self._load_page_into_inspector(self._current_inspector_page - 1)

    def _inspector_next_page(self) -> None:
        self._load_page_into_inspector(self._current_inspector_page + 1)

    def _inspector_jump_page(self) -> None:
        try:
            pnum = int(self.inspector_page_var.get().strip())
            if pnum >= 1:
                self._load_page_into_inspector(pnum)
        except ValueError:
            pass

    def _copy_inspector_text(self) -> None:
        txt = self.inspector_text.get("1.0", tk.END).strip()
        if txt:
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
            messagebox.showinfo("Copied", f"Extracted text of Page #{self._current_inspector_page} copied to clipboard!")
        else:
            messagebox.showwarning("Empty", "No text to copy.")

    def _save_inspector_text(self) -> None:
        txt = self.inspector_text.get("1.0", tk.END).strip()
        if not txt:
            messagebox.showwarning("Empty Text", "There is no extracted text to save.")
            return

        default_name = f"page_{self._current_inspector_page:05d}_proof.txt"
        fpath = filedialog.asksaveasfilename(
            title="Save Page Text",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if fpath:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(txt)
            messagebox.showinfo("Saved", f"Page #{self._current_inspector_page} saved to:\n{fpath}")

    # -------------------------------------------------------------------------
    # Quick Launch & Logging Utilities
    # -------------------------------------------------------------------------
    def _clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def _save_log(self) -> None:
        txt = self.log_text.get("1.0", tk.END).strip()
        if not txt:
            messagebox.showwarning("Empty Log", "There is no log content to save.")
            return
        fpath = filedialog.asksaveasfilename(
            title="Save Log File",
            initialfile="digitizer_pipeline.log",
            defaultextension=".log",
            filetypes=[("Log Files", "*.log"), ("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if fpath:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(txt)
            messagebox.showinfo("Saved", f"Log saved to:\n{fpath}")

    def _launch_file(self, file_type: str) -> None:
        filename_map = {
            "docx": "digitized_book.docx",
            "sandwich_pdf": "digitized_book_sandwich.pdf",
            "pdf": "digitized_book_reflowable.pdf",
            "epub": "digitized_book.epub",
            "txt": "digitized_book.txt",
            "html": "digitized_book_replica.html",
            "alto": "digitized_book_alto.xml",
            "pagexml": "page_xml",
            "report": "quality_report.txt",
        }

        filename = filename_map.get(file_type)
        if not filename:
            return

        out_dir = self.output_dir_var.get().strip() or self.config.output_dir
        fpath = os.path.join(out_dir, filename)
        # Fallback for reflowable PDF naming
        if file_type == "pdf" and not os.path.exists(fpath):
            alt_path = os.path.join(out_dir, "digitized_book_searchable.pdf")
            if os.path.exists(alt_path):
                fpath = alt_path

        if not os.path.exists(fpath):
            messagebox.showinfo(
                "File Not Found",
                f"'{filename}' has not been generated yet.\n\n"
                f"Expected location: {fpath}\n\n"
                "Please run the conversion and verify the format is checked in Output Formats.",
            )
            return

        try:
            if platform.system() == "Windows":
                os.startfile(fpath)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", fpath])
            else:
                subprocess.Popen(["xdg-open", fpath])
        except Exception as exc:
            messagebox.showerror("Error Opening File", f"Could not launch '{filename}':\n{exc}")

    def _show_system_health(self) -> None:
        self._sync_config_from_ui()
        health = check_system_health(self.config)

        win = tk.Toplevel(self.root)
        win.title("System Diagnostics & Health Check")
        win.geometry("640x520")
        win.minsize(540, 420)
        win.transient(self.root)
        win.grab_set()

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text="Pipeline Diagnostics & Component Verification",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 10))

        # Tesseract Status
        tess_status = "✅ OK" if health["tesseract_installed"] else "❌ MISSING"
        tess_box = ttk.LabelFrame(frame, text=f"Tesseract OCR Engine — {tess_status}", padding=8)
        tess_box.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(tess_box, text=f"Version: {health['tesseract_version']}").pack(anchor=tk.W)
        ttk.Label(tess_box, text=f"Path: {health['tesseract_path']}").pack(anchor=tk.W)
        langs_str = ", ".join(health["installed_languages"]) if health["installed_languages"] else "None detected"
        ttk.Label(tess_box, text=f"Installed Languages: {langs_str}").pack(anchor=tk.W)

        # PDF Rendering & Extraction Engine Status
        pdf_status = "✅ OK" if (health.get("pypdfium2_installed") or health["poppler_installed"]) else "⚠️ MISSING"
        pdf_box = ttk.LabelFrame(frame, text=f"PDF Engine & Rasterizer — {pdf_status}", padding=8)
        pdf_box.pack(fill=tk.X, pady=(0, 6))

        if health.get("pypdfium2_installed"):
            ttk.Label(pdf_box, text="• Google PDFium: Active (Zero-dependency rendering & born-digital extraction)").pack(anchor=tk.W)
        if health.get("pypdf_installed"):
            ttk.Label(pdf_box, text="• pypdf Stream Writer: Active (Chunked memory-safe PDF assembly)").pack(anchor=tk.W)
        if health["poppler_installed"]:
            ttk.Label(pdf_box, text=f"• Poppler Path: {health['poppler_path']}").pack(anchor=tk.W)
        elif not health.get("pypdfium2_installed"):
            ttk.Label(pdf_box, text="• Warning: No PDF rasterizer detected. Please run: pip install pypdfium2", foreground="#b71c1c").pack(anchor=tk.W)

        # Typography & Bundled RTL Fonts Status
        b_fonts = health.get("bundled_fonts_detected", [])
        font_status = f"✅ OK ({len(b_fonts)} Fonts)" if b_fonts else "ℹ️ System Fonts Active"
        font_box = ttk.LabelFrame(frame, text=f"Typography & RTL Ligature Fonts — {font_status}", padding=8)
        font_box.pack(fill=tk.X, pady=(0, 6))
        if b_fonts:
            ttk.Label(font_box, text=f"Bundled Fonts: {', '.join(b_fonts)}").pack(anchor=tk.W)
            ttk.Label(font_box, text="Authentic Nastaliq & Naskh ligatures active with 0 missing glyphs.", font=("Segoe UI", 8), foreground="#2e7d32").pack(anchor=tk.W)
        else:
            ttk.Label(font_box, text="Using OS system fonts. Bundled fonts can be placed in book_digitizer/fonts/").pack(anchor=tk.W)

        # Vision LLM Status
        llm_status = "✅ Configured" if health["vision_llm_configured"] else "ℹ️ Optional (Not set)"
        llm_box = ttk.LabelFrame(frame, text=f"Multimodal Vision LLM Fallback — {llm_status}", padding=8)
        llm_box.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(llm_box, text=f"Model: {health['vision_llm_model']}").pack(anchor=tk.W)
        ttk.Label(
            llm_box,
            text=f"API Key: {'Set (Active)' if health['vision_llm_configured'] else 'None (Hybrid fallback disabled)'}",
        ).pack(anchor=tk.W)


        # Warnings Box
        if health["warnings"]:
            warn_box = ttk.LabelFrame(frame, text="⚠️ Diagnostics Warnings", padding=8)
            warn_box.pack(fill=tk.X, pady=(0, 6))
            for w in health["warnings"]:
                ttk.Label(warn_box, text=f"• {w}", foreground="#b71c1c", wraplength=560).pack(anchor=tk.W)

        ttk.Button(frame, text="Close", command=win.destroy).pack(anchor=tk.E, pady=(10, 0))

    def _open_output_folder(self) -> None:
        out_dir = self.output_dir_var.get().strip() or self.config.output_dir
        os.makedirs(out_dir, exist_ok=True)
        try:
            if platform.system() == "Windows":
                os.startfile(out_dir)  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", out_dir])
            else:
                subprocess.Popen(["xdg-open", out_dir])
        except Exception as exc:
            messagebox.showerror("Error", f"Could not open directory:\n{exc}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = BookDigitizerGUI(root)
    root.mainloop()
