@echo off
rem bump-version.bat 1.0
rem build_tools\bump_version.py validates and updates app_metadata.json; the commit, tag and push
rem below are plain git commands.
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

if "%~1"=="" (
    echo Usage: bump-version.bat ^<major.minor^> [--build-number N] [--no-tag] [--no-push] [--no-commit]
    echo.
    echo Updates app_metadata.json, commits it on main, creates the annotated tag v^<major.minor^>.^<HEAD hash^>,
    echo pushes app_common main, then pushes main together with the tag to origin.
    echo --no-tag skips the tag, --no-push keeps the commit and tag local,
    echo --no-commit only updates app_metadata.json.
    exit /b 1
)

rem Prefer the repo .venv; the script needs only the standard library, so fall back to Python 3.
set "BUMP_PY="
if exist ".venv\Scripts\python.exe" set "BUMP_PY=.venv\Scripts\python.exe"
if not defined BUMP_PY where python >nul 2>&1 && set "BUMP_PY=python"
if not defined BUMP_PY where py >nul 2>&1 && set "BUMP_PY=py -3"
if not defined BUMP_PY (
    echo [ERROR] Python 3 not found. Run init_dev.py or install Python 3. 1>&2
    exit /b 1
)

set "BUMP_PLAN=%TEMP%\superbirdtools-bump-plan-%RANDOM%%RANDOM%.txt"
set "SUPERBIRDTOOLS_BUMP_ENTRY=1"
%BUMP_PY% build_tools\bump_version.py %* > "%BUMP_PLAN%"
if errorlevel 1 (
    del /q "%BUMP_PLAN%" >nul 2>&1
    exit /b 1
)
for %%K in (version commit create_tag push push_tag submodule) do set "BUMP_%%K="
for /f "usebackq tokens=1,* delims==" %%A in ("%BUMP_PLAN%") do set "BUMP_%%A=%%B"
del /q "%BUMP_PLAN%" >nul 2>&1
if not defined BUMP_version (
    echo [ERROR] build_tools\bump_version.py returned an incomplete plan. 1>&2
    exit /b 1
)
set "BUMP_PREFIX=%BUMP_version%"

rem --only commits app_metadata.json without other staged work or local runtime changes.
if "%BUMP_commit%"=="1" git commit --only -m "Bump version to %BUMP_version%" -- app_metadata.json || (
    echo [ERROR] app_metadata.json was updated, but the commit failed. Changes were kept; fix the Git error, then commit only app_metadata.json and rerun: bump-version.bat %BUMP_PREFIX% 1>&2
    exit /b 1
)

rem Resolve HEAD only after the version-prefix commit has completed.
if "%BUMP_create_tag%"=="1" goto finalize
if "%BUMP_push%"=="1" goto finalize
goto finalized
:finalize
%BUMP_PY% build_tools\bump_version.py %* --finalize > "%BUMP_PLAN%"
if errorlevel 1 (
    del /q "%BUMP_PLAN%" >nul 2>&1
    exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in ("%BUMP_PLAN%") do set "BUMP_%%A=%%B"
del /q "%BUMP_PLAN%" >nul 2>&1
:finalized
set "BUMP_TAG=v%BUMP_version%"
set "BUMP_HEAD="
for /f "usebackq delims=" %%H in (`git rev-parse HEAD`) do set "BUMP_HEAD=%%H"
if "%BUMP_create_tag%"=="1" git tag -a "%BUMP_TAG%" %BUMP_HEAD% -m "Release %BUMP_TAG%" || (
    echo [ERROR] Version commit %BUMP_HEAD% was kept, but creating tag %BUMP_TAG% failed. Fix the Git error and rerun the same version. Existing tags are never overwritten. 1>&2
    exit /b 1
)
if "%BUMP_create_tag%"=="1" echo Created annotated tag %BUMP_TAG% at %BUMP_HEAD%.

if not "%BUMP_push%"=="1" exit /b 0
rem CI must be able to fetch the app_common commit recorded by main, so push app_common main first.
if defined BUMP_submodule git -C "%BUMP_submodule%" push origin refs/heads/main:refs/heads/main || (
    echo [ERROR] The local version commit and tag were kept, but pushing %BUMP_submodule% main failed. Fix the Git error, then rerun: bump-version.bat %BUMP_PREFIX% 1>&2
    exit /b 1
)
set "BUMP_REFS=refs/heads/main:refs/heads/main"
if "%BUMP_push_tag%"=="1" set "BUMP_REFS=%BUMP_REFS% refs/tags/%BUMP_TAG%:refs/tags/%BUMP_TAG%"
rem --atomic: origin gets main and the tag together, or neither.
git push --atomic origin %BUMP_REFS% || (
    echo [ERROR] The local version commit and tag were kept, but pushing to origin failed. Fix the Git error, for example merge origin/main into main, then rerun: bump-version.bat %BUMP_PREFIX% 1>&2
    exit /b 1
)
if "%BUMP_push_tag%"=="1" (echo Pushed main and %BUMP_TAG% to origin.) else echo Pushed main to origin.
exit /b 0
