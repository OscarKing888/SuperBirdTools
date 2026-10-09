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
set "APPS_ONLY=0"
set "BUNDLE_MODELS="
:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--clean" (
  set "CLEAN=1"
  shift
  goto parse_args
)
if /I "%~1"=="--apps-only" (
  set "APPS_ONLY=1"
  shift
  goto parse_args
)
if /I "%~1"=="--bundle-all-models" (
  set "BUNDLE_MODELS=all"
  shift
  goto parse_args
)
if /I "%~1"=="--bundle-models" (
  if "%~2"=="" (
    echo --bundle-models requires comma-separated model names >&2
    exit /b 1
  )
  set "BUNDLE_MODELS=%~2"
  shift
  shift
  goto parse_args
)
echo Unknown option: %~1
exit /b 1

:args_done
rem Bundle only the requested catalog models; release builds clear inherited settings.
set "SUPERBIRDTOOLS_BUNDLE_MODELS=%BUNDLE_MODELS%"
set MODEL_ARGS="%BUNDLE_MODELS%"
if "%BUNDLE_MODELS%"=="all" set "MODEL_ARGS="
set "PYINSTALLER_ARGS=--noconfirm"
if "%CLEAN%"=="1" (
  set "PYINSTALLER_ARGS=--noconfirm --clean"
  echo [INFO] Clean build requested; removing local build outputs and PyInstaller cache.
  if exist "%DIST_ROOT%" rmdir /s /q "%DIST_ROOT%"
  if exist "%BUILD_ROOT%" rmdir /s /q "%BUILD_ROOT%"
) else (
  echo [INFO] Incremental build cache enabled: %BUILD_ROOT%\merged_win
)
if not exist "%DIST_ROOT%" mkdir "%DIST_ROOT%"
if not exist "%BUILD_ROOT%" mkdir "%BUILD_ROOT%"

set "PYINSTALLER_BOOTSTRAP_DIR=%ROOT_DIR%build_tools\pyinstaller_bootstrap"
if defined PYTHONPATH (
  set "PYTHONPATH=%PYINSTALLER_BOOTSTRAP_DIR%;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%PYINSTALLER_BOOTSTRAP_DIR%"
)

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
"%PYTHON_EXE%" "%ROOT_DIR%build_tools\set_build_version.py"
if errorlevel 1 exit /b 1
"%PYTHON_EXE%" "%ROOT_DIR%build_tools\download_denoise_model.py"
if errorlevel 1 exit /b 1
if defined BUNDLE_MODELS (
  echo [INFO] Bundling selected models from SuperViewer\models; verifying...
  "%PYTHON_EXE%" "%ROOT_DIR%build_tools\download_models.py" --check-only --no-denoise %MODEL_ARGS%
  if errorlevel 1 goto models_missing
)
"%PYTHON_EXE%" -m PyInstaller %PYINSTALLER_ARGS% ^
  --distpath "%DIST_ROOT%" ^
  --workpath "%BUILD_ROOT%\merged_win" ^
  "%ROOT_DIR%build_all_win_merged.spec"
set "BUILD_EXIT_CODE=%ERRORLEVEL%"
goto after_build

:launcher_ready
echo [INFO] Using Python launcher: %PYTHON_LAUNCHER%
%PYTHON_LAUNCHER% "%ROOT_DIR%build_tools\set_build_version.py"
if errorlevel 1 exit /b 1
%PYTHON_LAUNCHER% "%ROOT_DIR%build_tools\download_denoise_model.py"
if errorlevel 1 exit /b 1
if defined BUNDLE_MODELS (
  echo [INFO] Bundling selected models from SuperViewer\models; verifying...
  %PYTHON_LAUNCHER% "%ROOT_DIR%build_tools\download_models.py" --check-only --no-denoise %MODEL_ARGS%
  if errorlevel 1 goto models_missing
)
%PYTHON_LAUNCHER% -m PyInstaller %PYINSTALLER_ARGS% ^
  --distpath "%DIST_ROOT%" ^
  --workpath "%BUILD_ROOT%\merged_win" ^
  "%ROOT_DIR%build_all_win_merged.spec"
set "BUILD_EXIT_CODE=%ERRORLEVEL%"

:after_build
if not "%BUILD_EXIT_CODE%"=="0" exit /b %BUILD_EXIT_CODE%
rem External ExifTool needs its complete private tree, even after MERGE deduplication.
rem Validate the installed copies before manifests/ZIPs can publish this build.
if defined PYTHON_EXE (
  "%PYTHON_EXE%" "%ROOT_DIR%build_tools\stage_windows_exiftool.py" --dist "%DIST_ROOT%"
) else (
  %PYTHON_LAUNCHER% "%ROOT_DIR%build_tools\stage_windows_exiftool.py" --dist "%DIST_ROOT%"
)
if errorlevel 1 exit /b 1
if defined PYTHON_EXE (
  "%PYTHON_EXE%" -m PyInstaller --noconfirm --distpath "%DIST_ROOT%" --workpath "%BUILD_ROOT%\SuperBirdUpdater" "%ROOT_DIR%SuperBirdUpdater\SuperBirdUpdater.spec"
) else (
  %PYTHON_LAUNCHER% -m PyInstaller --noconfirm --distpath "%DIST_ROOT%" --workpath "%BUILD_ROOT%\SuperBirdUpdater" "%ROOT_DIR%SuperBirdUpdater\SuperBirdUpdater.spec"
)
if errorlevel 1 exit /b 1
if "%APPS_ONLY%"=="1" (
  echo [INFO] Apps only: skipping update manifests and release ZIPs; existing release artifacts are not refreshed.
  goto outputs
)
if defined PYTHON_EXE (
  "%PYTHON_EXE%" "%ROOT_DIR%build_tools\generate_update_manifest.py" --dist "%DIST_ROOT%" --platform windows --arch x86_64 --package
) else (
  %PYTHON_LAUNCHER% "%ROOT_DIR%build_tools\generate_update_manifest.py" --dist "%DIST_ROOT%" --platform windows --arch x86_64 --package
)
if errorlevel 1 exit /b 1

:outputs
echo [OK] outputs:
echo   %DIST_ROOT%\SuperViewer\SuperViewer.exe
echo   %DIST_ROOT%\SuperBirdStamp\SuperBirdStamp.exe
echo   %DIST_ROOT%\SuperBirdUpdater\SuperBirdUpdater.exe
if "%APPS_ONLY%"=="0" echo   %DIST_ROOT%\updates

endlocal
exit /b 0

:models_missing
echo [ERROR] Models missing or incomplete; run download_models.bat first. 1>&2
exit /b 1
