"""Tag history actions that leave native text editing shortcuts available."""
from __future__ import annotations

try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtGui import QKeySequence
    from PyQt6.QtWidgets import QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit
except ImportError:
    from PyQt5.QtCore import QObject
    from PyQt5.QtGui import QKeySequence
    from PyQt5.QtWidgets import QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit

from .qt_compat import QAction, QApplication


class TagHistoryActions(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self._panel = None
        self.undo_action = QAction("撤销标签", window)
        self.redo_action = QAction("重做标签", window)
        self.undo_action.setStatusTip("撤销上一次标签修改")
        self.redo_action.setStatusTip("重做标签修改")
        standard = getattr(QKeySequence, "StandardKey", QKeySequence)
        self._undo_keys = QKeySequence.keyBindings(standard.Undo)
        self._redo_keys = QKeySequence.keyBindings(standard.Redo)
        self.undo_action.triggered.connect(self._undo)
        self.redo_action.triggered.connect(self._redo)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self.refresh()

    def set_panel(self, panel) -> None:
        if panel is self._panel:
            return
        if self._panel is not None:
            self._panel.command_history_changed.disconnect(self.refresh)
        self._panel = panel
        if panel is not None:
            panel.command_history_changed.connect(self.refresh)
        self.refresh()

    def add_to_menu(self, menu) -> None:
        menu.addAction(self.undo_action)
        menu.addAction(self.redo_action)

    @staticmethod
    def _is_text_editor(widget) -> bool:
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return True
            widget = widget.parentWidget()
        return False

    def refresh(self) -> None:
        panel = self._panel
        self.undo_action.setEnabled(bool(panel is not None and panel.can_undo))
        self.redo_action.setEnabled(bool(panel is not None and panel.can_redo))
        app = QApplication.instance()
        editing = self._is_text_editor(app.focusWidget() if app is not None else None)
        self.undo_action.setShortcuts([] if editing else self._undo_keys)
        self.redo_action.setShortcuts([] if editing else self._redo_keys)

    def _on_focus_changed(self, _old, _new) -> None:
        self.refresh()

    def _undo(self, _checked=False) -> None:
        if self._panel is not None:
            self._panel.undo()
        self.refresh()

    def _redo(self, _checked=False) -> None:
        if self._panel is not None:
            self._panel.redo()
        self.refresh()
