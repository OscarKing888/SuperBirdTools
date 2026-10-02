"""可编辑 FPS 下拉框：预置选项 + 手动输入，接口与 QSpinBox 保持一致。"""
from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QRegularExpressionValidator
from PyQt6.QtWidgets import QComboBox, QWidget

from birdstamp.gui import editor_options


class FpsComboBox(QComboBox):
    """整数 FPS 输入；下拉列出范围内的整数预置值，方向键按 1 步进。"""

    valueChanged = pyqtSignal(int)

    def __init__(
        self,
        minimum: int,
        maximum: int,
        value: int,
        presets: Iterable[float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumContentsLength(4)
        self.lineEdit().setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,4}"), self))
        source = editor_options.VIDEO_FPS_OPTIONS if presets is None else presets
        options = sorted({int(v) for v in source if float(v).is_integer() and minimum <= v <= maximum})
        for option in options:
            self.addItem(str(option), option)
        self._value = self._clamp(value)
        self._sync_text()
        self.lineEdit().textEdited.connect(self._on_text_edited)
        self.lineEdit().editingFinished.connect(self._on_editing_finished)
        # activated 在重新选中同一预置项时也会触发，手动输入后再选回原项仍能生效。
        self.activated.connect(self._on_activated)

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        self._commit(self._clamp(value))
        self._sync_text()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        step = {Qt.Key.Key_Up: 1, Qt.Key.Key_Down: -1}.get(event.key())
        if step is None or event.modifiers() & Qt.KeyboardModifier.AltModifier:
            super().keyPressEvent(event)
            return
        self.setValue(self._value + step)
        event.accept()

    def _clamp(self, value: float) -> int:
        return max(self._minimum, min(self._maximum, int(round(float(value)))))

    def _commit(self, value: int) -> None:
        if value != self._value:
            self._value = value
            self.valueChanged.emit(value)

    def _on_text_edited(self, text: str) -> None:
        # 输入过程中只接受范围内数值，越界/空白留到编辑结束时再纠正。
        if text and self._minimum <= int(text) <= self._maximum:
            self._commit(int(text))

    def _on_activated(self, index: int) -> None:
        data = self.itemData(index)
        if data is not None:
            self._commit(int(data))

    def _on_editing_finished(self) -> None:
        text = self.currentText()
        if text:
            self._commit(self._clamp(int(text)))
        self._sync_text()

    def _sync_text(self) -> None:
        index = self.findData(self._value)
        blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(index)
            self.setEditText(str(self._value))
        finally:
            self.blockSignals(blocked)
