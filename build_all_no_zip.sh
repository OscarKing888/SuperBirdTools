#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 只构建应用，跳过更新清单、更新分卷和所有发布 ZIP；其余参数原样传递。
exec bash "${ROOT_DIR}/build_all.sh" --apps-only "$@"
