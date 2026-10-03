#!/usr/bin/env bash
# Run the repository test suite manually with the repo-root .venv.
#
#   ./run_tests.sh                          # all tests, headless (Qt offscreen)
#   ./run_tests.sh SuperViewer/tests -k tag # any pytest arguments
#   ./run_tests.sh --show-windows SuperBirdStamp/tests/test_dejitter_tab.py
#                                           # show real Qt windows for debugging
#
# Tests are never started by the apps or run.sh; they only run when invoked.

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

args=()
show_windows=0
for arg in "$@"; do
    if [[ "$arg" == "--show-windows" ]]; then
        show_windows=1
    else
        args+=("$arg")
    fi
done

cd "$SCRIPT_DIR"
if [[ "$show_windows" == "1" ]]; then
    export SUPERBIRD_TEST_SHOW_WINDOWS=1
    unset QT_QPA_PLATFORM
else
    export QT_QPA_PLATFORM=offscreen
fi

exec "$VENV_PYTHON" -m pytest ${args[@]+"${args[@]}"}
