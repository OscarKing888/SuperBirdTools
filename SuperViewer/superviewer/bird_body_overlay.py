# -*- coding: utf-8 -*-
"""Viewer 鸟体叠加绘制；检测与坐标转换由 controller / PreviewPanel 负责。"""
from __future__ import annotations

import math

try:
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QPen
except ImportError:
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import QColor, QPen


def _valid_box(box):
    try:
        value = tuple(float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if len(value) != 4 or not all(math.isfinite(v) for v in value) or value[0] >= value[2] or value[1] >= value[3]:
        return None
    return value


def bird_overlay_boxes(value) -> tuple:
    """One box ``(l, t, r, b)`` or several ``((l, t, r, b), ...)`` (main bird first) -> tuple of boxes."""
    if value is None:
        return ()
    try:
        several = len(value) > 0 and not isinstance(value[0], (int, float))
    except TypeError:
        return ()
    boxes = (_valid_box(b) for b in value) if several else (_valid_box(value),)
    return tuple(b for b in boxes if b is not None)


def map_bird_overlay(value, crop, mapper):
    """Map a single box or every box of a flock with ``mapper(box, crop)`` (camera -> pixels)."""
    boxes = bird_overlay_boxes(value)
    if not boxes:
        return None
    mapped = tuple(m for m in (mapper(b, crop) for b in boxes) if m is not None)
    if not mapped:
        return None
    return mapped[0] if len(boxes) == 1 else mapped


class BirdBodyOverlayMixin:
    """在共享 PreviewCanvas 的绘制扩展点上叠加与 BirdStamp 一致的蓝色鸟体框。

    鸟群时画出每只鸟的框，主鸟（置信度 × 面积最大，排在第一）加粗；只有一只鸟时与原来完全一样。
    """

    def __init__(self, *args, **kwargs):
        self._bird_box = None
        self._show_bird_box = False
        super().__init__(*args, **kwargs)

    def set_bird_box(self, box) -> None:
        boxes = bird_overlay_boxes(box)
        value = None if not boxes else (boxes[0] if len(boxes) == 1 else boxes)
        if value != self._bird_box:
            self._bird_box = value
            self.update()

    def set_show_bird_box(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled != self._show_bird_box:
            self._show_bird_box = enabled
            self.update()

    def _on_source_cleared(self) -> None:
        super()._on_source_cleared()
        self._bird_box = None

    def _paint_overlays(self, painter, draw_rect, content_rect) -> None:
        super()._paint_overlays(painter, draw_rect, content_rect)
        boxes = bird_overlay_boxes(self._bird_box) if self._show_bird_box else ()
        if not boxes:
            return
        flock = len(boxes) > 1
        painter.save()
        try:
            # Others first so the main bird's frame stays on top.
            for index in range(len(boxes) - 1, -1, -1):
                left, top, right, bottom = boxes[index]
                rect = QRectF(
                    draw_rect.left() + left * draw_rect.width(),
                    draw_rect.top() + top * draw_rect.height(),
                    (right - left) * draw_rect.width(),
                    (bottom - top) * draw_rect.height(),
                ).intersected(draw_rect).intersected(QRectF(content_rect))
                if rect.width() < 1.0 or rect.height() < 1.0:
                    continue
                main = index == 0
                fill = QColor("#A9DBFF")
                fill.setAlpha(96 if main else 56)
                painter.fillRect(rect, fill)
                pen = QPen(QColor("#8BCBFF") if main or not flock else QColor(139, 203, 255, 200))
                pen.setWidth(2 if main and flock else 1)
                pen.setJoinStyle(getattr(Qt, "PenJoinStyle", Qt).MiterJoin)
                painter.setBrush(getattr(Qt, "BrushStyle", Qt).NoBrush)
                painter.setPen(pen)
                painter.drawRect(rect)
        finally:
            painter.restore()
