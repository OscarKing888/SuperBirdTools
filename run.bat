@echo off
setlocal
chcp 65001 >nul

set "ROOT_DIR=%~dp0"
set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"
set "PYTHONW_EXE=%ROOT_DIR%.venv\Scripts\pythonw.exe"

if not exist "%PYTHON_EXE%" (
    echo 未找到虚拟环境 Python: %PYTHON_EXE%
    echo 请先在仓库根目录执行: py -3 init_dev.py
    exit /b 1
)

set "GUI_PYTHON=%PYTHON_EXE%"
if exist "%PYTHONW_EXE%" (
    set "GUI_PYTHON=%PYTHONW_EXE%"
)

cd /d "%ROOT_DIR%"

start "SuperViewer" "%GUI_PYTHON%" -m SuperViewer.entry
start "SuperBirdStamp" "%GUI_PYTHON%" -m SuperBirdStamp.entry

endlocal
