# -*- coding: utf-8 -*-
"""单张照片的预览期内内存缓存（焦点、构图辅助线等）。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from .photo_focus_memory_cache_state import (
    FOCUS_CACHE_STATUS_MISS,
    FOCUS_CACHE_STATUS_READY,
    PhotoFocusMemoryCacheState,
)

PHOTO_PREVIEW_FOCUS_SIZE_VARIANT_LIMIT = 4


@dataclass(slots=True)
class PhotoPreviewMemoryEntry:
    """
    单张照片的预览期内内存缓存。

    焦点、构图辅助线、检测框等和“当前预览体验”强相关的内存态，
    统一收敛到这里，避免未来把缓存散落成多个平行 dict。
    这样后续 AI Coding 工具扩展新 overlay 时，只需要沿着这个 entry 加字段。
    """

    source_path: str
    preview_path: str = ""
    focus_source_path: str = ""
    focus_states_by_preview_size: "OrderedDict[tuple[int, int], PhotoFocusMemoryCacheState]" = field(
        default_factory=OrderedDict
    )

    def get_or_create_focus_state(self, size: tuple[int, int]) -> PhotoFocusMemoryCacheState:
        size_key = (max(0, int(size[0])), max(0, int(size[1])))
        state = self.focus_states_by_preview_size.get(size_key)
        if state is None:
            state = PhotoFocusMemoryCacheState()
            self.focus_states_by_preview_size[size_key] = state
        else:
            self.focus_states_by_preview_size.move_to_end(size_key)
        while len(self.focus_states_by_preview_size) > PHOTO_PREVIEW_FOCUS_SIZE_VARIANT_LIMIT:
            self.focus_states_by_preview_size.popitem(last=False)
        return state

    def find_reusable_focus_state(self) -> PhotoFocusMemoryCacheState | None:
        for size_key, state in reversed(list(self.focus_states_by_preview_size.items())):
            if not state.size_independent:
                continue
            if state.status not in (FOCUS_CACHE_STATUS_READY, FOCUS_CACHE_STATUS_MISS):
                continue
            self.focus_states_by_preview_size.move_to_end(size_key)
            return state
        return None
