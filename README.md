# Scripta 📜

> **Production-Grade Archival Book Digitizer, Layout Analyzer & Searchable Facsimile Studio**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![RTL & Complex Scripts](https://img.shields.io/badge/Scripts-Nastaliq%20%7C%20Naskh%20%7C%20Latin%20%7C%20100+-orange.svg)]()
[![Archival Standards](https://img.shields.io/badge/Archival-ALTO%204.2%20%7C%20PageXML%20%7C%20Sandwich%20PDF-purple.svg)]()

---

## 🌟 Overview

**Scripta** is an open-source, enterprise-grade book digitization suite engineered specifically to preserve and digitize rare historical manuscripts, lithographs, and multi-lingual printed books. 

While conventional OCR tools struggle with right-to-left (RTL) scripts, complex ligatures, bleed-through ink, and non-standard layouts, **Scripta** provides an end-to-end archival preservation pipeline: from raw scan preprocessing and layout decomposition to bidirectional text shaping, historical orthography protection, and dual-layer searchable facsimile generation.

---

## ✨ Key Features

### 1. 🔍 Advanced Dual-Engine OCR & Vector Stream Ingestion
- **Born-Digital Fast Path**: Inspects incoming PDFs for existing high-fidelity digital text streams via `pypdf`/`pdfium2`, bypassing unnecessary OCR while maintaining 100% vector fidelity.
- **Scanned Raster Pipeline**: Automatic high-DPI rendering and OCR via Tesseract engine with dynamic fallback cascades.
- **Multilingual Support**: First-class support for Urdu (Nastaliq), Arabic (Naskh & Quranic Rasm), Persian (Farsi), Ottoman Turkish, Sindhi, Pashto, English, and 100+ standard Tesseract language models.

### 2. 🧪 Archival Preprocessing
- **Onion-Skin & Ghosting Removal**: Attenuates reverse-page bleed-through and ghost dots typical of fragile 19th/20th-century paper stock.
- **Adaptive Document Binarization**: Combines Sauvola, Otsu, and local adaptive thresholding to retain faint ink strokes while eliminating paper discoloration.
- **Skew & Orientation Correction**: Automatic Hough-transform and contour-based deskewing up to $\pm 45^\circ$.
- **Margin & Border Denoising**: Strips binding gutters, scanner edges, and finger artifacts.

### 3. 📐 Layout Analysis & Reading Order Reconstruction
- **Multi-Column Decomposition**: Automatically detects 1-, 2-, and 3-column newspaper and treatise layouts.
- **Interlinear Translation Detection**: Segments paired bilingual texts (e.g., Arabic scripture with sublinear Urdu or English commentary) and maintains parallel synchronization.
- **Marginalia & Footnote Isolation**: Separates body text from side commentaries (*hashiya*), running headers, and page numbers.

### 4. ✍️ RTL Reshaping & Historical Orthography Protection
- **BiDi & Complex Shaping**: Full integration with `arabic-reshaper` and `python-bidi` for correct visual rendering of connected cursive scripts.
- **Rasm & Archaic Preservation**: Protected dictionary rules preserve archaic orthography, honorific ligatures, tanwīn, superscript alifs, and Quranic stop marks without unwanted "auto-corrections".
- **Bundled Authentic Typography**: Includes licensed SIL OFL typefaces: *Amiri* (Naskh) and *Noto Nastaliq Urdu*.

### 5. 🏛️ Archival Export Studio
- **Searchable Facsimile ("Sandwich") PDF**: Combines original high-resolution page scans with an invisible, selectable, search-indexed vector text overlay.
- **Library of Congress ALTO XML (v4.2)**: Industry standard schema for digital libraries and academic archives.
- **PRImA PageXML (2019-07-15)**: Granular geometric polygon and baseline markup for researchers and academic DH pipelines.
- **Clean Formatted DOCX / EPUB3**: Exports styled Microsoft Word documents with proper RTL paragraph markers and valid EPUB3 digital books.
- **Markdown & JSON**: Machine-readable structured outputs for LLM fine-tuning and digital corpus ingestion.

### 6. ⚡ Batch Processing & Resilient Checkpointing
- **Zero-Loss Crash Recovery**: Checkpoint ledger saves page states incrementally; resume interrupted multi-thousand-page jobs with a single click.
- **Parallel Workers**: Multi-threaded page rendering and OCR execution utilizing available CPU cores.

---

## 📂 Repository Structure

```plaintext
Scripta/
├── book_digitizer/
│   ├── batch_processor.py   # Multi-threaded job orchestrator & checkpoint engine
│   ├── builder.py           # Document exporters: Sandwich PDF, ALTO XML, PageXML, DOCX, EPUB
│   ├── config.py            # Global settings, OCR engine configs & typography defaults
│   ├── corrector.py         # Historical orthography protector & BiDi text reshaper
│   ├── layout_analyzer.py   # Multi-column & interlinear layout segmentation engine
│   ├── main.py              # Modern CustomTkinter graphical user interface (GUI)
│   ├── ocr_engine.py        # Dual-engine OCR dispatcher (vector extraction + Tesseract)
│   ├── preprocessor.py      # Ghost-dot, bleed-through, deskew & binarization filters
│   ├── requirements.txt     # Internal module dependency specification
│   └── fonts/               # Bundled OFL TrueType fonts (Amiri, Noto Nastaliq Urdu)
├── launch_digitizer.ps1     # Native PowerShell 1-click launcher
├── run_gui.bat              # Windows batch launcher
├── requirements.txt         # Root project dependency pointer
├── .gitignore               # Archival output & environment exclusion rules
└── README.md                # Documentation & usage manual
```

---

## 🚀 Quick Start

### Prerequisites
1. **Python 3.10+** (64-bit recommended)
2. **Tesseract OCR**:
   - Windows: Install via [UB-Mannheim Tesseract installer](https://github.com/UB-Mannheim/tesseract/wiki).
   - Ensure the language packs you need (e.g., `urd`, `ara`, `fas`, `eng`) are selected during installation or placed into `tessdata`.

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/tahanawab4848/Scripta.git
   cd Scripta
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   # Windows:
   .\venv\Scripts\activate
   # Linux/macOS:
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## 🖥️ Launching the Application

### Option A: 1-Click Launchers (Windows)
- Double-click **`run_gui.bat`** OR execute **`.\launch_digitizer.ps1`** in PowerShell.
- The script automatically verifies your Python environment, installs missing dependencies, checks for Tesseract, and launches the application.

### Option B: Terminal Command
```bash
python book_digitizer/main.py
```

---

## 📖 Supported Formats & Workflows

| Input Formats | Export Targets | Standards Complied |
| :--- | :--- | :--- |
| PDF (Scanned & Born-Digital) | Searchable PDF (Sandwich) | ISO 32000-1 (PDF) |
| TIFF / Multi-page TIFF | Library of Congress ALTO XML | ALTO v4.2 Schema |
| JPEG, PNG, WEBP, BMP | PRImA PageXML | PageXML 2019-07-15 |
| Folder Batches | Microsoft Word (.docx) | OpenXML RTL Standard |
| | EPUB3 E-Book (.epub) | IDPF EPUB 3.2 |
| | Markdown & Plain Text | UTF-8 Universal |

---

## ⚙️ Configuration & Customization

Scripta can be customized via `book_digitizer/config.py`:
- **Tesseract Executable Path**: Set default fallback paths for Windows/Linux/macOS installations.
- **Bleed-Through Attenuation**: Toggle onion-skin ghost-dot filtering sensitivity.
- **Bilingual Segmentation**: Configure line spacing thresholds for interlinear translation synchronization.
- **Font Selection**: Direct paths to custom system or project TrueType fonts.

---

## 🛡️ License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. Bundled fonts are licensed under the SIL Open Font License (OFL).
