@echo off
setlocal EnableExtensions

set "ROOT_DIR=%~dp0"
if /I "%~1"=="--help" goto help
if /I "%~1"=="/?" goto help
set "CUDA_PYTHON="
if defined PYTHON_EXE if exist "%PYTHON_EXE%" set "CUDA_PYTHON=%PYTHON_EXE%"
if not defined CUDA_PYTHON if defined PYTHON_BIN if exist "%PYTHON_BIN%" set "CUDA_PYTHON=%PYTHON_BIN%"
if not defined CUDA_PYTHON if exist "%ROOT_DIR%.venv\Scripts\python.exe" set "CUDA_PYTHON=%ROOT_DIR%.venv\Scripts\python.exe"
if not defined CUDA_PYTHON (
  for /f "usebackq delims=" %%G in (`git -C "%ROOT_DIR%." rev-parse "--path-format=absolute" --git-common-dir 2^>nul`) do (
    if exist "%%~dpG.venv\Scripts\python.exe" set "CUDA_PYTHON=%%~dpG.venv\Scripts\python.exe"
  )
)
if not defined CUDA_PYTHON (
  echo [ERROR] Repository .venv not found. Run python init_dev.py first. 1>&2
  exit /b 1
)

set "PYTHON_EXE=%CUDA_PYTHON%"
set "PYTHONUTF8=1"
set "PYTHONPATH=%ROOT_DIR%build_tools\pyinstaller_bootstrap;%PYTHONPATH%"
if /I "%~1"=="--check-only" goto check_only

rem Separate CUDA outputs from the ordinary build, including its completed ZIP.
if not defined SUPERBIRDTOOLS_DIST_ROOT set "SUPERBIRDTOOLS_DIST_ROOT=%ROOT_DIR%dist\cuda"
if not defined SUPERBIRDTOOLS_BUILD_ROOT set "SUPERBIRDTOOLS_BUILD_ROOT=%ROOT_DIR%build\cuda"
rem CUDA distributions may exceed GitHub's per-asset limit; local ZIP64 is valid.
set "SUPERBIRDTOOLS_ALLOW_LARGE_PACKAGE=1"
echo [INFO] Preparing CUDA PyTorch in: %PYTHON_EXE%
"%PYTHON_EXE%" "%ROOT_DIR%build_tools\prepare_windows_cuda.py" --install
if errorlevel 1 exit /b %ERRORLEVEL%

rem CPU/CUDA changes must never reuse an incompatible PyInstaller cache.
call "%ROOT_DIR%build_all.bat" --clean %*
exit /b %ERRORLEVEL%

:check_only
"%PYTHON_EXE%" "%ROOT_DIR%build_tools\prepare_windows_cuda.py"
exit /b %ERRORLEVEL%

:help
echo Usage: build_all_cuda.bat [build_all.bat options]
echo        build_all_cuda.bat --check-only
echo Installs PyTorch 2.10.0 + torchvision 0.25.0 for CUDA 12.8 if needed.
echo Uses the shared .venv; this changes its Torch installation from CPU to CUDA.
echo Builds all applications with --clean into dist\cuda; cache: build\cuda.
echo Supports --apps-only, --bundle-models and --bundle-all-models.
echo --check-only validates the current environment without installing or building.
exit /b 0
