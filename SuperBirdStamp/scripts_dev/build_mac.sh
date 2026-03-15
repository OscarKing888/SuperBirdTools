#!/usr/bin/env bash
# build_mac.sh — Build SuperBirdStamp.app for macOS using PyInstaller
#
# Usage:
#   bash scripts_dev/build_mac.sh [--clean] [--arch universal2] [--console]
#
# Options:
#   --clean            Remove dist/ and build/ before building
#   --arch universal2  Build a universal (Intel + Apple Silicon) binary
#   --console          Build with console (Terminal) so logs are visible
#
# Prerequisites (run once):
#   pip install pyinstaller pyinstaller-hooks-contrib
#
# Output:
#   dist/SuperBirdStamp.app
#   dist/SuperBirdStamp-mac.zip
# ---------------------------------------------------------------------------

set -euo pipefail

# ── locate project root (parent of this script) ──────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$PROJECT_ROOT")"
cd "$PROJECT_ROOT"

DIST_ROOT="${SUPERBIRDTOOLS_DIST_ROOT:-${REPO_ROOT}/dist}"
WORK_ROOT="${SUPERBIRDTOOLS_BUILD_ROOT:-${REPO_ROOT}/build/SuperBirdStamp}"
SPEC_BUILD_DIR="${WORK_ROOT}/spec"

# ── defaults ─────────────────────────────────────────────────────────────────
CLEAN=0
TARGET_ARCH=""   # empty = native arch
CONSOLE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)   CLEAN=1; shift ;;
        --arch)    TARGET_ARCH="$2"; shift 2 ;;
        --console) CONSOLE=1; shift ;;
        *)         echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── sanity checks ─────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null && [[ -z "${PYTHON_BIN:-}" ]]; then
    echo "ERROR: python3 not found. Activate your venv first." >&2
    exit 1
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
    PYTHON="$PYTHON_BIN"
elif [[ -x "$PROJECT_ROOT/.venv/bin/python3" ]]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python3"
elif [[ -x "$REPO_ROOT/.venv/bin/python3" ]]; then
    PYTHON="$REPO_ROOT/.venv/bin/python3"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
    PYTHON="$VIRTUAL_ENV/bin/python3"
else
    PYTHON="python3"
fi

if ! "$PYTHON" -c "import PyInstaller" 2>/dev/null; then
    echo "PyInstaller not found. Installing..."
    "$PYTHON" -m pip install pyinstaller pyinstaller-hooks-contrib
fi

APP_NAME="SuperBirdStamp"
APP_DIR="${DIST_ROOT}/${APP_NAME}.app"
ZIP_FILE="${DIST_ROOT}/${APP_NAME}-mac.zip"
COLLECT_DIR="${DIST_ROOT}/${APP_NAME}"

# ── optional clean ────────────────────────────────────────────────────────────
if [[ $CLEAN -eq 1 ]]; then
    echo "Cleaning ${APP_DIR} and ${WORK_ROOT} ..."
    rm -rf "$APP_DIR" "$ZIP_FILE" "$WORK_ROOT"
fi

# ── patch target_arch in spec if --arch was passed ───────────────────────────
SPEC_FILE="BirdStamp_mac.spec"
mkdir -p "$SPEC_BUILD_DIR"
if [[ -n "$TARGET_ARCH" ]]; then
    echo "Setting target_arch to: $TARGET_ARCH"
    SPEC_FILE="${SPEC_BUILD_DIR}/BirdStamp_mac_patched.spec"
    sed "s/target_arch=None/target_arch=\"$TARGET_ARCH\"/" BirdStamp_mac.spec > "$SPEC_FILE"
fi
if [[ $CONSOLE -eq 1 ]]; then
    echo "Building with CONSOLE (logs visible in Terminal)."
    CONSOLE_SPEC="${SPEC_BUILD_DIR}/BirdStamp_mac_console.spec"
    sed 's/console=False/console=True/' "$SPEC_FILE" > "$CONSOLE_SPEC"
    SPEC_FILE="$CONSOLE_SPEC"
fi

# ── build ─────────────────────────────────────────────────────────────────────
echo "============================================================"
echo " Building ${APP_NAME}.app (this may take several minutes) ..."
echo "============================================================"

"$PYTHON" -m PyInstaller "$SPEC_FILE" --noconfirm --distpath "$DIST_ROOT" --workpath "$WORK_ROOT"

if [[ ! -d "$APP_DIR" ]]; then
    echo "ERROR: Build failed — $APP_DIR not found." >&2
    exit 1
fi

if [[ -d "$COLLECT_DIR" ]]; then
    rm -rf "$COLLECT_DIR"
fi

# ── bundle sanity check ───────────────────────────────────────────────────────
PLIST_FILE="$APP_DIR/Contents/Info.plist"
if [[ -f "$PLIST_FILE" ]] && command -v plutil &>/dev/null; then
    if plutil -p "$PLIST_FILE" | grep -q '"LSBackgroundOnly" => 1'; then
        echo "ERROR: Build produced a background-only app (LSBackgroundOnly=1)." >&2
        echo "       Check the PyInstaller spec console/windowed setting." >&2
        exit 1
    fi
echo "Bundle sanity check PASSED (foreground GUI app)."
fi

# ── exiftool availability check (Finder-like env) ────────────────────────────
echo ""
echo "Metadata smoke test — checking exiftool discovery under app-like PATH ..."
SMOKE_PYTHONPATH="$REPO_ROOT:$PROJECT_ROOT"
if env -i HOME="$HOME" PATH="/usr/bin:/bin:/usr/sbin:/sbin" PYTHONPATH="$SMOKE_PYTHONPATH" "$PYTHON" - <<'PY'
from app_common.exif_io.exiftool_path import get_exiftool_executable_path

path = get_exiftool_executable_path()
if not path:
    raise SystemExit(1)
print(path)
PY
then
    echo "  Metadata smoke test PASSED"
else
    echo "  WARNING: Finder-like environment could not resolve exiftool."
    echo "           打包后的 app 可能无法读取焦点元数据；请安装 exiftool 到常见绝对路径。"
fi

# ── smoke test ────────────────────────────────────────────────────────────────
echo ""
echo "Smoke test — checking executable launches ..."
EXEC="$APP_DIR/Contents/MacOS/SuperBirdStamp"
if [[ -x "$EXEC" ]]; then
    timeout 10 "$EXEC" --help &>/dev/null \
        && echo "  Smoke test PASSED (--help exit 0)" \
        || echo "  Smoke test note: non-zero exit (normal for GUI-only builds)"
else
    echo "  WARNING: executable not found at $EXEC"
fi

# ── create zip ────────────────────────────────────────────────────────────────
echo ""
if [[ "${BIRDSTAMP_CREATE_ZIP:-0}" == "1" ]]; then
    echo "Creating zip: $ZIP_FILE ..."
    # ditto preserves resource forks and symlinks inside .app bundles
    if command -v ditto &>/dev/null; then
        ditto -c -k --sequesterRsrc --keepParent "$APP_DIR" "$ZIP_FILE"
    else
        (cd "$DIST_ROOT" && zip -ry "$(basename "$ZIP_FILE")" "$(basename "$APP_DIR")")
    fi
    echo "Zip created: $ZIP_FILE"
else
    echo "Zip step skipped (set BIRDSTAMP_CREATE_ZIP=1 to enable)."
fi

echo ""
echo "Done."
echo "  App : $APP_DIR"
if [[ -f "$ZIP_FILE" ]]; then
    echo "  Zip : $ZIP_FILE"
fi
echo ""
echo "To open the app:"
echo "  open $APP_DIR"
