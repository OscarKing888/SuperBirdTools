"""中文鸟名的带声调拼音查询，移植自 SuperPicky tools/pinyin_names.py。

使用随包发布的 config/pinyin_toned.json，保留鸟类多音字校正；运行时无需
pypinyin、Qt 或网络。表只加载一次，未知鸟名返回空串，不猜测读音。
"""

from __future__ import annotations

import json
import logging
import threading

from birdstamp.config import resolve_bundled_path

_table: dict[str, str] | None = None
_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def _load_table() -> dict[str, str]:
    path = resolve_bundled_path("config", "pinyin_toned.json")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("expected a bird-name dictionary")
    except (OSError, ValueError) as exc:
        _logger.warning("Bird-name pinyin lookup unavailable (%s): %s", path, exc)
        return {}
    return {name: value.strip() for name, value in data.items() if isinstance(value, str)}


def reset_cache() -> None:
    """丢弃只读表缓存，供测试隔离或更新资源后重新加载。"""
    global _table
    with _lock:
        _table = None


def pinyin_for(chinese_name: str | None) -> str:
    """按完整中文鸟名查询带声调拼音；空鸟名或未收录的名称返回空串。"""
    name = (chinese_name or "").strip()
    if not name:
        return ""
    global _table
    if _table is None:
        with _lock:
            if _table is None:
                _table = _load_table()
    return _table.get(name, "")
