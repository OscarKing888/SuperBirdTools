#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" && -x "${PYTHON_BIN}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
    return
  fi
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
    printf '%s\n' "${VIRTUAL_ENV}/bin/python3"
    return
  fi
  if [[ -x "${SCRIPT_DIR}/.venv/bin/python3" ]]; then
    printf '%s\n' "${SCRIPT_DIR}/.venv/bin/python3"
    return
  fi
  if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
    printf '%s\n' "${SCRIPT_DIR}/.venv/bin/python"
    return
  fi
  return 1
}

if ! SELECTED_PYTHON="$(resolve_python)"; then
  echo "未找到可用 Python。请先执行: ./init_dev.sh" >&2
  echo "当前检查顺序: PYTHON_BIN, VIRTUAL_ENV, ./.venv" >&2
  exit 1
fi

export APP_COMMON_LOG_FILE="${APP_COMMON_LOG_FILE:-${SCRIPT_DIR}/logs/SuperViewer.log}"
mkdir -p "$(dirname "${APP_COMMON_LOG_FILE}")"

viewer_pid=""

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "$viewer_pid" ]] && kill -0 "$viewer_pid" 2>/dev/null; then
    kill "$viewer_pid" 2>/dev/null || true
  fi
  wait "$viewer_pid" 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

echo "[INFO] Using Python: ${SELECTED_PYTHON}"
"${SELECTED_PYTHON}" -m SuperViewer.entry &
viewer_pid=$!
wait "$viewer_pid"
