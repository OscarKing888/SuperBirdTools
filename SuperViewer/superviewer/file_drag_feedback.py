"""文件拖放的操作光标；只绘制反馈，不读取照片或修改拖放策略。"""
from __future__ import annotations

import math

try:
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QFontMetricsF, QPainter, QPen, QPixmap, QPolygonF
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QPointF, QRectF, Qt
    from PyQt5.QtGui import QFontMetricsF, QPainter, QPen, QPixmap, QPolygonF


def file_drag_cursor(action, palette, font, device_pixel_ratio=1.0):
    """箭头热点在左上角；高 DPI 和大字体下按实际文字尺寸绘制。"""
    copy = action == Qt.DropAction.CopyAction
    text = "复制" if copy else "移动"
    metrics = QFontMetricsF(font)
    height = max(24, math.ceil(metrics.height()) + 8)
    width = math.ceil(metrics.horizontalAdvance(text)) + 36
    ratio = max(1.0, device_pixel_ratio)
    pixmap = QPixmap(math.ceil((width + 20) * ratio), math.ceil((height + 12) * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(palette.window().color(), 1.5))
        painter.setBrush(palette.windowText())
        painter.drawPolygon(QPolygonF([QPointF(1, 1), QPointF(1, 22), QPointF(6, 17),
                                       QPointF(10, 26), QPointF(14, 24), QPointF(10, 15),
                                       QPointF(18, 15)]))
        rect = QRectF(20, 12, width - 1, height - 1)
        painter.setPen(QPen(palette.windowText().color(), 1))
        painter.setBrush(palette.window())
        painter.drawRoundedRect(rect, 6, 6)
        center = QPointF(34, rect.center().y())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.highlight())
        painter.drawEllipse(center, 9, 9)
        painter.setPen(QPen(palette.highlightedText().color(), 1.8))
        painter.drawLine(QPointF(29, center.y()), QPointF(39, center.y()))
        if copy:
            painter.drawLine(QPointF(34, center.y() - 5), QPointF(34, center.y() + 5))
        else:
            painter.drawPolyline(QPolygonF([QPointF(35, center.y() - 4),
                                            QPointF(39, center.y()), QPointF(35, center.y() + 4)]))
        painter.setFont(font)
        painter.setPen(palette.windowText().color())
        painter.drawText(QRectF(48, 12, width - 32, height - 1), Qt.AlignmentFlag.AlignCenter, text)
    finally:
        painter.end()
    return pixmap
