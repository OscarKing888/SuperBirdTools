#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 只构建应用，跳过更新清单、更新分卷和所有发布 ZIP，并把全部鸟清晰度模型（YOLO / SAM，
# 约 3.45 GB）打进 SuperViewer；模型须先用 ./download_models.sh 下载到 SuperViewer/models，
# build 不联网。其余参数原样传递。
exec bash "${ROOT_DIR}/build_all.sh" --apps-only --bundle-all-models "$@"
