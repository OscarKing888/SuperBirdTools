"""BirdStamp 拼音兼容入口；与 SuperViewer 共用 SuperPicky 带声调词表。"""
from app_common.bird_pinyin import pinyin_for, reset_cache

__all__ = ["pinyin_for", "reset_cache"]
