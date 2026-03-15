@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
for %%I in ("%ROOT_DIR%..") do set "REPO_ROOT=%%~fI"
cd /d "%ROOT_DIR%"

if defined SUPERBIRDTOOLS_DIST_ROOT (
  set "DIST_ROOT=%SUPERBIRDTOOLS_DIST_ROOT%"
) else (
  set "DIST_ROOT=%REPO_ROOT%\dist"
)
if defined SUPERBIRDTOOLS_BUILD_ROOT (
  set "WORK_ROOT=%SUPERBIRDTOOLS_BUILD_ROOT%\SuperBirdStamp"
) else (
  set "WORK_ROOT=%REPO_ROOT%\build\SuperBirdStamp"
)

if exist "%ROOT_DIR%\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"
  goto :build_with_exe
)

if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
  goto :build_with_exe
)

if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
  set "PYTHON_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
  goto :build_with_exe
)

where py >nul 2>nul
if %errorlevel%==0 goto :build_with_launcher
goto :build_with_python

:build_with_exe
echo [INFO] Using Python: %PYTHON_EXE%
"%PYTHON_EXE%" -c "import PyQt6" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Selected Python does not have PyQt6: %PYTHON_EXE%
  goto :end
)
"%PYTHON_EXE%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --distpath "%DIST_ROOT%" ^
  --workpath "%WORK_ROOT%" ^
  BirdStamp_win.spec
goto :after_build

:build_with_launcher
echo [INFO] Using Python launcher: py -3
py -3 -c "import PyQt6" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python launcher py -3 does not resolve to an environment with PyQt6.
  goto :end
)
py -3 -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --distpath "%DIST_ROOT%" ^
  --workpath "%WORK_ROOT%" ^
  BirdStamp_win.spec
goto :after_build

:build_with_python
echo [INFO] Using Python: python
python -c "import PyQt6" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python does not have PyQt6: python
  goto :end
)
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --distpath "%DIST_ROOT%" ^
  --workpath "%WORK_ROOT%" ^
  BirdStamp_win.spec

:after_build
if errorlevel 1 goto :end

echo [OK] 打包完成: %DIST_ROOT%\SuperBirdStamp

set "SUFFIX=%~1"
if not "%SUFFIX%"=="" (
  set "ZIP_NAME=SuperBirdStamp%SUFFIX%.zip"
  powershell -NoProfile -Command "Compress-Archive -Path '%DIST_ROOT%\SuperBirdStamp' -DestinationPath '%DIST_ROOT%\SuperBirdStamp%SUFFIX%.zip' -Force"
  echo [OK] ZIP 已生成: %DIST_ROOT%\SuperBirdStamp%SUFFIX%.zip
)

:end
endlocal
