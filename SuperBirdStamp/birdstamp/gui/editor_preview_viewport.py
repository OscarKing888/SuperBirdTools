"""A/B 共用的视口布局：选择行、视口操作行、画布和状态栏。"""
from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app_common.preview_canvas import configure_preview_scale_preset_combo, sync_preview_scale_preset_combo
from . import editor_options


class PreviewViewportPanel(QWidget):
    metrics_changed = pyqtSignal()

    def __init__(self, name, preview, relationship, *, center=None, scale=None):
        super().__init__()
        self.preview = preview
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.header, head = self._row()
        self.name_label = QLabel(name)
        head.addWidget(self.name_label)
        self.photos = QComboBox()
        self.photos.setMinimumWidth(110)
        self.photos.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.photos.setToolTip(f'选择 {name} 视口照片')
        head.addWidget(self.photos, 1)
        self.relationship = relationship
        head.addWidget(relationship)
        self.mode = QComboBox()
        head.addWidget(self.mode)
        layout.addWidget(self.header)

        self.toolbar, tools = self._row()
        self.center = center if center is not None else QCheckBox('自动焦点居中')
        if center is None:
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
        tools.addStretch(1)
        layout.addWidget(self.toolbar)
        layout.addWidget(preview, 1)

        # 长状态文本不能撑开其中一个视口；保持单行等高，完整内容仍可悬停查看。
        preview._status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        preview._status_label.installEventFilter(self)
        self.header.installEventFilter(self)
        self.toolbar.installEventFilter(self)
        preview.display_scale_percent_changed.connect(self._update_available)
        self._update_available()

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
        if watched is self.preview._status_label and event.type() == QEvent.Type.ToolTip:
            watched.setToolTip(watched.text())
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.metrics_changed.emit()
        return super().eventFilter(watched, event)


def align_viewport_rows(first, second):
    """按当前字体/平台度量同步行高和尾部列宽，不使用某台机器的固定像素高度。"""
    for key in ('mode', 'relationship', 'name_label'):
        widgets = (getattr(first, key), getattr(second, key))
        width = max(widget.sizeHint().width() for widget in widgets)
        for widget in widgets:
            if widget.minimumWidth() != width or widget.maximumWidth() != width:
                widget.setFixedWidth(width)
    for key in ('header', 'toolbar'):
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
