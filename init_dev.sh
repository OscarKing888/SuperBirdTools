#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  exec "$PYTHON_BIN" "$ROOT_DIR/init_dev.py" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$ROOT_DIR/init_dev.py" "$@"
fi

exec python "$ROOT_DIR/init_dev.py" "$@"
