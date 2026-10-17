# Changelog

All notable changes to **Scripta** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-10-17

### Added
- **Born-Digital Fast Path**: High-speed direct vector text stream extraction using `pypdf` and `pdfium2`.
- **Archival Preprocessing Pipeline**:
  - Reverse-page ink bleed-through and onion-skin ghost-dot attenuation.
  - Sauvola, Otsu, and local adaptive binarization filters.
  - Deskewing with contour and Hough transform angle estimation ($\pm 45^\circ$).
- **Layout Analysis**:
  - Multi-column reading order reconstruction.
  - Interlinear bilingual translation pair synchronization (e.g. Arabic with Urdu/English sublinear text).
  - Marginalia and footnote isolation.
- **Linguistic Engine**:
  - Quranic *Rasm* and historical orthography protection (tanwīn, dagger alifs, honorific symbols).
  - BiDi layout shaper and ligature normalization for Arabic and Urdu.
  - Bundled authentic SIL Open Font License typefaces: *Amiri* and *Noto Nastaliq Urdu*.
- **Archival Export Studio**:
  - Dual-layer Searchable Facsimile ("Sandwich") PDF generator.
  - Library of Congress ALTO XML v4.2 generator.
  - PRImA PageXML (2019-07-15) markup generator.
  - Styled RTL Microsoft Word (.docx) and EPUB3 e-book export.
- **User Interface**:
  - Modern CustomTkinter graphical interface with dark/light theme toggle.
  - Real-time thumbnail previewer and live progress tracking.
  - Multi-threaded batch processor with fault-tolerant checkpoint resumption.
  - 1-click execution scripts (`run_gui.bat` and `launch_digitizer.ps1`).
