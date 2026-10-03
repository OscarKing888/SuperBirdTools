@echo off
rem Run the repository test suite manually with the repo-root .venv.
rem
rem   run_tests.bat                          all tests, headless (Qt offscreen)
rem   run_tests.bat SuperViewer\tests -k tag any pytest arguments
rem   run_tests.bat --show-windows SuperBirdStamp\tests\test_dejitter_tab.py
rem                                          show real Qt windows for debugging
rem
rem Tests are never started by the apps or run.bat; they only run when invoked.
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

set "SHOW_WINDOWS=0"
set "PYTEST_ARGS="
:parse
if "%~1"=="" goto run
if /I "%~1"=="--show-windows" (
    set "SHOW_WINDOWS=1"
) else (
    set PYTEST_ARGS=%PYTEST_ARGS% %1
)
shift
goto parse

:run
cd /d "%SCRIPT_DIR%"
if "%SHOW_WINDOWS%"=="1" (
    set "SUPERBIRD_TEST_SHOW_WINDOWS=1"
    set "QT_QPA_PLATFORM="
) else (
    set "QT_QPA_PLATFORM=offscreen"
)
"%VENV_PYTHON%" -m pytest %PYTEST_ARGS%
exit /b %ERRORLEVEL%
