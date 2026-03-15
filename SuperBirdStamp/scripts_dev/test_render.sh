#!/usr/bin/env bash
# test_render.sh — BirdStamp CLI 渲染回归测试
#
# 用法：
#   bash scripts_dev/test_render.sh
#
# 流程：
#   对每个模板分别渲染 images/default.jpg，
#   输出到 output/ 目录，文件名采用 {stem}__{template}.{ext} 格式，
#   最后校验每个输出文件存在且大小合理。
# ---------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ── resolve Python ────────────────────────────────────────────────────────────
PYTHON="python3"
if [[ -f ".venv/bin/python3" ]]; then PYTHON=".venv/bin/python3"; fi

# ── paths ─────────────────────────────────────────────────────────────────────
INPUT="images/default.jpg"
OUT_DIR="output"
NAME_TMPL="{stem}__{template}.{ext}"
MIN_SIZE=10240   # 10 KB

if [[ ! -f "$INPUT" ]]; then
    echo "ERROR: 输入文件不存在: $INPUT" >&2
    exit 1
fi

# ── 测试模板列表 ──────────────────────────────────────────────────────────────
# 格式：<模板名或路径>
TEMPLATES=(
    "default"
    "config/templates/横版(9_16).json"
    "config/templates/竖版(9_16).json"
)

# ── clean previous output ─────────────────────────────────────────────────────
if [[ -d "$OUT_DIR" ]]; then
    echo "清理旧输出目录: $OUT_DIR"
    rm -rf "$OUT_DIR"
fi
mkdir -p "$OUT_DIR"

echo "============================================================"
echo " BirdStamp CLI 渲染回归测试"
echo "   输入   : $INPUT"
echo "   输出   : $OUT_DIR/"
echo "   命名   : $NAME_TMPL"
echo "   模板数 : ${#TEMPLATES[@]}"
echo "============================================================"

# ── run renders ───────────────────────────────────────────────────────────────
PASS=0
FAIL=0
FAIL_MSGS=()

for TPL in "${TEMPLATES[@]}"; do
    echo ""
    echo "──────────────────────────────────────────────────────────"
    echo " 模板: $TPL"

    "$PYTHON" -m birdstamp render "$INPUT" \
        --out "$OUT_DIR" \
        --template "$TPL" \
        --name "$NAME_TMPL" \
        --no-skip-existing \
        --log-level info
done

echo ""
echo "============================================================"
echo " 验证输出文件"
echo "============================================================"

# ── verify outputs ────────────────────────────────────────────────────────────
shopt -s nullglob
OUTPUT_FILES=("$OUT_DIR"/*.jpg "$OUT_DIR"/*.jpeg "$OUT_DIR"/*.png)
shopt -u nullglob

if [[ ${#OUTPUT_FILES[@]} -eq 0 ]]; then
    echo "FAIL: output/ 目录中未找到任何输出图像" >&2
    exit 1
fi

for f in "${OUTPUT_FILES[@]}"; do
    FILE_SIZE=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
    if [[ $FILE_SIZE -lt $MIN_SIZE ]]; then
        MSG="FAIL: 文件过小 (${FILE_SIZE} bytes)，可能渲染异常: $f"
        echo "$MSG"
        FAIL=$((FAIL + 1))
        FAIL_MSGS+=("$MSG")
    else
        SIZE_KB=$(( FILE_SIZE / 1024 ))
        echo "  OK  $(basename "$f")  (${SIZE_KB} KB)"
        PASS=$((PASS + 1))
    fi
done

echo ""
echo "============================================================"
if [[ $FAIL -eq 0 ]]; then
    echo " PASS  共 ${PASS} 个文件全部通过验证"
else
    echo " FAIL  通过 ${PASS}，失败 ${FAIL}"
    for msg in "${FAIL_MSGS[@]}"; do
        echo "   $msg"
    done
fi
echo "============================================================"

[[ $FAIL -eq 0 ]] || exit 1
