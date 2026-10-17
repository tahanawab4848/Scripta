@echo off
setlocal enabledelayedexpansion
title Scripta - Multilingual Book Digitizer and Facsimile OCR Studio

echo =====================================================================
echo    Scripta - Multilingual Book Digitizer and Facsimile OCR Studio
echo    Urdu, Arabic, Persian and English High-Fidelity Preservation
echo =====================================================================
echo.

:: 1. Check Python installation
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python was not found on your system PATH!
    echo Please install Python 3.10+ from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 2. Set PYTHONPATH to ensure modules can resolve sibling packages
set "PYTHONPATH=%~dp0book_digitizer;%~dp0;!PYTHONPATH!"

:: 3. Configure portable Tesseract if present in local directory
if exist "%~dp0tesseract\tesseract.exe" (
    echo [INFO] Detected portable Tesseract at %~dp0tesseract
    set "PATH=%~dp0tesseract;!PATH!"
    set "TESSERACT_CMD=%~dp0tesseract\tesseract.exe"
) else if exist "%~dp0bin\tesseract\tesseract.exe" (
    echo [INFO] Detected portable Tesseract at %~dp0bin\tesseract
    set "PATH=%~dp0bin\tesseract;!PATH!"
    set "TESSERACT_CMD=%~dp0bin\tesseract\tesseract.exe"
)

:: 4. Configure portable Poppler if present in local directory
if exist "%~dp0poppler\Library\bin" (
    echo [INFO] Detected portable Poppler at %~dp0poppler\Library\bin
    set "PATH=%~dp0poppler\Library\bin;!PATH!"
    set "POPPLER_PATH=%~dp0poppler\Library\bin"
) else if exist "%~dp0poppler\bin" (
    echo [INFO] Detected portable Poppler at %~dp0poppler\bin
    set "PATH=%~dp0poppler\bin;!PATH!"
    set "POPPLER_PATH=%~dp0poppler\bin"
)

:: 5. Launch Application
echo [INFO] Launching Digitizer Studio...
echo.
python "%~dp0book_digitizer\main.py"

if %ERRORLEVEL% neq 0 (
    echo.
    echo [NOTICE] Application closed with return code %ERRORLEVEL%.
    pause
)
