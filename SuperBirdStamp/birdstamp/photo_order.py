"""照片列表手动排序；只计算位置，不依赖 Qt 或文件系统。"""
from __future__ import annotations

from collections.abc import Iterable


def moved_photo_rows(count: int, selected_rows: Iterable[int], direction: int) -> list[int]:
    """选中行向上/下移动一步；连续选择保持整体，边界处不循环。"""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    rows = list(range(count))
    selected = set(selected_rows)
    candidates = range(count) if direction < 0 else range(count - 1, -1, -1)
    for row in candidates:
        neighbor = row + direction
        if (rows[row] in selected and 0 <= neighbor < count
                and rows[neighbor] not in selected):
            rows[row], rows[neighbor] = rows[neighbor], rows[row]
    return rows
