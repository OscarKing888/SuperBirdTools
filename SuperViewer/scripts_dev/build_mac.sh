#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

DIST_ROOT="${SUPERBIRDTOOLS_DIST_ROOT:-${REPO_ROOT}/dist}"
WORK_ROOT="${SUPERBIRDTOOLS_BUILD_ROOT:-${REPO_ROOT}/build/SuperViewer}"
APP_DIR="${DIST_ROOT}/SuperViewer.app"
COLLECT_DIR="${DIST_ROOT}/SuperViewer"

PYINSTALLER_ARGS=(--noconfirm)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) PYINSTALLER_ARGS+=(--clean); shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "${PYTHON_BIN:-}" ]]; then
  SELECTED_PYTHON="${PYTHON_BIN}"
elif [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
  SELECTED_PYTHON="${REPO_ROOT}/.venv/bin/python3"
elif [[ -x "${ROOT_DIR}/.venv/bin/python3" ]]; then
  SELECTED_PYTHON="${ROOT_DIR}/.venv/bin/python3"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
  SELECTED_PYTHON="${VIRTUAL_ENV}/bin/python3"
else
  SELECTED_PYTHON="python3"
fi

"${SELECTED_PYTHON}" -m PyInstaller \
  "${PYINSTALLER_ARGS[@]}" \
  --distpath "${DIST_ROOT}" \
  --workpath "${WORK_ROOT}" \
  SuperViewer_mac.spec

if [[ -d "${COLLECT_DIR}" ]]; then
  rm -rf "${COLLECT_DIR}"
fi

echo "[OK] 打包完成: ${APP_DIR}"
