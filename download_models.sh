#!/usr/bin/env bash
# Download every model into the workspace (SuperViewer/models) so builds,
# packaging and local runs never download: the bird sharpness YOLO / SAM
# catalog and the NAFNet denoise model, each verified by size and SHA-256.
#
#   ./download_models.sh                          # everything (~3.9 GB)
#   ./download_models.sh yolo11x-seg.pt sam2.1_b  # only these
#   ./download_models.sh --dry-run                # plan only
#   ./download_models.sh --check-only             # verify offline
#   ./download_models.sh --list                   # every available model

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Interpreter order: $PYTHON_EXE, this checkout's .venv, then the main checkout's
# .venv (feature worktrees share it instead of creating their own).
find_python() {
    if [[ -n "${PYTHON_EXE:-}" && -x "$PYTHON_EXE" ]]; then
        echo "$PYTHON_EXE"; return 0
    fi
    local roots=("$SCRIPT_DIR")
    local common_dir
    if common_dir="$(git -C "$SCRIPT_DIR" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
        roots+=("$(dirname "$common_dir")")
    fi
    local root candidate
    for root in "${roots[@]}"; do
        for candidate in "$root/.venv/bin/python3" "$root/.venv/bin/python"; do
            if [[ -x "$candidate" ]]; then
                echo "$candidate"; return 0
            fi
        done
    done
    return 1
}

if ! VENV_PYTHON="$(find_python)"; then
    echo "未找到虚拟环境 Python（.venv）" >&2
    echo "请先在仓库根目录执行: python init_dev.py" >&2
    exit 1
fi

cd "$SCRIPT_DIR"
exec "$VENV_PYTHON" "$SCRIPT_DIR/build_tools/download_models.py" "$@"
