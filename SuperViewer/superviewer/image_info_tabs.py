# -*- coding: utf-8 -*-
"""Compatibility exports for SuperViewer image information tabs."""
from __future__ import annotations

from .image_info_tab_base import ImageInfoTabPanel
from .image_info_tab_exif import ImageInfoTabPanel_EXIF
from .image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
from .image_info_tab_tags import ImageInfoTabPanel_Tags
from .image_info_tab_widget import ImageInfoTabWidget


__all__ = [
    "ImageInfoTabPanel",
    "ImageInfoTabPanel_EXIF",
    "ImageInfoTabPanel_ImageInfo",
    "ImageInfoTabPanel_Tags",
    "ImageInfoTabWidget",
]
