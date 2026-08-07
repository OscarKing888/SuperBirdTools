@echo off
setlocal
chcp 65001 >nul

REM 调用 init_dev.py：创建/复用 .venv，安装 pytest，再初始化各应用依赖。
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

if defined PYTHON_EXE if exist "%PYTHON_EXE%" goto run_with_exe

where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_LAUNCHER=py -3"
  goto run_with_launcher
)

where python >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_LAUNCHER=python"
  goto run_with_launcher
)

echo 未找到可用 Python。请安装 Python 3，或设置 PYTHON_EXE 指向 python.exe。
exit /b 1

:run_with_exe
echo [INFO] Using Python: %PYTHON_EXE%
"%PYTHON_EXE%" "%ROOT_DIR%init_dev.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
goto after_init

:run_with_launcher
echo [INFO] Using Python launcher: %PYTHON_LAUNCHER%
%PYTHON_LAUNCHER% "%ROOT_DIR%init_dev.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

:after_init
if not "%EXIT_CODE%"=="0" (
  echo [ERROR] 开发环境初始化失败，退出码: %EXIT_CODE%
  exit /b %EXIT_CODE%
)

echo [OK] 开发环境初始化完成: %ROOT_DIR%.venv
endlocal
exit /b 0
