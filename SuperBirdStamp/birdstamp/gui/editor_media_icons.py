"""Palette-colored Qt media icons for the editor's playback controls."""

from PyQt6.QtGui import QColor, QIcon, QPainter
from PyQt6.QtWidgets import QStyle, QToolButton


def media_icon(button: QToolButton, kind: QStyle.StandardPixmap, color: QColor) -> QIcon:
    pixmap = button.style().standardIcon(kind).pixmap(button.iconSize())
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color)
    painter.end()
    return QIcon(pixmap)
