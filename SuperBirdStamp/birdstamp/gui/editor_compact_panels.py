"""编辑器的固定导出区，只负责控件布局，不持有渲染设置。"""
from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .editor_collapsible import CollapsibleSection, refresh_layout_chain


class _ExportSettingsScrollArea(QScrollArea):
    """按当前参数所需高度展开，长表单内部滚动，不挤走常驻操作按钮。"""

    def __init__(self, content):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMaximumHeight(240)
        self.setWidget(content)
        content.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        content.installEventFilter(self)

    def sizeHint(self):
        content = self.widget()
        height = content.layout().totalSizeHint().height()
        return QSize(content.sizeHint().width(), min(240, max(36, height)))

    def minimumSizeHint(self):
        return QSize(0, 36)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)


class ExportActionBar(QFrame):
    """当前导出类型的参数就地折叠，原按钮和进度始终留在主滚动区外。"""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setObjectName('BirdStampExportActionBar')
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.mode_label = QLabel('导出')
        header.addWidget(self.mode_label)
        header.addWidget(editor.export_stage_widget, 1)
        layout.addLayout(header)

        self.settings_section = CollapsibleSection('导出设置')
        content = QWidget()
        settings_layout = QVBoxLayout(content)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(6)
        # 移动现有实例，保留参数、信号连接、工作区恢复和忙碌状态。
        settings_layout.addWidget(editor.image_export_group)
        settings_layout.addWidget(editor.video_export_panel)
        settings_layout.addWidget(editor.dejitter_export_group)
        self.settings_scroll = _ExportSettingsScrollArea(content)
        self.settings_section.set_content_widget(self.settings_scroll)
        layout.addWidget(self.settings_section)

        self.actions = QStackedWidget()
        layout.addWidget(self.actions)
        image_page = QWidget()
        image_layout = QVBoxLayout(image_page)
        image_layout.setContentsMargins(0, 0, 0, 0)
        self.format_label = QLabel()
        editor.image_export_actions.layout().insertWidget(0, self.format_label)
        image_layout.addWidget(editor.image_export_actions)
        image_layout.addWidget(editor.image_export_progress)
        self.actions.addWidget(image_page)
        # 整体移动按钮容器，保留视频生成/取消按钮各自的显隐和信号绑定。
        self.actions.addWidget(editor.video_export_panel.export_actions)

        dejitter_page = QWidget()
        dejitter_layout = QVBoxLayout(dejitter_page)
        dejitter_layout.setContentsMargins(0, 0, 0, 0)
        dejitter_layout.addWidget(editor.dejitter_export_btn)
        dejitter_layout.addWidget(editor.dejitter_export_progress)
        dejitter_layout.addWidget(editor.dejitter_export_complete)
        self.actions.addWidget(dejitter_page)
        editor.export_tabs.currentChanged.connect(self.sync)
        self.sync()

    def sync(self, *_args):
        editor = self.editor
        dejitter = editor._dejitter_tab_active()
        video = editor._is_video_output_selected()
        gif = editor._is_gif_output_selected()
        self.mode_label.setText('去抖动导出' if dejitter else '导出')
        editor.export_stage_widget.setVisible(not dejitter)
        editor.image_export_group.setVisible(not dejitter and not video)
        editor.video_export_panel.setVisible(not dejitter and video)
        editor.dejitter_export_group.setVisible(dejitter)
        self.actions.setCurrentIndex(2 if dejitter else 1 if video else 0)
        self.format_label.setText('GIF' if gif else editor._selected_output_suffix().upper())
        editor.export_current_btn.setVisible(not gif)
        refresh_layout_chain(self.settings_scroll.widget())
        self.settings_scroll.updateGeometry()
