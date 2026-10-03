"""「重复播放」编辑器：在完整序列之后追加若干遍，每遍独立 FPS。GIF 与视频导出共用。"""
from __future__ import annotations

import math
from typing import Callable, Iterable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from birdstamp.gui.editor_collapsible import refresh_layout_chain
from birdstamp.gui.editor_fps_combo import FpsComboBox

REPEAT_FPS_MIN = 1
REPEAT_FPS_MAX = 240
REPEAT_PASS_LIMIT = 16


def normalize_repeat_fps(
    values: object,
    *,
    minimum: int = REPEAT_FPS_MIN,
    maximum: int = REPEAT_FPS_MAX,
) -> list[float]:
    """Parse persisted repeat-pass FPS values, dropping invalid entries."""
    if not isinstance(values, (list, tuple)):
        return []
    result: list[float] = []
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            fps = float(value)
        except Exception:
            continue
        if not math.isfinite(fps) or fps <= 0:
            continue
        result.append(float(max(minimum, min(maximum, int(round(fps))))))
        if len(result) >= REPEAT_PASS_LIMIT:
            break
    return result


class RepeatFpsEditor(QWidget):
    """逐行编辑「第 2 遍」「第 3 遍」……的 FPS；行尾「添加一遍」默认取上一遍 FPS 的一半。"""

    changed = pyqtSignal()

    def __init__(
        self,
        base_fps: Callable[[], float],
        *,
        minimum: int = REPEAT_FPS_MIN,
        maximum: int = REPEAT_FPS_MAX,
        presets: Iterable[float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._base_fps = base_fps
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        self._presets = None if presets is None else list(presets)
        self._rows: list[tuple[QWidget, QLabel, FpsComboBox]] = []

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.add_button = QPushButton("添加一遍")
        self.add_button.setToolTip("在完整序列之后再按新的 FPS 播放一遍，可添加多遍，例如 20 → 10 → 5 FPS。")
        self.add_button.clicked.connect(self._on_add_clicked)
        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.addWidget(self.add_button)
        add_row.addStretch(1)
        self._layout.addLayout(add_row)

    @property
    def rows(self) -> list[tuple[QWidget, QLabel, FpsComboBox]]:
        return list(self._rows)

    def values(self) -> list[float]:
        return [float(combo.value()) for _row, _label, combo in self._rows]

    def set_values(self, values: object) -> None:
        """恢复状态：新行在连接信号前设定初值，不发出 changed。"""
        for row, _label, _combo in self._rows:
            self._layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._rows.clear()
        for fps in normalize_repeat_fps(values, minimum=self._minimum, maximum=self._maximum):
            self._append_row(fps)
        self._after_rows_changed()

    def _on_add_clicked(self) -> None:
        previous = self._rows[-1][2].value() if self._rows else float(self._base_fps())
        # 默认减半，便于快速得到 20 → 10 → 5 这样的渐慢回放。
        self._append_row(max(self._minimum, int(round(previous / 2.0))))
        self._after_rows_changed()
        self.changed.emit()

    def _on_remove_clicked(self, row: QWidget) -> None:
        for index, (candidate, _label, _combo) in enumerate(self._rows):
            if candidate is row:
                del self._rows[index]
                break
        else:
            return
        self._layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._after_rows_changed()
        self.changed.emit()

    def _append_row(self, fps: float) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        label = QLabel()
        # 与主 FPS 相同的预置下拉；构造时设定初值不会发出 valueChanged。
        combo = FpsComboBox(self._minimum, self._maximum, fps, self._presets)
        combo.valueChanged.connect(lambda _value: self.changed.emit())
        remove_button = QPushButton("删除")
        remove_button.setToolTip("删除这一遍重复播放。")
        remove_button.clicked.connect(lambda _checked=False, target=row: self._on_remove_clicked(target))
        row_layout.addWidget(label)
        row_layout.addWidget(combo)
        row_layout.addWidget(QLabel("FPS"))
        row_layout.addWidget(remove_button)
        row_layout.addStretch(1)
        self._layout.insertWidget(len(self._rows), row)
        self._rows.append((row, label, combo))

    def _after_rows_changed(self) -> None:
        for index, (_row, label, _combo) in enumerate(self._rows, start=2):
            label.setText(f"第 {index} 遍")
        self.add_button.setEnabled(len(self._rows) < REPEAT_PASS_LIMIT)
        refresh_layout_chain(self)
