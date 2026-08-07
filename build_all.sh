#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_ROOT="${SUPERBIRDTOOLS_DIST_ROOT:-${ROOT_DIR}/dist}"
BUILD_ROOT="${SUPERBIRDTOOLS_BUILD_ROOT:-${ROOT_DIR}/build}"
CLEAN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ $CLEAN -eq 1 ]]; then
  rm -rf "$DIST_ROOT" "$BUILD_ROOT"
fi
mkdir -p "$DIST_ROOT" "$BUILD_ROOT"

export SUPERBIRDTOOLS_DIST_ROOT="$DIST_ROOT"
export SUPERBIRDTOOLS_BUILD_ROOT="$BUILD_ROOT"

echo "[build_all] dist=${DIST_ROOT}"
echo "[build_all] build=${BUILD_ROOT}"

bash "${ROOT_DIR}/SuperViewer/scripts_dev/build_mac.sh"

echo "[OK] outputs:"
echo "  ${DIST_ROOT}/SuperViewer.app"
