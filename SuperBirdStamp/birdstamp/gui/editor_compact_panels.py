"""编辑器的叠加侧栏与固定导出区，只负责控件布局，不持有渲染设置。"""
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import (
    QDockWidget, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)


class OverlayEditorDock(QDockWidget):
    """可停靠、可浮动的叠加编辑器；关闭只收起并提交待输入文字。"""

    def __init__(self, editor, panel, actions):
        super().__init__('叠加层编辑', editor)
        self.setObjectName('BirdStampOverlayEditorDock')
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.panel = panel
        # 颜色等复合控件较宽时换行，窄侧栏和 Windows 高 DPI 下无需横向滚动。
        for form in {panel.form, *panel.forms.values(), panel.layout_box.layout()}:
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.context_label = QLabel('请选择照片')
        self.context_label.setWordWrap(True)
        self.context_label.setTextFormat(Qt.TextFormat.PlainText)
        self.context_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.context_label)
        layout.addWidget(panel)
        layout.addLayout(actions)
        layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(content)
        self.setWidget(self.scroll)
        self.setMinimumWidth(340)
        editor.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self)
        self.hide()
        self._opened = False

    def reveal(self):
        if not self._opened:
            self._opened = True
            editor = self.parentWidget()
            # 小窗口避免挤窄照片；原生 Dock 标题栏仍允许用户拖回右侧停靠。
            sidebar_width = editor.left_scroll.width()
            if editor.width() - sidebar_width < 840:
                self.setFloating(True)
                screen = editor.screen().availableGeometry()
                width, height = min(420, screen.width()), min(720, screen.height() - 60)
                origin = editor.frameGeometry().topRight()
                x = max(screen.left(), min(origin.x() - width, screen.right() - width + 1))
                y = max(screen.top() + 30, min(origin.y() + 40, screen.bottom() - height + 1))
                self.setGeometry(x, y, width, height)
            else:
                editor.resizeDocks([self], [400], Qt.Orientation.Horizontal)
        self.show()
        self.raise_()

    def closeEvent(self, event):
        self.panel.flush_text()
        super().closeEvent(event)


class ExportActionBar(QFrame):
    """复用原导出按钮和进度控件，始终放在主设置滚动区外。"""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setObjectName('BirdStampExportActionBar')
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.mode_label = QLabel('导出')
        header.addWidget(self.mode_label)
        header.addWidget(editor.export_stage_widget, 1)
        self.settings_button = QPushButton('导出设置…')
        self.settings_button.clicked.connect(self.reveal_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)
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
        self.actions.addWidget(dejitter_page)
        editor.export_tabs.currentChanged.connect(self.sync)
        self.sync()

    def sync(self, *_args):
        editor = self.editor
        dejitter = editor._dejitter_tab_active()
        video = editor._is_video_output_selected()
        self.mode_label.setText('去抖动导出' if dejitter else '导出')
        editor.export_stage_widget.setVisible(not dejitter)
        self.actions.setCurrentIndex(2 if dejitter else 1 if video else 0)
        self.format_label.setText(editor._selected_output_suffix().upper())
        editor.export_current_btn.setVisible(not editor._is_gif_output_selected())

    def reveal_settings(self):
        editor = self.editor
        editor._export_section.set_expanded(True)
        target = (editor.dejitter_export_group if editor._dejitter_tab_active()
                  else editor.video_export_panel if editor._is_video_output_selected()
                  else editor.image_export_group)
        # 折叠区展开会重新布局；先落实几何，再定位到当前输出类型的参数。
        editor._left_panel_layout.activate()
        editor.left_scroll.widget().adjustSize()
        top = target.mapTo(editor.left_scroll.widget(), QPoint()).y()
        editor.left_scroll.ensureVisible(0, top, 0, 12)
