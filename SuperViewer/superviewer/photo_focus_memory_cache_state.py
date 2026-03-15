# -*- coding: utf-8 -*-
"""单张照片在某一种预览图像尺寸下的焦点缓存状态（dataclass）。"""

from __future__ import annotations

from dataclasses import dataclass

FOCUS_CACHE_STATUS_UNKNOWN = "unknown"
FOCUS_CACHE_STATUS_LOADING = "loading"
FOCUS_CACHE_STATUS_READY = "ready"
FOCUS_CACHE_STATUS_MISS = "miss"


@dataclass(slots=True)
class PhotoFocusMemoryCacheState:
    """
    单张照片在某一种预览图像尺寸下的焦点缓存。

    这里按预览图像像素尺寸分桶，而不是只按文件路径缓存，
    是为了兼容 report.db 保底路径在拿不到原始宽高时仍可能依赖当前预览尺寸。
    以后如果新增别的尺寸相关 overlay，也沿着这个结构扩展。
    """

    status: str = FOCUS_CACHE_STATUS_UNKNOWN
    focus_box: tuple[float, float, float, float] | None = None
    used_path: str = ""
    request_id: int = 0
    size_independent: bool = False
