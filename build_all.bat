@echo off
setlocal

set "ROOT_DIR=%~dp0"
if defined SUPERBIRDTOOLS_DIST_ROOT (
  set "DIST_ROOT=%SUPERBIRDTOOLS_DIST_ROOT%"
) else (
  set "DIST_ROOT=%ROOT_DIR%dist"
)
if defined SUPERBIRDTOOLS_BUILD_ROOT (
  set "BUILD_ROOT=%SUPERBIRDTOOLS_BUILD_ROOT%"
) else (
  set "BUILD_ROOT=%ROOT_DIR%build"
)

set "CLEAN=0"
:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--clean" (
  set "CLEAN=1"
  shift
  goto parse_args
)
echo Unknown option: %~1
exit /b 1

:args_done
if "%CLEAN%"=="1" (
  if exist "%DIST_ROOT%" rmdir /s /q "%DIST_ROOT%"
  if exist "%BUILD_ROOT%" rmdir /s /q "%BUILD_ROOT%"
)
if not exist "%DIST_ROOT%" mkdir "%DIST_ROOT%"
if not exist "%BUILD_ROOT%" mkdir "%BUILD_ROOT%"

if defined PYTHON_EXE if exist "%PYTHON_EXE%" goto python_ready
if defined PYTHON_BIN if exist "%PYTHON_BIN%" (
  set "PYTHON_EXE=%PYTHON_BIN%"
  goto python_ready
)
if exist "%ROOT_DIR%\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"
  goto python_ready
)
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
  set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
  goto python_ready
)
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_LAUNCHER=py -3"
  goto launcher_ready
)
set "PYTHON_LAUNCHER=python"
goto launcher_ready

:python_ready
echo [INFO] Using Python: %PYTHON_EXE%
"%PYTHON_EXE%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --distpath "%DIST_ROOT%" ^
  --workpath "%BUILD_ROOT%\merged_win" ^
  "%ROOT_DIR%build_all_win_merged.spec"
goto after_build

:launcher_ready
echo [INFO] Using Python launcher: %PYTHON_LAUNCHER%
%PYTHON_LAUNCHER% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --distpath "%DIST_ROOT%" ^
  --workpath "%BUILD_ROOT%\merged_win" ^
  "%ROOT_DIR%build_all_win_merged.spec"

:after_build
if errorlevel 1 exit /b %errorlevel%

echo [OK] outputs:
echo   %DIST_ROOT%\SuperViewer\SuperViewer.exe
echo   %DIST_ROOT%\SuperBirdStamp\SuperBirdStamp.exe

endlocal

