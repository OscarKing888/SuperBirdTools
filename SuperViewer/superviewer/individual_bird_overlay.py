# -*- coding: utf-8 -*-
"""逐只识别悬停框，独立于鸟体开关，仅用于视口绘制。"""
from .bird_body_overlay import _valid_box
try:
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QPen
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import QColor, QPen


class IndividualBirdOverlayMixin:
    def __init__(self, *args, **kwargs):
        self._individual_highlight = None
        super().__init__(*args, **kwargs)

    def set_individual_highlight(self, box, color="#00c8ff"):
        box = _valid_box(box)
        value = (box, color) if box else None
        if value != self._individual_highlight:
            self._individual_highlight = value
            self.update()

    def _on_source_cleared(self):
        super()._on_source_cleared()
        self._individual_highlight = None

    def render_source_pixmap_with_overlays(self):
        # 悬停属于临时交互状态；导出仍包含原有焦点、构图网格和鸟体叠加。
        previous = self._individual_highlight
        try:
            self._individual_highlight = None
            return super().render_source_pixmap_with_overlays()
        finally:
            self._individual_highlight = previous

    def _paint_overlays(self, painter, draw_rect, content_rect):
        super()._paint_overlays(painter, draw_rect, content_rect)
        if self._individual_highlight is None:
            return
        box, color = self._individual_highlight
        l, t, r, b = box
        rect = QRectF(draw_rect.left() + l * draw_rect.width(), draw_rect.top() + t * draw_rect.height(),
                      (r-l) * draw_rect.width(), (b-t) * draw_rect.height())
        painter.save()
        try:
            painter.setClipRect(draw_rect.intersected(QRectF(content_rect)))
            tint = QColor(color)
            tint.setAlpha(45)
            painter.fillRect(rect, tint)
            painter.setPen(QPen(QColor(color), 3))
            painter.setBrush(getattr(Qt, "BrushStyle", Qt).NoBrush)
            painter.drawRect(rect)
        finally:
            painter.restore()
