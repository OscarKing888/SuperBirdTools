@echo off
setlocal
chcp 65001 >nul

set "ROOT_DIR=%~dp0"
set "REQUESTED_PYTHON_EXE=%PYTHON_EXE%"
set "PYTHON_EXE="
set "PYTHONW_EXE="

if defined REQUESTED_PYTHON_EXE if exist "%REQUESTED_PYTHON_EXE%" (
    set "PYTHON_EXE=%REQUESTED_PYTHON_EXE%"
    goto python_ready
)

if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
    set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
    goto python_ready
)

if exist "%ROOT_DIR%..\SuperBirdTools\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%ROOT_DIR%..\SuperBirdTools\.venv\Scripts\python.exe"
    goto python_ready
)

if exist "%ROOT_DIR%.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"
    goto python_ready
)

for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)

if not defined PYTHON_EXE (
    echo No usable Python found. Set PYTHON_EXE or initialize the shared virtual environment.
    echo Checked: PYTHON_EXE, VIRTUAL_ENV, ..\SuperBirdTools\.venv, .\.venv, PATH python
    exit /b 1
)

:python_ready
for %%I in ("%PYTHON_EXE%") do set "PYTHON_DIR=%%~dpI"
if exist "%PYTHON_DIR%pythonw.exe" (
    set "PYTHONW_EXE=%PYTHON_DIR%pythonw.exe"
)
set "GUI_PYTHON=%PYTHON_EXE%"
if defined PYTHONW_EXE if exist "%PYTHONW_EXE%" (
    set "GUI_PYTHON=%PYTHONW_EXE%"
)

cd /d "%ROOT_DIR%"

if not defined APP_COMMON_LOG_FILE (
    if not exist "%ROOT_DIR%logs" mkdir "%ROOT_DIR%logs" >nul 2>nul
    set "APP_COMMON_LOG_FILE=%ROOT_DIR%logs\SuperViewer.log"
)

::start "SuperViewer" "%GUI_PYTHON%" -m SuperViewer.entry
start "SuperBirdStamp" "%GUI_PYTHON%" -m SuperBirdStamp.entry

endlocal
