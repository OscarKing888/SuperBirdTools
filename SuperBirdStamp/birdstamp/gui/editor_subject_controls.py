"""可替换识别方法的表单；核心参数独立于 Qt。"""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QFormLayout, QComboBox, QSpinBox, QLabel, QCheckBox
from birdstamp.image_dejitter.recognition import (SubjectSettings, METHOD_CHOICES, METHOD_KEY, MODE_KEY, WINDOW_KEY,
                                                 FOLLOW_KEY, FOLLOW_WINDOW_KEY)


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
        # 基本方法的两段式稳定：背景去抖后，画框平滑跟随目标鸟。
        self.follow = QCheckBox('背景稳定后，画框平滑跟随目标鸟')
        self.follow.setAccessibleName('两段式稳定：跟随目标鸟')
        self.follow.setToolTip('先用背景参考区逐帧去掉相机抖动，再按目标鸟相对背景的平滑趋势移动画框；'
                               '低头、转身、展翅等快速动作保留。目标鸟未选择时参考图须只识别到一只鸟。')
        layout.addRow(self.follow)
        self.follow_window = QSpinBox()
        self.follow_window.setRange(3,31)
        self.follow_window.setSingleStep(2)
        self.follow_window.setSuffix(' 帧')
        self.follow_window.setAccessibleName('目标鸟跟随窗口')
        self.follow_window_label = QLabel('目标鸟跟随窗口')
        layout.addRow(self.follow_window_label,self.follow_window)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addRow(self.hint)
        self.set_settings(defaults or {})
        self.method.currentIndexChanged.connect(self._changed)
        self.mode.currentIndexChanged.connect(self._changed)
        self.window.valueChanged.connect(self._changed)
        self.follow.toggled.connect(self._changed)
        self.follow_window.valueChanged.connect(self._changed)

    def settings(self):
        return SubjectSettings.from_settings({METHOD_KEY:self.method.currentData(),MODE_KEY:self.mode.currentData(),
                                              WINDOW_KEY:self.window.value(),FOLLOW_KEY:self.follow.isChecked(),
                                              FOLLOW_WINDOW_KEY:self.follow_window.value()}).as_settings()

    def set_settings(self, settings):
        config = SubjectSettings.from_settings(settings)
        for control,value in ((self.method,config.method),(self.mode,config.mode),(self.window,config.window),
                              (self.follow,config.follow_bird),(self.follow_window,config.follow_window)):
            blocked = control.blockSignals(True)
            if isinstance(control,QComboBox):
                control.setCurrentIndex(control.findData(value))
            elif isinstance(control,QCheckBox):
                control.setChecked(value)
            else:
                control.setValue(value)
            control.blockSignals(blocked)
        self._update()

    def _update(self):
        advanced = self.method.currentData() == 'subject_local'
        self.mode.setEnabled(advanced)
        self.window.setEnabled(advanced and self.mode.currentData() == 'follow')
        for control in (self.follow,self.follow_window,self.follow_window_label):
            control.setVisible(not advanced)
        self.follow_window.setEnabled(not advanced and self.follow.isChecked())
        self.hint.setText(('在同一目标上框选有纹理的局部，或使用实验性部位推荐；只平移，不缩放、不旋转。'
                           '请勿把鸟体与远处背景混选。局部推荐区会补充图像配准，并对短失配段做双向关键帧核验。'
                           '仍失败时，在目标图拖动全部选区修正，即作为后续帧的新关键帧。'
                           '自然跟随保留轨迹趋势；两帧按锁定处理。' if advanced else
                           '基本方法适合静止场景或近似刚性的参考区域；支持平移与轻微旋转。'
                           + ('已开启两段式：参考区只选背景（树木、建筑等），不要框鸟；画框按目标鸟的平滑趋势跟随，'
                              '窗口越大跟随越平缓。' if self.follow.isChecked() else '')))

    def _changed(self):
        self._update()
        self.changed.emit()
