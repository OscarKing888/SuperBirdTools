"""Compact reference matching controls; algorithms live in image_dejitter."""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox, QDoubleSpinBox, QPushButton

from birdstamp.image_dejitter.matching_options import (
    MATCHING_KEYS, ROTATION_RANGE, TOLERANCE_RANGE, normalize_matching_settings,
)


class DejitterMatchingControls(QWidget):
    changed = pyqtSignal()

    def __init__(self, defaults, parent=None):
        super().__init__(parent)
        self.defaults = normalize_matching_settings(defaults)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel('匹配设置'))
        self.mode = QComboBox()
        self.mode.addItem('自动（推荐）', 'auto')
        self.mode.addItem('高级自定义', 'custom')
        row.addWidget(self.mode, 1)
        self.reset = QPushButton('恢复默认')
        row.addWidget(self.reset)
        layout.addLayout(row)
        self.advanced = QWidget()
        form = QFormLayout(self.advanced)
        form.setContentsMargins(0, 0, 0, 0)
        self.rotation = QDoubleSpinBox()
        self.rotation.setRange(*ROTATION_RANGE)
        self.rotation.setDecimals(1)
        self.rotation.setSingleStep(.1)
        self.rotation.setSuffix('°')
        self.rotation.setToolTip('允许多个实测选区之间存在轻微相机转动。0° 仅使用平移一致性；不会旋转或重采样输出图片。')
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(*TOLERANCE_RANGE)
        self.tolerance.setDecimals(2)
        self.tolerance.setSingleStep(.05)
        self.tolerance.setSuffix('%')
        self.tolerance.setToolTip('按原图短边计算，至少 1 像素。例如短边 3744 像素时，0.3% 约为 11 像素。调大可容忍更多位置差异，也更容易错配。')
        for spin in (self.rotation, self.tolerance):
            spin.setKeyboardTracking(False)
        form.addRow('允许的轻微旋转', self.rotation)
        form.addRow('位置容差（短边比例）', self.tolerance)
        note = QLabel('仅在几何匹配失败时调整；数值越大越容易错配。重复纹理或选区出画面，请调整选区。输出仍只补偿平移。')
        note.setWordWrap(True)
        form.addRow(note)
        layout.addWidget(self.advanced)
        self.summary = QLabel('自动：允许轻微旋转 2°，位置容差为图像短边的 0.3%。')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.set_settings(self.defaults)
        self.mode.currentIndexChanged.connect(self._changed)
        self.rotation.valueChanged.connect(self._changed)
        self.tolerance.valueChanged.connect(self._changed)
        self.reset.clicked.connect(self._reset)

    def settings(self):
        return dict(zip(MATCHING_KEYS, (self.mode.currentData(), self.rotation.value(), self.tolerance.value())))

    def set_settings(self, settings):
        normalized = normalize_matching_settings(settings)
        widgets = (self.mode, self.rotation, self.tolerance)
        previous = [w.blockSignals(True) for w in widgets]
        try:
            self.mode.setCurrentIndex(self.mode.findData(normalized[MATCHING_KEYS[0]]))
            self.rotation.setValue(normalized[MATCHING_KEYS[1]])
            self.tolerance.setValue(normalized[MATCHING_KEYS[2]])
        finally:
            for widget, blocked in zip(widgets, previous):
                widget.blockSignals(blocked)
        self._sync()

    def _sync(self):
        custom = self.mode.currentData() == 'custom'
        self.advanced.setVisible(custom)
        self.summary.setVisible(not custom)

    def _changed(self):
        self._sync()
        self.changed.emit()

    def _reset(self):
        before = self.settings()
        self.set_settings({**self.defaults, MATCHING_KEYS[0]: 'auto'})
        if before != self.settings():
            self.changed.emit()
