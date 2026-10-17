# Scripta System Architecture

This document describes the architectural topology, pipeline dataflow, and design decisions underpinning **Scripta**.

---

## 🏛️ High-Level Architectural Flowchart

```
┌────────────────────────────────────────────────────────┐
│                   Raw Book Input                      │
│        (Scanned PDF, Born-Digital PDF, Images)         │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │  ocr_engine.py Inspector  │
              └─────────────┬─────────────┘
                            │
             Is Vector Text Stream Present?
             ├── Yes ──► Direct Stream Extraction (100% Fidelity)
             └── No  ──► Render High-DPI Scanned Bitmap
                            │
                            ▼
              ┌───────────────────────────┐
              │      preprocessor.py      │
              ├───────────────────────────┤
              │ • Ghost-dot Bleed Filter  │
              │ • Deskew Contour Analysis │
              │ • Sauvola/Otsu Binarize   │
              └─────────────┬─────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │    layout_analyzer.py     │
              ├───────────────────────────┤
              │ • Multi-Column Detection  │
              │ • Interlinear Alignment   │
              │ • Marginalia Isolation    │
              └─────────────┬─────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │      ocr_engine.py        │
              │   (Tesseract Cascade)     │
              └─────────────┬─────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │       corrector.py        │
              ├───────────────────────────┤
              │ • Rasm / Tanwin Protect   │
              │ • BiDi & Reshaping Engine │
              │ • ZWNJ Ligature Fixes     │
              └─────────────┬─────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │        builder.py         │
              ├───────────────────────────┤
              │ • Searchable Sandwich PDF │
              │ • ALTO XML 4.2            │
              │ • PRImA PageXML           │
              │ • Styled DOCX & EPUB3     │
              └───────────────────────────┘
```

---

## 🧩 Core Modules

| Module | Responsibility | Key Interfaces |
| :--- | :--- | :--- |
| `preprocessor.py` | Image normalization, deskewing, bleed attenuation | `attenuate_bleedthrough()`, `binarize_sauvola()`, `deskew()` |
| `layout_analyzer.py` | Column detection, interlinear pairs, reading order | `analyze_layout()`, `detect_columns()`, `pair_interlinear()` |
| `ocr_engine.py` | Vector extraction & Tesseract dispatcher | `process_page()`, `extract_vector_stream()`, `run_ocr()` |
| `corrector.py` | Orthography protection, BiDi reshaping, font linking | `clean_and_shape()`, `protect_rasm()`, `shape_bidi()` |
| `builder.py` | Document generation across archival schemas | `build_sandwich_pdf()`, `build_alto_xml()`, `build_page_xml()` |
| `batch_processor.py`| Worker pool orchestration & checkpoint resilience | `BatchJob`, `CheckpointLedger`, `WorkerPool` |
| `main.py` | CustomTkinter GUI & presentation layer | `AppWindow`, `PreviewCanvas`, `ProgressMonitor` |
