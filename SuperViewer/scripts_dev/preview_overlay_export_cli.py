#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export one preview image with the same overlay pipeline used by PreviewCanvas.

用途：
1. 验证预览 canvas 上的焦点框是否正确；
2. 验证导出图片时是否带上当前 overlays（焦点/构图线）。

示例：
  QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/preview_overlay_export_cli.py \
    "/path/to/source.HIF" \
    "/tmp/out.png" \
    --grid thirds
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import main  # noqa: E402
from app_common.preview_canvas import PreviewCanvas, PreviewOverlayOptions, PreviewOverlayState  # noqa: E402


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Export PreviewCanvas image with overlays.")
    parser.add_argument("source", help="Source image path used for metadata/focus extraction.")
    parser.add_argument("output", help="Output image path, e.g. /tmp/out.png")
    parser.add_argument(
        "--preview",
        help="Optional preview image path. Defaults to source path.",
    )
    parser.add_argument(
        "--hide-focus",
        action="store_true",
        help="Do not render focus overlay.",
    )
    parser.add_argument(
        "--grid",
        default="none",
        help="Composition grid mode passed to PreviewCanvas.",
    )
    parser.add_argument(
        "--grid-width",
        type=int,
        default=1,
        help="Composition grid line width.",
    )
    args = parser.parse_args()

    source_path = os.path.normpath(args.source)
    preview_path = os.path.normpath(args.preview) if args.preview else source_path
    output_path = os.path.normpath(args.output)

    if not os.path.isfile(source_path):
        print(f"source_not_found={source_path}")
        return 2
    if not os.path.isfile(preview_path):
        print(f"preview_not_found={preview_path}")
        return 2

    app = main.QApplication.instance() or main.QApplication([])
    pixmap = main._load_preview_pixmap_for_canvas(preview_path)
    if pixmap is None or pixmap.isNull():
        print(f"preview_load_failed={preview_path}")
        return 3

    canvas = PreviewCanvas()
    canvas.set_source_pixmap(pixmap, reset_view=True)

    focus_box = None
    if not args.hide_focus:
        focus_box = main._load_focus_box_for_preview(source_path, pixmap.width(), pixmap.height())
    canvas.apply_overlay_state(PreviewOverlayState(focus_box=focus_box))
    canvas.apply_overlay_options(
        PreviewOverlayOptions(
            show_focus_box=not args.hide_focus,
            composition_grid_mode=args.grid,
            composition_grid_line_width=args.grid_width,
        )
    )

    rendered = canvas.render_source_pixmap_with_overlays()
    if rendered is None or rendered.isNull():
        print("render_failed")
        return 4

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    if not rendered.save(output_path):
        print(f"save_failed={output_path}")
        return 5

    print(f"source={source_path}")
    print(f"preview={preview_path}")
    print(f"output={output_path}")
    print(f"size={rendered.width()}x{rendered.height()}")
    print(f"focus_box={focus_box}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
