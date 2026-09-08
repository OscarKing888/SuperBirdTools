"""Tag history actions that leave native text editing shortcuts available."""
from __future__ import annotations

import math

try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtGui import QKeySequence, QPainterPath
    from PyQt6.QtWidgets import QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit, QToolBar
except ImportError:
    from PyQt5.QtCore import QObject
    from PyQt5.QtGui import QKeySequence, QPainterPath
    from PyQt5.QtWidgets import QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit, QToolBar

from . import qt_compat
from .qt_compat import (
    QAction, QApplication, QBrush, QColor, QIcon, QPainter, QPen,
    QPixmap, QPoint, QPolygon, QSize,
)


def _build_history_icon(*, redo: bool = False) -> QIcon:
    """Office 风格弯箭头：撤销=向左弯箭（蓝），重做=向右弯箭（绿）。"""
    logical = 20
    dpr = 2
    pixel = logical * dpr
    pixmap = QPixmap(pixel, pixel)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    except Exception:
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)  # type: ignore[attr-defined]
        except Exception:
            pass
    painter.scale(dpr, dpr)
    # Base stroke is a left-pointing undo arrow; redo mirrors it to the right.
    if redo:
        painter.translate(logical, 0)
        painter.scale(-1, 1)

    color = QColor(16, 124, 16) if redo else QColor(0, 120, 212)  # green / Office blue
    pen = QPen(color, 2.1)
    round_cap = getattr(getattr(qt_compat.Qt, "PenCapStyle", qt_compat.Qt), "RoundCap", None)
    round_join = getattr(getattr(qt_compat.Qt, "PenJoinStyle", qt_compat.Qt), "RoundJoin", None)
    if round_cap is None:
        round_cap = getattr(qt_compat.Qt, "RoundCap", None)
    if round_join is None:
        round_join = getattr(qt_compat.Qt, "RoundJoin", None)
    if round_cap is not None:
        pen.setCapStyle(round_cap)
    if round_join is not None:
        pen.setJoinStyle(round_join)
    painter.setPen(pen)
    no_brush = getattr(getattr(qt_compat.Qt, "BrushStyle", qt_compat.Qt), "NoBrush", None)
    if no_brush is None:
        no_brush = getattr(qt_compat.Qt, "NoBrush", 0)
    painter.setBrush(no_brush)

    # Arc: tip at ~132° (upper-left). 0°=east, positive=CCW.
    cx = logical / 2.0
    cy = logical / 2.0 + 0.4
    radius = 5.8
    tip_deg = 132.0
    span_deg = -245.0  # clockwise body so the free tip faces left for undo
    path = QPainterPath()
    path.arcMoveTo(cx - radius, cy - radius, radius * 2, radius * 2, tip_deg)
    path.arcTo(cx - radius, cy - radius, radius * 2, radius * 2, tip_deg, span_deg)
    painter.drawPath(path)

    tip_rad = math.radians(tip_deg)
    tip_x = cx + radius * math.cos(tip_rad)
    tip_y = cy - radius * math.sin(tip_rad)
    # Arrowhead points left along the undo tip.
    heading = math.pi  # left
    length = 4.4
    width = 3.5
    back_x = tip_x - length * math.cos(heading)
    back_y = tip_y + length * math.sin(heading)
    ortho = heading + math.pi / 2.0
    left = QPoint(
        int(round(back_x + width * math.cos(ortho))),
        int(round(back_y - width * math.sin(ortho))),
    )
    right = QPoint(
        int(round(back_x - width * math.cos(ortho))),
        int(round(back_y + width * math.sin(ortho))),
    )
    tip = QPoint(int(round(tip_x - 0.6)), int(round(tip_y)))
    painter.setBrush(QBrush(color))
    painter.setPen(QPen(color, 1.0))
    painter.drawPolygon(QPolygon([tip, left, right]))
    painter.end()

    pixmap.setDevicePixelRatio(dpr)
    icon = QIcon()
    icon.addPixmap(pixmap)
    return icon


class TagHistoryActions(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self._panel = None
        self.undo_action = QAction("撤销标签", window)
        self.redo_action = QAction("重做标签", window)
        self.undo_action.setIcon(_build_history_icon())
        self.redo_action.setIcon(_build_history_icon(redo=True))
        self.undo_action.setStatusTip("撤销上一次标签修改")
        self.redo_action.setStatusTip("重做标签修改")
        standard = getattr(QKeySequence, "StandardKey", QKeySequence)
        self._undo_keys = QKeySequence.keyBindings(standard.Undo)
        self._redo_keys = QKeySequence.keyBindings(standard.Redo)
        self.undo_action.triggered.connect(self._undo)
        self.redo_action.triggered.connect(self._redo)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self.refresh()

    def set_panel(self, panel) -> None:
        if panel is self._panel:
            return
        if self._panel is not None:
            self._panel.command_history_changed.disconnect(self.refresh)
        self._panel = panel
        if panel is not None:
            panel.command_history_changed.connect(self.refresh)
        self.refresh()

    def add_to_menu(self, menu) -> None:
        menu.addAction(self.undo_action)
        menu.addAction(self.redo_action)

    def create_toolbar(self, window) -> QToolBar:
        """Share the menu actions so history state and shortcuts have one owner."""
        toolbar = QToolBar("编辑", window)
        toolbar.setObjectName("editToolBar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        styles = getattr(qt_compat.Qt, "ToolButtonStyle", qt_compat.Qt)
        toolbar.setToolButtonStyle(styles.ToolButtonIconOnly)
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)
        return toolbar

    @staticmethod
    def _is_text_editor(widget) -> bool:
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return True
            widget = widget.parentWidget()
        return False

    def refresh(self) -> None:
        panel = self._panel
        self.undo_action.setEnabled(bool(panel is not None and panel.can_undo))
        self.redo_action.setEnabled(bool(panel is not None and panel.can_redo))
        app = QApplication.instance()
        editing = self._is_text_editor(app.focusWidget() if app is not None else None)
        self.undo_action.setShortcuts([] if editing else self._undo_keys)
        self.redo_action.setShortcuts([] if editing else self._redo_keys)

    def _on_focus_changed(self, _old, _new) -> None:
        self.refresh()

    def _undo(self, _checked=False) -> None:
        if self._panel is not None:
            self._panel.undo()
        self.refresh()

    def _redo(self, _checked=False) -> None:
        if self._panel is not None:
            self._panel.redo()
        self.refresh()
