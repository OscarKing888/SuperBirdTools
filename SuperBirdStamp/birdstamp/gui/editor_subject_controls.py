"""可替换识别方法的表单；核心参数独立于 Qt。"""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QFormLayout, QComboBox, QSpinBox, QLabel, QCheckBox
from birdstamp.image_dejitter.recognition import (SubjectSettings, METHOD_CHOICES, METHOD_KEY, MODE_KEY, WINDOW_KEY,
                                                 FOLLOW_KEY, FOLLOW_WINDOW_KEY)


_ADVANCED_HINT = ('在同一目标上框选有纹理的局部，或使用实验性部位推荐；只平移，不缩放、不旋转。'
                  '请勿把鸟体与远处背景混选。局部推荐区会补充图像配准，并对短失配段做双向关键帧核验。'
                  '仍失败时，在目标图拖动全部选区修正，即作为后续帧的新关键帧。'
                  '自然跟随保留轨迹趋势；两帧按锁定处理。')
_BASIC_HINT = '基本方法适合静止场景或近似刚性的参考区域；支持平移与轻微旋转。'
_FOLLOW_HINT = '已开启两段式：参考区只选背景（树木、建筑等），不要框鸟；画框按目标鸟的平滑趋势跟随，窗口越大跟随越平缓。'


class SubjectControls(QWidget):
    """“1. 方式”表单：识别方法及其从属选项；推荐面板的部位/目标鸟/模型行由编辑器插入。"""
    changed = pyqtSignal()

    def __init__(self, defaults=None):
        super().__init__()
        layout = QFormLayout(self)
        self.form = layout
        layout.setContentsMargins(0,0,0,0)
        self.method = QComboBox()
        for label,value in METHOD_CHOICES:
            self.method.addItem(label,value)
        self.method.setAccessibleName('去抖动识别方法')
        layout.addRow('识别方法',self.method)
        # 基本方法的两段式稳定：背景去抖后，画框平滑跟随目标鸟。
        self.follow = QCheckBox('两段式：背景稳定后画框跟随目标鸟')
        self.follow.setAccessibleName('两段式稳定：跟随目标鸟')
        self.follow.setToolTip('先用背景参考区逐帧去掉相机抖动，再按目标鸟相对背景的平滑趋势移动画框；'
                               '低头、转身、展翅等快速动作保留。目标鸟未选择时参考图须只识别到一只鸟。')
        layout.addRow(self.follow)
        self.follow_window = QSpinBox()
        self.follow_window.setRange(3,31)
        self.follow_window.setSingleStep(2)
        self.follow_window.setSuffix(' 帧')
        self.follow_window.setAccessibleName('目标鸟跟随窗口')
        self.follow_window.setToolTip('窗口越大，画框跟随越平缓。')
        self.follow_window_label = QLabel('跟随窗口')
        layout.addRow(self.follow_window_label,self.follow_window)
        self.mode = QComboBox()
        self.mode.addItem('局部锁定','lock')
        self.mode.addItem('自然跟随','follow')
        self.mode_label = QLabel('主体稳定模式')
        layout.addRow(self.mode_label,self.mode)
        self.window = QSpinBox()
        self.window.setRange(3,31)
        self.window.setSingleStep(2)
        self.window.setSuffix(' 帧')
        self.window_label = QLabel('跟随平滑窗口')
        layout.addRow(self.window_label,self.window)
        # 方法说明由预览区 HUD 显示；表单内只保留数据，避免面板过长。
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.hide()
        self.set_settings(defaults or {})
        self.method.currentIndexChanged.connect(self._changed)
        self.mode.currentIndexChanged.connect(self._changed)
        self.window.valueChanged.connect(self._changed)
        self.follow.toggled.connect(self._changed)
        self.follow_window.valueChanged.connect(self._changed)

    def insert_row(self, before, label, field=None):
        """在 before 控件所在行之前插入一行；before 为 None 时追加到末尾。"""
        row = self.form.rowCount() if before is None else self.form.getWidgetPosition(before)[0]
        if field is None:
            self.form.insertRow(row,label)
        else:
            self.form.insertRow(row,label,field)

    def set_row_visible(self, widget, visible):
        if self.form.indexOf(widget) >= 0:
            self.form.setRowVisible(widget,bool(visible))
        else:
            widget.setVisible(bool(visible))

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
        follow_mode = self.mode.currentData() == 'follow'
        self.mode.setEnabled(advanced)
        self.window.setEnabled(advanced and follow_mode)
        # 与当前方法无关的选项整行隐藏（含行距），不以禁用态占位。
        self.form.setRowVisible(self.mode,advanced)
        self.form.setRowVisible(self.window,advanced and follow_mode)
        self.form.setRowVisible(self.follow,not advanced)
        self.follow_window.setEnabled(not advanced and self.follow.isChecked())
        self.form.setRowVisible(self.follow_window,not advanced and self.follow.isChecked())
        self.hint.setText(_ADVANCED_HINT if advanced else
                          _BASIC_HINT + (_FOLLOW_HINT if self.follow.isChecked() else ''))

    def _changed(self):
        self._update()
        self.changed.emit()
