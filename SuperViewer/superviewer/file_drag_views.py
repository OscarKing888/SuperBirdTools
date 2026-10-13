"""Viewer 专用拖放视图；共享浏览器与 BirdStamp 默认不启用。"""
from __future__ import annotations

import sys

from app_common.file_browser import DirectoryBrowserWidget
from app_common.file_browser._models import FileTableView
from .rarity_file_table import RarityFileTableModel
from .thumbnail_metadata import BirdThumbnailModel

try:
    from PyQt6.QtCore import QPersistentModelIndex, QTimer, Qt
    from PyQt6.QtGui import QDrag, QPen
    from PyQt6.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate, QTreeWidget
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QPersistentModelIndex, QTimer, Qt
    from PyQt5.QtGui import QDrag, QPen
    from PyQt5.QtWidgets import QAbstractItemView, QListView, QStyledItemDelegate, QTreeWidget


def physical_ctrl_pressed(modifiers):
    # Qt 在 macOS 将 ControlModifier 映射到 Command，MetaModifier 才是实体 Ctrl。
    modifier = Qt.KeyboardModifier.MetaModifier if sys.platform == "darwin" else Qt.KeyboardModifier.ControlModifier
    return bool(modifiers & modifier)


def drop_action(modifiers):
    return Qt.DropAction.CopyAction if physical_ctrl_pressed(modifiers) else Qt.DropAction.MoveAction


def event_position(event):
    return event.position().toPoint() if hasattr(event, "position") else event.pos()


class _DragModel:
    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else flags


class DragFileTableModel(_DragModel, RarityFileTableModel):
    pass


class DragThumbnailModel(_DragModel, BirdThumbnailModel):
    pass


class _DragView:
    create_drag_mime = None
    drag_finished = None

    def startDrag(self, supported_actions):
        if self.create_drag_mime is None:
            return
        mime = self.create_drag_mime()
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        try:
            execute = getattr(drag, "exec", None) or drag.exec_
            execute(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.MoveAction)
        finally:
            if self.drag_finished is not None:
                self.drag_finished()
            drag.deleteLater()


class DragFileTableView(_DragView, FileTableView):
    pass


class DragThumbnailView(_DragView, QListView):
    pass


class _DropTargetDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        tree = self.parent()
        if tree.drop_target == index:
            painter.save()
            painter.setPen(QPen(option.palette.highlight().color(), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.restore()


class DirectoryDropTree(QTreeWidget):
    accepts_file_drag = None
    submit_file_drop = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drop_target = QPersistentModelIndex()
        self._hover_position = None
        self._hover_elapsed = 0
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(75)
        self._hover_timer.timeout.connect(self._hover_tick)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setItemDelegate(_DropTargetDelegate(self))

    def _accepts(self, event):
        return self.accepts_file_drag is not None and self.accepts_file_drag(event.mimeData())

    def dragEnterEvent(self, event):
        if self._accepts(event):
            event.setDropAction(drop_action(event.modifiers() if hasattr(event, "modifiers") else event.keyboardModifiers()))
            event.accept()
        else:
            event.ignore()

    def _update_target(self, pos):
        index = self.indexAt(pos)
        if not index.data(Qt.ItemDataRole.UserRole):
            index = self.rootIndex()  # 无效索引；空白与占位行不能接收。
        target = QPersistentModelIndex(index)
        if self.drop_target != target:
            self.drop_target = target
            self._hover_elapsed = 0
            self.viewport().update()

    def dragMoveEvent(self, event):
        if not self._accepts(event):
            self.clear_drop_target()
            event.ignore()
            return
        self._hover_position = event_position(event)
        self._update_target(self._hover_position)
        self._hover_timer.start()
        if self.drop_target.isValid():
            modifiers = event.modifiers() if hasattr(event, "modifiers") else event.keyboardModifiers()
            event.setDropAction(drop_action(modifiers))
            event.accept()
        else:
            event.ignore()

    def _hover_tick(self):
        if self._hover_position is None:
            return
        self._hover_elapsed += self._hover_timer.interval()
        if self._hover_elapsed >= 600 and self.drop_target.isValid():
            item = self.itemAt(self._hover_position)
            if item is not None:
                item.setExpanded(True)
        y, height = self._hover_position.y(), self.viewport().height()
        step = -1 if y < 24 else (1 if y > height - 24 else 0)
        if step:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + step * max(1, bar.singleStep()))
        self._update_target(self._hover_position)

    def clear_drop_target(self):
        self._hover_timer.stop()
        self._hover_position = None
        self.drop_target = QPersistentModelIndex()
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self.clear_drop_target()
        event.accept()

    def dropEvent(self, event):
        index = self.indexAt(event_position(event))
        destination = index.data(Qt.ItemDataRole.UserRole)
        accepted = False
        if destination and self._accepts(event) and self.submit_file_drop is not None:
            modifiers = event.modifiers() if hasattr(event, "modifiers") else event.keyboardModifiers()
            action = drop_action(modifiers)
            accepted = self.submit_file_drop(event.mimeData(), destination, action == Qt.DropAction.CopyAction)
            if accepted:
                event.setDropAction(action)
                event.accept()
        if not accepted:
            event.ignore()
        self.clear_drop_target()


class ViewerDirectoryBrowser(DirectoryBrowserWidget):
    directory_tree_class = DirectoryDropTree
