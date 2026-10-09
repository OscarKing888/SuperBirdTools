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

    def set_individual_highlight(self, box, color="#FF0000"):
        box = _valid_box(box)
        value = (box, color) if box else None
        if value != self._individual_highlight:
            self._individual_highlight = value
            self.update()
        if value is not None:
            self._center_individual_bird()

    def _individual_center(self):
        if self._individual_highlight is None or self._source_pixmap is None:
            return None
        (left, top, right, bottom), _color = self._individual_highlight
        return ((left + right) / 2, (top + bottom) / 2)

    def _center_individual_bird(self):
        center = self._individual_center()
        if center is None:
            return
        # 仅平移，不经过缩放接口；允许边缘留白，让画面边缘的鸟也真正居中。
        self._apply_view_center_ratio(center)
        self.update()
        self.individual_centered.emit()

    def _clamp_offset(self):
        center = self._individual_center()
        if center is not None:
            # 悬停期间优先于相机焦点居中，异步预览升级/元数据刷新不能把鸟拉走。
            self._apply_view_center_ratio(center)
            return
        super()._clamp_offset()

    def set_source_pixmap(self, pixmap, **kwargs):
        if self._individual_center() is not None and pixmap is not None and not pixmap.isNull():
            # 这次悬停是用户选择的视野；后台清晰图到达不能重新适应窗口或放大鸟框。
            kwargs.update(reset_view=False, preserve_view=True, preserve_scale=False)
        super().set_source_pixmap(pixmap, **kwargs)

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
            painter.setPen(QPen(QColor(color), 3))
            painter.setBrush(getattr(Qt, "BrushStyle", Qt).NoBrush)
            painter.drawRect(rect)
        finally:
            painter.restore()
