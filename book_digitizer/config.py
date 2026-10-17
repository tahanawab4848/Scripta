import os
import platform
import shutil
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional


def _default_tesseract_path() -> str:
    """Auto-detect Tesseract binary path across Windows, macOS, Linux, and local portable folders."""
    env_path = os.environ.get("TESSERACT_CMD")
    if env_path and os.path.exists(env_path):
        return env_path

    which_path = shutil.which("tesseract")
    if which_path:
        return which_path

    # Check local portable folders in workspace
    module_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(module_dir)
    local_candidates = [
        os.path.join(module_dir, "tesseract", "tesseract.exe"),
        os.path.join(workspace_dir, "tesseract", "tesseract.exe"),
        os.path.join(workspace_dir, "bin", "tesseract", "tesseract.exe"),
        os.path.join(workspace_dir, "tools", "tesseract", "tesseract.exe"),
    ]
    for p in local_candidates:
        if os.path.exists(p):
            return p

    system = platform.system()
    if system == "Windows":
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        ]
    elif system == "Darwin":
        candidates = [
            "/opt/homebrew/bin/tesseract",
            "/usr/local/bin/tesseract",
        ]
    else:
        candidates = [
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
        ]

    for path in candidates:
        if os.path.exists(path):
            return path
    return "tesseract"


def _default_poppler_path() -> Optional[str]:
    """Auto-detect Poppler bin folder if not already on system PATH."""
    env_path = os.environ.get("POPPLER_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    if shutil.which("pdftoppm") or shutil.which("pdfinfo"):
        return None

    module_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(module_dir)
    local_candidates = [
        os.path.join(module_dir, "poppler", "Library", "bin"),
        os.path.join(module_dir, "poppler", "bin"),
        os.path.join(workspace_dir, "poppler", "Library", "bin"),
        os.path.join(workspace_dir, "poppler", "bin"),
        os.path.join(workspace_dir, "bin", "poppler", "bin"),
    ]
    for p in local_candidates:
        if os.path.exists(p):
            return p

    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\poppler\Library\bin",
            r"C:\Program Files\poppler-24.07.0\Library\bin",
            r"C:\poppler\Library\bin",
            r"C:\tools\poppler\Library\bin",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
    return None


def _default_unicode_font() -> str:
    """
    Locate high-quality TrueType font for Nastaliq, Arabic, Persian, and Latin.
    Checks bundled project fonts first, then local fonts dir, then OS system fonts.
    """
    module_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(module_dir)

    # 1. Bundled project fonts (guaranteed authentic Nastaliq & Naskh ligatures)
    bundled_names = [
        "NotoNastaliqUrdu-Regular.ttf",
        "Amiri-Regular.ttf",
        "NotoNaskhArabic-Regular.ttf",
        "Vazirmatn-Regular.ttf",
    ]
    font_search_dirs = [
        os.path.join(module_dir, "fonts"),
        os.path.join(workspace_dir, "fonts"),
    ]
    for fdir in font_search_dirs:
        for fname in bundled_names:
            candidate = os.path.join(fdir, fname)
            if os.path.exists(candidate):
                return candidate

    # 2. Operating System Fonts fallback
    system = platform.system()
    candidates = []
    if system == "Windows":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts_dir = os.path.join(windir, "Fonts")
        candidates = [
            os.path.join(fonts_dir, "NotoNastaliqUrdu-Regular.ttf"),
            os.path.join(fonts_dir, "Amiri-Regular.ttf"),
            os.path.join(fonts_dir, "arial.ttf"),
            os.path.join(fonts_dir, "tahoma.ttf"),
            os.path.join(fonts_dir, "segoeui.ttf"),
        ]
    elif system == "Darwin":
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoNastaliqUrdu-Regular.ttf",
            "/usr/share/fonts/truetype/amiri/amiri-regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return ""


LANGUAGE_PRESETS: Dict[str, Dict[str, Any]] = {
    "Urdu (اردو)": {
        "languages": "urd+eng",
        "primary_language": "urd",
        "rtl_font_name": "Noto Nastaliq Urdu",
        "ltr_font_name": "Times New Roman",
    },
    "Arabic (العربية)": {
        "languages": "ara+eng",
        "primary_language": "ara",
        "rtl_font_name": "Amiri",
        "ltr_font_name": "Times New Roman",
    },
    "Persian (فارسی)": {
        "languages": "fas+eng",
        "primary_language": "fas",
        "rtl_font_name": "Vazirmatn",
        "ltr_font_name": "Times New Roman",
    },
    "English": {
        "languages": "eng",
        "primary_language": "eng",
        "rtl_font_name": "Arial",
        "ltr_font_name": "Times New Roman",
    },
    "Multilingual (All)": {
        "languages": "urd+eng+ara+fas",
        "primary_language": "multilingual",
        "rtl_font_name": "Noto Nastaliq Urdu",
        "ltr_font_name": "Times New Roman",
    },
}


@dataclass
class AppConfig:
    # External Binary & Engine Settings
    tesseract_cmd: str = field(default_factory=_default_tesseract_path)
    poppler_path: Optional[str] = field(default_factory=_default_poppler_path)
    tessdata_dir: Optional[str] = None
    ocr_backend: str = "tesseract"  # "tesseract", "hybrid", or "vision_llm"
    vision_llm_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("VISION_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY")
    )
    vision_llm_model: str = "gemini-2.0-flash"
    hybrid_confidence_threshold: float = 65.0  # Fallback to Vision LLM when page confidence < 65%

    # Input / Output Paths
    input_pdf_path: str = os.path.abspath("input_book.pdf")
    output_dir: str = os.path.abspath("digitized_output")
    start_page: int = 1
    end_page: Optional[int] = None

    # OCR Engine Settings
    languages: str = "urd+eng+ara+fas"
    primary_language: str = "urd"  # "urd", "ara", "fas", "eng", or "multilingual"
    language_preset: str = "Multilingual (All)"
    dpi: int = 350
    oem: int = 3
    psm: int = 6
    auto_tune_psm: bool = True

    # OpenCV Physical Scans Preprocessing & Dewarping
    enable_border_removal: bool = True
    border_margin_px: int = 18
    enable_deskew: bool = True
    max_skew_angle: float = 12.0
    enable_polynomial_dewarping: bool = True  # 3D Page curl & curved line dewarping
    enable_spine_shadow_removal: bool = True
    enable_bleed_through_reduction: bool = True
    enable_ghost_dot_removal: bool = True  # Filters faint bleed-through ghost dots on onion-skin paper
    enable_denoise: bool = True

    denoise_strength: int = 7
    enable_contrast_normalization: bool = True
    clahe_clip_limit: float = 2.0
    enable_adaptive_threshold: bool = False
    extract_embedded_images: bool = True
    min_illustration_area_ratio: float = 0.035

    # Layout, Poetry & Footnote Analysis Settings
    enable_poetry_detection: bool = True
    poetry_max_hemistich_words: int = 10
    enable_adaptive_column_detection: bool = True
    enable_cross_page_footnote_stitching: bool = True

    # Linguistic Correction & Clitic Morphology Settings
    confidence_threshold: float = 78.0
    enable_spellcheck: bool = True
    enable_clitic_morphology: bool = True  # Agglutinative clitic stemmer for Arabic/Persian
    enable_english_languagetool: bool = False
    enable_rtl_reshaping_for_pdf: bool = True
    preserve_diacritics: bool = True
    flag_low_confidence: bool = True
    low_conf_prefix: str = "[LOW_CONF"
    clean_bidi_tagging: bool = True
    custom_dictionary_dir: Optional[str] = None
    enable_historical_orthography_mode: bool = True  # Protects Quranic rasm & classical Persian/Urdu archaic spellings
    enable_interlinear_detection: bool = True  # Detects and pairs word-by-word interlinear translations

    # Output Formats & Archival Metadata
    export_docx: bool = True
    export_sandwich_pdf: bool = True  # Searchable Facsimile (Sandwich) PDF: original scan + invisible selectable text
    export_pdf: bool = True  # Reflowable styled PDF
    export_epub: bool = True
    export_txt: bool = True
    export_html_weasyprint: bool = True  # Generates native HarfBuzz-ready HTML copy-pasteable replica
    export_alto_xml: bool = True  # Library of Congress ALTO XML 4.2 archival interchange standard
    export_page_xml: bool = True  # PRImA PageXML 2019-07-15 digital library standard
    docx_group_runs: bool = True  # Group words into contiguous runs for proper cursive shaping


    # Vision LLM & Rate Limiting Settings
    vision_llm_max_retries: int = 3
    vision_llm_base_delay: float = 2.0

    # Checkpoint & Inspector Optimization Settings
    inspector_image_quality: int = 85
    inspector_image_max_dim: int = 1600

    rtl_font_name: str = "Noto Nastaliq Urdu"
    rtl_fallback_font: str = "Arial"
    ltr_font_name: str = "Times New Roman"
    pdf_ttf_font_path: str = field(default_factory=_default_unicode_font)
    book_title: str = "Digitized Multilingual Book"
    book_author: str = "OCR Replica Pipeline"

    # Hybrid Born-Digital PDF Extraction Settings
    detect_born_digital_pdf: bool = True  # Bypass rasterization/OCR if page has native digital text
    born_digital_char_threshold: int = 40  # Minimum character count to qualify as born-digital

    # Large Document Compilation & Memory Settings
    chunked_pdf_compilation: bool = True  # Chunked streaming assembly for massive books (1,000+ pages)
    pdf_compilation_chunk_size: int = 50  # Number of pages per flush chunk
    enable_opencv_multithreading: bool = True  # Enable CPU multithreading in OpenCV

    # Multiprocessing & Scale Settings
    max_workers: int = field(default_factory=lambda: max(1, (os.cpu_count() or 2) - 1))
    resume_enabled: bool = True
    retry_failed_on_resume: bool = True

    def apply_preset(self, preset_name: str) -> None:
        """Apply a language preset configuration."""
        if preset_name in LANGUAGE_PRESETS:
            p = LANGUAGE_PRESETS[preset_name]
            self.language_preset = preset_name
            self.languages = p["languages"]
            self.primary_language = p["primary_language"]
            self.rtl_font_name = p["rtl_font_name"]
            self.ltr_font_name = p["ltr_font_name"]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def ensure_directories(self) -> None:
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        os.makedirs(self.extracted_images_dir, exist_ok=True)

    @property
    def checkpoint_dir(self) -> str:
        return os.path.join(self.output_dir, ".checkpoints")

    @property
    def extracted_images_dir(self) -> str:
        return os.path.join(self.output_dir, ".checkpoints", "images")

    @property
    def state_file_path(self) -> str:
        return os.path.join(self.output_dir, ".checkpoints", "run_state.json")


DEFAULT_CONFIG = AppConfig()


def check_system_health(config: Optional[AppConfig] = None) -> Dict[str, Any]:
    """Inspect system environment, Tesseract, PDF backends (Poppler/PDFium), bundled fonts, and API keys."""
    cfg = config or AppConfig()
    health: Dict[str, Any] = {
        "tesseract_installed": False,
        "tesseract_version": "Not detected",
        "tesseract_path": cfg.tesseract_cmd,
        "installed_languages": [],
        "poppler_installed": False,
        "poppler_path": cfg.poppler_path or "System PATH",
        "pypdfium2_installed": False,
        "pypdf_installed": False,
        "bundled_fonts_detected": [],
        "vision_llm_configured": bool(cfg.vision_llm_api_key),
        "vision_llm_model": cfg.vision_llm_model,
        "warnings": [],
        "all_healthy": True,
    }

    # 1. Check PDF Backends (pypdfium2 and pypdf provide zero-dependency pure-Python rendering)
    try:
        import pypdfium2
        health["pypdfium2_installed"] = True
    except ImportError:
        pass

    try:
        import pypdf
        health["pypdf_installed"] = True
    except ImportError:
        pass

    # 2. Check Bundled and System Fonts
    module_dir = os.path.dirname(os.path.abspath(__file__))
    fonts_dir = os.path.join(module_dir, "fonts")
    if os.path.exists(fonts_dir):
        found_fonts = [f for f in os.listdir(fonts_dir) if f.lower().endswith((".ttf", ".otf"))]
        health["bundled_fonts_detected"] = found_fonts

    # 3. Check Tesseract binary
    try:
        import subprocess
        tess_bin = cfg.tesseract_cmd or "tesseract"
        res = subprocess.run([tess_bin, "--version"], capture_output=True, text=True, timeout=6)
        if res.returncode == 0:
            health["tesseract_installed"] = True
            first_line = res.stdout.splitlines()[0] if res.stdout else "Detected"
            health["tesseract_version"] = first_line.strip()
        else:
            health["warnings"].append(f"Tesseract returned code {res.returncode}")
            health["all_healthy"] = False
    except Exception as exc:
        health["warnings"].append(f"Tesseract binary check failed: {exc}")
        health["all_healthy"] = False

    # 4. Check installed OCR languages
    if health["tesseract_installed"]:
        try:
            res_langs = subprocess.run(
                [cfg.tesseract_cmd or "tesseract", "--list-langs"],
                capture_output=True,
                text=True,
                timeout=6,
            )
            if res_langs.returncode == 0:
                langs = [ln.strip() for ln in res_langs.stdout.splitlines()[1:] if ln.strip()]
                health["installed_languages"] = langs
        except Exception:
            pass

    # 5. Check Poppler
    try:
        import shutil
        if shutil.which("pdftoppm") or (
            cfg.poppler_path and os.path.exists(os.path.join(cfg.poppler_path, "pdftoppm.exe"))
        ):
            health["poppler_installed"] = True
        else:
            cand = _default_poppler_path()
            if cand:
                health["poppler_installed"] = True
                health["poppler_path"] = cand
            else:
                # If pypdfium2 is present, Poppler is optional!
                if not health["pypdfium2_installed"]:
                    health["warnings"].append(
                        "Neither Poppler nor pypdfium2 detected. Please run: pip install pypdfium2"
                    )
                    health["all_healthy"] = False
    except Exception as exc:
        if not health["pypdfium2_installed"]:
            health["warnings"].append(f"Poppler check failed: {exc}")
            health["all_healthy"] = False

    return health

