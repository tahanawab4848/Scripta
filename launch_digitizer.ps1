# Multilingual Book Digitizer - PowerShell Launcher
$ErrorActionPreference = "Stop"

Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "   Multilingual Book Digitizer & Facsimile OCR Studio" -ForegroundColor White
Write-Host "   Urdu, Arabic, Persian & English High-Fidelity Preservation" -ForegroundColor Gray
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. Portable Tesseract detection
$LocalTess = Join-Path $ScriptDir "tesseract\tesseract.exe"
if (Test-Path $LocalTess) {
    Write-Host "[INFO] Detected portable Tesseract at $LocalTess" -ForegroundColor Green
    $env:PATH = "$(Split-Path $LocalTess);$($env:PATH)"
    $env:TESSERACT_CMD = $LocalTess
}

# 2. Portable Poppler detection
$LocalPoppler = Join-Path $ScriptDir "poppler\Library\bin"
if (Test-Path $LocalPoppler) {
    Write-Host "[INFO] Detected portable Poppler at $LocalPoppler" -ForegroundColor Green
    $env:PATH = "$LocalPoppler;$($env:PATH)"
    $env:POPPLER_PATH = $LocalPoppler
}

# 3. Environment & PYTHONPATH setup
$DigitizerDir = Join-Path $ScriptDir "book_digitizer"
$env:PYTHONPATH = "$DigitizerDir;$ScriptDir;$($env:PYTHONPATH)"

# 4. Launch Application
Write-Host "[INFO] Launching Digitizer Studio..." -ForegroundColor Green
python (Join-Path $DigitizerDir "main.py")
