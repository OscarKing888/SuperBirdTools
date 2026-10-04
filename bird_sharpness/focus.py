"""Camera focus box lookup and the "at least 128 x 128" measurement window.

A focus provider is ``callable(path, display_width, display_height) -> box | None``
returning a normalised ``(left, top, right, bottom)`` box in display-oriented
camera-frame coordinates (the frame SuperViewer draws focus boxes in). Apps can
inject their own provider; :func:`default_focus_box` uses only ``app_common``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Tuple

from app_common.log import get_logger

_log = get_logger("bird_sharpness")

FOCUS_MIN_SIDE = 128

Box = Tuple[float, float, float, float]
FocusProvider = Callable[[str, int, int], Optional[Box]]
ManualFocusProvider = Callable[[str], Optional[bool]]  # path -> manual focus? (None = unknown)


def _focus_metadata(path: str) -> Optional[dict]:
    """Focus-related metadata: RAW maker notes first, else EXIF/XMP; ``None`` when absent."""
    from app_common.image_formats import RAW_EXTENSIONS

    raw = None
    if Path(path).suffix.lower() in RAW_EXTENSIONS:
        from app_common.raw_focus_metadata import read_raw_embedded_focus_metadata

        raw = read_raw_embedded_focus_metadata(path) or None
    if raw is None:
        from app_common.exif_io import extract_metadata_with_xmp_priority

        raw = extract_metadata_with_xmp_priority(Path(path), mode="auto")
    return raw if isinstance(raw, dict) and raw else None


def default_focus_box(path: str, width: int, height: int) -> Optional[Box]:
    """Focus box from file metadata (RAW maker notes or EXIF/XMP); ``None`` when absent."""
    from app_common.focus_calc import (
        extract_focus_box_for_display,
        resolve_focus_camera_type_from_metadata,
    )

    try:
        raw = _focus_metadata(path)
        if raw is None:
            return None
        camera_type = resolve_focus_camera_type_from_metadata(raw)
        return extract_focus_box_for_display(raw, width, height, camera_type=camera_type)
    except Exception as exc:
        _log.debug("[BirdSharpness] focus metadata unavailable path=%r: %s", path, exc)
        return None


def is_manual_focus(meta: dict) -> Optional[bool]:
    """Whether the camera's ``FocusMode`` tag says manual focus; ``None`` when no such tag.

    Text values (ExifTool, and makers exifread decodes to text) count when they say
    "manual" or "MF" (Sony "DMF" is autofocus with manual touch-up: not manual).
    Raw numbers are only interpreted for Sony (MakerNote 0x201B: 0 = Manual,
    2 = AF-S, 3 = AF-C, 4 = AF-A, 6 = DMF), checked against ExifTool on ILCE-1M2 files.
    """
    make = str(meta.get("Make") or meta.get("Image Make") or "").strip().upper()
    seen = False
    for key, value in meta.items():
        name = str(key).lower().replace(" ", "").replace(":", "")
        if not name.endswith("focusmode"):  # FocusMode itself, not FocusMode2/3 or AF area modes
            continue
        seen = True
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if isinstance(item, str):
                text = item.strip().lower()
                if "manual" in text or text == "mf":
                    return True
            elif isinstance(item, int) and not isinstance(item, bool) and make.startswith("SONY") and item == 0:
                return True
    return False if seen else None


def default_manual_focus(path: str) -> Optional[bool]:
    """Manual focus from file metadata (see :func:`is_manual_focus`); ``None`` when unknown."""
    try:
        raw = _focus_metadata(path)
        return None if raw is None else is_manual_focus(raw)
    except Exception as exc:
        _log.debug("[BirdSharpness] focus mode unavailable path=%r: %s", path, exc)
        return None


def focus_window(box_px: Tuple[float, float, float, float], image_w: int, image_h: int,
                 min_side: int = FOCUS_MIN_SIDE) -> Tuple[int, int, int, int]:
    """Pixel window to measure for a focus box given in image pixels.

    A focus box no larger than ``min_side`` on both sides becomes a
    ``min_side`` x ``min_side`` window centred on it; a box larger on either side
    is used as is, with a too-short side widened to ``min_side`` so the window
    still holds enough edges. The window is shifted (not shrunk) to stay inside
    the image whenever the image is large enough.
    """
    x1, y1, x2, y2 = box_px
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    bw, bh = max(0.0, x2 - x1), max(0.0, y2 - y1)
    if bw <= min_side and bh <= min_side:
        w = h = float(min_side)
    else:
        w, h = max(bw, float(min_side)), max(bh, float(min_side))

    def span(center: float, size: float, limit: int) -> Tuple[int, int]:
        size = min(size, float(limit))
        start = int(round(center - size / 2.0))
        start = max(0, min(start, limit - int(round(size))))
        return start, min(limit, start + int(round(size)))

    left, right = span(cx, w, image_w)
    top, bottom = span(cy, h, image_h)
    return left, top, right, bottom
