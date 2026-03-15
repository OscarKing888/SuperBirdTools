# -*- coding: utf-8 -*-
"""ExifTool 批量读取：已迁至 app_common.exif_io，此处仅为兼容性 re-export。"""
from __future__ import annotations

from app_common.exif_io import get_exiftool_executable_path
from app_common.exif_io import extract_many


def is_exiftool_available() -> bool:
    return bool(get_exiftool_executable_path())


__all__ = ["extract_many", "is_exiftool_available"]
