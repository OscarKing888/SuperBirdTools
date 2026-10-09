#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 只构建应用，跳过更新清单、更新分卷和所有发布 ZIP；额外的 YOLO / SAM 模型
# 仅带 yolo11l-seg.pt、sam2.1_b.pt，须预先放到 SuperViewer/models，build 不联网。
# 原有基础模型照常打包，其余参数原样传递。
exec bash "${ROOT_DIR}/build_all.sh" --apps-only --bundle-models yolo11l-seg.pt,sam2.1_b.pt "$@"
