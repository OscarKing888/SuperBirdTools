#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "未找到虚拟环境 Python: $VENV_PYTHON" >&2
    echo "请先在仓库根目录执行: python init_dev.py" >&2
    exit 1
fi

cd "$SCRIPT_DIR"

viewer_pid=""
stamp_pid=""

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM

    if [[ -n "$viewer_pid" ]] && kill -0 "$viewer_pid" 2>/dev/null; then
        kill "$viewer_pid" 2>/dev/null || true
    fi
    if [[ -n "$stamp_pid" ]] && kill -0 "$stamp_pid" 2>/dev/null; then
        kill "$stamp_pid" 2>/dev/null || true
    fi

    wait "$viewer_pid" 2>/dev/null || true
    wait "$stamp_pid" 2>/dev/null || true
    exit "$exit_code"
}

trap cleanup EXIT INT TERM

"$VENV_PYTHON" -m SuperViewer.entry &
viewer_pid=$!

"$VENV_PYTHON" -m SuperBirdStamp.entry &
stamp_pid=$!

wait "$viewer_pid"
wait "$stamp_pid"
