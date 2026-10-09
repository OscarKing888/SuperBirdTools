"""照片实例叠加编辑的非模态窗口；编辑控件与模板管理器共用 OverlayPanel。"""
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


class OverlayEditorDialog(QDialog):
    def __init__(self, editor, panel, actions):
        super().__init__(editor, Qt.WindowType.Tool)
        self.setWindowTitle('编辑叠加层')
        self.setObjectName('BirdStampOverlayEditorDialog')
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.panel = panel
        root = QVBoxLayout(self)
        self.context_label = QLabel('请选择照片')
        self.context_label.setWordWrap(True)
        self.context_label.setTextFormat(Qt.TextFormat.PlainText)
        self.context_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self.context_label)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(panel)
        layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)
        root.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        # 回车只提交当前输入，不意外触发恢复模板或批量应用。
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.resize(960, 840)
        self.setMinimumSize(560, 360)

    def reveal(self):
        if not self.isVisible():
            anchor = self.parentWidget().left_scroll.mapToGlobal(QPoint(12, 12))
            screen = (QApplication.screenAt(anchor) or self.parentWidget().screen()).availableGeometry()
            # 每次重新打开靠左侧设置区定位；可拖动和缩放，连续编辑时不抢回位置。
            bounds = screen.adjusted(12, 36, -12, -12)
            self.setMinimumSize(min(560, bounds.width()), min(360, bounds.height()))
            self.resize(min(self.width(), bounds.width()), min(self.height(), bounds.height()))
            x = max(bounds.left(), min(anchor.x(), bounds.right() - self.width() + 1))
            y = max(bounds.top(), min(anchor.y(), bounds.bottom() - self.height() + 1))
            self.move(x, y)
        self.show()
        self.raise_()

    def done(self, result):
        # 关闭按钮、Esc 和系统关闭均只收起；保留修改及撤销历史。
        self.panel.flush_text()
        super().done(result)
