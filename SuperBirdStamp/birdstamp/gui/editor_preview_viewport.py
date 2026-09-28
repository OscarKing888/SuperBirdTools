"""A/B 共用视口：照片信息及操作工具栏、画布和状态栏。"""
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStyle, QToolButton, QVBoxLayout, QWidget

from app_common.preview_canvas import configure_preview_scale_preset_combo, sync_preview_scale_preset_combo
from . import editor_options


class PreviewModeButtons(QWidget):
    currentIndexChanged = pyqtSignal(int)
    activated = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.group = QButtonGroup(self)
        for index, text in enumerate(('原图', '去抖动成片')):
            button = QToolButton()
            button.setText(text)
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.group.addButton(button, index)
            row.addWidget(button)
        self.group.idToggled.connect(lambda index, checked: self.currentIndexChanged.emit(index) if checked else None)
        self.group.idClicked.connect(self.activated)

    def currentIndex(self):
        return self.group.checkedId()

    def currentText(self):
        return self.group.checkedButton().text()

    def setCurrentIndex(self, index):
        self.group.button(index).setChecked(True)


class PreviewViewportPanel(QWidget):
    metrics_changed = pyqtSignal()
    activated = pyqtSignal()

    def __init__(self, name, preview, *, center=None, scale=None):
        super().__init__()
        self.name = name
        self.preview = preview
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.toolbar, tools = self._row()
        self.name_label = QLabel(name)
        tools.addWidget(self.name_label)
        self.filename = QLabel('未选择')
        self.filename.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.filename.setMinimumWidth(0)
        tools.addWidget(self.filename, 1)
        self.play = QToolButton()
        self.play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play.setToolTip(f'播放 {name} 侧照片序列')
        self.play.setAccessibleName(f'播放 {name} 侧照片序列')
        tools.addWidget(self.play)
        self.mode = PreviewModeButtons()
        tools.addWidget(self.mode)
        self.center = center if center is not None else QToolButton()
        if center is None:
            self.center.setText('自动焦点居中')
            self.center.setCheckable(True)
            self.center.setChecked(editor_options.PREVIEW_AUTO_FOCUS_CENTER)
            self.center.setToolTip('仅将本视口的焦点保持在中央；无焦点时使用图像中心。')
            self.center.toggled.connect(preview.canvas.set_auto_focus_center)
            preview.canvas.set_auto_focus_center(self.center.isChecked())
        tools.addWidget(self.center)
        self.fit = QPushButton('适应窗口')
        self.fit.setToolTip('只重置本视口的缩放与位置，保留焦点居中开关。')
        self.fit.clicked.connect(preview.canvas.fit_to_window)
        tools.addWidget(self.fit)
        self.scale = scale if scale is not None else QComboBox()
        if scale is None:
            configure_preview_scale_preset_combo(self.scale, fixed_width=96,
                                                tooltip='仅设置本视口的预览缩放比例。')
            self.scale.activated.connect(self._zoom)
            preview.display_scale_percent_changed.connect(self._sync_scale)
            self._sync_scale(preview.current_display_scale_percent())
        tools.addWidget(self.scale)
        layout.addWidget(self.toolbar)
        self.viewport_frame = QFrame()
        self.viewport_frame.setObjectName('ABViewportFrame')
        frame_layout = QVBoxLayout(self.viewport_frame)
        frame_layout.setContentsMargins(2, 2, 2, 2)
        frame_layout.setSpacing(0)
        frame_layout.addWidget(preview)
        layout.addWidget(self.viewport_frame, 1)
        self._filename = '未选择'
        self.set_active(False)

        # 长状态文本不能撑开其中一个视口；保持单行等高，完整内容仍可悬停查看。
        preview._status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        preview._status_label.installEventFilter(self)
        self.toolbar.installEventFilter(self)
        preview.display_scale_percent_changed.connect(self._update_available)
        self._update_available()
        for widget in (self, *self.findChildren(QWidget)):
            widget.installEventFilter(self)

    def set_path(self, path):
        self._filename = path.name if path else '未选择'
        self._elide_filename()
        self.filename.setToolTip(str(path) if path else '')

    def set_active(self, active, *, compare_mode=True):
        highlighted = active and compare_mode
        self.name_label.setVisible(compare_mode)
        self.name_label.setText(f'{self.name} · 当前' if highlighted else self.name)
        self.name_label.setStyleSheet('color: #2196f3; font-weight: 600;' if highlighted else '')
        color = '#2196f3' if highlighted else 'transparent'
        self.viewport_frame.setStyleSheet(f'QFrame#ABViewportFrame {{ border: 2px solid {color}; }}')
        self.toolbar.setToolTip('当前视图响应照片列表选择' if highlighted else '点击激活此视图，再从照片列表选图')
        self.metrics_changed.emit()

    def _elide_filename(self):
        width = self.filename.contentsRect().width()
        self.filename.setText(self.filename.fontMetrics().elidedText(
            self._filename, Qt.TextElideMode.ElideMiddle, max(0, width)))

    @staticmethod
    def _row():
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        return widget, row

    def _zoom(self, index):
        value = self.scale.itemData(index)
        if value is not None:
            self.preview.set_display_scale_percent(value, preserve_view=True)
            self._sync_scale(self.preview.current_display_scale_percent())

    def _sync_scale(self, value):
        sync_preview_scale_preset_combo(self.scale, value)

    def _update_available(self, *_args):
        available = self.preview.current_display_scale_percent() is not None
        # 空图或等待分析时仍保留整行，只禁用依赖图像的操作。
        self.fit.setEnabled(available)
        self.scale.setEnabled(available)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn):
            self.activated.emit()
        if watched is self.filename and event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange):
            self._elide_filename()
        if watched is self.preview._status_label and event.type() == QEvent.Type.ToolTip:
            watched.setToolTip(watched.text())
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.metrics_changed.emit()
        return super().eventFilter(watched, event)


def align_viewport_rows(first, second):
    """按当前字体/平台度量同步行高和尾部列宽，不使用某台机器的固定像素高度。"""
    for key in ('mode', 'name_label'):
        widgets = (getattr(first, key), getattr(second, key))
        width = max(widget.sizeHint().width() for widget in widgets)
        for widget in widgets:
            if widget.minimumWidth() != width or widget.maximumWidth() != width:
                widget.setFixedWidth(width)
    for key in ('toolbar',):
        widgets = (getattr(first, key), getattr(second, key))
        height = max(widget.layout().sizeHint().height() for widget in widgets)
        for widget in widgets:
            if widget.minimumHeight() != height or widget.maximumHeight() != height:
                widget.setFixedHeight(height)
    statuses = (first.preview._status_label, second.preview._status_label)
    height = max(widget.sizeHint().height() for widget in statuses)
    for widget in statuses:
        if widget.minimumHeight() != height or widget.maximumHeight() != height:
            widget.setFixedHeight(height)
