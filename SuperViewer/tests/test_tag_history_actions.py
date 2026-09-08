from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QMainWindow, QPushButton, QVBoxLayout, QWidget

from SuperViewer.superviewer.tag_history_actions import TagHistoryActions


_APP = QApplication.instance() or QApplication([])


class _HistoryPanel(QObject):
    command_history_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.can_undo = True
        self.can_redo = False
        self.undo_count = 0
        self.redo_count = 0

    def undo(self):
        self.undo_count += 1
        self.can_undo, self.can_redo = False, True
        self.command_history_changed.emit()

    def redo(self):
        self.redo_count += 1
        self.can_undo, self.can_redo = True, False
        self.command_history_changed.emit()


def test_tag_history_actions_track_history_and_preserve_text_undo():
    window = QMainWindow()
    content = QWidget(window)
    layout = QVBoxLayout(content)
    edit = QLineEdit(content)
    button = QPushButton("文件列表", content)
    layout.addWidget(edit)
    layout.addWidget(button)
    window.setCentralWidget(content)
    actions = TagHistoryActions(window)
    menu = window.menuBar().addMenu("编辑")
    actions.add_to_menu(menu)
    window.addToolBar(actions.create_toolbar(window))
    assert not actions.undo_action.isEnabled()
    panel = _HistoryPanel()
    actions.set_panel(panel)
    window.show()
    window.activateWindow()
    try:
        button.setFocus()
        _APP.processEvents()
        assert actions.undo_action.isEnabled()
        assert not actions.redo_action.isEnabled()
        assert actions.undo_action.shortcuts() == QKeySequence.keyBindings(QKeySequence.StandardKey.Undo)
        actions.undo_action.trigger()
        assert panel.undo_count == 1 and actions.redo_action.isEnabled()
        actions.redo_action.trigger()
        assert panel.redo_count == 1 and actions.undo_action.isEnabled()

        edit.setFocus()
        _APP.processEvents()
        QTest.keyClicks(edit, "abc")
        assert edit.text() == "abc"
        assert not actions.undo_action.shortcuts()
        assert not actions.redo_action.shortcuts()
        QTest.keySequence(edit, QKeySequence(QKeySequence.StandardKey.Undo))
        assert edit.text() == ""
        assert panel.undo_count == 1
        button.setFocus()
        _APP.processEvents()
        assert actions.undo_action.shortcuts()
        QTest.keySequence(button, QKeySequence(QKeySequence.StandardKey.Undo))
        assert panel.undo_count == 2
    finally:
        actions.set_panel(None)
        window.close()
