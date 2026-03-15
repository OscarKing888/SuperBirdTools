#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  SELECTED_PYTHON="${PYTHON_BIN}"
elif [[ -x "${ROOT_DIR}/.venv/bin/python3" ]]; then
  SELECTED_PYTHON="${ROOT_DIR}/.venv/bin/python3"
elif [[ -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
  SELECTED_PYTHON="${REPO_ROOT}/.venv/bin/python3"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
  SELECTED_PYTHON="${VIRTUAL_ENV}/bin/python3"
else
  SELECTED_PYTHON="python3"
fi

"${SELECTED_PYTHON}" -m PyInstaller \
  --noconfirm \
  --clean \
  SuperViewer_mac.spec

echo "[OK] 打包完成: dist/SuperViewer.app"
