"""Geometry for the video aspect guide shown over an editor preview."""

from __future__ import annotations

from math import isfinite


def inscribed_safe_frame(
    bounds: tuple[float, float, float, float],
    frame_size: tuple[int, int],
) -> tuple[float, float, float, float] | None:
    """Center the selected video aspect inside the visible crop rectangle."""
    x, y, width, height = bounds
    frame_width, frame_height = frame_size
    if (not all(isfinite(value) for value in bounds) or width <= 0 or height <= 0
            or frame_width <= 0 or frame_height <= 0):
        return None

    ratio = frame_width / frame_height
    safe_width = min(width, height * ratio)
    safe_height = min(height, width / ratio)
    return (x + (width - safe_width) / 2, y + (height - safe_height) / 2,
            safe_width, safe_height)
