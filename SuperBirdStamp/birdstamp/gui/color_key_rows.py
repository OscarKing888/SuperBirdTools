"""画布线框标记的说明。"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


class _ColorFrame(QWidget):
    def __init__(self, color: str, *, dashed: bool = False, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.dashed = dashed
        self.setFixedSize(20, 18)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.color, 2,
                            Qt.PenStyle.DashLine if self.dashed else Qt.PenStyle.SolidLine))
        painter.drawRect(2, 3, 15, 11)


class ColorKeyRows(QWidget):
    """每行先显示画布线框标记，再显示对应说明。"""

    def __init__(self, entries: tuple[tuple[str, bool, str], ...], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._labels = []
        for color, dashed, description in entries:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(_ColorFrame(color, dashed=dashed, parent=self), 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel(description, self)
            label.setWordWrap(True)
            row.addWidget(label, 1)
            layout.addLayout(row)
            self._labels.append(label)
        self._update_accessible_name()

    def set_lines(self, lines: tuple[str, ...]):
        if len(lines) != len(self._labels):
            raise ValueError('线框说明行数不匹配')
        for label, line in zip(self._labels, lines):
            label.setText(line)
        self._update_accessible_name()

    def text(self):
        return '\n'.join(label.text() for label in self._labels)

    def _update_accessible_name(self):
        self.setAccessibleName(self.text())
