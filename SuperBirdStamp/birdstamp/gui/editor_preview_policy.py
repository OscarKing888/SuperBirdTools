"""Single-selection preview thresholds shared in behavior with SuperViewer."""
from __future__ import annotations

import os
from pathlib import Path

from app_common.image_formats import HEIF_EXTENSIONS, RAW_EXTENSIONS
from birdstamp.decoders.image_decoder import read_decoded_image_size


def _max_pixels(path: Path) -> int:
    name = ("SuperViewer_SYNC_FULL_PREVIEW_HEIF_MAX_MP"
            if path.suffix.lower() in HEIF_EXTENSIONS else "SuperViewer_SYNC_FULL_PREVIEW_MAX_MP")
    default = 4.0 if path.suffix.lower() in HEIF_EXTENSIONS else 40.0
    try:
        megapixels = max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        megapixels = default
    return int(megapixels * 1_000_000)


def load_full_synchronously(path: Path) -> bool:
    if path.suffix.lower() in RAW_EXTENSIONS:
        return False
    limit = _max_pixels(path)
    if limit <= 0:
        return False
    try:
        width, height = read_decoded_image_size(path)
    except Exception:
        return False
    return 0 < width * height <= limit
