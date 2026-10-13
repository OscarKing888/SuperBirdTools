"""编辑器的固定导出区，只负责控件布局，不持有渲染设置。"""
from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .editor_collapsible import CollapsibleSection, refresh_layout_chain


class _ExportSettingsScrollArea(QScrollArea):
    """视频按完整表单高度展开，其余导出参数保持紧凑高度。"""

    def __init__(self, content):
        super().__init__()
        self._show_full_content = False
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMaximumHeight(240)
        self.setWidget(content)
        content.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        content.installEventFilter(self)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_show_full_content(self, enabled):
        self._show_full_content = bool(enabled)
        self.setMaximumHeight(16777215 if enabled else 240)
        self.updateGeometry()

    def heightForWidth(self, width):
        layout = self.widget().layout()
        height = (layout.totalHeightForWidth(width) if layout.hasHeightForWidth()
                  else layout.totalSizeHint().height())
        height = max(36, height)
        return height if self._show_full_content else min(240, height)

    def sizeHint(self):
        content = self.widget()
        return QSize(content.sizeHint().width(), self.heightForWidth(self.viewport().width()))

    def minimumSizeHint(self):
        return QSize(0, 36)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)


class _CurrentExportActions(QStackedWidget):
    """仅当前操作页参与高度计算，隐藏的拼图状态/进度不挤占视频参数空间。"""

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()

    def heightForWidth(self, width):
        page = self.currentWidget()
        if page is None:
            return super().heightForWidth(width)
        height = page.heightForWidth(width) if page.hasHeightForWidth() else page.sizeHint().height()
        return max(page.minimumSizeHint().height(), height)


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

        self.actions = _CurrentExportActions()
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
        self.actions.addWidget(editor.collage.actions)
        editor.export_tabs.currentChanged.connect(self.sync)
        self.sync()

    def sync(self, *_args):
        editor = self.editor
        dejitter = editor._dejitter_tab_active()
        collage = editor.export_tabs.currentWidget() is editor.collage.page
        video = editor._is_video_output_selected()
        gif = editor._is_gif_output_selected()
        self.mode_label.setText('拼图导出' if collage else '去抖动导出' if dejitter else '导出')
        editor.export_stage_widget.setVisible(not dejitter and not collage)
        self.settings_section.setVisible(not collage)
        editor.image_export_group.setVisible(not dejitter and not video)
        editor.video_export_panel.setVisible(not dejitter and video)
        editor.dejitter_export_group.setVisible(dejitter)
        # 视频优先展示完整参数；只有超出窗口的极长重复播放列表才需要滚动。
        self.settings_scroll.set_show_full_content(not dejitter and not collage and video)
        self.actions.setCurrentIndex(3 if collage else 2 if dejitter else 1 if video else 0)
        self.actions.updateGeometry()
        self.format_label.setText('GIF' if gif else editor._selected_output_suffix().upper())
        editor.export_current_btn.setVisible(not gif)
        refresh_layout_chain(self.settings_scroll.widget())
        self.settings_scroll.updateGeometry()
