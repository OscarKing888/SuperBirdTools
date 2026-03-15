#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_ROOT="${SUPERBIRDTOOLS_DIST_ROOT:-${ROOT_DIR}/dist}"
BUILD_ROOT="${SUPERBIRDTOOLS_BUILD_ROOT:-${ROOT_DIR}/build}"
CLEAN=0
SKIP_DEDUPE=0
TARGET_ARCH=""
CONSOLE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=1; shift ;;
    --skip-dedupe) SKIP_DEDUPE=1; shift ;;
    --arch) TARGET_ARCH="${2:-}"; shift 2 ;;
    --console) CONSOLE=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ $CLEAN -eq 1 ]]; then
  rm -rf "$DIST_ROOT" "$BUILD_ROOT"
fi
mkdir -p "$DIST_ROOT" "$BUILD_ROOT"

export SUPERBIRDTOOLS_DIST_ROOT="$DIST_ROOT"
export SUPERBIRDTOOLS_BUILD_ROOT="$BUILD_ROOT"

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

echo "[build_all] dist=${DIST_ROOT}"
echo "[build_all] build=${BUILD_ROOT}"

bash "${ROOT_DIR}/scripts/build_superviewer_mac.sh"

BIRD_ARGS=()
if [[ -n "$TARGET_ARCH" ]]; then
  BIRD_ARGS+=(--arch "$TARGET_ARCH")
fi
if [[ $CONSOLE -eq 1 ]]; then
  BIRD_ARGS+=(--console)
fi
if [[ ${#BIRD_ARGS[@]} -gt 0 ]]; then
  bash "${ROOT_DIR}/scripts/build_superbirdstamp_mac.sh" "${BIRD_ARGS[@]}"
else
  bash "${ROOT_DIR}/scripts/build_superbirdstamp_mac.sh"
fi

if [[ $SKIP_DEDUPE -eq 0 ]]; then
  "$BUILD_PYTHON" "${ROOT_DIR}/build_tools/hardlink_dedupe.py" \
    "${DIST_ROOT}/SuperViewer.app" \
    "${DIST_ROOT}/SuperBirdStamp.app"
fi

echo "[OK] outputs:"
echo "  ${DIST_ROOT}/SuperViewer.app"
echo "  ${DIST_ROOT}/SuperBirdStamp.app"
