#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quick CLI for validating Super Viewer focus extraction on a single source file.

Usage:
  .venv\\Scripts\\python.exe SuperViewer\\scripts_dev\\focus_extract_cli.py "F:\\path\\to\\file.CR3"
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from SuperViewer.superviewer.focus_preview_loader import (  # noqa: E402
    _load_focus_metadata_for_path, _load_focus_box_for_preview, _resolve_focus_calc_image_size,
)
from app_common.exif_io import close_exiftool_process  # noqa: E402
from app_common.focus_calc import extract_focus_box, resolve_focus_camera_type_from_metadata  # noqa: E402


def _print_focus_related(metadata: dict) -> None:
    print(f"metadata_keys={len(metadata)}")
    for key in sorted(metadata.keys(), key=lambda k: str(k).lower()):
        lk = str(key).lower()
        if (lk.split(":")[-1].startswith(("af", "numaf", "validaf"))
                or any(t in lk for t in ("focus", "subject", "region", "model", "make", "imagewidth", "imageheight", "orientation", "0x2027", "0x204a"))):
            value = metadata.get(key)
            sv = str(value)
            if len(sv) > 180:
                sv = sv[:180] + "..."
            print(f"  {key} = {sv}")


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Validate focus extraction pipeline for one file.")
    parser.add_argument("path", help="Source image path (RAW/HEIF/HIF/HEIC).")
    parser.add_argument("--width", type=int, default=6144, help="Fallback width for extraction.")
    parser.add_argument("--height", type=int, default=4096, help="Fallback height for extraction.")
    args = parser.parse_args()

    path = os.path.normpath(args.path)
    if not os.path.isfile(path):
        print(f"file_not_found={path}")
        return 2

    print(f"source={path}")
    print(f"fallback_size={args.width}x{args.height}")

    metadata = _load_focus_metadata_for_path(path) or {}

    _print_focus_related(metadata)
    focus_width, focus_height = _resolve_focus_calc_image_size(metadata, fallback=(args.width, args.height))
    print(f"resolved_size={focus_width}x{focus_height}")
    camera_type = resolve_focus_camera_type_from_metadata(metadata)
    print(f"camera_type={camera_type}")

    box_calc = extract_focus_box(metadata, focus_width, focus_height, camera_type=camera_type)
    print(f"focus_box_calc={box_calc}")
    box_preview = _load_focus_box_for_preview(path, args.width, args.height)
    print(f"focus_box_preview={box_preview}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_cli())
    finally:
        close_exiftool_process()
