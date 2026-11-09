"""Viewport-only split guides; reuse the editor's crop mouse/zoom behaviour."""
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPen

from .editor_preview_canvas import EditorPreviewCanvas


class SplitPreviewCanvas(EditorPreviewCanvas):
    pageRequested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.split_count = 3
        self.split_box = None
        self.show_lines = True
        self.show_numbers = True
        self.single_view = False

    def keyPressEvent(self, event):
        if self.single_view and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.pageRequested.emit(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def _paint_composition_grid(self, painter, draw_rect, content_rect):
        # The crop editor's ordinary thirds grid has no meaning for a carousel.
        pass

    def _paint_overlays(self, painter, draw_rect, content_rect):
        super()._paint_overlays(painter, draw_rect, content_rect)
        box = self._crop_effect_box if self._crop_edit_mode else self.split_box
        if self.single_view or box is None:
            return
        l, t, r, b = box
        x, y = draw_rect.left() + l * draw_rect.width(), draw_rect.top() + t * draw_rect.height()
        w, h = (r - l) * draw_rect.width(), (b - t) * draw_rect.height()
        painter.save()
        painter.setClipRect(draw_rect.intersected(QRectF(content_rect)))
        palette = self.palette()
        # Double stroke keeps the seam visible over both bright and dark photos.
        if self.show_lines:
            for i in range(1, self.split_count):
                a, z = QPointF(x + w * i / self.split_count, y), QPointF(x + w * i / self.split_count, y + h)
                painter.setPen(QPen(palette.base().color(), 3))
                painter.drawLine(a, z)
                painter.setPen(QPen(palette.text().color(), 1, Qt.PenStyle.DashLine))
                painter.drawLine(a, z)
        if self.show_numbers:
            metrics = painter.fontMetrics()
            for i in range(self.split_count):
                label = str(i + 1)
                tw = metrics.horizontalAdvance(label) + 10
                rect = QRectF(x + w * (i + .5) / self.split_count - tw / 2, y + 6, tw, metrics.height() + 4)
                painter.fillRect(rect, palette.base())
                painter.setPen(palette.text().color())
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()
