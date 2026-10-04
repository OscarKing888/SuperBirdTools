#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_ROOT="${SUPERBIRDTOOLS_DIST_ROOT:-${ROOT_DIR}/dist}"
BUILD_ROOT="${SUPERBIRDTOOLS_BUILD_ROOT:-${ROOT_DIR}/build}"
CLEAN=0
APPS_ONLY=0
SKIP_DEDUPE=0
TARGET_ARCH=""
CONSOLE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=1; shift ;;
    --apps-only) APPS_ONLY=1; shift ;;
    --skip-dedupe) SKIP_DEDUPE=1; shift ;;
    --arch) TARGET_ARCH="${2:-}"; shift 2 ;;
    --console) CONSOLE=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ $CLEAN -eq 1 ]]; then
  echo "[build_all] Clean build requested; removing local build outputs."
  rm -rf "$DIST_ROOT" "$BUILD_ROOT"
else
  echo "[build_all] Incremental build cache enabled: ${BUILD_ROOT}"
fi
mkdir -p "$DIST_ROOT" "$BUILD_ROOT"

export SUPERBIRDTOOLS_DIST_ROOT="$DIST_ROOT"
export SUPERBIRDTOOLS_BUILD_ROOT="$BUILD_ROOT"
if [[ $APPS_ONLY -eq 1 ]]; then
  # 本地应用验证不生成任何 ZIP，包括子脚本的可选单应用包。
  export BIRDSTAMP_CREATE_ZIP=0
fi

resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
    return
  fi
  if [[ -x "${ROOT_DIR}/.venv/bin/python3" ]]; then
    printf '%s\n' "${ROOT_DIR}/.venv/bin/python3"
    return
  fi
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
    printf '%s\n' "${VIRTUAL_ENV}/bin/python3"
    return
  fi
  printf '%s\n' "python3"
}

BUILD_PYTHON="$(resolve_python)"
# Keep both app builders and post-processing on the same interpreter.
export PYTHON_BIN="$BUILD_PYTHON"
if [[ -n "$TARGET_ARCH" ]]; then
  export SUPERBIRDTOOLS_TARGET_ARCH="$TARGET_ARCH"
fi

echo "[build_all] python=${BUILD_PYTHON}"
echo "[build_all] dist=${DIST_ROOT}"
echo "[build_all] build=${BUILD_ROOT}"

"$BUILD_PYTHON" "$ROOT_DIR/build_tools/set_build_version.py"

# Clear PyInstaller's shared native-library cache only once, before Viewer.
# BirdStamp can then reuse libraries processed by Viewer in this same build.
if [[ $CLEAN -eq 1 ]]; then
  bash "${ROOT_DIR}/SuperViewer/scripts_dev/build_mac.sh" --clean
else
  bash "${ROOT_DIR}/SuperViewer/scripts_dev/build_mac.sh"
fi

BIRD_ARGS=()
if [[ -n "$TARGET_ARCH" ]]; then
  BIRD_ARGS+=(--arch "$TARGET_ARCH")
fi
if [[ $CONSOLE -eq 1 ]]; then
  BIRD_ARGS+=(--console)
fi
if [[ ${#BIRD_ARGS[@]} -gt 0 ]]; then
  bash "${ROOT_DIR}/SuperBirdStamp/scripts_dev/build_mac.sh" "${BIRD_ARGS[@]}"
else
  bash "${ROOT_DIR}/SuperBirdStamp/scripts_dev/build_mac.sh"
fi

"$BUILD_PYTHON" -m PyInstaller --noconfirm \
  --distpath "$DIST_ROOT" --workpath "$BUILD_ROOT/SuperBirdUpdater" \
  "$ROOT_DIR/SuperBirdUpdater/SuperBirdUpdater.spec"
# 兼容旧构建留下的 COLLECT 目录；macOS spec 现在直接生成 BUNDLE。
if [[ -d "$DIST_ROOT/SuperBirdUpdater.app" && -d "$DIST_ROOT/SuperBirdUpdater" ]]; then
  rm -rf "$DIST_ROOT/SuperBirdUpdater"
fi

if [[ $SKIP_DEDUPE -eq 0 ]]; then
  "$BUILD_PYTHON" "${ROOT_DIR}/build_tools/hardlink_dedupe.py" \
    "${DIST_ROOT}/SuperViewer.app" \
    "${DIST_ROOT}/SuperBirdStamp.app"
fi

if [[ $APPS_ONLY -eq 0 ]]; then
  MANIFEST_ARGS=(--dist "$DIST_ROOT" --package)
  if [[ -n "$TARGET_ARCH" ]]; then
    MANIFEST_ARGS+=(--arch "$TARGET_ARCH")
  fi
  "$BUILD_PYTHON" "$ROOT_DIR/build_tools/generate_update_manifest.py" "${MANIFEST_ARGS[@]}"
else
  echo "[build_all] Apps only: skipping update manifests and release ZIPs; existing release artifacts are not refreshed."
fi

echo "[OK] outputs:"
echo "  ${DIST_ROOT}/SuperViewer.app"
echo "  ${DIST_ROOT}/SuperBirdStamp.app"
echo "  ${DIST_ROOT}/SuperBirdUpdater.app"
if [[ $APPS_ONLY -eq 0 ]]; then
  echo "  ${DIST_ROOT}/updates/"
fi
