"""Bounded, session-only directory selection memory, independent of Qt."""
from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Sequence


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


@dataclass(frozen=True)
class DirectorySelection:
    path: str
    row: int

    def resolve(self, paths: Sequence[str], available: Callable[[str], bool]) -> str:
        """Prefer the same photo; otherwise clamp the old row and find a survivor."""
        if not paths:
            return ""
        key = _path_key(self.path)
        for path in paths:
            if _path_key(path) == key and available(path):
                return path
        # 文件被删除、过滤或重新排序后，旧索引只能作为位置提示，不能直接索引。
        row = min(max(self.row, 0), len(paths) - 1)
        for distance in range(len(paths)):
            candidates = (row,) if distance == 0 else (row + distance, row - distance)
            for index in candidates:
                if 0 <= index < len(paths) and available(paths[index]):
                    return paths[index]
        return ""


class DirectorySelectionCache:
    """Keep the most recently visited 256 directories; never retain model indexes."""

    def __init__(self, max_entries: int = 256) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, DirectorySelection] = OrderedDict()

    def remember(self, directory: str, path: str, row: int) -> None:
        if not directory or not path or row < 0:
            return
        key = _path_key(directory)
        self._entries[key] = DirectorySelection(path, row)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def get(self, directory: str) -> DirectorySelection | None:
        if not directory:
            return None
        key = _path_key(directory)
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry
