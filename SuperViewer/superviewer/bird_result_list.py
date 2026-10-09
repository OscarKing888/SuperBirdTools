# -*- coding: utf-8 -*-
"""清晰度 Debug 与图片信息共用的鸟结果列表；仅依赖 Qt。"""
from typing import List, Optional
from .qt_compat import QWidget, QLabel, pyqtSignal
try:
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QGridLayout
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QEvent, Qt
    from PyQt5.QtGui import QPalette
    from PyQt5.QtWidgets import QGridLayout

_Qt = getattr(Qt, "AlignmentFlag", Qt)
_ROLE = getattr(QPalette, "ColorRole", QPalette)
_RICH = getattr(getattr(Qt, "TextFormat", Qt), "RichText")
_EVENT = getattr(QEvent, "Type", QEvent)


class TraceBirdList(QWidget):
    """A step's birds: colour swatch, label and details per row (``TraceBirdRow``).

    Hovering a row tints it and emits ``hovered(row)`` so the views can highlight
    that bird; leaving the row or replacing the rows emits ``None``.
    """

    hovered = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 4, 0, 4)
        # No column/row gaps: the padding lives inside the labels, so the pointer never
        # falls between two cells of a row and the highlight does not flicker.
        self.grid.setHorizontalSpacing(0)
        self.grid.setVerticalSpacing(0)
        self.rows: List = []
        self.cells: List[tuple] = []  # per row: (swatch, label, value)
        self._detail_cells = []
        self._detail_children = set()
        self._row_of: dict = {}  # cell widget -> row index (stale widgets of old rows are absent)
        self.hovered_row: Optional[int] = None
        self.setVisible(False)

    def set_rows(self, rows, *, details=None) -> None:
        """可选第四列放扩展信息；子控件共享本行悬停，Debug 的三列接口不变。"""
        self._set_hovered(None)
        self._clear_rows()
        self.rows, self.cells, self._row_of = list(rows), [], {}
        self._detail_cells = list(details or [])
        self._detail_children = set()
        for r, row in enumerate(self.rows):
            swatch = QLabel(f'<span style="color:{row.color}">■</span>', self)
            swatch.setTextFormat(_RICH)
            swatch.setContentsMargins(0, 3, 6, 3)
            label = QLabel(row.label, self)
            label.setTextFormat(getattr(Qt, "TextFormat", Qt).PlainText)
            label.setForegroundRole(_ROLE.PlaceholderText)
            label.setContentsMargins(0, 3, 12, 3)
            value = QLabel(row.value, self)
            value.setTextFormat(getattr(Qt, "TextFormat", Qt).PlainText)
            value.setWordWrap(True)
            value.setContentsMargins(0, 3, 0, 3)
            for c, widget in enumerate((swatch, label, value)):
                widget.setAlignment(_Qt.AlignLeft | _Qt.AlignTop)  # cells fill the row: the tint covers it whole
                self.grid.addWidget(widget, r, c)
                widget.installEventFilter(self)
                self._row_of[widget] = r
            self.cells.append((swatch, label, value))
            if r < len(self._detail_cells):
                detail = self._detail_cells[r]
                detail.setObjectName("birdRowDetails")
                self.grid.addWidget(detail, r, 3)
                for child in [detail, *detail.findChildren(QWidget)]:
                    child.installEventFilter(self)
                    self._row_of[child] = r
                    if child is not detail:
                        self._detail_children.add(child)
        self.grid.setColumnStretch(2, 1)
        self.grid.setColumnStretch(3, 1 if self._detail_cells else 0)
        self.setVisible(bool(self.rows))

    def _clear_rows(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()

    def hideEvent(self, event):
        self._set_hovered(None)
        super().hideEvent(event)

    def _set_hovered(self, r: Optional[int]) -> None:
        if r == self.hovered_row:
            return
        if self.hovered_row is not None and self.hovered_row < len(self.cells):
            for widget in self.cells[self.hovered_row]:
                widget.setStyleSheet("")
            if self.hovered_row < len(self._detail_cells):
                self._detail_cells[self.hovered_row].setStyleSheet("")
        self.hovered_row = r
        if r is not None:
            tint = self.palette().color(_ROLE.Highlight)
            for widget in self.cells[r]:
                widget.setStyleSheet(f"background: rgba({tint.red()}, {tint.green()}, {tint.blue()}, 90);")
            if r < len(self._detail_cells):
                # 仅染父容器，不能覆盖徽章自身的保护等级/稀有度颜色。
                self._detail_cells[r].setStyleSheet(
                    f"QWidget#birdRowDetails {{ background: rgba({tint.red()}, {tint.green()}, {tint.blue()}, 90); }}")
        self.hovered.emit(None if r is None else self.rows[r])

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        kind = event.type()
        if kind in (_EVENT.Enter, _EVENT.Leave):
            r = self._row_of.get(obj)
            if r is not None:
                if kind == _EVENT.Enter:
                    self._set_hovered(r)
                elif self.hovered_row == r and obj not in self._detail_children:
                    # 子徽章移到容器留白时父控件不会重新 Enter；由父容器 Leave 清除。
                    self._set_hovered(None)
        return super().eventFilter(obj, event)
