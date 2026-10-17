# Supported Languages & Scripts

Scripta is engineered with a special emphasis on right-to-left (RTL) cursive scripts and complex historical typography, alongside standard left-to-right (LTR) scripts.

---

## 🌍 Language Compatibility Matrix

| Language | Script | Tesseract Code | BiDi Reshaper | Embedded Typeface |
| :--- | :--- | :--- | :--- | :--- |
| **Urdu** | Nastaliq / Naskh | `urd` | Yes | Noto Nastaliq Urdu |
| **Arabic** | Naskh / Rasm | `ara` | Yes | Amiri Regular |
| **Persian (Farsi)** | Naskh / Nastaliq | `fas` | Yes | Amiri Regular |
| **Ottoman Turkish**| Arabic Script | `ota` | Yes | Amiri Regular |
| **Pashto** | Arabic Extended | `pus` | Yes | Noto Nastaliq Urdu |
| **Sindhi** | Perso-Arabic | `snd` | Yes | Amiri Regular |
| **English** | Latin | `eng` | No | System Standard |
| **German / French / Spanish** | Latin | `deu`, `fra`, `spa` | No | System Standard |
| **Russian** | Cyrillic | `rus` | No | System Standard |
| **100+ Others** | Latin / Indic / Asian | Standard codes | As applicable | System Standard |

---

## 🔤 Special Orthographic Features

### 1. Quranic Rasm & Diacritics Protection
When historical orthography protection is enabled:
- **Tanwīn** ($ً, ٍ, ٌ$) is protected from automatic stripping.
- **Superscript Dagger Alifs** ($ٰ$) are preserved.
- **Stop Signs & Marks** ($ۛ, ۜ, ۚ, ۖ, ۗ$) are kept in place.
- **Sukun and Shaddah** are maintained in correct visual sequence.

### 2. Zero-Width Non-Joiner (ZWNJ) Normalization
Urdu and Persian suffixes (such as *می‌خواهم* or *کتاب‌ها*) require ZWNJ (`\u200C`) for correct typographic presentation without merging adjacent character forms. Scripta automatically recognizes and standardizes ZWNJ boundaries.
