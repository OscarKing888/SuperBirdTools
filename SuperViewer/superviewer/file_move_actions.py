"""编辑菜单和目录菜单共享的文件移动历史动作。"""
from .qt_compat import QAction
from .tag_history_actions import _build_history_icon


class FileMoveHistoryActions:
    def __init__(self, window):
        self._controller = None
        self.undo_action = QAction("撤销移动", window)
        self.redo_action = QAction("重做移动", window)
        for action, redo in ((self.undo_action, False), (self.redo_action, True)):
            action.setIcon(_build_history_icon(redo=redo))
            action.setStatusTip("重做上一次文件移动（包含 XMP）" if redo else "撤销上一次文件移动（包含 XMP）")
        self.undo_action.triggered.connect(lambda: self._controller.undo() if self._controller else None)
        self.redo_action.triggered.connect(lambda: self._controller.redo() if self._controller else None)
        self.refresh()

    def set_controller(self, controller):
        self._controller = controller
        controller.history_changed.connect(self.refresh)
        self.refresh()

    def add_to_menu(self, menu):
        menu.addSeparator()
        menu.addAction(self.undo_action)
        menu.addAction(self.redo_action)

    def refresh(self):
        controller = self._controller
        self.undo_action.setEnabled(bool(controller and controller.can_undo))
        self.redo_action.setEnabled(bool(controller and controller.can_redo))
