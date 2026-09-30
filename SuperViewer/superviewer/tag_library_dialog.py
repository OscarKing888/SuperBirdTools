"""Qt views for tag-tree drafts and cancellable background transactions."""
from __future__ import annotations

import threading
from time import monotonic

from .qt_compat import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QThread, QTimer, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    pyqtSignal,
)
from .tag_library_model import TagLibraryDraft


def exec_dialog(dialog):
    return dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()


class _Task(QThread):
    progress = pyqtSignal(str, int, int)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.cancelled = threading.Event()
        self.result = None
        self.error = None
        self._last_progress = ("", 0.0)

    def _report_progress(self, text, current, total):
        phase, last = self._last_progress
        now = monotonic()
        if text != phase or now - last >= .03 or (total and current == total):
            self._last_progress = (text, now)
            self.progress.emit(text, current, total)

    def run(self):
        try:
            self.result = self.operation(cancel=self.cancelled.is_set, progress=self._report_progress)
        except Exception as exc:
            self.error = exc


class TagTaskDialog(QDialog):
    """Stay alive until QThread.finished, including cancellation/rollback."""
    def __init__(self, parent, title, operation, cancellable=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.worker = _Task(operation)
        self.label = QLabel("正在准备…", self)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setEnabled(cancellable)
        self.cancellable = cancellable
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.cancel_button)
        self.cancel_button.clicked.connect(self.reject)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)

    def _progress(self, text, current, total):
        self.label.setText(f"{text}：{current}" + (f" / {total}" if total else ""))
        if text == "恢复原标签":
            self.cancel_button.setEnabled(False)

    def reject(self):
        if self.cancellable:
            self.worker.cancelled.set()
            self.cancel_button.setEnabled(False)
            self.label.setText("正在取消并恢复，请稍候…")

    def closeEvent(self, event):
        self.reject()
        event.ignore()

    def _finished(self):
        self.accept()

    def run(self):
        QTimer.singleShot(0, self.worker.start)
        exec_dialog(self)
        self.worker.wait()
        if self.worker.error is not None:
            raise self.worker.error
        return self.worker.result


def run_tag_task(parent, title, operation, *, cancellable=True):
    dialog = TagTaskDialog(parent, title, operation, cancellable)
    try:
        return dialog.run()
    finally:
        dialog.deleteLater()


class TagLibraryDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.draft = TagLibraryDraft(controller.panel._tag_config.read_bytes())
        self.setWindowTitle("编辑标签")
        self.resize(660, 600)
        layout = QVBoxLayout(self)
        self.scope = QLabel(
            f"标签配置：{controller.panel._tag_config.path}\n"
            f"照片同步：{controller.panel.get_current_dir() or '未选择目录'}（包含全部子目录，不受筛选影响）\n"
            "共享此配置的其他目录也会显示新标签列表；仅同步以上范围内的照片。", self)
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["标签 / 分组", "类型"])
        modes = getattr(QHeaderView, "ResizeMode", QHeaderView)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, modes.Stretch)
        self.tree.header().setSectionResizeMode(1, modes.ResizeToContents)
        self.tree.currentItemChanged.connect(self._selected)
        layout.addWidget(self.tree, 1)
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("输入标签或分组名称")
        layout.addWidget(self.name_edit)
        row = QHBoxLayout()
        for label, callback in [("新增标签", lambda: self._add(False)), ("新增分组", lambda: self._add(True)),
                                ("重命名", self._rename), ("删除", self._remove),
                                ("上移", lambda: self._reorder(-1)), ("下移", lambda: self._reorder(1))]:
            button = QPushButton(label, self)
            button.clicked.connect(lambda _checked=False, fn=callback: self._try(fn))
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("所属分组："))
        self.parent_combo = QComboBox(self)
        row.addWidget(self.parent_combo, 1)
        move = QPushButton("移动", self)
        move.clicked.connect(lambda: self._try(self._move))
        row.addWidget(move)
        layout.addLayout(row)
        self.error_label = QLabel("", self)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.recover_button = QPushButton("重试恢复", self)
        self.recover_button.clicked.connect(self._recover)
        self.save_button = QPushButton("保存", self)
        self.save_button.clicked.connect(self._save)
        cancel = QPushButton("取消", self)
        cancel.clicked.connect(self.reject)
        for button in (self.recover_button, self.save_button, cancel):
            row.addWidget(button)
        layout.addLayout(row)
        self._items = {}
        self._refresh()

    def _node(self):
        return self._items.get(id(self.tree.currentItem()))

    def _selected(self, current, previous):
        node = self._items.get(id(current))
        if node:
            self.name_edit.setText(node.name)

    def _refresh(self, selected=None):
        self.tree.clear()
        self._items = {}
        self.parent_combo.clear()
        self.parent_combo.addItem("顶层", None)
        def populate(nodes, parent=None, prefix=""):
            for node in nodes:
                item = QTreeWidgetItem([node.name, "分组" if node.group else "标签"])
                self._items[id(item)] = node
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                if node.group:
                    self.parent_combo.addItem(prefix + node.name, node)
                populate(node.children, item, prefix + node.name + " / ")
                if node is selected:
                    self.tree.setCurrentItem(item)
        populate(self.draft.roots)
        self.tree.expandAll()
        self.recover_button.setVisible(self.controller.blocked)
        self.save_button.setEnabled(not self.controller.blocked)

    def _try(self, callback):
        try:
            self.error_label.clear()
            selected = callback()
            self._refresh(selected)
        except ValueError as exc:
            self.error_label.setText(str(exc))

    def _add(self, group):
        node = self._node()
        return self.draft.add(self.name_edit.text(), group=group,
                              parent=node if node and node.group else self.parent_combo.currentData())

    def _rename(self):
        node = self._node()
        if node:
            self.draft.rename(node, self.name_edit.text())
        return node

    def _remove(self):
        node = self._node()
        if node:
            self.draft.remove(node)

    def _reorder(self, offset):
        node = self._node()
        if node:
            self.draft.reorder(node, offset)
        return node

    def _move(self):
        node = self._node()
        if node:
            self.draft.move(node, self.parent_combo.currentData())
        return node

    def _save(self):
        try:
            if self.controller.save_draft(self.draft, self):
                self.accept()
        except Exception as exc:
            self.error_label.setText(str(exc))
            self._refresh(self._node())

    def _recover(self):
        try:
            self.controller.recover(self)
            self.draft = TagLibraryDraft(self.controller.panel._tag_config.read_bytes())
            self.error_label.setText("恢复完成，请重新编辑。")
        except Exception as exc:
            self.error_label.setText(str(exc))
        self._refresh()
