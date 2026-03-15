# -*- coding: utf-8 -*-
"""Pillow EXIF 回退：已迁至 app_common.exif_io，此处仅为兼容性 re-export。"""
from __future__ import annotations

from app_common.exif_io import extract_pillow_metadata

__all__ = ["extract_pillow_metadata"]
