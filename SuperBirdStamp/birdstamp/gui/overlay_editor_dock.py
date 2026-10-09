"""实例编辑的中间停靠区；与模板管理共用 OverlayPanel，不持有编辑数据。"""
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QDialogButtonBox, QDockWidget, QFrame, QLabel, QMainWindow,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


class _OverlayScroll(QScrollArea):
    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        if self.widget() is not None:
            # 只约束图层工具栏的宽度，三列属性已有自己的横向滚动区。
            size.setWidth(self.widget().minimumSizeHint().width()
                          + self.verticalScrollBar().sizeHint().width() + 2 * self.frameWidth())
        return size

    def eventFilter(self, watched, event):
        result = super().eventFilter(watched, event)
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return result


class OverlayEditorDock(QDockWidget):
    def __init__(self, editor, panel, actions):
        super().__init__('编辑叠加层', editor)
        self.setObjectName('BirdStampOverlayEditorDock')
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                         | QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.panel = panel
        self.host = None
        self._opened = False
        self._floated = False
        self._docked_width = 360
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(6, 6, 6, 6)
        self.context_label = QLabel('请选择照片')
        self.context_label.setWordWrap(True)
        self.context_label.setTextFormat(Qt.TextFormat.PlainText)
        self.context_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self.context_label)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(panel)
        layout.addStretch(1)
        self.scroll = _OverlayScroll()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)
        root.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(self.close)
        self.float_button = buttons.addButton('浮动', QDialogButtonBox.ButtonRole.ActionRole)
        self.float_button.clicked.connect(lambda: self.setFloating(not self.isFloating()))
        self.float_button.setToolTip('也可以拖动或双击标题栏，在浮动与中间停靠之间切换。')
        root.addWidget(buttons)
        self.setWidget(body)
        # 只在编辑面板及其子控件内响应 Esc，保留预览画布自己的退出编辑行为。
        shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.close)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.topLevelChanged.connect(self._floating_changed)
        self.hide()

    def create_host(self, preview):
        # 嵌入式 QMainWindow 只管理预览旁的停靠区；左侧设置仍在外层 splitter 中。
        # 不能挂到主窗口的 LeftDockWidgetArea，否则会排到整个设置区的左边。
        self.host = QMainWindow(flags=Qt.WindowType.Widget)
        self.host.setObjectName('BirdStampPreviewDockHost')
        self.host.setCentralWidget(preview)
        self.host.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self)
        self.hide()
        return self.host

    def reveal(self):
        self.show()
        if not self._opened:
            # 首次分配约 45% 给编辑器，其余留给预览；Qt 尊重预览的最小尺寸。
            self._docked_width = max(240, min(880, int(self.host.width() * .45)))
            self.host.resizeDocks([self], [self._docked_width], Qt.Orientation.Horizontal)
            self._opened = True
        if self.isFloating():
            self._fit_floating_to_screen()
        self.raise_()

    def _fit_floating_to_screen(self):
        screen = QApplication.screenAt(self.mapToGlobal(QPoint())) or self.screen()
        bounds = screen.availableGeometry().adjusted(12, 36, -12, -12)
        self.resize(min(self.width(), bounds.width()), min(self.height(), bounds.height()))
        self.move(max(bounds.left(), min(self.x(), bounds.right() - self.width() + 1)),
                  max(bounds.top(), min(self.y(), bounds.bottom() - self.height() + 1)))

    def _floating_changed(self, floating):
        self.float_button.setText('停靠' if floating else '浮动')
        if floating:
            # 原生拖动进行中不改变几何，避免鼠标锚点跳动；按钮浮动时展开编辑空间。
            if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
                return
            if not self._floated:
                self.resize(960, 840)
                self._floated = True
            self._fit_floating_to_screen()
        elif self.host is not None:
            self.host.resizeDocks([self], [self._docked_width], Qt.Orientation.Horizontal)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible() and not self.isFloating():
            self._docked_width = self.width()

    def closeEvent(self, event):
        # 只收起，提交防抖中的文字并保留当前照片的模型、选中层和撤销历史。
        self.panel.flush_text()
        super().closeEvent(event)
