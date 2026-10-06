"""百分比滑动条和精确输入；常用区间便于拖动，超出区间仍可完整编辑。"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QSlider, QDoubleSpinBox


class PercentEditor(QWidget):
    valueChanged = pyqtSignal(float)
    dragStarted = pyqtSignal()
    dragFinished = pyqtSignal()

    def __init__(self, minimum, maximum, *, slider_range=None, label='', parent=None):
        super().__init__(parent)
        self._slider_range = slider_range or (minimum, maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setSingleStep(100)  # 一个键盘步长 = 1%，内部保留两位小数。
        self.slider.setPageStep(1000)
        self.slider.setMinimumWidth(65)
        self.slider.setAccessibleName(label + '滑动条')
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(2)
        self.spin.setRange(minimum, maximum)
        self.spin.setSuffix(' %')
        self.spin.setKeyboardTracking(False)
        self.spin.setAccessibleName(label)
        # 按字体度量留足完整数值，兼容 macOS/Windows 的不同控件字体。
        self.spin.setFixedWidth(self.spin.sizeHint().width())
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        self.setValue(self.spin.value())
        self.slider.valueChanged.connect(self._slider_changed)
        self.spin.valueChanged.connect(self._spin_changed)
        self.slider.sliderPressed.connect(self.dragStarted)
        self.slider.sliderReleased.connect(self.dragFinished)
        self.setToolTip('拖动调整百分比，也可直接输入精确数值；输入超出常用区间时滑动范围会随之扩展。')

    def value(self):
        return self.spin.value()

    def setValue(self, value):
        """同步配置时不发出编辑信号，不因滑动条取整而改写已保存的值。"""
        blocked = self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(blocked)
        self._sync_slider()

    def _sync_slider(self):
        value = self.spin.value()
        blocked = self.slider.blockSignals(True)
        # 拖动过程中固定映射，避免数值变化使滑动范围缩放、手柄跳动。
        if not self.slider.isSliderDown():
            low, high = self._slider_range
            self.slider.setRange(round(min(low, value) * 100), round(max(high, value) * 100))
        self.slider.setValue(round(value * 100))
        self.slider.blockSignals(blocked)

    def _slider_changed(self, value):
        percent = value / 100
        blocked = self.spin.blockSignals(True)
        self.spin.setValue(percent)
        self.spin.blockSignals(blocked)
        self.valueChanged.emit(self.spin.value())

    def _spin_changed(self, value):
        self._sync_slider()
        self.valueChanged.emit(value)
