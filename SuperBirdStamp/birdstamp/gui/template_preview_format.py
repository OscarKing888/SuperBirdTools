"""模板管理器的临时画幅；不改变模板或导出设置。"""
from __future__ import annotations

import math


def preview_format_overridden(orientation: str, ratio: float | None) -> bool:
    return orientation in {"landscape", "portrait"} or ratio is not None


def template_preview_settings(payload: dict, source_size: tuple[int, int], *,
                              orientation: str = "template", ratio: float | None = None) -> dict:
    settings = dict(payload)
    if not preview_format_overridden(orientation, ratio):
        return settings
    source_ratio = source_size[0] / max(1, source_size[1])
    base = payload.get("ratio") if ratio is None else ratio
    try:
        value = float(base)
        if not math.isfinite(value) or value <= 0:
            value = source_ratio
    except (ValueError, TypeError):
        value = source_ratio
    if orientation == "landscape":
        value = max(value, 1 / value)
    elif orientation == "portrait":
        value = min(value, 1 / value)
    settings["ratio"] = value
    # 已保存的手动裁切框会优先于比例，试览画幅时只在副本中移除。
    settings["crop_box"] = None
    return settings
