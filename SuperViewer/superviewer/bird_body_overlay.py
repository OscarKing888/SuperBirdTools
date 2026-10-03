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


class BirdBodyOverlayMixin:
    """在共享 PreviewCanvas 的绘制扩展点上叠加与 BirdStamp 一致的蓝色鸟体框。"""

    def __init__(self, *args, **kwargs):
        self._bird_box = None
        self._show_bird_box = False
        super().__init__(*args, **kwargs)

    def set_bird_box(self, box) -> None:
        try:
            value = tuple(float(v) for v in box) if box is not None else None
            if value is not None and (
                len(value) != 4 or not all(math.isfinite(v) for v in value)
                or value[0] >= value[2] or value[1] >= value[3]
            ):
                value = None
        except (TypeError, ValueError):
            value = None
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
        if not self._show_bird_box or self._bird_box is None:
            return
        left, top, right, bottom = self._bird_box
        rect = QRectF(
            draw_rect.left() + left * draw_rect.width(),
            draw_rect.top() + top * draw_rect.height(),
            (right - left) * draw_rect.width(),
            (bottom - top) * draw_rect.height(),
        ).intersected(draw_rect).intersected(QRectF(content_rect))
        if rect.width() < 1.0 or rect.height() < 1.0:
            return
        painter.save()
        try:
            fill = QColor("#A9DBFF")
            fill.setAlpha(96)
            painter.fillRect(rect, fill)
            pen = QPen(QColor("#8BCBFF"))
            pen.setWidth(1)
            pen.setJoinStyle(getattr(Qt, "PenJoinStyle", Qt).MiterJoin)
            painter.setBrush(getattr(Qt, "BrushStyle", Qt).NoBrush)
            painter.setPen(pen)
            painter.drawRect(rect)
        finally:
            painter.restore()
