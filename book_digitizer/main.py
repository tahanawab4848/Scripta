import ctypes
import html
import json
import multiprocessing
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Any, Optional, List, Set, Tuple
from PIL import Image, ImageTk, ImageDraw

# Ensure book_digitizer directory is on sys.path for direct module resolution
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from config import DEFAULT_CONFIG, AppConfig, LANGUAGE_PRESETS, check_system_health
from batch_processor import BatchProcessor, extract_page_plain_text
from ocr_engine import get_pdf_page_count
from builder import BookBuilder

# Session state persistence file
SESSION_FILE = os.path.join(os.path.expanduser("~"), ".scripta_session.json")


# -----------------------------------------------------------------------------
# Color Palettes & Visual Themes
# -----------------------------------------------------------------------------
THEMES: Dict[str, Dict[str, str]] = {
    "dark": {
        "bg_root": "#0b0f19",
        "bg_nav": "#070a12",
        "nav_btn_active": "#1e293b",
        "nav_btn_fg": "#94a3b8",
        "nav_btn_fg_active": "#38bdf8",
        "bg_header": "#0f172a",
        "bg_card": "#131b2e",
        "bg_card_inner": "#0d1322",
        "border": "#1e293b",
        "border_subtle": "#27354f",
        "text_primary": "#f8fafc",
        "text_secondary": "#94a3b8",
        "text_muted": "#64748b",
        "accent": "#3b82f6",
        "accent_hover": "#2563eb",
        "accent_green": "#10b981",
        "accent_amber": "#f59e0b",
        "accent_red": "#ef4444",
        "accent_purple": "#a855f7",
        "console_bg": "#030712",
        "console_fg": "#38bdf8",
        "canvas_bg": "#090d16",
        "editor_bg": "#0d1322",
        "editor_fg": "#f8fafc",
        "stat_bg": "#111827",
        "chip_bg": "#1e293b",
        "chip_active": "#2563eb",
    },
    "light": {
        "bg_root": "#f1f5f9",
        "bg_nav": "#ffffff",
        "nav_btn_active": "#e0e7ff",
        "nav_btn_fg": "#475569",
        "nav_btn_fg_active": "#1d4ed8",
        "bg_header": "#0f172a",
        "bg_card": "#ffffff",
        "bg_card_inner": "#f8fafc",
        "border": "#e2e8f0",
        "border_subtle": "#cbd5e1",
        "text_primary": "#0f172a",
        "text_secondary": "#475569",
        "text_muted": "#94a3b8",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "accent_green": "#059669",
        "accent_amber": "#d97706",
        "accent_red": "#dc2626",
        "accent_purple": "#7e22ce",
        "console_bg": "#0f172a",
        "console_fg": "#38bdf8",
        "canvas_bg": "#1e293b",
        "editor_bg": "#ffffff",
        "editor_fg": "#0f172a",
        "stat_bg": "#ffffff",
        "chip_bg": "#f1f5f9",
        "chip_active": "#2563eb",
    },
}


class BookDigitizerGUI:
    """
    Next-Generation Commercial-Grade Desktop Studio for Multilingual Scanned Book Digitization.
    
    Complete Senior UX Improvements:
    1. Interactive Bounding Box Highlight Sync: Click text in editor -> highlights bounding box on scan!
    2. Proofreader Typography Controls: Quick Font Size Scaling (A- / A+) & Arabic/Urdu Font Family switcher.
    3. In-Editor Find & Replace Drawer (Ctrl+F / Ctrl+H): Instant bulk fix for misread characters across the page.
    4. Non-Intrusive Floating Toast Notifications: Zero modal popup interruptions during rapid proofreading.
    5. Table Context Menu & On-Demand Re-Assembly: Re-run individual pages or re-compile DOCX/PDF instantly.
    6. Windows Immersive Dark Titlebar: Seamless native dark mode frame on Windows 10/11 via DWM API.
    7. Bidirectional Proofreader Checkpoint Commit + Pan/Drag Zoom Canvas + Quality Filter Chips + Session Persistence.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Scripta Pro — Multilingual Book Digitizer & Facsimile OCR Studio")
        self.root.geometry("1440x940")
        self.root.minsize(1200, 780)

        # Load session state or defaults
        self.session_data: Dict[str, Any] = self._load_session_state()
        self.current_theme_name: str = self.session_data.get("theme", "dark")
        self.t: Dict[str, str] = THEMES[self.current_theme_name]

        # Apply Windows DWM dark titlebar if on Windows
        self._apply_windows_dark_titlebar(self.root, self.current_theme_name == "dark")

        self.config: AppConfig = AppConfig.from_dict(DEFAULT_CONFIG.to_dict())
        self._apply_session_to_config()

        self.processor: Optional[BatchProcessor] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        # Cache of page results for instant inspector lookup
        self.page_results_cache: Dict[int, Dict[str, Any]] = {}
        self._current_inspector_page: int = 1
        self._current_photo_image: Optional[ImageTk.PhotoImage] = None
        self._last_canvas_w: int = 0
        self._last_canvas_h: int = 0
        self._zoom_factor: float = 1.0
        self._auto_fit_mode: bool = True

        # Pan and Drag coordinates
        self._pan_start_x: int = 0
        self._pan_start_y: int = 0
        self._pan_offset_x: int = 0
        self._pan_offset_y: int = 0
        self._original_image_path: str = ""
        self._image_scale_ratio: float = 1.0

        # Typography settings for proofreader
        self.editor_font_size: int = self.session_data.get("editor_font_size", 14)
        self.editor_font_family: str = self.session_data.get("editor_font_family", "Tahoma")

        # Active highlighted bounding box coordinates
        self._active_bbox: Optional[Tuple[int, int, int, int]] = None

        # Detached Floating Windows references
        self._detached_inspector_win: Optional[tk.Toplevel] = None
        self._detached_console_win: Optional[tk.Toplevel] = None

        # Filter state for Page Table ("all", "low_conf", "med_conf", "failed")
        self.current_table_filter: str = "all"
        self.table_search_query: str = ""

        # Find & Replace bar visibility
        self._find_bar_visible: bool = False

        # Canvas drag tracking for reverse sync click detection
        self._pan_has_dragged: bool = False

        # Thumbnail Filmstrip scrubber state
        self._thumbnail_cache: Dict[int, ImageTk.PhotoImage] = {}
        self._filmstrip_cards: Dict[int, tk.Frame] = {}
        self._filmstrip_visible: bool = True
        self._filmstrip_filter: str = "all"  # "all" or "flagged"
        self.filmstrip_panel: Optional[tk.Frame] = None
        self.filmstrip_content: Optional[tk.Frame] = None
        self.filmstrip_canvas: Optional[tk.Canvas] = None
        self.filmstrip_cards_frame: Optional[tk.Frame] = None
        self.filmstrip_toggle_btn: Optional[tk.Button] = None
        self.filmstrip_count_label: Optional[tk.Label] = None
        self.filmstrip_chip_all: Optional[tk.Button] = None
        self.filmstrip_chip_flagged: Optional[tk.Button] = None

        self._init_tk_vars()
        self._setup_styles()
        self._build_layout()
        self._bind_keyboard_shortcuts()
        self.root.after(100, self._poll_queue)

        # Prompt resume if checkpoints exist in configured output directory
        self.root.after(600, self._check_existing_checkpoints_prompt)

    # -------------------------------------------------------------------------
    # Windows DWM Immersive Dark Title Bar API
    # -------------------------------------------------------------------------
    def _apply_windows_dark_titlebar(self, window: tk.Tk | tk.Toplevel, dark_mode: bool) -> None:
        if platform.system() == "Windows":
            try:
                window.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
                if not hwnd:
                    hwnd = window.winfo_id()
                # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 18985+ / Windows 11)
                value = ctypes.c_int(1 if dark_mode else 0)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
                )
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Floating Non-Intrusive Toast Notifications
    # -------------------------------------------------------------------------
    def show_toast(self, message: str, level: str = "success", duration_ms: int = 2400) -> None:
        """Displays a sleek non-intrusive floating toast badge that auto-dismisses."""
        t = self.t
        color_map = {
            "success": (t["accent_green"], "#ffffff"),
            "info": (t["accent"], "#ffffff"),
            "warn": (t["accent_amber"], "#000000"),
            "error": (t["accent_red"], "#ffffff"),
        }
        bg_col, fg_col = color_map.get(level, (t["accent"], "#ffffff"))

        toast = tk.Label(
            self.root,
            text=f"  {message}  ",
            font=("Segoe UI", 9, "bold"),
            bg=bg_col,
            fg=fg_col,
            relief=tk.FLAT,
            padx=16,
            pady=8,
            bd=1,
        )
        toast.place(relx=0.98, rely=0.96, anchor="se")

        def _fade():
            try:
                toast.destroy()
            except Exception:
                pass

        self.root.after(duration_ms, _fade)

    # -------------------------------------------------------------------------
    # Session Persistence & Restoration
    # -------------------------------------------------------------------------
    def _load_session_state(self) -> Dict[str, Any]:
        if os.path.exists(SESSION_FILE):
            try:
                with open(SESSION_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_session_state(self) -> None:
        try:
            self._sync_config_from_ui()
            data = {
                "theme": self.current_theme_name,
                "input_pdf_path": self.input_pdf_var.get(),
                "output_dir": self.output_dir_var.get(),
                "preset": self.preset_var.get(),
                "languages": self.lang_var.get(),
                "start_page": self.start_page_var.get(),
                "end_page": self.end_page_var.get(),
                "export_docx": self.docx_var.get(),
                "export_sandwich_pdf": self.sandwich_pdf_var.get(),
                "export_pdf": self.pdf_var.get(),
                "export_epub": self.epub_var.get(),
                "export_txt": self.txt_var.get(),
                "export_html": self.html_var.get(),
                "last_inspector_page": self._current_inspector_page,
                "editor_font_size": self.editor_font_size,
                "editor_font_family": self.editor_font_family,
            }
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _apply_session_to_config(self) -> None:
        s = self.session_data
        if s.get("input_pdf_path") and os.path.exists(s["input_pdf_path"]):
            self.config.input_pdf_path = s["input_pdf_path"]
        if s.get("output_dir"):
            self.config.output_dir = s["output_dir"]
        if s.get("preset") in LANGUAGE_PRESETS:
            self.config.language_preset = s["preset"]
            self.config.apply_preset(s["preset"])

    def _init_tk_vars(self) -> None:
        s = self.session_data
        self.input_pdf_var = tk.StringVar(value=self.config.input_pdf_path)
        self.output_dir_var = tk.StringVar(value=self.config.output_dir)
        self.preset_var = tk.StringVar(value=self.config.language_preset)
        self.lang_var = tk.StringVar(value=self.config.languages)
        self.dpi_var = tk.IntVar(value=self.config.dpi)
        self.conf_var = tk.DoubleVar(value=self.config.confidence_threshold)
        self.pdf_pages_info_var = tk.StringVar(value="No PDF loaded")

        if self.config.input_pdf_path and os.path.exists(self.config.input_pdf_path):
            try:
                cnt = get_pdf_page_count(self.config.input_pdf_path)
                if cnt > 0:
                    self.pdf_pages_info_var.set(f"✓ Detected: {cnt} pages in PDF")
            except Exception:
                pass

        # Page Range & Preview Vars
        self.start_page_var = tk.StringVar(value=s.get("start_page", "1"))
        self.end_page_var = tk.StringVar(value=s.get("end_page", ""))
        self.preview_page_var = tk.StringVar(value="1")

        # Output Formats
        self.docx_var = tk.BooleanVar(value=s.get("export_docx", self.config.export_docx))
        self.sandwich_pdf_var = tk.BooleanVar(value=s.get("export_sandwich_pdf", getattr(self.config, "export_sandwich_pdf", True)))
        self.pdf_var = tk.BooleanVar(value=s.get("export_pdf", self.config.export_pdf))
        self.epub_var = tk.BooleanVar(value=s.get("export_epub", self.config.export_epub))
        self.txt_var = tk.BooleanVar(value=s.get("export_txt", self.config.export_txt))
        self.html_var = tk.BooleanVar(value=s.get("export_html", self.config.export_html_weasyprint))
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
        self.status_var = tk.StringVar(value="Ready. Select a scanned PDF book and configure your options.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_pct_var = tk.StringVar(value="0.0%")
        self.speed_var = tk.StringVar(value="0.0 p/min")
        self.elapsed_var = tk.StringVar(value="00:00:00")
        self.eta_var = tk.StringVar(value="--:--:--")
        self.progress_count_var = tk.StringVar(value="0 / 0")
        self.mean_conf_var = tk.StringVar(value="0.0%")

        # Inspector View Vars
        self.inspector_page_var = tk.StringVar(value=str(s.get("last_inspector_page", 1)))
        self.inspector_view_mode_var = tk.StringVar(value="processed")
        self.inspector_meta_var = tk.StringVar(value="No page loaded. Select a page from the table or enter a page number.")
        self.zoom_display_var = tk.StringVar(value="Fit")
        self.proofreader_status_var = tk.StringVar(value="")

        # Typography variables
        self.font_size_display_var = tk.StringVar(value=f"{self.editor_font_size}pt")
        self.font_family_var = tk.StringVar(value=self.editor_font_family)

        # Find & Replace variables
        self.find_query_var = tk.StringVar(value="")
        self.replace_query_var = tk.StringVar(value="")

        # Log search var
        self.log_filter_var = tk.StringVar(value="")

        # Current Active Page View
        self.active_page_name = "studio"

        # Quality & Analytics KPI variables
        self.qa_kpi_pages_var = tk.StringVar(value="0 Pages")
        self.qa_kpi_conf_var = tk.StringVar(value="0.0%")
        self.qa_kpi_words_var = tk.StringVar(value="0 Words")
        self.qa_kpi_flags_var = tk.StringVar(value="0 Low-Conf")
        self.qa_kpi_edits_var = tk.StringVar(value="0 Corrected")

    # -------------------------------------------------------------------------
    # Keyboard Shortcuts Suite
    # -------------------------------------------------------------------------
    def _bind_keyboard_shortcuts(self) -> None:
        self.root.bind("<Control-s>", lambda e: self._commit_inspector_edits())
        self.root.bind("<Control-S>", lambda e: self._commit_inspector_edits())
        self.root.bind("<Alt-Right>", lambda e: self._inspector_next_page())
        self.root.bind("<Alt-Left>", lambda e: self._inspector_prev_page())
        self.root.bind("<Control-o>", lambda e: self._pick_pdf())
        self.root.bind("<Control-O>", lambda e: self._pick_pdf())
        self.root.bind("<Control-f>", lambda e: self._toggle_find_replace_bar())
        self.root.bind("<Control-F>", lambda e: self._toggle_find_replace_bar())
        self.root.bind("<Control-h>", lambda e: self._toggle_find_replace_bar())
        self.root.bind("<Control-H>", lambda e: self._toggle_find_replace_bar())
        self.root.bind("<Control-plus>", lambda e: self._zoom_in())
        self.root.bind("<Control-equal>", lambda e: self._zoom_in())
        self.root.bind("<Control-minus>", lambda e: self._zoom_out())
        self.root.bind("<Control-0>", lambda e: self._zoom_fit())
        self.root.bind("<F5>", lambda e: self._toggle_scan_view_mode())
        self.root.bind("<F6>", lambda e: self._toggle_filmstrip_visibility())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        self._save_session_state()
        self.root.destroy()

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        t = self.t
        self.root.configure(background=t["bg_root"])

        style.configure("TFrame", background=t["bg_root"])
        style.configure("Card.TFrame", background=t["bg_card"])
        style.configure("Sidebar.TFrame", background=t["bg_card"])

        style.configure(
            "TProgressbar",
            thickness=6,
            troughcolor=t["border"],
            background=t["accent"],
            borderwidth=0,
        )

        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 10, "bold"),
            background=t["accent"],
            foreground="#ffffff",
            padding=(14, 8),
            borderwidth=0,
        )
        style.map("Primary.TButton", background=[("active", t["accent_hover"]), ("disabled", t["border"])])

        style.configure("Action.TButton", font=("Segoe UI", 8, "bold"), padding=(8, 4))
        style.configure("QuickLaunch.TButton", font=("Segoe UI", 8, "bold"), padding=(6, 3))
        style.configure("TCheckbutton", background=t["bg_card"], foreground=t["text_primary"], font=("Segoe UI", 9))
        style.configure("TRadiobutton", background=t["bg_card"], foreground=t["text_primary"], font=("Segoe UI", 9))
        style.configure("TCombobox", padding=3)
        style.configure("TEntry", padding=3)

        style.configure(
            "Treeview",
            background=t["bg_card"],
            foreground=t["text_primary"],
            fieldbackground=t["bg_card"],
            font=("Segoe UI", 9),
            rowheight=26,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI", 9, "bold"),
            background=t["border"],
            foreground=t["text_primary"],
            padding=(6, 4),
        )
        style.map("Treeview", background=[("selected", t["accent"])], foreground=[("selected", "#ffffff")])

    def _toggle_theme(self) -> None:
        self.current_theme_name = "light" if self.current_theme_name == "dark" else "dark"
        self.t = THEMES[self.current_theme_name]
        self._apply_windows_dark_titlebar(self.root, self.current_theme_name == "dark")
        self._setup_styles()
        self._save_session_state()
        for child in self.root.winfo_children():
            child.destroy()
        self._build_layout()
        if self._current_inspector_page:
            self._load_page_into_inspector(self._current_inspector_page)

    # -------------------------------------------------------------------------
    # Layout Builder
    # -------------------------------------------------------------------------
    def _build_layout(self) -> None:
        self._build_header(self.root)

        main_container = tk.Frame(self.root, bg=self.t["bg_root"])
        main_container.pack(fill=tk.BOTH, expand=True)

        self._build_nav_rail(main_container)

        self.viewport = tk.Frame(main_container, bg=self.t["bg_root"])
        self.viewport.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10), pady=(0, 10))

        self.pages: Dict[str, tk.Frame] = {}
        self.pages["studio"] = self._create_studio_page(self.viewport)
        self.pages["inspector"] = self._create_inspector_page(self.viewport)
        self.pages["quality"] = self._create_quality_page(self.viewport)
        self.pages["console"] = self._create_console_page(self.viewport)
        self.pages["settings"] = self._create_settings_page(self.viewport)

        self._switch_page(self.active_page_name)

    def _build_header(self, parent: tk.Tk) -> None:
        t = self.t
        header = tk.Frame(parent, bg=t["bg_header"], height=52)
        header.pack(fill=tk.X, side=tk.TOP, pady=(0, 6))
        header.pack_propagate(False)

        inner = tk.Frame(header, bg=t["bg_header"])
        inner.pack(fill=tk.BOTH, expand=True, padx=14)

        left_box = tk.Frame(inner, bg=t["bg_header"])
        left_box.pack(side=tk.LEFT, fill=tk.Y)

        title_lbl = tk.Label(
            left_box,
            text="⚡ SCRIPTA PRO",
            font=("Segoe UI", 12, "bold"),
            fg="#38bdf8",
            bg=t["bg_header"],
        )
        title_lbl.pack(side=tk.LEFT, pady=12)

        sub_lbl = tk.Label(
            left_box,
            text=" |  Multilingual Scanned Book Digitizer & Facsimile OCR Studio",
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg=t["bg_header"],
        )
        sub_lbl.pack(side=tk.LEFT, pady=12)

        badge_lbl = tk.Label(
            left_box,
            text=" Urdu • Arabic • Persian • English ",
            font=("Segoe UI", 8, "bold"),
            fg="#10b981",
            bg="#064e3b",
            padx=8,
            pady=2,
        )
        badge_lbl.pack(side=tk.LEFT, padx=12, pady=14)

        right_box = tk.Frame(inner, bg=t["bg_header"])
        right_box.pack(side=tk.RIGHT, fill=tk.Y)

        shortcuts_btn = tk.Button(
            right_box,
            text="⌨ Shortcuts",
            font=("Segoe UI", 8),
            fg="#94a3b8",
            bg="#1e293b",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._show_shortcuts_modal,
        )
        shortcuts_btn.pack(side=tk.LEFT, padx=4, pady=12)

        theme_icon = "☀️ Light Mode" if self.current_theme_name == "dark" else "🌙 Dark Mode"
        theme_btn = tk.Button(
            right_box,
            text=theme_icon,
            font=("Segoe UI", 8, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._toggle_theme,
        )
        theme_btn.pack(side=tk.LEFT, padx=4, pady=12)

        health_btn = tk.Button(
            right_box,
            text="🩺 Diagnostics",
            font=("Segoe UI", 8, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._show_system_health,
        )
        health_btn.pack(side=tk.LEFT, padx=4, pady=12)

        fmt_btn = tk.Button(
            right_box,
            text="📦 Output Formats",
            font=("Segoe UI", 8, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=lambda: self._prompt_output_formats_modal(
                title="Select Output Document Formats", action_btn_text="Save Preferences"
            ),
        )
        fmt_btn.pack(side=tk.LEFT, padx=4, pady=12)

        out_btn = tk.Button(
            right_box,
            text="📁 Output Folder",
            font=("Segoe UI", 8, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
            activebackground="#334155",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._open_output_folder,
        )
        out_btn.pack(side=tk.LEFT, padx=4, pady=12)

    def _show_shortcuts_modal(self) -> None:
        msg = (
            "Scripta Pro Productivity Shortcuts:\n\n"
            "• Click on Scan Image: Reverse Sync to corresponding word in proofreader\n"
            "• Click in Proofreader Text: Highlights word bounding box on scan\n"
            "• Ctrl + S: Commit edited proofreading text directly to book checkpoint\n"
            "• Ctrl + F / Ctrl + H: Open In-Editor Find & Replace drawer\n"
            "• Alt + Right / Left: Navigate Next / Previous Page\n"
            "• F6: Toggle Visual Page Filmstrip / Scrubber drawer\n"
            "• Ctrl + + / -: Zoom In / Zoom Out on scan\n"
            "• Ctrl + 0: Fit scan to window\n"
            "• Left Click + Drag on Scan: Pan around zoomed image\n"
            "• Ctrl + Mouse Wheel: Cursor-centered fluid zoom\n"
            "• F5: Toggle between Processed Scan and Original Scan\n"
            "• Ctrl + O: Open and browse for new PDF book\n"
            "• Right-click in Table: Re-process individual page or re-assemble book\n"
        )
        messagebox.showinfo("Keyboard Shortcuts", msg)

    # -------------------------------------------------------------------------
    # Navigation Rail
    # -------------------------------------------------------------------------
    def _build_nav_rail(self, parent: tk.Frame) -> None:
        t = self.t
        self.nav_frame = tk.Frame(parent, bg=t["bg_nav"], width=175, bd=1, relief=tk.SOLID)
        self.nav_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 8), pady=(0, 10))
        self.nav_frame.pack_propagate(False)

        nav_header = tk.Label(
            self.nav_frame,
            text="STUDIO PAGES",
            font=("Segoe UI", 7, "bold"),
            fg=t["text_muted"],
            bg=t["bg_nav"],
        )
        nav_header.pack(anchor=tk.W, padx=12, pady=(12, 6))

        self.nav_buttons: Dict[str, tk.Button] = {}

        nav_items = [
            ("studio", "🚀 Studio & Run"),
            ("inspector", "🔍 Page Inspector"),
            ("quality", "📊 Quality & Output"),
            ("console", "💻 Pipeline Log"),
            ("settings", "⚙️ Engine Tuning"),
        ]

        for key, label in nav_items:
            btn = tk.Button(
                self.nav_frame,
                text=f" {label}",
                anchor="w",
                font=("Segoe UI", 9, "bold"),
                fg=t["nav_btn_fg"],
                bg=t["bg_nav"],
                activebackground=t["nav_btn_active"],
                activeforeground=t["nav_btn_fg_active"],
                relief=tk.FLAT,
                bd=0,
                padx=10,
                pady=10,
                cursor="hand2",
                command=lambda k=key: self._switch_page(k),
            )
            btn.pack(fill=tk.X, padx=6, pady=2)
            self.nav_buttons[key] = btn

        ttk.Separator(self.nav_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=(16, 10))

        tk.Label(
            self.nav_frame,
            text="MULTI-WINDOW",
            font=("Segoe UI", 7, "bold"),
            fg=t["text_muted"],
            bg=t["bg_nav"],
        ).pack(anchor=tk.W, padx=12, pady=(0, 6))

        pop_insp_btn = tk.Button(
            self.nav_frame,
            text="🗗 Pop-out Inspector",
            anchor="w",
            font=("Segoe UI", 8),
            fg=t["accent"],
            bg=t["bg_nav"],
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._popout_inspector_window,
        )
        pop_insp_btn.pack(fill=tk.X, padx=6, pady=2)

        pop_term_btn = tk.Button(
            self.nav_frame,
            text="🗗 Pop-out Console",
            anchor="w",
            font=("Segoe UI", 8),
            fg=t["accent"],
            bg=t["bg_nav"],
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._popout_console_window,
        )
        pop_term_btn.pack(fill=tk.X, padx=6, pady=2)

    def _switch_page(self, page_name: str) -> None:
        self.active_page_name = page_name
        t = self.t

        for key, btn in self.nav_buttons.items():
            if key == page_name:
                btn.configure(
                    bg=t["nav_btn_active"],
                    fg=t["nav_btn_fg_active"],
                    relief=tk.SOLID,
                    bd=1,
                )
            else:
                btn.configure(
                    bg=t["bg_nav"],
                    fg=t["nav_btn_fg"],
                    relief=tk.FLAT,
                    bd=0,
                )

        for key, frame in self.pages.items():
            if key == page_name:
                frame.pack(fill=tk.BOTH, expand=True)
            else:
                frame.pack_forget()

        if page_name == "inspector":
            self.root.after(50, self._render_current_inspector_image)
            if hasattr(self, "_refresh_filmstrip"):
                self.root.after(100, self._refresh_filmstrip)
        elif page_name == "quality":
            if hasattr(self, "_refresh_quality_page"):
                self.root.after(50, self._refresh_quality_page)

    # -------------------------------------------------------------------------
    # Existing Checkpoint Detection
    # -------------------------------------------------------------------------
    def _check_existing_checkpoints_prompt(self) -> None:
        out_dir = self.output_dir_var.get().strip()
        chk_dir = os.path.join(out_dir, "checkpoints") if out_dir else self.config.checkpoint_dir
        if os.path.exists(chk_dir):
            try:
                cfiles = [f for f in os.listdir(chk_dir) if f.startswith("page_") and f.endswith(".json")]
                if len(cfiles) > 0:
                    ans = messagebox.askyesno(
                        "Resume Interrupted Run?",
                        f"Found {len(cfiles)} processed page checkpoint(s) in:\n{chk_dir}\n\n"
                        "Would you like to populate the studio grid with these existing checkpoints?",
                    )
                    if ans:
                        self._load_all_existing_checkpoints(chk_dir)
            except Exception:
                pass

    def _load_all_existing_checkpoints(self, chk_dir: str) -> None:
        cfiles = sorted([f for f in os.listdir(chk_dir) if f.startswith("page_") and f.endswith(".json")])
        for f in cfiles:
            try:
                p = os.path.join(chk_dir, f)
                with open(p, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                pnum = data.get("page_number")
                if pnum:
                    self.page_results_cache[pnum] = data
            except Exception:
                pass
        self._refresh_table_rows()
        if hasattr(self, "_refresh_filmstrip"):
            self._refresh_filmstrip()
        self.show_toast(f"Loaded {len(cfiles)} checkpoints into studio", "success")

    # -------------------------------------------------------------------------
    # Page 1: Studio & Primary Run Center
    # -------------------------------------------------------------------------
    def _create_studio_page(self, parent: tk.Frame) -> tk.Frame:
        t = self.t
        page = tk.Frame(parent, bg=t["bg_root"])

        self._build_top_kpi_bar(page)

        body = tk.Frame(page, bg=t["bg_root"])
        body.pack(fill=tk.BOTH, expand=True)

        left_col = tk.Frame(body, bg=t["bg_root"], width=420)
        left_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_col.pack_propagate(False)

        canvas = tk.Canvas(left_col, background=t["bg_card"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_col, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=t["bg_card"], padx=12, pady=12)

        scrollable_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas_win = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=400)

        def _resize_canvas(event):
            canvas.itemconfig(canvas_win, width=event.width)

        canvas.bind("<Configure>", _resize_canvas)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._build_sidebar_source_card(scrollable_frame)
        self._build_sidebar_lang_card(scrollable_frame)
        self._build_sidebar_formats_card(scrollable_frame)
        self._build_sidebar_preprocessing_card(scrollable_frame)
        self._build_sidebar_action_center(scrollable_frame)

        right_col = tk.Frame(body, bg=t["bg_root"])
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_quick_launch_bar(right_col)

        # Quality Grid with Filter Chips Header
        table_card = tk.LabelFrame(
            right_col,
            text=" Batch Page Grid & Quality Filters (Right-Click Row for Actions) ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=8,
            pady=8,
            relief=tk.GROOVE,
        )
        table_card.pack(fill=tk.BOTH, expand=True)

        filter_bar = tk.Frame(table_card, bg=t["bg_card"])
        filter_bar.pack(fill=tk.X, pady=(0, 6))

        tk.Label(filter_bar, text="Filter:", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["text_muted"]).pack(
            side=tk.LEFT, padx=(0, 6)
        )

        self.chip_all = tk.Button(
            filter_bar,
            text="All Pages",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self._apply_table_filter("all"),
        )
        self.chip_all.pack(side=tk.LEFT, padx=2)

        self.chip_low = tk.Button(
            filter_bar,
            text="🔴 Needs Review (<65%)",
            font=("Segoe UI", 8),
            fg=t["accent_red"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self._apply_table_filter("low_conf"),
        )
        self.chip_low.pack(side=tk.LEFT, padx=2)

        self.chip_med = tk.Button(
            filter_bar,
            text="🟡 Fair (65-80%)",
            font=("Segoe UI", 8),
            fg=t["accent_amber"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self._apply_table_filter("med_conf"),
        )
        self.chip_med.pack(side=tk.LEFT, padx=2)

        self.chip_failed = tk.Button(
            filter_bar,
            text="🛑 Failed",
            font=("Segoe UI", 8),
            fg=t["accent_red"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self._apply_table_filter("failed"),
        )
        self.chip_failed.pack(side=tk.LEFT, padx=2)

        # On-Demand Re-assembly Action Button
        reassemble_btn = tk.Button(
            filter_bar,
            text="📑 Re-assemble Outputs Now",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent_green"],
            relief=tk.FLAT,
            padx=10,
            pady=2,
            cursor="hand2",
            command=self._reassemble_outputs_on_demand,
        )
        reassemble_btn.pack(side=tk.RIGHT, padx=2)

        columns = ("page", "status", "conf", "words", "flagged", "dir", "cols")
        self.tree = ttk.Treeview(table_card, columns=columns, show="headings")
        self.tree.heading("page", text="Page #")
        self.tree.heading("status", text="Status")
        self.tree.heading("conf", text="Confidence")
        self.tree.heading("words", text="Words")
        self.tree.heading("flagged", text="Flagged")
        self.tree.heading("dir", text="Direction")
        self.tree.heading("cols", text="Cols")

        self.tree.column("page", width=65, anchor=tk.CENTER)
        self.tree.column("status", width=85, anchor=tk.CENTER)
        self.tree.column("conf", width=95, anchor=tk.CENTER)
        self.tree.column("words", width=75, anchor=tk.CENTER)
        self.tree.column("flagged", width=85, anchor=tk.CENTER)
        self.tree.column("dir", width=80, anchor=tk.CENTER)
        self.tree.column("cols", width=60, anchor=tk.CENTER)

        self.tree.tag_configure("high_conf", background="#064e3b" if self.current_theme_name == "dark" else "#f0fdf4", foreground="#34d399" if self.current_theme_name == "dark" else "#166534")
        self.tree.tag_configure("med_conf", background="#78350f" if self.current_theme_name == "dark" else "#fefce8", foreground="#fde047" if self.current_theme_name == "dark" else "#854d0e")
        self.tree.tag_configure("low_conf", background="#7f1d1d" if self.current_theme_name == "dark" else "#fef2f2", foreground="#fca5a5" if self.current_theme_name == "dark" else "#991b1b")
        self.tree.tag_configure("failed", background="#991b1b" if self.current_theme_name == "dark" else "#fee2e2", foreground="#ffffff" if self.current_theme_name == "dark" else "#991b1b")

        tree_scroll = ttk.Scrollbar(table_card, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._show_tree_context_menu)

        # Context menu for Page Table
        self.tree_menu = tk.Menu(self.root, tearoff=0, bg=t["bg_card"], fg=t["text_primary"])
        self.tree_menu.add_command(label="🔍 Open in Inspector", command=self._open_selected_in_inspector)
        self.tree_menu.add_command(label="🔄 Re-process Selected Page", command=self._reprocess_selected_table_page)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="📑 Re-assemble Book Outputs", command=self._reassemble_outputs_on_demand)

        tbl_foot = tk.Frame(table_card, bg=t["bg_card"])
        tbl_foot.pack(fill=tk.X, pady=(6, 0))

        insp_btn = tk.Button(
            tbl_foot,
            text="🔍 Open Selected Page in Inspector",
            font=("Segoe UI", 8, "bold"),
            bg=t["accent"],
            fg="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._open_selected_in_inspector,
        )
        insp_btn.pack(side=tk.LEFT)

        return page

    def _show_tree_context_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree_menu.tk_popup(event.x_root, event.y_root)

    def _reprocess_selected_table_page(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        pnum = int(self.tree.item(sel[0], "values")[0])
        self.preview_page_var.set(str(pnum))
        self._test_preview_page()

    def _reassemble_outputs_on_demand(self) -> None:
        """Regenerates final DOCX, PDF, EPUB, TXT, HTML from all checkpoints immediately."""
        self._sync_config_from_ui()
        if not self._prompt_output_formats_modal(
            title="Select Output Formats to Re-assemble",
            action_btn_text="📦  Re-assemble Outputs",
        ):
            return
        self.show_toast("Re-assembling book outputs...", "info")

        def run():
            try:
                proc = BatchProcessor(self.config, on_log=lambda m: self.ui_queue.put({"type": "log", "message": m}))
                report = proc.reassemble_books_from_checkpoints()
                self.ui_queue.put({"type": "reassemble_complete", "report": report})
            except Exception as exc:
                self.ui_queue.put({"type": "error", "message": f"Re-assembly error: {exc}"})

        threading.Thread(target=run, daemon=True).start()

    def _apply_table_filter(self, filter_mode: str) -> None:
        self.current_table_filter = filter_mode
        t = self.t

        chips = [
            (self.chip_all, "all", "All Pages"),
            (self.chip_low, "low_conf", "🔴 Needs Review (<65%)"),
            (self.chip_med, "med_conf", "🟡 Fair (65-80%)"),
            (self.chip_failed, "failed", "🛑 Failed"),
        ]
        for btn, mode, _ in chips:
            if mode == filter_mode:
                btn.configure(bg=t["accent"], fg="#ffffff", font=("Segoe UI", 8, "bold"))
            else:
                btn.configure(bg=t["bg_card_inner"], fg=t["text_secondary"], font=("Segoe UI", 8))

        self._refresh_table_rows()

    def _refresh_table_rows(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for pnum, res in sorted(self.page_results_cache.items()):
            conf = float(res.get("mean_confidence", 0.0))
            status = res.get("status", "success")
            low_cnt = int(res.get("low_confidence_word_count", 0))

            if status != "success":
                tag = "failed"
            elif conf >= 80.0 and low_cnt == 0:
                tag = "high_conf"
            elif conf >= 65.0:
                tag = "med_conf"
            else:
                tag = "low_conf"

            if self.current_table_filter == "low_conf" and tag != "low_conf":
                continue
            if self.current_table_filter == "med_conf" and tag != "med_conf":
                continue
            if self.current_table_filter == "failed" and tag != "failed":
                continue

            item_id = f"p_{pnum}"
            row_vals = (
                pnum,
                status.upper(),
                f"{conf:.1f}%",
                res.get("word_count", 0),
                low_cnt,
                res.get("page_direction", "LTR"),
                res.get("column_count", 1),
            )
            self.tree.insert("", tk.END, iid=item_id, values=row_vals, tags=(tag,))

    # -------------------------------------------------------------------------
    # Top KPI Metrics Strip
    # -------------------------------------------------------------------------
    def _build_top_kpi_bar(self, parent: tk.Frame) -> None:
        t = self.t
        outer = tk.Frame(parent, bg=t["bg_root"])
        outer.pack(fill=tk.X, pady=(0, 8))

        cards_row = tk.Frame(outer, bg=t["bg_root"])
        cards_row.pack(fill=tk.X, pady=(0, 6))

        def make_kpi(parent_frame, title: str, textvar: tk.StringVar, color: str):
            tile = tk.Frame(parent_frame, bg=t["stat_bg"], bd=1, relief=tk.SOLID, padx=14, pady=8)
            tk.Label(tile, text=title.upper(), font=("Segoe UI", 7, "bold"), fg=t["text_muted"], bg=t["stat_bg"]).pack(
                anchor=tk.W
            )
            tk.Label(tile, textvariable=textvar, font=("Segoe UI", 13, "bold"), fg=color, bg=t["stat_bg"]).pack(
                anchor=tk.W
            )
            return tile

        kpi1 = make_kpi(cards_row, "Pages Processed", self.progress_count_var, t["text_primary"])
        kpi1.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        kpi2 = make_kpi(cards_row, "Throughput Speed", self.speed_var, t["accent"])
        kpi2.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        kpi3 = make_kpi(cards_row, "Elapsed / ETA", self.eta_var, t["accent_amber"])
        kpi3.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        kpi4 = make_kpi(cards_row, "Mean Accuracy", self.mean_conf_var, t["accent_green"])
        kpi4.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        prog_frame = tk.Frame(outer, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=12, pady=6)
        prog_frame.pack(fill=tk.X)

        top_info = tk.Frame(prog_frame, bg=t["bg_card"])
        top_info.pack(fill=tk.X, pady=(0, 2))

        tk.Label(
            top_info,
            textvariable=self.status_var,
            font=("Segoe UI", 8),
            fg=t["text_secondary"],
            bg=t["bg_card"],
        ).pack(side=tk.LEFT)

        tk.Label(
            top_info,
            textvariable=self.progress_pct_var,
            font=("Segoe UI", 8, "bold"),
            fg=t["accent"],
            bg=t["bg_card"],
        ).pack(side=tk.RIGHT)

        self.progress_bar = ttk.Progressbar(
            prog_frame, variable=self.progress_var, maximum=100.0, mode="determinate"
        )
        self.progress_bar.pack(fill=tk.X, pady=(2, 2))

    # -------------------------------------------------------------------------
    # Quick Launch Output Toolbar
    # -------------------------------------------------------------------------
    def _build_quick_launch_bar(self, parent: tk.Frame) -> None:
        t = self.t
        bar = tk.Frame(parent, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=8, pady=6)
        bar.pack(fill=tk.X, pady=(0, 8))

        tk.Label(bar, text="Quick Open Output:", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["text_secondary"]).pack(
            side=tk.LEFT, padx=(0, 8)
        )

        buttons = [
            ("📄 DOCX", "docx"),
            ("📑 Sandwich PDF", "sandwich_pdf"),
            ("📑 Reflow PDF", "pdf"),
            ("📖 EPUB3", "epub"),
            ("🌐 HTML5", "html"),
            ("📝 Report", "report"),
        ]

        for lbl, key in buttons:
            b = tk.Button(
                bar,
                text=lbl,
                font=("Segoe UI", 8, "bold"),
                fg=t["text_primary"],
                bg=t["bg_card_inner"],
                activebackground=t["accent"],
                activeforeground="#ffffff",
                relief=tk.FLAT,
                bd=1,
                padx=6,
                pady=2,
                cursor="hand2",
                command=lambda k=key: self._launch_file(k),
            )
            b.pack(side=tk.LEFT, padx=2)

        open_folder_btn = tk.Button(
            bar,
            text="📁 Folder",
            font=("Segoe UI", 8, "bold"),
            fg=t["accent_green"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            bd=1,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._open_output_folder,
        )
        open_folder_btn.pack(side=tk.RIGHT, padx=2)

    # -------------------------------------------------------------------------
    # Page 2: Facsimile Inspector with Bounding Box Sync & Typography Scaling
    # -------------------------------------------------------------------------
    def _create_inspector_page(self, parent: tk.Frame) -> tk.Frame:
        t = self.t
        page = tk.Frame(parent, bg=t["bg_root"])

        insp_bar = tk.Frame(page, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=10, pady=6)
        insp_bar.pack(fill=tk.X, pady=(0, 6))

        # Page Stepper
        tk.Button(
            insp_bar,
            text="◀ Prev",
            font=("Segoe UI", 8, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._inspector_prev_page,
        ).pack(side=tk.LEFT, padx=2)

        tk.Label(insp_bar, text="Page:", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["text_primary"]).pack(
            side=tk.LEFT, padx=(6, 2)
        )
        ttk.Spinbox(
            insp_bar,
            from_=1,
            to=99999,
            textvariable=self.inspector_page_var,
            width=5,
            command=self._inspector_jump_page,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            insp_bar,
            text="Go",
            font=("Segoe UI", 8, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._inspector_jump_page,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            insp_bar,
            text="Next ▶",
            font=("Segoe UI", 8, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._inspector_next_page,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Separator(insp_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Zoom & Pan Reset Controls
        tk.Button(
            insp_bar,
            text="🔍+ Zoom In",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._zoom_in,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            insp_bar,
            text="🔍- Zoom Out",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._zoom_out,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            insp_bar,
            text="⛶ Fit Window",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._zoom_fit,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            insp_bar,
            text="1:1 (100%)",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._zoom_100,
        ).pack(side=tk.LEFT, padx=2)

        tk.Label(
            insp_bar,
            textvariable=self.zoom_display_var,
            font=("Segoe UI", 8, "bold"),
            fg=t["accent"],
            bg=t["bg_card"],
        ).pack(side=tk.LEFT, padx=4)

        ttk.Separator(insp_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Typography Controls (A- / A+ / Font Family)
        tk.Label(insp_bar, text="Text Font:", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["text_muted"]).pack(
            side=tk.LEFT, padx=(2, 2)
        )
        font_combo = ttk.Combobox(
            insp_bar,
            textvariable=self.font_family_var,
            values=["Tahoma", "Noto Nastaliq Urdu", "Amiri", "Times New Roman", "Segoe UI", "Consolas"],
            state="readonly",
            width=12,
        )
        font_combo.pack(side=tk.LEFT, padx=2)
        font_combo.bind("<<ComboboxSelected>>", self._on_editor_font_change)

        tk.Button(
            insp_bar,
            text="A-",
            font=("Segoe UI", 8, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._font_size_decrease,
        ).pack(side=tk.LEFT, padx=2)

        tk.Label(
            insp_bar,
            textvariable=self.font_size_display_var,
            font=("Segoe UI", 8),
            fg=t["accent"],
            bg=t["bg_card"],
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            insp_bar,
            text="A+",
            font=("Segoe UI", 8, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._font_size_increase,
        ).pack(side=tk.LEFT, padx=2)

        ttk.Separator(insp_bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Find & Replace Toggle
        tk.Button(
            insp_bar,
            text="🔍 Find & Replace (Ctrl+F)",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._toggle_find_replace_bar,
        ).pack(side=tk.LEFT, padx=2)

        # Right side actions
        pop_btn = tk.Button(
            insp_bar,
            text="🗗 Pop-out Window",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent_purple"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._popout_inspector_window,
        )
        pop_btn.pack(side=tk.RIGHT, padx=4)

        # COMMIT EDITS BUTTON
        commit_btn = tk.Button(
            insp_bar,
            text="💾 Commit Corrections (Ctrl+S)",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent_green"],
            activebackground="#059669",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=2,
            cursor="hand2",
            command=self._commit_inspector_edits,
        )
        commit_btn.pack(side=tk.RIGHT, padx=4)

        # SINGLE-PAGE EXPORT BUTTON
        export_page_btn = tk.Button(
            insp_bar,
            text="📄 Export Page...",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent"],
            activebackground=t.get("accent_hover", "#2563eb"),
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=9,
            pady=2,
            cursor="hand2",
            command=self._export_active_page_dialog,
        )
        export_page_btn.pack(side=tk.RIGHT, padx=4)

        # Slide-down Find & Replace Drawer
        self.find_replace_bar = tk.Frame(page, bg=t["bg_card_inner"], bd=1, relief=tk.SOLID, padx=10, pady=4)

        tk.Label(self.find_replace_bar, text="Find:", font=("Segoe UI", 8, "bold"), bg=t["bg_card_inner"], fg=t["text_primary"]).pack(side=tk.LEFT, padx=(0, 2))
        f_entry = ttk.Entry(self.find_replace_bar, textvariable=self.find_query_var, width=16)
        f_entry.pack(side=tk.LEFT, padx=4)
        f_entry.bind("<Return>", lambda e: self._find_next_in_editor())

        tk.Button(
            self.find_replace_bar,
            text="Find Next",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card"],
            relief=tk.FLAT,
            padx=6,
            pady=1,
            cursor="hand2",
            command=self._find_next_in_editor,
        ).pack(side=tk.LEFT, padx=2)

        tk.Label(self.find_replace_bar, text="Replace with:", font=("Segoe UI", 8, "bold"), bg=t["bg_card_inner"], fg=t["text_primary"]).pack(side=tk.LEFT, padx=(10, 2))
        r_entry = ttk.Entry(self.find_replace_bar, textvariable=self.replace_query_var, width=16)
        r_entry.pack(side=tk.LEFT, padx=4)

        tk.Button(
            self.find_replace_bar,
            text="Replace",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card"],
            relief=tk.FLAT,
            padx=6,
            pady=1,
            cursor="hand2",
            command=self._replace_one_in_editor,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            self.find_replace_bar,
            text="Replace All",
            font=("Segoe UI", 8, "bold"),
            fg=t["accent"],
            bg=t["bg_card"],
            relief=tk.FLAT,
            padx=8,
            pady=1,
            cursor="hand2",
            command=self._replace_all_in_editor,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            self.find_replace_bar,
            text="✕ Close",
            font=("Segoe UI", 8),
            fg=t["text_muted"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=1,
            cursor="hand2",
            command=self._toggle_find_replace_bar,
        ).pack(side=tk.RIGHT, padx=4)

        # Page Quality Metadata Pill + Commit Status
        meta_frame = tk.Frame(page, bg=t["stat_bg"], bd=1, relief=tk.SOLID, padx=10, pady=4)
        meta_frame.pack(fill=tk.X, pady=(0, 6))

        self.inspector_meta_label = tk.Label(
            meta_frame,
            textvariable=self.inspector_meta_var,
            font=("Segoe UI", 8, "bold"),
            fg=t["accent"],
            bg=t["stat_bg"],
        )
        self.inspector_meta_label.pack(side=tk.LEFT)

        self.proofreader_status_label = tk.Label(
            meta_frame,
            textvariable=self.proofreader_status_var,
            font=("Segoe UI", 8, "bold"),
            fg=t["accent_green"],
            bg=t["stat_bg"],
        )
        self.proofreader_status_label.pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(page, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left: Interactive Pan & Zoom Scan Canvas
        img_card = tk.LabelFrame(
            paned,
            text=" Facsimile Page Scan Viewer (Left-Click+Drag to Pan • Ctrl+Scroll to Zoom) ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=4,
            pady=4,
            relief=tk.GROOVE,
        )
        self.image_canvas = tk.Canvas(img_card, background=t["canvas_bg"], highlightthickness=0, cursor="fleur")
        self.image_canvas.pack(fill=tk.BOTH, expand=True)

        # Bind Pan, Zoom, and Canvas Clicks
        self.image_canvas.bind("<Configure>", self._on_canvas_configure)
        self.image_canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.image_canvas.bind("<B1-Motion>", self._on_pan_drag)
        self.image_canvas.bind("<ButtonRelease-1>", self._on_canvas_click)
        self.image_canvas.bind("<MouseWheel>", self._on_canvas_mousewheel)

        paned.add(img_card, weight=1)

        # Right: Rich Text Proofreader (Editable with Bounding Box Sync)
        text_card = tk.LabelFrame(
            paned,
            text=" Extracted Text Proofreader & Editor (Click Word to Highlight Bounding Box on Scan) ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=4,
            pady=4,
            relief=tk.GROOVE,
        )
        self.inspector_text = tk.Text(
            text_card,
            wrap=tk.WORD,
            font=(self.editor_font_family, self.editor_font_size),
            background=t["editor_bg"],
            foreground=t["editor_fg"],
            insertbackground=t["text_primary"],
            undo=True,
            maxundo=-1,
            padx=14,
            pady=14,
            relief=tk.FLAT,
        )
        text_scroll = ttk.Scrollbar(text_card, orient=tk.VERTICAL, command=self.inspector_text.yview)
        self.inspector_text.configure(yscrollcommand=text_scroll.set)
        self.inspector_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.inspector_text.tag_configure("rtl_text", justify=tk.RIGHT)
        self.inspector_text.tag_configure("ltr_text", justify=tk.LEFT)
        self.inspector_text.tag_configure(
            "low_conf_highlight",
            background="#fef08a" if self.current_theme_name == "light" else "#78350f",
            foreground="#b91c1c" if self.current_theme_name == "light" else "#fde047",
            font=(self.editor_font_family, self.editor_font_size, "bold"),
        )
        self.inspector_text.tag_configure("header_tag", font=(self.editor_font_family, self.editor_font_size + 1, "bold"), foreground=t["accent"])
        self.inspector_text.tag_configure("poetry_tag", foreground=t["accent_purple"], font=(self.editor_font_family, self.editor_font_size, "italic"))
        self.inspector_text.tag_configure("find_match", background="#3b82f6", foreground="#ffffff")
        self.inspector_text.tag_configure("active_word_sync", background="#0284c7", foreground="#ffffff")

        # Monitor text changes to notify user to save
        self.inspector_text.bind("<<Modified>>", self._on_proofreader_modified)
        # Bind cursor click / selection to trigger Bounding Box highlight
        self.inspector_text.bind("<ButtonRelease-1>", self._on_text_cursor_clicked)
        self.inspector_text.bind("<KeyRelease>", self._on_text_cursor_clicked)

        paned.add(text_card, weight=1)

        # Collapsible Visual Page Thumbnails Filmstrip Drawer
        self._build_inspector_filmstrip(page)

        return page

    # -------------------------------------------------------------------------
    # In-Editor Typography Scaling Handlers
    # -------------------------------------------------------------------------
    def _font_size_increase(self) -> None:
        self.editor_font_size = min(32, self.editor_font_size + 2)
        self.font_size_display_var.set(f"{self.editor_font_size}pt")
        self._apply_editor_font()

    def _font_size_decrease(self) -> None:
        self.editor_font_size = max(10, self.editor_font_size - 2)
        self.font_size_display_var.set(f"{self.editor_font_size}pt")
        self._apply_editor_font()

    def _on_editor_font_change(self, event=None) -> None:
        chosen = self.font_family_var.get().strip()
        if chosen:
            self.editor_font_family = chosen
            self._apply_editor_font()

    def _apply_editor_font(self) -> None:
        self.inspector_text.configure(font=(self.editor_font_family, self.editor_font_size))
        self.inspector_text.tag_configure("header_tag", font=(self.editor_font_family, self.editor_font_size + 1, "bold"))
        self.inspector_text.tag_configure("poetry_tag", font=(self.editor_font_family, self.editor_font_size, "italic"))
        self.inspector_text.tag_configure("low_conf_highlight", font=(self.editor_font_family, self.editor_font_size, "bold"))
        self._save_session_state()

    # -------------------------------------------------------------------------
    # In-Editor Find & Replace Handlers
    # -------------------------------------------------------------------------
    def _toggle_find_replace_bar(self) -> None:
        if self._find_bar_visible:
            self.find_replace_bar.pack_forget()
            self._find_bar_visible = False
            self.inspector_text.tag_remove("find_match", "1.0", tk.END)
        else:
            self.find_replace_bar.pack(fill=tk.X, pady=(0, 4), before=self.inspector_meta_label.master)
            self._find_bar_visible = True

    def _find_next_in_editor(self) -> None:
        q = self.find_query_var.get()
        self.inspector_text.tag_remove("find_match", "1.0", tk.END)
        if not q:
            return

        # Start search from insert cursor or 1.0
        start = self.inspector_text.index(tk.INSERT)
        pos = self.inspector_text.search(q, start, nocase=True, stopindex=tk.END)
        if not pos:
            # Wrap around to beginning
            pos = self.inspector_text.search(q, "1.0", nocase=True, stopindex=tk.END)

        if pos:
            end = f"{pos}+{len(q)}c"
            self.inspector_text.tag_add("find_match", pos, end)
            self.inspector_text.mark_set(tk.INSERT, end)
            self.inspector_text.see(pos)
        else:
            self.show_toast(f"No matches for '{q}'", "info")

    def _replace_one_in_editor(self) -> None:
        q = self.find_query_var.get()
        r = self.replace_query_var.get()
        if not q:
            return

        ranges = self.inspector_text.tag_ranges("find_match")
        if ranges:
            start, end = ranges[0], ranges[1]
            self.inspector_text.delete(start, end)
            self.inspector_text.insert(start, r)
            self.inspector_text.tag_remove("find_match", "1.0", tk.END)
            self.show_toast("Replaced occurrence", "success")
            self._find_next_in_editor()
        else:
            self._find_next_in_editor()

    def _replace_all_in_editor(self) -> None:
        q = self.find_query_var.get()
        r = self.replace_query_var.get()
        if not q:
            return

        content = self.inspector_text.get("1.0", tk.END)
        count = content.count(q)
        if count == 0:
            self.show_toast(f"No occurrences of '{q}' found", "info")
            return

        new_content = content.replace(q, r)
        self.inspector_text.delete("1.0", tk.END)
        self.inspector_text.insert("1.0", new_content)
        self.show_toast(f"Replaced {count} occurrence(s) on Page #{self._current_inspector_page}", "success")

    # -------------------------------------------------------------------------
    # Interactive Bounding Box Highlight Sync (Text -> Image)
    # -------------------------------------------------------------------------
    def _on_text_cursor_clicked(self, event=None) -> None:
        """Finds the word under the text cursor and draws its bounding box on the scan canvas."""
        data = self.page_results_cache.get(self._current_inspector_page)
        if not data or not data.get("layout"):
            return

        # Get word at cursor
        try:
            cursor_idx = self.inspector_text.index(tk.INSERT)
            line_str = self.inspector_text.get(f"{cursor_idx} linestart", f"{cursor_idx} lineend").strip()
            word_str = self.inspector_text.get(f"{cursor_idx} wordstart", f"{cursor_idx} wordend").strip()

            if not word_str:
                return

            # Highlight word in editor
            self.inspector_text.tag_remove("active_word_sync", "1.0", tk.END)
            self.inspector_text.tag_add("active_word_sync", f"{cursor_idx} wordstart", f"{cursor_idx} wordend")

            # Look through layout blocks and words for a coordinate match
            layout = data["layout"]
            bbox_found = None

            for el in layout.get("elements", []):
                for w in el.get("words", []):
                    if w.get("text") and word_str in w["text"]:
                        b = w.get("bbox") or w.get("box")
                        if b:
                            bbox_found = b
                            break
                if bbox_found:
                    break

            # Fallback to paragraph block bbox if word-level wasn't isolated
            if not bbox_found:
                for el in layout.get("elements", []):
                    if el.get("logical_text") and word_str in el["logical_text"]:
                        b = el.get("bbox") or el.get("box")
                        if b:
                            bbox_found = b
                            break

            if bbox_found:
                self._active_bbox = tuple(bbox_found)
                self._draw_bounding_box_overlay()
            else:
                self._active_bbox = None
                self._clear_bounding_box_overlay()

        except Exception:
            pass

    def _draw_bounding_box_overlay(self) -> None:
        """Draws a glowing neon rectangular overlay on the image canvas matching _active_bbox."""
        self._clear_bounding_box_overlay()
        if not self._active_bbox or not self._current_photo_image:
            return

        bx, by, bw, bh = self._active_bbox

        # Transform scan pixel coordinates to canvas coordinates
        scale = self._image_scale_ratio
        c_mid_x = (self.image_canvas.winfo_width() // 2) + self._pan_offset_x
        c_mid_y = (self.image_canvas.winfo_height() // 2) + self._pan_offset_y

        img_w = self._current_photo_image.width()
        img_h = self._current_photo_image.height()

        top_left_x = c_mid_x - (img_w // 2)
        top_left_y = c_mid_y - (img_h // 2)

        x1 = top_left_x + int(bx * scale)
        y1 = top_left_y + int(by * scale)
        x2 = x1 + int(bw * scale)
        y2 = y1 + int(bh * scale)

        # Draw glowing highlight rectangle
        self.image_canvas.create_rectangle(
            x1 - 2, y1 - 2, x2 + 2, y2 + 2,
            outline="#38bdf8",
            width=3,
            tags=("bbox_highlight",),
        )
        self.image_canvas.create_rectangle(
            x1, y1, x2, y2,
            outline="#ffffff",
            width=1,
            tags=("bbox_highlight",),
        )

    def _clear_bounding_box_overlay(self) -> None:
        self.image_canvas.delete("bbox_highlight")

    # -------------------------------------------------------------------------
    # Proofreader Modification & Checkpoint Commit
    # -------------------------------------------------------------------------
    def _on_proofreader_modified(self, event=None) -> None:
        if self.inspector_text.edit_modified():
            self.proofreader_status_var.set("● Unsaved Edits (Press Ctrl+S to Commit)")
            self.proofreader_status_label.configure(fg=self.t["accent_amber"])
            self.inspector_text.edit_modified(False)

    def _commit_inspector_edits(self) -> None:
        pnum = self._current_inspector_page
        text = self.inspector_text.get("1.0", tk.END).strip()
        if not text:
            return

        data = self.page_results_cache.get(pnum)
        if not data:
            data = {"page_number": pnum, "status": "success", "mean_confidence": 99.0}
            self.page_results_cache[pnum] = data

        data["extracted_text"] = text
        data["manually_edited"] = True

        words = text.split()
        data["word_count"] = len(words)

        chk_dir = self.config.checkpoint_dir
        os.makedirs(chk_dir, exist_ok=True)
        cfile = os.path.join(chk_dir, f"page_{pnum:05d}.json")
        try:
            with open(cfile, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.proofreader_status_var.set(f"✓ Page #{pnum} Committed to Checkpoint!")
            self.proofreader_status_label.configure(fg=self.t["accent_green"])
            self.show_toast(f"Page #{pnum} edits saved to book checkpoint!", "success")
        except Exception as exc:
            self.show_toast(f"Commit failed: {exc}", "error")

    def _export_active_page_dialog(self) -> None:
        """Export the currently inspected page directly to DOCX, PDF, HTML, or TXT."""
        pnum = self._current_inspector_page
        data = self.page_results_cache.get(pnum)
        if not data:
            cfile = os.path.join(self.config.checkpoint_dir, f"page_{pnum:05d}.json")
            if os.path.exists(cfile):
                try:
                    with open(cfile, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.page_results_cache[pnum] = data
                except Exception:
                    data = None

        if not data or not data.get("layout"):
            messagebox.showwarning(
                "Export Single Page",
                f"Page #{pnum} has not been processed or has no layout data.\n"
                "Please process the document or page first.",
                parent=self.root,
            )
            return

        layout = dict(data.get("layout", {}))
        if data.get("manually_edited") and data.get("extracted_text"):
            from batch_processor import BatchProcessor
            BatchProcessor(self.config)._apply_manual_text_to_layout(layout, data["extracted_text"])

        default_name = f"page_{pnum:04d}"
        file_path = filedialog.asksaveasfilename(
            title=f"Export Page #{pnum} Digital File",
            initialfile=default_name,
            filetypes=[
                ("Microsoft Word Document (*.docx)", "*.docx"),
                ("Searchable Reflowable PDF (*.pdf)", "*.pdf"),
                ("Plain Text File (*.txt)", "*.txt"),
                ("HTML5 Replica (*.html)", "*.html"),
            ],
            defaultextension=".docx",
            parent=self.root,
        )
        if not file_path:
            return

        try:
            builder = BookBuilder(self.config)
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".docx":
                builder.build_docx([layout], file_path)
            elif ext == ".pdf":
                builder.build_pdf([layout], file_path)
            elif ext == ".html":
                builder.build_html_replica([layout], file_path)
            else:
                builder.build_txt([layout], file_path)

            self.show_toast(f"Page #{pnum} exported to {os.path.basename(file_path)}!", "success")
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Could not export page: {exc}", parent=self.root)

    # -------------------------------------------------------------------------
    # Interactive Canvas Pan & Mousewheel Zoom Handlers
    # -------------------------------------------------------------------------
    def _on_pan_start(self, event) -> None:
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self._pan_has_dragged = False

    def _on_pan_drag(self, event) -> None:
        dx = event.x - self._pan_start_x
        dy = event.y - self._pan_start_y
        if abs(dx) > 3 or abs(dy) > 3:
            self._pan_has_dragged = True
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self._pan_offset_x += dx
        self._pan_offset_y += dy
        self._render_current_inspector_image(redraw_only=True)

    def _on_canvas_click(self, event) -> None:
        if getattr(self, "_pan_has_dragged", False):
            return
        self._sync_canvas_to_text(event.x, event.y)

    def _sync_canvas_to_text(self, canvas_x: int, canvas_y: int) -> None:
        """
        Reverse Sync: Clicking on the scan image locates the corresponding word or block
        in layout data and jumps the proofreader text cursor to that position.
        """
        if not self._current_photo_image or self._image_scale_ratio <= 0:
            return

        pnum = self._current_inspector_page
        data = self.page_results_cache.get(pnum)
        if not data or not data.get("layout"):
            return

        scale = self._image_scale_ratio
        c_mid_x = (self.image_canvas.winfo_width() // 2) + self._pan_offset_x
        c_mid_y = (self.image_canvas.winfo_height() // 2) + self._pan_offset_y

        img_w = self._current_photo_image.width()
        img_h = self._current_photo_image.height()

        top_left_x = c_mid_x - (img_w // 2)
        top_left_y = c_mid_y - (img_h // 2)

        rel_x = canvas_x - top_left_x
        rel_y = canvas_y - top_left_y

        # Check bounds
        if rel_x < 0 or rel_x > img_w or rel_y < 0 or rel_y > img_h:
            self._active_bbox = None
            self._clear_bounding_box_overlay()
            return

        scan_x = rel_x / scale
        scan_y = rel_y / scale

        layout = data["layout"]
        elements = layout.get("elements", [])
        if not elements:
            return

        best_word = None
        best_bbox = None
        best_dist = float("inf")
        best_exact_match = False

        # First pass: check word-level bounding boxes
        for el in elements:
            for w in el.get("words", []):
                wb = w.get("bbox") or w.get("box")
                if not wb or len(wb) < 4:
                    continue
                bx, by, bw, bh = wb
                if bx <= scan_x <= bx + bw and by <= scan_y <= by + bh:
                    best_word = w.get("text", "").strip()
                    best_bbox = tuple(wb)
                    best_exact_match = True
                    break
                cx = bx + (bw / 2.0)
                cy = by + (bh / 2.0)
                dist = ((cx - scan_x) ** 2 + (cy - scan_y) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_word = w.get("text", "").strip()
                    best_bbox = tuple(wb)
            if best_exact_match:
                break

        # Fallback to paragraph block if no exact word
        if not best_exact_match:
            for el in elements:
                eb = el.get("bbox") or el.get("box")
                if not eb or len(eb) < 4:
                    continue
                ex, ey, ew, eh = eb
                if ex <= scan_x <= ex + ew and ey <= scan_y <= ey + eh:
                    el_text = el.get("logical_text", "").strip()
                    if el_text:
                        words = el_text.split()
                        best_word = words[0] if words else el_text
                        best_bbox = tuple(eb)
                        best_exact_match = True
                        break

        if best_bbox and (best_exact_match or best_dist <= 50):
            self._active_bbox = best_bbox
            self._draw_bounding_box_overlay()

            if best_word:
                search_word = best_word.strip(",.?!;:\"'()[]{}«»-")
                if not search_word:
                    search_word = best_word

                pos = self.inspector_text.search(search_word, "1.0", nocase=True, stopindex=tk.END)
                if not pos and len(search_word) > 3:
                    pos = self.inspector_text.search(search_word[:3], "1.0", nocase=True, stopindex=tk.END)

                if pos:
                    end_pos = f"{pos}+{len(search_word)}c"
                    self.inspector_text.tag_remove("active_word_sync", "1.0", tk.END)
                    self.inspector_text.tag_add("active_word_sync", pos, end_pos)
                    self.inspector_text.mark_set(tk.INSERT, pos)
                    self.inspector_text.see(pos)
                    self.show_toast(f"📍 Linked scan to '{search_word}'", "info", duration_ms=1600)
        else:
            self._active_bbox = None
            self._clear_bounding_box_overlay()

    # -------------------------------------------------------------------------
    # Visual Page Filmstrip / Bottom Scrubber Drawer
    # -------------------------------------------------------------------------
    def _build_inspector_filmstrip(self, parent: tk.Frame) -> None:
        t = self.t
        self.filmstrip_panel = tk.Frame(parent, bg=t["bg_card"], bd=1, relief=tk.SOLID)
        self.filmstrip_panel.pack(fill=tk.X, pady=(6, 0))

        # Filmstrip header bar
        strip_hdr = tk.Frame(self.filmstrip_panel, bg=t["bg_card_inner"], padx=8, pady=4)
        strip_hdr.pack(fill=tk.X)

        tk.Label(
            strip_hdr,
            text="🖼️ Page Filmstrip & Scrubber",
            font=("Segoe UI", 8, "bold"),
            fg=t["accent"],
            bg=t["bg_card_inner"],
        ).pack(side=tk.LEFT, padx=(2, 8))

        # Filter Chips
        self.filmstrip_chip_all = tk.Button(
            strip_hdr,
            text="All Pages",
            font=("Segoe UI", 7, "bold"),
            fg="#ffffff" if self._filmstrip_filter == "all" else t["text_secondary"],
            bg=t["accent"] if self._filmstrip_filter == "all" else t["bg_card"],
            relief=tk.FLAT,
            padx=6,
            pady=1,
            cursor="hand2",
            command=lambda: self._set_filmstrip_filter("all"),
        )
        self.filmstrip_chip_all.pack(side=tk.LEFT, padx=2)

        self.filmstrip_chip_flagged = tk.Button(
            strip_hdr,
            text="🔴 Flagged Only",
            font=("Segoe UI", 7, "bold"),
            fg="#ffffff" if self._filmstrip_filter == "flagged" else t["accent_red"],
            bg=t["accent_red"] if self._filmstrip_filter == "flagged" else t["bg_card"],
            relief=tk.FLAT,
            padx=6,
            pady=1,
            cursor="hand2",
            command=lambda: self._set_filmstrip_filter("flagged"),
        )
        self.filmstrip_chip_flagged.pack(side=tk.LEFT, padx=2)

        self.filmstrip_count_label = tk.Label(
            strip_hdr,
            text="0 pages",
            font=("Segoe UI", 8),
            fg=t["text_muted"],
            bg=t["bg_card_inner"],
        )
        self.filmstrip_count_label.pack(side=tk.LEFT, padx=8)

        self.filmstrip_toggle_btn = tk.Button(
            strip_hdr,
            text="▼ Hide Filmstrip (F6)",
            font=("Segoe UI", 7, "bold"),
            fg=t["text_secondary"],
            bg=t["bg_card"],
            relief=tk.FLAT,
            padx=8,
            pady=1,
            cursor="hand2",
            command=self._toggle_filmstrip_visibility,
        )
        self.filmstrip_toggle_btn.pack(side=tk.RIGHT, padx=2)

        # Content Frame
        self.filmstrip_content = tk.Frame(self.filmstrip_panel, bg=t["canvas_bg"])
        self.filmstrip_content.pack(fill=tk.X, expand=False)

        self.filmstrip_canvas = tk.Canvas(
            self.filmstrip_content,
            bg=t["canvas_bg"],
            height=116,
            highlightthickness=0,
            bd=0,
        )
        strip_scroll = ttk.Scrollbar(
            self.filmstrip_content,
            orient=tk.HORIZONTAL,
            command=self.filmstrip_canvas.xview,
        )
        self.filmstrip_canvas.configure(xscrollcommand=strip_scroll.set)

        self.filmstrip_cards_frame = tk.Frame(self.filmstrip_canvas, bg=t["canvas_bg"])
        self.filmstrip_cards_frame.bind(
            "<Configure>",
            lambda e: self.filmstrip_canvas.configure(scrollregion=self.filmstrip_canvas.bbox("all")),
        )
        self.filmstrip_canvas.create_window((0, 0), window=self.filmstrip_cards_frame, anchor="nw")

        self.filmstrip_canvas.pack(fill=tk.X, expand=False)
        strip_scroll.pack(fill=tk.X, side=tk.BOTTOM)

        # Mousewheel horizontal scrolling
        self.filmstrip_canvas.bind("<MouseWheel>", self._on_filmstrip_mousewheel)
        self.filmstrip_canvas.bind("<Shift-MouseWheel>", self._on_filmstrip_mousewheel)

        self._refresh_filmstrip()

    def _toggle_filmstrip_visibility(self) -> None:
        if self._filmstrip_visible:
            self.filmstrip_content.pack_forget()
            self._filmstrip_visible = False
            if self.filmstrip_toggle_btn:
                self.filmstrip_toggle_btn.configure(text="▲ Show Filmstrip (F6)")
        else:
            self.filmstrip_content.pack(fill=tk.X, expand=False)
            self._filmstrip_visible = True
            if self.filmstrip_toggle_btn:
                self.filmstrip_toggle_btn.configure(text="▼ Hide Filmstrip (F6)")
            self._refresh_filmstrip()

    def _set_filmstrip_filter(self, filter_mode: str) -> None:
        self._filmstrip_filter = filter_mode
        t = self.t
        if self.filmstrip_chip_all and self.filmstrip_chip_flagged:
            if filter_mode == "all":
                self.filmstrip_chip_all.configure(bg=t["accent"], fg="#ffffff")
                self.filmstrip_chip_flagged.configure(bg=t["bg_card"], fg=t["accent_red"])
            else:
                self.filmstrip_chip_all.configure(bg=t["bg_card"], fg=t["text_secondary"])
                self.filmstrip_chip_flagged.configure(bg=t["accent_red"], fg="#ffffff")
        self._refresh_filmstrip()

    def _on_filmstrip_mousewheel(self, event) -> None:
        if self.filmstrip_canvas:
            self.filmstrip_canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

    def _create_placeholder_thumbnail(self, pnum: int) -> ImageTk.PhotoImage:
        is_dark = self.current_theme_name == "dark"
        bg_col = "#1e293b" if is_dark else "#e2e8f0"
        border_col = "#334155" if is_dark else "#cbd5e1"
        line_col = "#475569" if is_dark else "#94a3b8"

        img = Image.new("RGB", (48, 62), color=bg_col)
        draw = ImageDraw.Draw(img)
        draw.rectangle([1, 1, 46, 60], outline=border_col, width=1)
        # Decorative header line
        draw.line([8, 10, 26, 10], fill=line_col, width=2)
        # Decorative text lines
        for y in (16, 22, 28, 34, 40, 46):
            draw.line([8, y, 40, y], fill=line_col, width=1)
        return ImageTk.PhotoImage(img)

    def _get_filmstrip_thumbnail_image(self, pnum: int) -> ImageTk.PhotoImage:
        if pnum in self._thumbnail_cache:
            return self._thumbnail_cache[pnum]

        data = self.page_results_cache.get(pnum)
        candidates = []
        if data and data.get("preview_image_path") and os.path.exists(data["preview_image_path"]):
            candidates.append(data["preview_image_path"])

        ext_dir = self.config.extracted_images_dir
        candidates.extend([
            os.path.join(ext_dir, f"page_{pnum:05d}_preview.jpg"),
            os.path.join(ext_dir, f"preview_p{pnum:05d}_proc.jpg"),
            os.path.join(ext_dir, f"preview_p{pnum:05d}_orig.jpg"),
            os.path.join(ext_dir, f"page_{pnum:05d}_preview.png"),
        ])

        found_path = next((p for p in candidates if os.path.exists(p)), None)
        if found_path:
            try:
                pil_img = Image.open(found_path)
                pil_img.thumbnail((48, 62), Image.Resampling.LANCZOS)
                canvas_bg = "#0f172a" if self.current_theme_name == "dark" else "#f8fafc"
                base_img = Image.new("RGB", (48, 62), color=canvas_bg)
                offset_x = (48 - pil_img.width) // 2
                offset_y = (62 - pil_img.height) // 2
                base_img.paste(pil_img, (offset_x, offset_y))
                photo = ImageTk.PhotoImage(base_img)
                self._thumbnail_cache[pnum] = photo
                return photo
            except Exception:
                pass

        placeholder = self._create_placeholder_thumbnail(pnum)
        self._thumbnail_cache[pnum] = placeholder
        return placeholder

    def _refresh_filmstrip(self) -> None:
        if not hasattr(self, "filmstrip_cards_frame") or not self.filmstrip_cards_frame:
            return

        for child in self.filmstrip_cards_frame.winfo_children():
            child.destroy()
        self._filmstrip_cards.clear()

        t = self.t
        cached_pages = set(self.page_results_cache.keys())
        tot = getattr(self, "_detected_total_pages", 0)
        if tot <= 0 and self.config.input_pdf_path and os.path.exists(self.config.input_pdf_path):
            try:
                tot = get_pdf_page_count(self.config.input_pdf_path)
                self._detected_total_pages = tot
            except Exception:
                tot = 0

        all_pages = set(range(1, tot + 1)) | cached_pages
        if not all_pages:
            all_pages = {1}

        sorted_pages = sorted(all_pages)

        if self._filmstrip_filter == "flagged":
            filtered = []
            for p in sorted_pages:
                d = self.page_results_cache.get(p)
                if d:
                    conf = float(d.get("mean_confidence", 0.0))
                    low = int(d.get("low_confidence_word_count", 0))
                    if conf < 80.0 or low > 0 or d.get("status") != "success":
                        filtered.append(p)
            pages_to_show = filtered
        else:
            pages_to_show = sorted_pages

        if self.filmstrip_count_label:
            count_str = f"{len(pages_to_show)} of {len(sorted_pages)} pages"
            if self._filmstrip_filter == "flagged":
                count_str += " (Flagged Only)"
            self.filmstrip_count_label.configure(text=count_str)

        for p in pages_to_show:
            d = self.page_results_cache.get(p)
            conf = float(d.get("mean_confidence", 0.0)) if d else None
            is_active = (p == self._current_inspector_page)

            card_border = "#38bdf8" if is_active else t["border"]
            card_bd = 2 if is_active else 1
            card_bg = t["bg_card"] if is_active else t["bg_card_inner"]

            card = tk.Frame(
                self.filmstrip_cards_frame,
                bg=card_bg,
                bd=card_bd,
                relief=tk.SOLID,
                padx=2,
                pady=2,
                cursor="hand2",
                width=58,
                height=106,
            )
            card.pack_propagate(False)
            card.pack(side=tk.LEFT, padx=3, pady=2)

            lbl_p = tk.Label(
                card,
                text=f"#{p}",
                font=("Segoe UI", 7, "bold"),
                fg="#38bdf8" if is_active else t["text_primary"],
                bg=card_bg,
            )
            lbl_p.pack(anchor=tk.CENTER)

            thumb_img = self._get_filmstrip_thumbnail_image(p)
            lbl_img = tk.Label(card, image=thumb_img, bg=card_bg, cursor="hand2")
            lbl_img.image = thumb_img
            lbl_img.pack(anchor=tk.CENTER, pady=1)

            if conf is not None:
                if d and d.get("status") != "success":
                    badge_text = "ERR"
                    badge_fg = t["accent_red"]
                elif conf >= 80.0 and d.get("low_confidence_word_count", 0) == 0:
                    badge_text = f"{int(conf)}%"
                    badge_fg = t["accent_green"]
                elif conf >= 65.0:
                    badge_text = f"{int(conf)}%"
                    badge_fg = t["accent_amber"]
                else:
                    badge_text = f"{int(conf)}%"
                    badge_fg = t["accent_red"]
            else:
                badge_text = "—"
                badge_fg = t["text_muted"]

            lbl_badge = tk.Label(
                card,
                text=badge_text,
                font=("Segoe UI", 7, "bold"),
                fg=badge_fg,
                bg=card_bg,
            )
            lbl_badge.pack(anchor=tk.CENTER)

            for w in (card, lbl_p, lbl_img, lbl_badge):
                w.bind("<Button-1>", lambda e, pn=p: self._on_filmstrip_card_clicked(pn))

            self._filmstrip_cards[p] = card

    def _highlight_filmstrip_card(self, pnum: int) -> None:
        t = self.t
        for p, card in self._filmstrip_cards.items():
            if not card.winfo_exists():
                continue
            is_active = (p == pnum)
            border_color = "#38bdf8" if is_active else t["border"]
            bd_size = 2 if is_active else 1
            bg_color = t["bg_card"] if is_active else t["bg_card_inner"]
            card.configure(bg=bg_color, bd=bd_size)
            for child in card.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg_color)
                    if child.cget("text").startswith("#"):
                        child.configure(fg="#38bdf8" if is_active else t["text_primary"])

    def _on_filmstrip_card_clicked(self, pnum: int) -> None:
        self._load_page_into_inspector(pnum)

    def _update_filmstrip_page(self, pnum: int, data: Dict[str, Any]) -> None:
        self._thumbnail_cache.pop(pnum, None)
        if pnum in self._filmstrip_cards and self._filmstrip_cards[pnum].winfo_exists():
            card = self._filmstrip_cards[pnum]
            t = self.t
            conf = float(data.get("mean_confidence", 0.0))
            if data.get("status") != "success":
                badge_text = "ERR"
                badge_fg = t["accent_red"]
            elif conf >= 80.0 and data.get("low_confidence_word_count", 0) == 0:
                badge_text = f"{int(conf)}%"
                badge_fg = t["accent_green"]
            elif conf >= 65.0:
                badge_text = f"{int(conf)}%"
                badge_fg = t["accent_amber"]
            else:
                badge_text = f"{int(conf)}%"
                badge_fg = t["accent_red"]

            children = card.winfo_children()
            if len(children) >= 3:
                thumb_img = self._get_filmstrip_thumbnail_image(pnum)
                children[1].configure(image=thumb_img)
                children[1].image = thumb_img
                children[2].configure(text=badge_text, fg=badge_fg)
        else:
            self._refresh_filmstrip()

    def _on_canvas_mousewheel(self, event) -> None:
        if event.delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()

    def _zoom_in(self) -> None:
        self._auto_fit_mode = False
        self._zoom_factor = min(4.0, self._zoom_factor * 1.25)
        self.zoom_display_var.set(f"{int(self._zoom_factor * 100)}%")
        self._render_current_inspector_image()

    def _zoom_out(self) -> None:
        self._auto_fit_mode = False
        self._zoom_factor = max(0.2, self._zoom_factor * 0.8)
        self.zoom_display_var.set(f"{int(self._zoom_factor * 100)}%")
        self._render_current_inspector_image()

    def _zoom_100(self) -> None:
        self._auto_fit_mode = False
        self._zoom_factor = 1.0
        self._pan_offset_x = 0
        self._pan_offset_y = 0
        self.zoom_display_var.set("100%")
        self._render_current_inspector_image()

    def _zoom_fit(self) -> None:
        self._auto_fit_mode = True
        self._zoom_factor = 1.0
        self._pan_offset_x = 0
        self._pan_offset_y = 0
        self.zoom_display_var.set("Fit")
        self._render_current_inspector_image()

    def _toggle_scan_view_mode(self) -> None:
        current = self.inspector_view_mode_var.get()
        self.inspector_view_mode_var.set("original" if current == "processed" else "processed")
        self._render_current_inspector_image()

    # -------------------------------------------------------------------------
    # Pop-Out Multi-Window Implementations
    # -------------------------------------------------------------------------
    def _popout_inspector_window(self) -> None:
        if self._detached_inspector_win and tk.Toplevel.winfo_exists(self._detached_inspector_win):
            self._detached_inspector_win.lift()
            return

        t = self.t
        win = tk.Toplevel(self.root)
        win.title(f"Detached Facsimile Inspector & Proofreader — Page #{self._current_inspector_page}")
        win.geometry("1100x820")
        win.minsize(800, 600)
        win.configure(background=t["bg_root"])
        self._apply_windows_dark_titlebar(win, self.current_theme_name == "dark")
        self._detached_inspector_win = win

        top_bar = tk.Frame(win, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=10, pady=6)
        top_bar.pack(fill=tk.X, pady=6, padx=8)

        tk.Button(
            top_bar,
            text="◀ Prev",
            font=("Segoe UI", 8, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._inspector_prev_page,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            top_bar,
            text="Next ▶",
            font=("Segoe UI", 8, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._inspector_next_page,
        ).pack(side=tk.LEFT, padx=4)

        tk.Label(
            top_bar,
            textvariable=self.inspector_meta_var,
            font=("Segoe UI", 8, "bold"),
            fg=t["accent"],
            bg=t["bg_card"],
        ).pack(side=tk.LEFT, padx=10)

        tk.Button(
            top_bar,
            text="💾 Commit Changes",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent_green"],
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._commit_inspector_edits,
        ).pack(side=tk.RIGHT, padx=4)

        tk.Button(
            top_bar,
            text="📋 Copy Text",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._copy_inspector_text,
        ).pack(side=tk.RIGHT, padx=2)

        paned = ttk.PanedWindow(win, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        c_frame = tk.Frame(paned, bg=t["canvas_bg"])
        pop_canvas = tk.Canvas(c_frame, background=t["canvas_bg"], highlightthickness=0)
        pop_canvas.pack(fill=tk.BOTH, expand=True)
        paned.add(c_frame, weight=1)

        t_frame = tk.Frame(paned, bg=t["editor_bg"])
        pop_text = tk.Text(
            t_frame,
            wrap=tk.WORD,
            font=(self.editor_font_family, self.editor_font_size),
            background=t["editor_bg"],
            foreground=t["editor_fg"],
            padx=14,
            pady=14,
            relief=tk.FLAT,
        )
        pop_text.pack(fill=tk.BOTH, expand=True)
        paned.add(t_frame, weight=1)

        current_text = self.inspector_text.get("1.0", tk.END)
        pop_text.insert(tk.END, current_text)

    def _popout_console_window(self) -> None:
        if self._detached_console_win and tk.Toplevel.winfo_exists(self._detached_console_win):
            self._detached_console_win.lift()
            return

        t = self.t
        win = tk.Toplevel(self.root)
        win.title("Scripta Pro — Detached Live Pipeline Terminal")
        win.geometry("900x600")
        win.minsize(600, 400)
        win.configure(background=t["console_bg"])
        self._apply_windows_dark_titlebar(win, self.current_theme_name == "dark")
        self._detached_console_win = win

        pop_text = tk.Text(
            win,
            wrap=tk.WORD,
            font=("Consolas", 10),
            background=t["console_bg"],
            foreground=t["console_fg"],
            insertbackground="#ffffff",
            padx=10,
            pady=10,
            relief=tk.FLAT,
        )
        scroll = ttk.Scrollbar(win, orient=tk.VERTICAL, command=pop_text.yview)
        pop_text.configure(yscrollcommand=scroll.set)
        pop_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        pop_text.insert(tk.END, self.log_text.get("1.0", tk.END))
        pop_text.see(tk.END)

    # -------------------------------------------------------------------------
    # Page 3: Quality & Output
    # -------------------------------------------------------------------------
    # -------------------------------------------------------------------------
    # Page 3: Quality & Output Hub
    # -------------------------------------------------------------------------
    def _create_quality_page(self, parent: tk.Frame) -> tk.Frame:
        t = self.t
        page = tk.Frame(parent, bg=t["bg_root"])

        # Top Bar
        top_bar = tk.Frame(page, bg=t["bg_root"])
        top_bar.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            top_bar,
            text="Batch Output Hub & Quality Analytics",
            font=("Segoe UI", 12, "bold"),
            fg=t["text_primary"],
            bg=t["bg_root"],
        ).pack(side=tk.LEFT)

        tk.Button(
            top_bar,
            text="📁 Open Output Folder",
            font=("Segoe UI", 8, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card"],
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._open_output_folder,
        ).pack(side=tk.RIGHT)

        # QA KPI Summary Tiles Bar
        kpi_row = tk.Frame(page, bg=t["bg_root"])
        kpi_row.pack(fill=tk.X, pady=(0, 10))

        def make_qa_kpi(p_frame, title: str, textvar: tk.StringVar, color: str):
            f = tk.Frame(p_frame, bg=t["stat_bg"], bd=1, relief=tk.SOLID, padx=12, pady=8)
            f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
            tk.Label(f, text=title, font=("Segoe UI", 7, "bold"), fg=t["text_muted"], bg=t["stat_bg"]).pack(anchor=tk.W)
            tk.Label(f, textvariable=textvar, font=("Segoe UI", 12, "bold"), fg=color, bg=t["stat_bg"]).pack(anchor=tk.W, pady=(2, 0))

        make_qa_kpi(kpi_row, "PAGES IN CHECKPOINTS", self.qa_kpi_pages_var, t["text_primary"])
        make_qa_kpi(kpi_row, "MEAN OCR ACCURACY", self.qa_kpi_conf_var, t["accent_green"])
        make_qa_kpi(kpi_row, "EXTRACTED WORDS", self.qa_kpi_words_var, t["accent"])
        make_qa_kpi(kpi_row, "FLAGGED WORDS", self.qa_kpi_flags_var, t["accent_amber"])
        make_qa_kpi(kpi_row, "PROOFREADER EDITS", self.qa_kpi_edits_var, t["accent_purple"])

        # Primary Re-assembly Action Banner Card
        re_card = tk.Frame(page, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=14, pady=10)
        re_card.pack(fill=tk.X, pady=(0, 12))

        left_info = tk.Frame(re_card, bg=t["bg_card"])
        left_info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(
            left_info,
            text="⚡ Dynamic Publication Exporter & Book Re-assembler",
            font=("Segoe UI", 9, "bold"),
            fg=t["accent"],
            bg=t["bg_card"],
        ).pack(anchor=tk.W)

        tk.Label(
            left_info,
            text="Instantly re-compiles all selected formats from saved checkpoints with 100% of human proofreading edits preserved. No re-running OCR required!",
            font=("Segoe UI", 8),
            fg=t["text_secondary"],
            bg=t["bg_card"],
        ).pack(anchor=tk.W, pady=(2, 0))

        right_actions = tk.Frame(re_card, bg=t["bg_card"])
        right_actions.pack(side=tk.RIGHT)

        tk.Button(
            right_actions,
            text="⚡ Re-compile All Book Outputs Now",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent_green"],
            activebackground="#059669",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            cursor="hand2",
            command=self._reassemble_outputs_on_demand,
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            right_actions,
            text="🔄 Refresh",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=6,
            cursor="hand2",
            command=self._refresh_quality_page,
        ).pack(side=tk.LEFT, padx=2)

        # Publications Grid
        cards_container = tk.Frame(page, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=14, pady=12)
        cards_container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            cards_container,
            text="Generated Facsimile Publications & Archival Replicas",
            font=("Segoe UI", 9, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card"],
        ).pack(anchor=tk.W, pady=(0, 8))

        grid = tk.Frame(cards_container, bg=t["bg_card"])
        grid.pack(fill=tk.BOTH, expand=True)

        outputs = [
            ("📄 Microsoft Word (.docx)", "docx", "Fully reflowable typography with authentic Urdu & Arabic ligatures."),
            ("📑 Sandwich Searchable PDF (.pdf)", "sandwich_pdf", "Invisible OCR text layer placed behind high-resolution page scan."),
            ("📑 Reflowable PDF (.pdf)", "pdf", "Clean vector PDF formatted with ReportLab typography."),
            ("📖 EPUB3 E-Book (.epub)", "epub", "Standard reflowable e-book package with complete table of contents."),
            ("🌐 HTML5 Visual Replica (.html)", "html", "Interactive browser replica maintaining multi-column layouts & poetry."),
            ("📝 Quality Assurance Report (.txt)", "report", "Comprehensive statistical audit of word count and low-conf flags."),
        ]

        self.qa_file_status_labels = {}

        for i, (title, key, desc) in enumerate(outputs):
            r, c = divmod(i, 2)
            card = tk.Frame(grid, bg=t["bg_card_inner"], bd=1, relief=tk.SOLID, padx=12, pady=8)
            card.grid(row=r, column=c, sticky=tk.NSEW, padx=5, pady=5)

            hdr = tk.Frame(card, bg=t["bg_card_inner"])
            hdr.pack(fill=tk.X)

            tk.Label(hdr, text=title, font=("Segoe UI", 9, "bold"), fg=t["text_primary"], bg=t["bg_card_inner"]).pack(
                side=tk.LEFT
            )

            status_lbl = tk.Label(
                hdr,
                text="⚪ Checking...",
                font=("Segoe UI", 7, "bold"),
                fg=t["text_muted"],
                bg=t["bg_card_inner"],
            )
            status_lbl.pack(side=tk.RIGHT)
            self.qa_file_status_labels[key] = status_lbl

            tk.Label(
                card, text=desc, font=("Segoe UI", 8), fg=t["text_secondary"], bg=t["bg_card_inner"], wraplength=380
            ).pack(anchor=tk.W, pady=(2, 6))

            btn_box = tk.Frame(card, bg=t["bg_card_inner"])
            btn_box.pack(anchor=tk.E)

            tk.Button(
                btn_box,
                text="📁 Show in Folder",
                font=("Segoe UI", 8),
                fg=t["text_primary"],
                bg=t["bg_card"],
                relief=tk.FLAT,
                padx=8,
                pady=2,
                cursor="hand2",
                command=lambda k=key: self._open_file_in_folder(k),
            ).pack(side=tk.LEFT, padx=3)

            tk.Button(
                btn_box,
                text="🚀 Open File",
                font=("Segoe UI", 8, "bold"),
                fg="#ffffff",
                bg=t["accent"],
                relief=tk.FLAT,
                padx=10,
                pady=2,
                cursor="hand2",
                command=lambda k=key: self._launch_file(k),
            ).pack(side=tk.LEFT, padx=3)

        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._refresh_quality_page()
        return page

    def _refresh_quality_page(self) -> None:
        """Dynamically audits all checkpoints and checks output file existence."""
        t = self.t
        cached = self.page_results_cache
        total_p = len(cached)
        if total_p > 0:
            avg_conf = sum(float(d.get("mean_confidence", 0.0)) for d in cached.values()) / float(total_p)
            tot_words = sum(int(d.get("word_count", 0)) for d in cached.values())
            tot_flags = sum(int(d.get("low_confidence_word_count", 0)) for d in cached.values())
            tot_edits = sum(1 for d in cached.values() if d.get("manually_edited"))

            self.qa_kpi_pages_var.set(f"{total_p} Pages")
            self.qa_kpi_conf_var.set(f"{avg_conf:.1f}%")
            self.qa_kpi_words_var.set(f"{tot_words:,} Words")
            self.qa_kpi_flags_var.set(f"{tot_flags:,} Low-Conf")
            self.qa_kpi_edits_var.set(f"{tot_edits} Corrected")
        else:
            self.qa_kpi_pages_var.set("0 Pages")
            self.qa_kpi_conf_var.set("0.0%")
            self.qa_kpi_words_var.set("0 Words")
            self.qa_kpi_flags_var.set("0 Low-Conf")
            self.qa_kpi_edits_var.set("0 Corrected")

        out_dir = self.output_dir_var.get().strip() or self.config.output_dir
        filename_map = {
            "docx": "digitized_book.docx",
            "sandwich_pdf": "digitized_book_sandwich.pdf",
            "pdf": "digitized_book_reflowable.pdf",
            "epub": "digitized_book.epub",
            "html": "digitized_book_replica.html",
            "report": "quality_report.txt",
        }

        for key, fname in filename_map.items():
            lbl = getattr(self, "qa_file_status_labels", {}).get(key)
            if not lbl or not lbl.winfo_exists():
                continue
            fpath = os.path.join(out_dir, fname)
            if key == "pdf" and not os.path.exists(fpath):
                alt = os.path.join(out_dir, "digitized_book_searchable.pdf")
                if os.path.exists(alt):
                    fpath = alt

            if os.path.exists(fpath):
                try:
                    sz = os.path.getsize(fpath)
                    sz_str = f"{sz / (1024 * 1024):.1f} MB" if sz >= 1024 * 1024 else f"{sz / 1024:.0f} KB"
                    lbl.configure(text=f"🟢 Generated ({sz_str})", fg=t["accent_green"])
                except Exception:
                    lbl.configure(text="🟢 Generated", fg=t["accent_green"])
            else:
                lbl.configure(text="⚪ Not Yet Generated", fg=t["text_muted"])

    def _open_file_in_folder(self, file_type: str) -> None:
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
        if file_type == "pdf" and not os.path.exists(fpath):
            alt_path = os.path.join(out_dir, "digitized_book_searchable.pdf")
            if os.path.exists(alt_path):
                fpath = alt_path

        if not os.path.exists(fpath):
            self.show_toast(f"'{filename}' has not been generated yet", "warn")
            return

        try:
            if platform.system() == "Windows":
                subprocess.Popen(f'explorer /select,"{os.path.normpath(fpath)}"')
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", "-R", fpath])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(fpath)])
            self.show_toast(f"Located in folder: {filename}", "info")
        except Exception as exc:
            self.show_toast(f"Error opening folder: {exc}", "error")

    # -------------------------------------------------------------------------
    # Page 4: Pipeline Execution Console with Syntax Highlighting & Search
    # -------------------------------------------------------------------------
    def _create_console_page(self, parent: tk.Frame) -> tk.Frame:
        t = self.t
        page = tk.Frame(parent, bg=t["bg_root"])

        header_bar = tk.Frame(page, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=10, pady=8)
        header_bar.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            header_bar,
            text="Pipeline Execution Log Stream",
            font=("Segoe UI", 10, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card"],
        ).pack(side=tk.LEFT)

        tk.Label(header_bar, text="Search Log:", font=("Segoe UI", 8), bg=t["bg_card"], fg=t["text_muted"]).pack(
            side=tk.LEFT, padx=(16, 4)
        )
        log_search_entry = ttk.Entry(header_bar, textvariable=self.log_filter_var, width=16)
        log_search_entry.pack(side=tk.LEFT, padx=(0, 4))
        log_search_entry.bind("<Return>", lambda e: self._search_log_text())
        tk.Button(
            header_bar,
            text="Find",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._search_log_text,
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            header_bar,
            text="🗗 Pop-out Console",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent_purple"],
            relief=tk.FLAT,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._popout_console_window,
        ).pack(side=tk.RIGHT, padx=4)

        tk.Button(
            header_bar,
            text="Save Log...",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._save_log,
        ).pack(side=tk.RIGHT, padx=4)

        tk.Button(
            header_bar,
            text="Clear",
            font=("Segoe UI", 8),
            fg=t["text_primary"],
            bg=t["bg_card_inner"],
            relief=tk.FLAT,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._clear_log,
        ).pack(side=tk.RIGHT, padx=4)

        log_frame = tk.Frame(page, bg=t["console_bg"])
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            background=t["console_bg"],
            foreground=t["console_fg"],
            insertbackground="#ffffff",
            padx=12,
            pady=12,
            relief=tk.FLAT,
        )
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_configure("log_info", foreground="#38bdf8")
        self.log_text.tag_configure("log_success", foreground="#34d399", font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("log_warn", foreground="#fbbf24", font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("log_error", foreground="#f87171", font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("log_highlight", background="#3b82f6", foreground="#ffffff")

        return page

    def _append_log_message(self, msg: str) -> None:
        tag = "log_info"
        upper = msg.upper()
        if "ERROR" in upper or "FAILED" in upper or "TRACEBACK" in upper:
            tag = "log_error"
        elif "WARN" in upper or "WARNING" in upper or "LOW CONF" in upper:
            tag = "log_warn"
        elif "SUCCESS" in upper or "COMPLETE" in upper or "FINISHED" in upper or "✓" in msg:
            tag = "log_success"

        self.log_text.insert(tk.END, msg + "\n", (tag,))
        self.log_text.see(tk.END)

    def _search_log_text(self) -> None:
        q = self.log_filter_var.get().strip()
        self.log_text.tag_remove("log_highlight", "1.0", tk.END)
        if not q:
            return

        start = "1.0"
        count = 0
        while True:
            pos = self.log_text.search(q, start, nocase=True, stopindex=tk.END)
            if not pos:
                break
            end = f"{pos}+{len(q)}c"
            self.log_text.tag_add("log_highlight", pos, end)
            if count == 0:
                self.log_text.see(pos)
            start = end
            count += 1

    # -------------------------------------------------------------------------
    # Page 5: Engine Tuning & Diagnostics
    # -------------------------------------------------------------------------
    def _create_settings_page(self, parent: tk.Frame) -> tk.Frame:
        t = self.t
        page = tk.Frame(parent, bg=t["bg_root"])

        container = tk.Frame(page, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=16, pady=16)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            container,
            text="Advanced OCR Engine Tuning & Vision LLM Settings",
            font=("Segoe UI", 12, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card"],
        ).pack(anchor=tk.W, pady=(0, 12))

        v_box = tk.LabelFrame(
            container,
            text=" Multimodal Vision LLM Fallback (Hybrid OCR) ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=12,
            pady=10,
            relief=tk.GROOVE,
        )
        v_box.pack(fill=tk.X, pady=(0, 12))

        tk.Label(
            v_box,
            text="Vision API Key (e.g. Gemini / OpenAI):",
            font=("Segoe UI", 8, "bold"),
            bg=t["bg_card"],
            fg=t["text_primary"],
        ).pack(anchor=tk.W, pady=(2, 2))
        ttk.Entry(v_box, textvariable=self.api_key_var, show="*", width=50).pack(anchor=tk.W, pady=(0, 6))
        tk.Label(
            v_box,
            text="Used when OCR backend is set to 'hybrid' or 'vision_llm'. Automatically resolves complex calligraphy and dense ligature clusters.",
            font=("Segoe UI", 8),
            fg=t["text_muted"],
            bg=t["bg_card"],
        ).pack(anchor=tk.W)

        diag_box = tk.LabelFrame(
            container,
            text=" System Components & Diagnostic Check ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=12,
            pady=10,
            relief=tk.GROOVE,
        )
        diag_box.pack(fill=tk.X, pady=(0, 12))

        tk.Button(
            diag_box,
            text="Run Full Pipeline Health Check",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg=t["accent"],
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor="hand2",
            command=self._show_system_health,
        ).pack(anchor=tk.W, pady=(2, 6))

        tk.Label(
            diag_box,
            text="Verifies Tesseract binaries, installed language packs, PDFium rendering speed, and RTL font availability.",
            font=("Segoe UI", 8),
            fg=t["text_muted"],
            bg=t["bg_card"],
        ).pack(anchor=tk.W)

        return page

    # -------------------------------------------------------------------------
    # Sidebar Sub-Cards
    # -------------------------------------------------------------------------
    def _build_sidebar_source_card(self, parent: tk.Frame) -> None:
        t = self.t
        card = tk.LabelFrame(
            parent,
            text=" 1. Source Scanned PDF & Output ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=10,
            pady=8,
            relief=tk.GROOVE,
        )
        card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(card, text="Input Scanned PDF (Ctrl+O):", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["text_secondary"]).pack(
            anchor=tk.W, pady=(2, 1)
        )
        pdf_row = tk.Frame(card, bg=t["bg_card"])
        pdf_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Entry(pdf_row, textvariable=self.input_pdf_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(pdf_row, text="Browse...", command=self._pick_pdf).pack(side=tk.RIGHT)

        self.pdf_info_label = tk.Label(
            card,
            textvariable=self.pdf_pages_info_var,
            font=("Segoe UI", 8),
            fg=t["accent_green"],
            bg=t["bg_card"],
        )
        self.pdf_info_label.pack(anchor=tk.W, pady=(0, 4))

        tk.Label(card, text="Output Directory:", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["text_secondary"]).pack(
            anchor=tk.W, pady=(4, 1)
        )
        out_row = tk.Frame(card, bg=t["bg_card"])
        out_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Entry(out_row, textvariable=self.output_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(out_row, text="Browse...", command=self._pick_output_dir).pack(side=tk.RIGHT)

    def _build_sidebar_lang_card(self, parent: tk.Frame) -> None:
        t = self.t
        card = tk.LabelFrame(
            parent,
            text=" 2. Language & Page Range ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=10,
            pady=8,
            relief=tk.GROOVE,
        )
        card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(card, text="Language Profile:", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["text_secondary"]).pack(
            anchor=tk.W, pady=(2, 1)
        )
        preset_names = list(LANGUAGE_PRESETS.keys())
        self.preset_combo = ttk.Combobox(
            card,
            textvariable=self.preset_var,
            values=preset_names,
            state="readonly",
        )
        self.preset_combo.pack(fill=tk.X, pady=(0, 6))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_change)

        code_frame = tk.Frame(card, bg=t["bg_card"])
        code_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Label(code_frame, text="Tesseract Codes:", font=("Segoe UI", 8), bg=t["bg_card"], fg=t["text_muted"]).pack(
            side=tk.LEFT
        )
        ttk.Entry(code_frame, textvariable=self.lang_var, width=18).pack(side=tk.RIGHT)

        tk.Label(card, text="Page Filter Scope:", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["text_secondary"]).pack(
            anchor=tk.W, pady=(4, 2)
        )
        range_row = tk.Frame(card, bg=t["bg_card"])
        range_row.pack(fill=tk.X, pady=(0, 2))

        tk.Label(range_row, text="From:", font=("Segoe UI", 8), bg=t["bg_card"], fg=t["text_primary"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Spinbox(range_row, from_=1, to=99999, textvariable=self.start_page_var, width=5).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        tk.Label(range_row, text="To:", font=("Segoe UI", 8), bg=t["bg_card"], fg=t["text_primary"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Entry(range_row, textvariable=self.end_page_var, width=6).pack(side=tk.LEFT, padx=(0, 6))

        tk.Label(
            range_row,
            text="(Blank=All)",
            font=("Segoe UI", 8),
            fg=t["text_muted"],
            bg=t["bg_card"],
        ).pack(side=tk.LEFT)

    def _build_sidebar_formats_card(self, parent: tk.Frame) -> None:
        t = self.t
        card = tk.LabelFrame(
            parent,
            text=" 3. Target Output Formats ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=10,
            pady=8,
            relief=tk.GROOVE,
        )
        card.pack(fill=tk.X, pady=(0, 10))

        fmt_grid = tk.Frame(card, bg=t["bg_card"])
        fmt_grid.pack(fill=tk.X, pady=2)

        ttk.Checkbutton(fmt_grid, text="Word DOCX", variable=self.docx_var).grid(
            row=0, column=0, sticky=tk.W, padx=2, pady=3
        )
        ttk.Checkbutton(fmt_grid, text="Sandwich PDF", variable=self.sandwich_pdf_var).grid(
            row=0, column=1, sticky=tk.W, padx=2, pady=3
        )
        ttk.Checkbutton(fmt_grid, text="Reflow PDF", variable=self.pdf_var).grid(
            row=1, column=0, sticky=tk.W, padx=2, pady=3
        )
        ttk.Checkbutton(fmt_grid, text="EPUB3 E-Book", variable=self.epub_var).grid(
            row=1, column=1, sticky=tk.W, padx=2, pady=3
        )
        ttk.Checkbutton(fmt_grid, text="HTML5 Web", variable=self.html_var).grid(
            row=2, column=0, sticky=tk.W, padx=2, pady=3
        )
        ttk.Checkbutton(fmt_grid, text="UTF-8 Plain TXT", variable=self.txt_var).grid(
            row=2, column=1, sticky=tk.W, padx=2, pady=3
        )
        ttk.Checkbutton(fmt_grid, text="ALTO XML", variable=self.alto_var).grid(
            row=3, column=0, sticky=tk.W, padx=2, pady=3
        )
        ttk.Checkbutton(fmt_grid, text="PageXML", variable=self.pagexml_var).grid(
            row=3, column=1, sticky=tk.W, padx=2, pady=3
        )

        fmt_grid.columnconfigure(0, weight=1)
        fmt_grid.columnconfigure(1, weight=1)

        tk.Button(
            card,
            text="⚙️  Select Output Formats...",
            font=("Segoe UI", 8, "bold"),
            bg=t["bg_card_inner"],
            fg=t["accent"],
            activebackground=t["bg_card"],
            activeforeground=t["text_primary"],
            relief=tk.FLAT,
            padx=6,
            pady=4,
            cursor="hand2",
            command=lambda: self._prompt_output_formats_modal(
                title="Select Output Formats", action_btn_text="Save Preferences"
            ),
        ).pack(fill=tk.X, pady=(6, 2))

    def _build_sidebar_preprocessing_card(self, parent: tk.Frame) -> None:
        t = self.t
        card = tk.LabelFrame(
            parent,
            text=" 4. Physical Scan Correction & Engine ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent"],
            padx=10,
            pady=8,
            relief=tk.GROOVE,
        )
        card.pack(fill=tk.X, pady=(0, 10))

        pre_grid = tk.Frame(card, bg=t["bg_card"])
        pre_grid.pack(fill=tk.X, pady=2)

        ttk.Checkbutton(pre_grid, text="Auto-Deskew", variable=self.deskew_var).grid(
            row=0, column=0, sticky=tk.W, padx=2, pady=2
        )
        ttk.Checkbutton(pre_grid, text="3D Polynomial Dewarp", variable=self.dewarp_var).grid(
            row=0, column=1, sticky=tk.W, padx=2, pady=2
        )
        ttk.Checkbutton(pre_grid, text="Spine Shadow", variable=self.spine_var).grid(
            row=1, column=0, sticky=tk.W, padx=2, pady=2
        )
        ttk.Checkbutton(pre_grid, text="Bleed-Thru Removal", variable=self.bleed_var).grid(
            row=1, column=1, sticky=tk.W, padx=2, pady=2
        )
        ttk.Checkbutton(pre_grid, text="Poetry Couplets", variable=self.poetry_var).grid(
            row=2, column=0, sticky=tk.W, padx=2, pady=2
        )
        ttk.Checkbutton(pre_grid, text="Clitic Morphology", variable=self.morphology_var).grid(
            row=2, column=1, sticky=tk.W, padx=2, pady=2
        )
        ttk.Checkbutton(pre_grid, text="Classical Ortho", variable=self.historical_ortho_var).grid(
            row=3, column=0, sticky=tk.W, padx=2, pady=2
        )
        ttk.Checkbutton(pre_grid, text="⚡ Born-Digital Fast", variable=self.born_digital_var).grid(
            row=3, column=1, sticky=tk.W, padx=2, pady=2
        )

        pre_grid.columnconfigure(0, weight=1)
        pre_grid.columnconfigure(1, weight=1)

        spin_row = tk.Frame(card, bg=t["bg_card"])
        spin_row.pack(fill=tk.X, pady=(6, 4))
        tk.Label(spin_row, text="DPI:", font=("Segoe UI", 8), bg=t["bg_card"], fg=t["text_primary"]).pack(side=tk.LEFT)
        ttk.Spinbox(spin_row, from_=200, to=600, increment=50, textvariable=self.dpi_var, width=4).pack(
            side=tk.LEFT, padx=(2, 10)
        )
        tk.Label(spin_row, text="Min Conf %:", font=("Segoe UI", 8), bg=t["bg_card"], fg=t["text_primary"]).pack(side=tk.LEFT)
        ttk.Spinbox(spin_row, from_=40, to=95, increment=1, textvariable=self.conf_var, width=3).pack(
            side=tk.LEFT, padx=(2, 0)
        )

        be_row = tk.Frame(card, bg=t["bg_card"])
        be_row.pack(fill=tk.X, pady=(4, 2))
        tk.Label(be_row, text="Backend:", font=("Segoe UI", 8), bg=t["bg_card"], fg=t["text_primary"]).pack(side=tk.LEFT)
        self.backend_combo = ttk.Combobox(
            be_row,
            textvariable=self.backend_var,
            values=["tesseract", "hybrid", "vision_llm"],
            state="readonly",
            width=14,
        )
        self.backend_combo.pack(side=tk.RIGHT)

    def _build_sidebar_action_center(self, parent: tk.Frame) -> None:
        t = self.t
        card = tk.LabelFrame(
            parent,
            text=" 5. Action Center ",
            font=("Segoe UI", 9, "bold"),
            bg=t["bg_card"],
            fg=t["accent_green"],
            padx=10,
            pady=10,
            relief=tk.GROOVE,
        )
        card.pack(fill=tk.X, pady=(0, 6))

        self.start_btn = tk.Button(
            card,
            text="▶  START DIGITIZATION",
            font=("Segoe UI", 11, "bold"),
            bg=t["accent"],
            fg="#ffffff",
            activebackground=t["accent_hover"],
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=12,
            pady=10,
            cursor="hand2",
            command=self._start_run,
        )
        self.start_btn.pack(fill=tk.X, pady=(0, 8))

        ctrl_row = tk.Frame(card, bg=t["bg_card"])
        ctrl_row.pack(fill=tk.X, pady=(0, 8))

        self.pause_btn = ttk.Button(
            ctrl_row, text="⏸ Pause", command=self._pause_run, state=tk.DISABLED
        )
        self.pause_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.resume_btn = ttk.Button(
            ctrl_row, text="⏯ Resume", command=self._resume_run, state=tk.DISABLED
        )
        self.resume_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        self.stop_btn = ttk.Button(
            ctrl_row, text="⏹ Stop", command=self._stop_run, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)

        test_row = tk.Frame(card, bg=t["bg_card"])
        test_row.pack(fill=tk.X, pady=2)
        tk.Label(test_row, text="Test Page #:", font=("Segoe UI", 8), bg=t["bg_card"], fg=t["text_primary"]).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Spinbox(test_row, from_=1, to=99999, textvariable=self.preview_page_var, width=5).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self.preview_btn = ttk.Button(
            test_row, text="🔍 Test Page", command=self._test_preview_page
        )
        self.preview_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True)

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
            try:
                pages = get_pdf_page_count(selected)
                if pages > 0:
                    self._detected_total_pages = pages
                    self.pdf_pages_info_var.set(f"✓ Detected: {pages} pages in PDF")
                    if not self.end_page_var.get().strip():
                        self.end_page_var.set(str(pages))
                    if hasattr(self, "_refresh_filmstrip"):
                        self._refresh_filmstrip()
                else:
                    self.pdf_pages_info_var.set("✓ PDF loaded")
            except Exception:
                self.pdf_pages_info_var.set("✓ PDF loaded")
            self._save_session_state()
            self.show_toast(f"Loaded: {os.path.basename(selected)}", "success")

    def _pick_output_dir(self) -> None:
        selected = filedialog.askdirectory(title="Select Output Directory")
        if selected:
            self.output_dir_var.set(selected)
            self._save_session_state()
            self.show_toast("Output folder updated", "info")

    def _sync_config_from_ui(self) -> None:
        self.config.input_pdf_path = self.input_pdf_var.get().strip()
        self.config.output_dir = self.output_dir_var.get().strip()
        self.config.languages = self.lang_var.get().strip()
        self.config.dpi = int(self.dpi_var.get())
        self.config.confidence_threshold = float(self.conf_var.get())

        try:
            s_val = int(self.start_page_var.get().strip())
            self.config.start_page = max(1, s_val)
        except (ValueError, TypeError):
            self.config.start_page = 1
            self.start_page_var.set("1")

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

    def _prompt_output_formats_modal(
        self,
        title: str = "Select Output Document Formats",
        action_btn_text: str = "▶  Start Digitization",
    ) -> bool:
        """
        Interactive modal dialog asking the user which output document formats to generate.
        Returns True if the user confirms, False if cancelled.
        """
        t = self.t
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg=t["bg_root"])
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        confirmed = False

        # Local BooleanVars initialized with current app states
        var_docx = tk.BooleanVar(value=bool(self.docx_var.get()))
        var_sandwich = tk.BooleanVar(value=bool(self.sandwich_pdf_var.get()))
        var_pdf = tk.BooleanVar(value=bool(self.pdf_var.get()))
        var_epub = tk.BooleanVar(value=bool(self.epub_var.get()))
        var_html = tk.BooleanVar(value=bool(self.html_var.get()))
        var_txt = tk.BooleanVar(value=bool(self.txt_var.get()))
        var_alto = tk.BooleanVar(value=bool(self.alto_var.get()))
        var_pagexml = tk.BooleanVar(value=bool(self.pagexml_var.get()))

        all_vars = [
            var_docx, var_sandwich, var_pdf, var_epub,
            var_html, var_txt, var_alto, var_pagexml,
        ]

        # Outer Container
        container = tk.Frame(dlg, bg=t["bg_root"], padx=20, pady=18)
        container.pack(fill=tk.BOTH, expand=True)

        # 1. Header Banner
        hdr_frame = tk.Frame(container, bg=t["bg_root"])
        hdr_frame.pack(fill=tk.X, pady=(0, 14))

        tk.Label(
            hdr_frame,
            text="📦  Which Output Files Should Be Generated?",
            font=("Segoe UI", 13, "bold"),
            fg=t["text_primary"],
            bg=t["bg_root"],
        ).pack(anchor=tk.W)

        tk.Label(
            hdr_frame,
            text="Select the digital document formats, reading editions, and archival preservation files you need:",
            font=("Segoe UI", 9),
            fg=t["text_secondary"],
            bg=t["bg_root"],
        ).pack(anchor=tk.W, pady=(3, 0))

        # 2. Quick Preset Pills Bar
        preset_bar = tk.Frame(container, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=10, pady=8)
        preset_bar.pack(fill=tk.X, pady=(0, 14))

        tk.Label(
            preset_bar,
            text="Quick Presets:",
            font=("Segoe UI", 8, "bold"),
            fg=t["accent"],
            bg=t["bg_card"],
        ).pack(side=tk.LEFT, padx=(0, 8))

        def _update_count_badge():
            selected_n = sum(1 for v in all_vars if v.get())
            count_label.config(text=f"{selected_n} of 8 formats selected")
            if selected_n == 0:
                count_label.config(fg=t["accent_red"])
            else:
                count_label.config(fg=t["accent_green"])

        def _apply_preset(preset_type: str):
            if preset_type == "all":
                for v in all_vars:
                    v.set(True)
            elif preset_type == "standard":
                var_docx.set(True)
                var_sandwich.set(True)
                var_pdf.set(False)
                var_epub.set(False)
                var_html.set(False)
                var_txt.set(True)
                var_alto.set(False)
                var_pagexml.set(False)
            elif preset_type == "ebook_web":
                var_docx.set(True)
                var_sandwich.set(False)
                var_pdf.set(True)
                var_epub.set(True)
                var_html.set(True)
                var_txt.set(True)
                var_alto.set(False)
                var_pagexml.set(False)
            elif preset_type == "archival":
                var_docx.set(False)
                var_sandwich.set(True)
                var_pdf.set(False)
                var_epub.set(False)
                var_html.set(False)
                var_txt.set(True)
                var_alto.set(True)
                var_pagexml.set(True)
            elif preset_type == "none":
                for v in all_vars:
                    v.set(False)
            _update_count_badge()

        def _make_pill(text, cmd):
            btn = tk.Button(
                preset_bar,
                text=text,
                font=("Segoe UI", 8),
                bg=t["bg_card_inner"],
                fg=t["text_primary"],
                relief=tk.FLAT,
                padx=8,
                pady=2,
                cursor="hand2",
                command=cmd,
            )
            btn.pack(side=tk.LEFT, padx=3)
            return btn

        _make_pill("✨ All Formats", lambda: _apply_preset("all"))
        _make_pill("📄 Standard (Word + PDF)", lambda: _apply_preset("standard"))
        _make_pill("📱 E-Book & Web", lambda: _apply_preset("ebook_web"))
        _make_pill("🏛️ Archival Preservation", lambda: _apply_preset("archival"))
        _make_pill("🧹 Clear All", lambda: _apply_preset("none"))

        # 3. Format Selection Grid (2 Columns, 8 Formats)
        grid_frame = tk.Frame(container, bg=t["bg_root"])
        grid_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 14))

        formats_info = [
            (
                "📘 Microsoft Word (.docx)",
                "Fully formatted typography, headings, tables, poetry & fonts",
                var_docx,
                0, 0,
            ),
            (
                "📑 Searchable Facsimile PDF (.pdf)",
                "Original scan facsimile with invisible selectable Unicode text layer",
                var_sandwich,
                0, 1,
            ),
            (
                "📄 Reflowable Digital PDF (.pdf)",
                "Clean typeset digital edition with reshaped Arabic/Urdu Naskh",
                var_pdf,
                1, 0,
            ),
            (
                "📱 EPUB3 E-Book (.epub)",
                "Reflowable eBook with RTL CSS for Apple Books, Kindle & tablets",
                var_epub,
                1, 1,
            ),
            (
                "🌐 HTML5 Web Replica (.html)",
                "Self-contained responsive web book with HarfBuzz cursive script",
                var_html,
                2, 0,
            ),
            (
                "📝 Clean Plain Text (.txt)",
                "Universal UTF-8 plain text preserving structure, notes & verses",
                var_txt,
                2, 1,
            ),
            (
                "🏛️ ALTO XML 4.2 (.xml)",
                "Library of Congress archival standard with word bboxes & confidence",
                var_alto,
                3, 0,
            ),
            (
                "🏛️ PRImA PageXML (.xml)",
                "European digital humanities research & archival preservation standard",
                var_pagexml,
                3, 1,
            ),
        ]

        for title_str, desc_str, var_obj, row_idx, col_idx in formats_info:
            card = tk.Frame(grid_frame, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=12, pady=10)
            card.grid(row=row_idx, column=col_idx, sticky="nsew", padx=6, pady=5)

            cb = tk.Checkbutton(
                card,
                text=title_str,
                variable=var_obj,
                font=("Segoe UI", 9, "bold"),
                fg=t["text_primary"],
                bg=t["bg_card"],
                activebackground=t["bg_card"],
                activeforeground=t["text_primary"],
                selectcolor=t["bg_card_inner"],
                command=_update_count_badge,
                cursor="hand2",
            )
            cb.pack(anchor=tk.W)

            tk.Label(
                card,
                text=desc_str,
                font=("Segoe UI", 8),
                fg=t["text_muted"],
                bg=t["bg_card"],
                wraplength=270,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, padx=(22, 0), pady=(2, 0))

        grid_frame.columnconfigure(0, weight=1)
        grid_frame.columnconfigure(1, weight=1)

        # 4. Destination Directory info
        dest_bar = tk.Frame(container, bg=t["stat_bg"], bd=1, relief=tk.SOLID, padx=12, pady=8)
        dest_bar.pack(fill=tk.X, pady=(0, 16))

        tk.Label(
            dest_bar,
            text=f"📁 Target Destination:  {self.config.output_dir}",
            font=("Segoe UI", 8),
            fg=t["text_secondary"],
            bg=t["stat_bg"],
            anchor=tk.W,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        count_label = tk.Label(
            dest_bar,
            text="",
            font=("Segoe UI", 8, "bold"),
            bg=t["stat_bg"],
        )
        count_label.pack(side=tk.RIGHT)
        _update_count_badge()

        # 5. Bottom Buttons (Cancel vs Confirm)
        btn_row = tk.Frame(container, bg=t["bg_root"])
        btn_row.pack(fill=tk.X)

        def _on_confirm():
            nonlocal confirmed
            if sum(1 for v in all_vars if v.get()) == 0:
                messagebox.showwarning(
                    "No Formats Selected",
                    "Please select at least one output document format to generate.",
                    parent=dlg,
                )
                return

            # Apply to app vars
            self.docx_var.set(var_docx.get())
            self.sandwich_pdf_var.set(var_sandwich.get())
            self.pdf_var.set(var_pdf.get())
            self.epub_var.set(var_epub.get())
            self.html_var.set(var_html.get())
            self.txt_var.set(var_txt.get())
            self.alto_var.set(var_alto.get())
            self.pagexml_var.set(var_pagexml.get())

            self._sync_config_from_ui()
            self._save_session_state()

            confirmed = True
            dlg.destroy()

        def _on_cancel():
            dlg.destroy()

        cancel_btn = tk.Button(
            btn_row,
            text="✕ Cancel",
            font=("Segoe UI", 9),
            fg=t["text_primary"],
            bg=t["bg_card"],
            relief=tk.FLAT,
            padx=16,
            pady=8,
            cursor="hand2",
            command=_on_cancel,
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

        start_btn = tk.Button(
            btn_row,
            text=action_btn_text,
            font=("Segoe UI", 9, "bold"),
            fg="#ffffff",
            bg=t["accent"],
            activebackground=t["accent_hover"],
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            command=_on_confirm,
        )
        start_btn.pack(side=tk.RIGHT)

        # Center on parent window
        dlg.update_idletasks()
        w = max(640, dlg.winfo_reqwidth())
        h = max(500, dlg.winfo_reqheight())
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_w = self.root.winfo_width()
        parent_h = self.root.winfo_height()
        pos_x = max(50, parent_x + (parent_w - w) // 2)
        pos_y = max(50, parent_y + (parent_h - h) // 2)
        dlg.geometry(f"{w}x{h}+{pos_x}+{pos_y}")

        # Wait until closed
        self.root.wait_window(dlg)
        return confirmed

    # -------------------------------------------------------------------------
    # Batch Run & Cancellation Handlers
    # -------------------------------------------------------------------------
    def _start_run(self) -> None:
        self._sync_config_from_ui()
        self._save_session_state()
        if not os.path.exists(self.config.input_pdf_path):
            messagebox.showerror(
                "Missing Input PDF", f"Scanned PDF file not found:\n{self.config.input_pdf_path}"
            )
            return

        if not self._prompt_output_formats_modal(
            title="Select Output Formats for Digitization",
            action_btn_text="▶  Start Digitization",
        ):
            return

        self.tree.delete(*self.tree.get_children())
        self.page_results_cache.clear()
        self.start_btn.configure(state=tk.DISABLED, bg=self.t["border"])
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
            self.show_toast("Run paused", "warn")

    def _resume_run(self) -> None:
        self._sync_config_from_ui()
        self.start_btn.configure(state=tk.DISABLED, bg=self.t["border"])
        self.pause_btn.configure(state=tk.NORMAL)
        self.resume_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.preview_btn.configure(state=tk.DISABLED)
        self._launch_worker()
        self.show_toast("Resuming digitization run...", "info")

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
        self.show_toast(f"Processing Page #{pnum}...", "info")

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
                self._append_log_message(event["message"])

            elif etype == "page":
                res = event["result"]
                done = event["done"]
                total = max(1, event["total"])
                stats = event.get("stats")

                pnum = res["page_number"]
                self.page_results_cache[pnum] = res

                pct = (done / float(total)) * 100.0
                self.progress_var.set(pct)
                self.progress_pct_var.set(f"{pct:.1f}%")

                if stats:
                    self.speed_var.set(f"{stats['pages_per_min']:.1f} p/min")
                    self.elapsed_var.set(f"{stats['elapsed_str']}")
                    self.eta_var.set(f"{stats['eta_str']}")
                    self.progress_count_var.set(f"{done} / {total}")

                self.mean_conf_var.set(f"{res['mean_confidence']:.1f}%")
                self.status_var.set(
                    f"Processed {done}/{total} pages ({pct:.1f}%) — Page #{pnum} Conf: {res['mean_confidence']:.1f}%"
                )

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

                if hasattr(self, "_update_filmstrip_page"):
                    self._update_filmstrip_page(pnum, res)

            elif etype == "preview_ready":
                res = event["result"]
                pnum = res["page_number"]
                self.page_results_cache[pnum] = res
                self.preview_btn.configure(state=tk.NORMAL)
                self.status_var.set(
                    f"Preview ready for Page #{pnum} (Mean Confidence: {res['mean_confidence']:.1f}%)"
                )
                self.show_toast(f"Page #{pnum} preview ready", "success")

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

                if hasattr(self, "_update_filmstrip_page"):
                    self._update_filmstrip_page(pnum, res)

                self._switch_page("inspector")
                self._load_page_into_inspector(pnum)

            elif etype == "preview_error":
                self.preview_btn.configure(state=tk.NORMAL)
                self.status_var.set(f"Preview error: {event['message']}")
                self.show_toast(f"Preview failed: {event['message']}", "error")

            elif etype == "reassemble_complete":
                rep = event["report"]
                self.show_toast("Book outputs successfully re-assembled!", "success")
                if hasattr(self, "_refresh_quality_page"):
                    self._refresh_quality_page()
                messagebox.showinfo(
                    "Re-assembly Complete",
                    f"Digital book outputs successfully compiled with all manual edits!\n\n"
                    f"Generated Files: {len(rep.get('generated_files', {}))}\n"
                    f"Output Folder: {self.config.output_dir}",
                )

            elif etype == "finished":
                summary = event["summary"]
                self.preview_btn.configure(state=tk.NORMAL)

                if summary.get("stopped"):
                    self.status_var.set(
                        f"Conversion stopped at {summary['completed_pages']}/{summary['total_pages']} pages."
                    )
                    self.start_btn.configure(state=tk.NORMAL, bg=self.t["accent"])
                    self.pause_btn.configure(state=tk.DISABLED)
                    self.resume_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self.show_toast("Digitization run cancelled", "warn")

                elif summary.get("paused"):
                    self.status_var.set(
                        f"Paused at {summary['completed_pages']}/{summary['total_pages']} pages."
                    )
                    self.start_btn.configure(state=tk.NORMAL, bg=self.t["accent"])
                    self.pause_btn.configure(state=tk.DISABLED)
                    self.resume_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self.show_toast("Run paused safely", "info")

                else:
                    self.progress_var.set(100.0)
                    self.progress_pct_var.set("100.0%")
                    avg_c = summary.get("overall_average_confidence", 0.0)
                    self.mean_conf_var.set(f"{avg_c:.1f}%")
                    self.status_var.set(
                        f"Finished! Processed {summary['successful_pages']}/{summary['total_pages']} pages | "
                        f"Mean Conf: {avg_c:.1f}% | Low-Conf Pages: {summary['pages_with_low_confidence_flags']}"
                    )
                    self.start_btn.configure(state=tk.NORMAL, bg=self.t["accent"])
                    self.pause_btn.configure(state=tk.DISABLED)
                    self.resume_btn.configure(state=tk.DISABLED)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self.show_toast("Conversion 100% complete!", "success")

            elif etype == "error":
                self.status_var.set(f"Pipeline Error: {event['message']}")
                self.start_btn.configure(state=tk.NORMAL, bg=self.t["accent"])
                self.pause_btn.configure(state=tk.DISABLED)
                self.resume_btn.configure(state=tk.DISABLED)
                self.stop_btn.configure(state=tk.DISABLED)
                self.preview_btn.configure(state=tk.NORMAL)
                self.show_toast(f"Pipeline error: {event['message']}", "error")

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
                self._switch_page("inspector")
                self._load_page_into_inspector(pnum)
            except Exception:
                pass

    def _open_selected_in_inspector(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select Page", "Please select a page row in the table first.")
            return
        pnum = int(self.tree.item(sel[0], "values")[0])
        self._switch_page("inspector")
        self._load_page_into_inspector(pnum)

    def _load_page_into_inspector(self, pnum: int) -> None:
        self._current_inspector_page = pnum
        self.inspector_page_var.set(str(pnum))
        self.proofreader_status_var.set("")
        self._active_bbox = None
        if hasattr(self, "_highlight_filmstrip_card"):
            self._highlight_filmstrip_card(pnum)

        data = self.page_results_cache.get(pnum)
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
                f"Page #{pnum}: Not processed yet. Click 'Reprocess' in toolbar to test."
            )
            self._display_empty_canvas(f"Page #{pnum} has not been processed yet.")
            self.inspector_text.delete("1.0", tk.END)
            self.inspector_text.insert(
                tk.END,
                f"[Page #{pnum} is not in checkpoints]\n\n"
                "Click 'Reprocess' in the toolbar to run preprocessing and OCR on this page immediately.",
            )
            return

        direction = data.get("page_direction", "RTL")
        conf = data.get("mean_confidence", 0.0)
        words = data.get("word_count", 0)
        low_cnt = data.get("low_confidence_word_count", 0)
        cols = data.get("column_count", 1)
        layout = data.get("layout") or {}
        couplets = sum(1 for el in layout.get("elements", []) if el.get("type") == "poetry")
        edited_badge = " [Edited ✓]" if data.get("manually_edited") else ""

        self.inspector_meta_var.set(
            f"Page #{pnum:04d}  |  Script: {direction}  |  Mean Conf: {conf:.1f}%  |  "
            f"Words: {words}  |  Flagged: {low_cnt}  |  Columns: {cols}  |  Poetry: {couplets}{edited_badge}"
        )

        self._render_current_inspector_image()

        text = data.get("extracted_text") or extract_page_plain_text(data.get("layout"))
        self.inspector_text.delete("1.0", tk.END)

        is_rtl = direction == "RTL" or self.config.primary_language in ("urd", "ara", "fas")
        tag_name = "rtl_text" if is_rtl else "ltr_text"

        self.inspector_text.insert(tk.END, text, (tag_name,))

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

    def _render_current_inspector_image(self, redraw_only: bool = False) -> None:
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

        self._original_image_path = img_path
        if not img_path or not os.path.exists(img_path):
            self._display_empty_canvas(
                f"No image preview saved for Page #{pnum} ({mode.capitalize()} mode).\n"
                "Click 'Reprocess' in the toolbar to generate it."
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
            fill=self.t["text_muted"],
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

            if self._auto_fit_mode:
                scale = min(c_w / float(orig_w), c_h / float(orig_h))
            else:
                scale = (c_w / float(orig_w)) * self._zoom_factor

            self._image_scale_ratio = scale
            new_w = max(50, int(orig_w * scale))
            new_h = max(50, int(orig_h * scale))

            resized_pil = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(resized_pil)
            self._current_photo_image = photo

            self.image_canvas.delete("all")

            draw_x = (self.image_canvas.winfo_width() // 2) + self._pan_offset_x
            draw_y = (self.image_canvas.winfo_height() // 2) + self._pan_offset_y

            self.image_canvas.create_image(
                draw_x,
                draw_y,
                anchor=tk.CENTER,
                image=photo,
            )

            # Re-draw bounding box overlay if active
            if self._active_bbox:
                self._draw_bounding_box_overlay()

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
            self.show_toast("Page text copied to clipboard!", "success")
        else:
            self.show_toast("No text to copy", "warn")

    def _save_inspector_text(self) -> None:
        txt = self.inspector_text.get("1.0", tk.END).strip()
        if not txt:
            self.show_toast("There is no extracted text to save", "warn")
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
            self.show_toast(f"Saved: {os.path.basename(fpath)}", "success")

    # -------------------------------------------------------------------------
    # Quick Launch & Logging Utilities
    # -------------------------------------------------------------------------
    def _clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def _save_log(self) -> None:
        txt = self.log_text.get("1.0", tk.END).strip()
        if not txt:
            self.show_toast("No log content to save", "warn")
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
            self.show_toast(f"Log saved to: {os.path.basename(fpath)}", "success")

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
        if file_type == "pdf" and not os.path.exists(fpath):
            alt_path = os.path.join(out_dir, "digitized_book_searchable.pdf")
            if os.path.exists(alt_path):
                fpath = alt_path

        if not os.path.exists(fpath):
            self.show_toast(f"'{filename}' has not been generated yet", "warn")
            return

        try:
            if platform.system() == "Windows":
                os.startfile(fpath)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", fpath])
            else:
                subprocess.Popen(["xdg-open", fpath])
            self.show_toast(f"Opened: {filename}", "success")
        except Exception as exc:
            self.show_toast(f"Error opening file: {exc}", "error")

    def _show_system_health(self) -> None:
        self._sync_config_from_ui()
        health = check_system_health(self.config)
        t = self.t

        win = tk.Toplevel(self.root)
        win.title("System Diagnostics & Health Check")
        win.geometry("680x580")
        win.minsize(580, 460)
        win.configure(background=t["bg_root"])
        self._apply_windows_dark_titlebar(win, self.current_theme_name == "dark")
        win.transient(self.root)
        win.grab_set()

        frame = tk.Frame(win, bg=t["bg_card"], bd=1, relief=tk.SOLID, padx=16, pady=16)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        tk.Label(
            frame,
            text="Pipeline Diagnostics & Component Verification",
            font=("Segoe UI", 12, "bold"),
            fg=t["text_primary"],
            bg=t["bg_card"],
        ).pack(anchor=tk.W, pady=(0, 10))

        # Tesseract Status
        tess_status = "✅ OK" if health["tesseract_installed"] else "❌ MISSING"
        tess_box = tk.LabelFrame(frame, text=f" Tesseract OCR Engine — {tess_status} ", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["accent"], padx=8, pady=6)
        tess_box.pack(fill=tk.X, pady=(0, 6))
        tk.Label(tess_box, text=f"Version: {health['tesseract_version']}", bg=t["bg_card"], fg=t["text_primary"]).pack(anchor=tk.W)
        tk.Label(tess_box, text=f"Path: {health['tesseract_path']}", bg=t["bg_card"], fg=t["text_muted"]).pack(anchor=tk.W)
        langs_str = ", ".join(health["installed_languages"]) if health["installed_languages"] else "None detected"
        tk.Label(tess_box, text=f"Installed Languages: {langs_str}", bg=t["bg_card"], fg=t["accent_green"]).pack(anchor=tk.W)

        # PDF Engine
        pdf_status = "✅ OK" if (health.get("pypdfium2_installed") or health["poppler_installed"]) else "⚠️ MISSING"
        pdf_box = tk.LabelFrame(frame, text=f" PDF Engine & Rasterizer — {pdf_status} ", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["accent"], padx=8, pady=6)
        pdf_box.pack(fill=tk.X, pady=(0, 6))
        if health.get("pypdfium2_installed"):
            tk.Label(pdf_box, text="• Google PDFium: Active (Zero-dependency vector rendering & born-digital extraction)", bg=t["bg_card"], fg=t["text_primary"]).pack(anchor=tk.W)
        if health.get("pypdf_installed"):
            tk.Label(pdf_box, text="• pypdf Stream Writer: Active (Memory-safe assembly)", bg=t["bg_card"], fg=t["text_primary"]).pack(anchor=tk.W)

        # Fonts
        b_fonts = health.get("bundled_fonts_detected", [])
        font_status = f"✅ OK ({len(b_fonts)} Fonts)" if b_fonts else "ℹ️ System Fonts Active"
        font_box = tk.LabelFrame(frame, text=f" Typography & RTL Ligature Fonts — {font_status} ", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["accent"], padx=8, pady=6)
        font_box.pack(fill=tk.X, pady=(0, 6))
        if b_fonts:
            tk.Label(font_box, text=f"Bundled Fonts: {', '.join(b_fonts)}", bg=t["bg_card"], fg=t["text_primary"]).pack(anchor=tk.W)
            tk.Label(font_box, text="Authentic Nastaliq & Naskh ligatures active with 0 missing glyphs.", font=("Segoe UI", 8), fg=t["accent_green"], bg=t["bg_card"]).pack(anchor=tk.W)
        else:
            tk.Label(font_box, text="Using OS system fonts. Place TTF fonts in book_digitizer/fonts/ for enhanced typography.", bg=t["bg_card"], fg=t["text_muted"]).pack(anchor=tk.W)

        # Vision LLM
        llm_status = "✅ Configured" if health["vision_llm_configured"] else "ℹ️ Optional (Not set)"
        llm_box = tk.LabelFrame(frame, text=f" Multimodal Vision LLM Fallback — {llm_status} ", font=("Segoe UI", 8, "bold"), bg=t["bg_card"], fg=t["accent"], padx=8, pady=6)
        llm_box.pack(fill=tk.X, pady=(0, 6))
        tk.Label(llm_box, text=f"Model: {health['vision_llm_model']}", bg=t["bg_card"], fg=t["text_primary"]).pack(anchor=tk.W)

        # Close
        tk.Button(
            frame,
            text="Close",
            font=("Segoe UI", 9, "bold"),
            fg="#ffffff",
            bg=t["accent"],
            relief=tk.FLAT,
            padx=14,
            pady=4,
            cursor="hand2",
            command=win.destroy,
        ).pack(anchor=tk.E, pady=(10, 0))

    def _open_output_folder(self) -> None:
        out_dir = self.output_dir_var.get().strip() or self.config.output_dir
        os.makedirs(out_dir, exist_ok=True)
        try:
            if platform.system() == "Windows":
                os.startfile(out_dir)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", out_dir])
            else:
                subprocess.Popen(["xdg-open", out_dir])
            self.show_toast(f"Opened: {out_dir}", "info")
        except Exception as exc:
            self.show_toast(f"Could not open directory: {exc}", "error")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = BookDigitizerGUI(root)
    root.mainloop()
