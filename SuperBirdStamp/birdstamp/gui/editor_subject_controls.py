"""可替换识别方法的表单；核心参数独立于 Qt。"""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QFormLayout, QComboBox, QSpinBox, QLabel
from birdstamp.image_dejitter.recognition import SubjectSettings, METHOD_CHOICES, METHOD_KEY, MODE_KEY, WINDOW_KEY


class SubjectControls(QWidget):
    changed = pyqtSignal()

    def __init__(self, defaults=None):
        super().__init__()
        layout = QFormLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.method = QComboBox()
        for label,value in METHOD_CHOICES:
            self.method.addItem(label,value)
        self.method.setAccessibleName('去抖动识别方法')
        layout.addRow('识别方法',self.method)
        self.mode = QComboBox()
        self.mode.addItem('局部锁定','lock')
        self.mode.addItem('自然跟随','follow')
        layout.addRow('主体稳定模式',self.mode)
        self.window = QSpinBox()
        self.window.setRange(3,31)
        self.window.setSingleStep(2)
        self.window.setSuffix(' 帧')
        layout.addRow('跟随平滑窗口',self.window)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addRow(self.hint)
        self.set_settings(defaults or {})
        self.method.currentIndexChanged.connect(self._changed)
        self.mode.currentIndexChanged.connect(self._changed)
        self.window.valueChanged.connect(self._changed)

    def settings(self):
        return SubjectSettings.from_settings({METHOD_KEY:self.method.currentData(),MODE_KEY:self.mode.currentData(),
                                              WINDOW_KEY:self.window.value()}).as_settings()

    def set_settings(self, settings):
        config = SubjectSettings.from_settings(settings)
        for control,value in ((self.method,config.method),(self.mode,config.mode),(self.window,config.window)):
            blocked = control.blockSignals(True)
            if isinstance(control,QComboBox):
                control.setCurrentIndex(control.findData(value))
            else:
                control.setValue(value)
            control.blockSignals(blocked)
        self._update()

    def _update(self):
        advanced = self.method.currentData() == 'subject_local'
        self.mode.setEnabled(advanced)
        self.window.setEnabled(advanced and self.mode.currentData() == 'follow')
        self.hint.setText(('在同一目标上框选有纹理的局部，或使用实验性部位推荐；只平移，不缩放、不旋转。'
                           '请勿同时选择运动不同的部位。失败帧可拖动全部选区修正，并作为后续帧的新关键帧。'
                           '自然跟随保留轨迹趋势；两帧按锁定处理。' if advanced else
                           '基本方法适合静止场景或近似刚性的参考区域；支持平移与轻微旋转。'))

    def _changed(self):
        self._update()
        self.changed.emit()
