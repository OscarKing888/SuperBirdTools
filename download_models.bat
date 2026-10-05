@echo off
rem Download every model into the workspace (SuperViewer\models) so builds,
rem packaging and local runs never download: the bird sharpness YOLO / SAM
rem catalog and the NAFNet denoise model, each verified by size and SHA-256.
rem
rem   download_models.bat                          everything (~3.9 GB)
rem   download_models.bat yolo11x-seg.pt sam2.1_b  only these
rem   download_models.bat --dry-run                plan only
rem   download_models.bat --check-only             verify offline
rem   download_models.bat --list                   every available model
setlocal EnableExtensions
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
rem Interpreter order: PYTHON_EXE, this checkout's .venv, then the main
rem checkout's .venv (feature worktrees share it instead of creating their own).
set "VENV_PYTHON="
if defined PYTHON_EXE if exist "%PYTHON_EXE%" set "VENV_PYTHON=%PYTHON_EXE%"
if not defined VENV_PYTHON if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not defined VENV_PYTHON (
    for /f "usebackq delims=" %%G in (`git -C "%SCRIPT_DIR%." rev-parse --path-format=absolute --git-common-dir 2^>nul`) do (
        if exist "%%~dpG.venv\Scripts\python.exe" set "VENV_PYTHON=%%~dpG.venv\Scripts\python.exe"
    )
)
if not defined VENV_PYTHON (
    echo .venv Python not found. 1>&2
    echo Run "python init_dev.py" in the repository root first. 1>&2
    exit /b 1
)

cd /d "%SCRIPT_DIR%"
set "PYTHONUTF8=1"
"%VENV_PYTHON%" "%SCRIPT_DIR%build_tools\download_models.py" %*
exit /b %ERRORLEVEL%
