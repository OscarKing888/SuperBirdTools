"""A/B 共用视口：照片信息及操作工具栏、画布和状态栏。"""
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStyle, QToolButton, QVBoxLayout, QWidget

from app_common.toggle_button import ToggleToolButton
from app_common.preview_canvas import configure_preview_scale_preset_combo, sync_preview_scale_preset_combo
from app_common.video import is_video
from birdstamp.constants import RAW_EXTENSIONS
from . import editor_options
from .editor_media_icons import media_icon


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
            button = ToggleToolButton()
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
    source_mode_changed = pyqtSignal(str)

    def __init__(self, name, preview, *, center=None, scale=None):
        super().__init__()
        self.name = name
        self.preview = preview
        self._source_mode = 'default'
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
        self.play.setIcon(media_icon(
            self.play, QStyle.StandardPixmap.SP_MediaPlay,
            self.play.palette().color(QPalette.ColorRole.ButtonText),
        ))
        self.play.setToolTip(f'播放 {name} 侧照片序列')
        self.play.setAccessibleName(f'播放 {name} 侧照片序列')
        tools.addWidget(self.play)
        self.mode = PreviewModeButtons()
        tools.addWidget(self.mode)
        self.show_raw = ToggleToolButton()
        self.source_button = self.show_raw  # 保留旧工具栏引用，来源状态由视口显式保存。
        self.show_raw.setText('默认预览')
        self.show_raw.setCheckable(True)
        self.show_raw.setToolTip(
            '点击切换：默认预览 → 显示 RAW → 显示降噪（非 RAW 照片跳过 RAW）。\n'
            '降噪显示已生成的成片，缺失时显示原图并提示。\n'
            '仅影响本侧原图预览；序列播放期间保留缩略图，停止后加载所选来源。\n'
            '导出仍使用原有来源设置。')
        self.show_raw.setAccessibleName(f'{name} 预览来源')
        self.show_raw.clicked.connect(self._cycle_preview_source)
        tools.addWidget(self.show_raw)
        self.mode.currentIndexChanged.connect(lambda _index: self._update_raw_toggle_visibility())
        self.center = center if center is not None else ToggleToolButton()
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
        self._path = None
        self._update_raw_toggle_visibility()
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
        self._path = path
        self._filename = path.name if path else '未选择'
        self._elide_filename()
        self.filename.setToolTip(str(path) if path else '')
        self._update_raw_toggle_visibility()

    def _update_raw_toggle_visibility(self):
        path = self._path
        self.source_button.setVisible(bool(path and not is_video(path)))
        self.source_button.setEnabled(bool(path and self.mode.currentIndex() == 0))
        mode = self.effective_source_mode()
        self.source_button.setText({'default': '默认预览', 'raw': '显示 RAW', 'denoised': '显示降噪'}[mode])
        self.source_button.setChecked(mode != 'default')

    def source_mode(self):
        return self._source_mode

    def effective_source_mode(self, path=None):
        path = self._path if path is None else path
        if self._source_mode == 'raw' and (path is None or path.suffix.lower() not in RAW_EXTENSIONS):
            return 'default'
        return self._source_mode

    def set_source_mode(self, mode):
        if mode not in ('default', 'raw', 'denoised'):
            raise ValueError(f'未知预览来源：{mode}')
        changed = mode != self._source_mode
        self._source_mode = mode
        self._update_raw_toggle_visibility()
        if changed:
            self.source_mode_changed.emit(mode)

    def _cycle_preview_source(self):
        if self._path is None or self.mode.currentIndex() != 0:
            self._update_raw_toggle_visibility()
            return
        modes = ('default', 'raw', 'denoised') if self._path.suffix.lower() in RAW_EXTENSIONS else ('default', 'denoised')
        self.set_source_mode(modes[(modes.index(self.effective_source_mode()) + 1) % len(modes)])

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
