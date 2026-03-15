#!/usr/bin/env python3
"""
从 icons/app_icon.png (512x512) 生成：
- app_icon.ico（Windows，多分辨率）
- app_icon.icns（macOS，仅 macOS 上通过 iconutil 生成）

依赖：Pillow (pip install Pillow)。macOS 上生成 .icns 需 Xcode 命令行工具 (iconutil)。

用法：在项目根目录运行
  python scripts_dev/build_app_icon.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "icons"
PNG_PATH = RESOURCES / "app_icon.png"
ICO_PATH = RESOURCES / "app_icon.ico"
ICONSET_DIR = RESOURCES / "app_icon.iconset"
ICNS_PATH = RESOURCES / "app_icon.icns"

# Windows ICO 嵌入尺寸
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (256, 256), (512, 512)]

# macOS iconset 所需尺寸：(文件名后缀, (宽, 高))
ICONSET_ENTRIES = [
    ("icon_16x16.png", (16, 16)),
    ("icon_16x16@2x.png", (32, 32)),
    ("icon_32x32.png", (32, 32)),
    ("icon_32x32@2x.png", (64, 64)),
    ("icon_128x128.png", (128, 128)),
    ("icon_128x128@2x.png", (256, 256)),
    ("icon_256x256.png", (256, 256)),
    ("icon_256x256@2x.png", (512, 512)),
    ("icon_512x512.png", (512, 512)),
    ("icon_512x512@2x.png", (1024, 1024)),
]


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow required: pip install Pillow", file=sys.stderr)
        return 1

    if not PNG_PATH.exists():
        print(f"Missing source: {PNG_PATH}", file=sys.stderr)
        return 1

    img = Image.open(PNG_PATH)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # 统一为 512x512 再生成各尺寸
    if img.size != (512, 512):
        img = img.resize((512, 512), Image.Resampling.LANCZOS)

    # 覆盖保存 512x512 源图
    img.save(PNG_PATH, format="PNG", optimize=True)
    print(f"OK: {PNG_PATH} (512x512)")

    # Windows ICO
    img.save(ICO_PATH, format="ICO", sizes=ICO_SIZES)
    print(f"OK: {ICO_PATH}")

    # macOS .icns（仅 macOS 上生成）
    if sys.platform == "darwin":
        if shutil.which("iconutil") is None:
            print("iconutil not found, skip .icns", file=sys.stderr)
        else:
            ICONSET_DIR.mkdir(parents=True, exist_ok=True)
            try:
                for name, size in ICONSET_ENTRIES:
                    out_path = ICONSET_DIR / name
                    resized = img.resize(size, Image.Resampling.LANCZOS)
                    resized.save(out_path, format="PNG", optimize=True)
                subprocess.run(
                    ["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(ICNS_PATH)],
                    check=True,
                    cwd=str(ROOT),
                )
                print(f"OK: {ICNS_PATH}")
            finally:
                if ICONSET_DIR.exists():
                    shutil.rmtree(ICONSET_DIR)
    else:
        print("Skip .icns (run on macOS to generate)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
