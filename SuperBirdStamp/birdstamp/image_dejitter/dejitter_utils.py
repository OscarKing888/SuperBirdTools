from __future__ import annotations

from typing import Any, Sequence

import numpy as np


def median_float(values: Sequence[float]) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) * 0.5


def clamp_percent(value: Any, default: int = 0) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = int(default)
    return max(0, min(100, parsed))


def to_float_gray(patch: np.ndarray) -> np.ndarray | None:
    if patch is None:
        return None
    arr = np.asarray(patch, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr.mean(axis=2)
    if arr.ndim != 2 or arr.size == 0:
        return None
    return arr


def hann_window_2d(height: int, width: int) -> np.ndarray:
    if height <= 1:
        wy = np.ones(max(1, height), dtype=np.float64)
    else:
        wy = np.hanning(height)
    if width <= 1:
        wx = np.ones(max(1, width), dtype=np.float64)
    else:
        wx = np.hanning(width)
    return np.outer(wy, wx)
