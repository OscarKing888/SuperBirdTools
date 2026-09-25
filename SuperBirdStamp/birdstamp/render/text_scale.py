"""逐图文本缩放参数；预览、导出和 CLI 共用同一范围。"""
from __future__ import annotations

import math
from typing import Any

TEXT_SCALE_DEFAULT = 1.0
TEXT_SCALE_MIN = 0.25
TEXT_SCALE_MAX = 3.0


def normalize_text_scale(value: Any) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError, OverflowError):
        return TEXT_SCALE_DEFAULT
    if not math.isfinite(scale):
        return TEXT_SCALE_DEFAULT
    return max(TEXT_SCALE_MIN, min(TEXT_SCALE_MAX, scale))
