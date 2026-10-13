"""目录新建和文件拖放协调；系统拥有文件冲突与操作进度。"""
from __future__ import annotations

import os
from pathlib import Path
import uuid

from .native_file_transfer import create_child_directory, native_transfer, transfer_paths_with_sidecars, TransferResult

try:
    from PyQt6.QtCore import QMimeData, QObject, QThread, Qt, QUrl, pyqtSignal
    from PyQt6.QtWidgets import QAbstractItemView, QInputDialog, QMessageBox
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QMimeData, QObject, QThread, Qt, QUrl, pyqtSignal
    from PyQt5.QtWidgets import QAbstractItemView, QInputDialog, QMessageBox

_DRAG_MIME = "application/x-superviewer-file-drag"


class NativeTransferWorker(QThread):
    def __init__(self, sources, destination, copy, owner):
        super().__init__()
        self.sources, self.destination = tuple(sources), destination
        self.copy, self.owner = copy, owner
        self.paths = ()
        self.moved = []
        self.result = TransferResult()

    def run(self):
        try:
            self.paths = transfer_paths_with_sidecars(self.sources, self.destination, copy=self.copy)
            if not self.isInterruptionRequested():
                self.result = native_transfer(self.paths, self.destination, copy=self.copy, owner=self.owner)
            # 取消/跳过可能只完成一部分；只移除确实不再存在的源文件，不猜测成功。
            if not self.copy:
                self.moved = [path for path in self.sources if path in self.paths and not os.path.exists(path)]
        except Exception as exc:
            self.result = TransferResult(error=str(exc))


class DirectoryFileController(QObject):
    photos_moved = pyqtSignal(object)

    def __init__(self, window, files, directories):
        super().__init__(window)
        self._main, self._files, self._directories = window, files, directories
        self._worker = None
        self._shutdown_requested = False
        self._drag_token = b""
        self._drag_sources = ()
        directories.add_context_menu_extender(self.extend_directory_menu)
        directories._tree.accepts_file_drag = self.accepts_drag
        directories._tree.submit_file_drop = self.submit_drop
        for view in (files._tree_widget, files._list_widget):
            view.create_drag_mime = self.create_drag_mime
            view.drag_finished = self.clear_drag
            view.setDragEnabled(True)
            view.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
            view.setDefaultDropAction(Qt.DropAction.MoveAction)
            view.setToolTip("拖到左侧目录以移动选中文件和 XMP；按住 Ctrl 拖放则复制。")

    def extend_directory_menu(self, menu, path):
        action = menu.addAction("新建子目录…", lambda: self.create_directory(path))
        action.setEnabled(not self._shutdown_requested)

    def create_directory(self, parent):
        name = "新建文件夹"
        while not self._shutdown_requested:
            name, accepted = QInputDialog.getText(self._main, "新建子目录", "目录名称：", text=name)
            if not accepted:
                return
            try:
                created = create_child_directory(parent, name)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self._main, "无法创建子目录", str(exc))
                continue
            # 增量插入，保留正在浏览的目录与已有展开子树。
            browser = self._directories
            item = browser._find_directory_item(parent)
            if item is not None:
                browser._ensure_children_loaded(item)
                if browser._find_child_item_by_path(item, created) is None:
                    item.addChild(browser._make_item(created, Path(created).name))
                    item.sortChildren(0, Qt.SortOrder.AscendingOrder)
                item.setExpanded(True)
            self._main.statusBar().showMessage(f"已创建目录：{created}", 5000)
            return

    def create_drag_mime(self):
        if self._shutdown_requested or self._worker is not None:
            return None
        selected = self._files._active_view_selected_paths()
        entries = self._files._collect_file_clipboard_entries(selected)
        if not entries:
            return None
        self._drag_sources = tuple(entry["source"] for entry in entries)
        self._drag_token = uuid.uuid4().hex.encode("ascii")
        self._drag_displays, self._drag_scopes = {}, {}
        for display in selected:
            source = os.path.abspath(self._files._resolve_source_path_for_action(display) or display)
            self._drag_displays[source] = display
            self._drag_scopes[source] = self._files._report_scope_path_key(self._files.get_report_row_for_path(display))
        mime = QMimeData()
        mime.setData(_DRAG_MIME, self._drag_token)
        mime.setUrls([QUrl.fromLocalFile(path) for path in self._files._expanded_paths_from_clipboard_entries(entries)])
        return mime

    def accepts_drag(self, mime):
        return (not self._shutdown_requested and self._worker is None and bool(self._drag_token)
                and bytes(mime.data(_DRAG_MIME)) == self._drag_token)

    def clear_drag(self):
        self._drag_token = b""
        self._drag_sources = ()
        self._directories._tree.clear_drop_target()

    def submit_drop(self, mime, destination, copy):
        if not self.accepts_drag(mime) or not os.path.isdir(destination):
            return False
        sources = self._drag_sources
        if not copy and not self._files._file_operation_paths_allowed(list(sources), "拖放移动", warn=True):
            return False
        self._displays, self._scopes = dict(self._drag_displays), dict(self._drag_scopes)
        # 系统窗口不归属主窗口 HWND：应用关闭等待时隐藏主窗，系统取消入口仍须可见。
        worker = self._worker = NativeTransferWorker(sources, os.path.abspath(destination), copy, 0)
        worker.finished.connect(lambda w=worker: self._finished(w))
        self._main.statusBar().showMessage("正在由系统复制文件…" if copy else "正在由系统移动文件…")
        worker.start()
        return True

    def _finished(self, worker):
        if worker is not self._worker:
            return
        self._worker = None
        if not self._shutdown_requested:
            from app_common.exif_io.writer import invalidate_metadata_cache
            invalidate_metadata_cache([*worker.paths, *(str(Path(worker.destination) / Path(p).name) for p in worker.paths)])
            if worker.moved:
                self._files._delete_report_rows_for_paths(
                    [self._displays[path] for path in worker.moved], resolved_paths=worker.moved,
                    scope_keys=[self._scopes[path] for path in worker.moved])
                self._files.clear_tag_history()
                self.photos_moved.emit(worker.moved)
            # 包含子目录、目标正位于当前根和取消后的部分完成都按实际磁盘刷新。
            directory = self._files.get_current_dir()
            if directory:
                touched = [worker.destination, *(str(Path(p).parent) for p in worker.paths)]
                if any(self._directories._is_same_or_parent_path(directory, path) for path in touched):
                    self._files.load_directory(directory, force_reload=True)
            result = worker.result
            message = result.error or ("系统文件操作已取消；已完成的文件保持系统处理结果。" if result.cancelled else "系统文件操作已完成。")
            self._main.statusBar().showMessage(message, 8000)
            if result.error:
                QMessageBox.warning(self._main, "文件移动/复制未完成", result.error)
        worker.deleteLater()

    def request_shutdown(self):
        self._shutdown_requested = True
        self.clear_drag()
        if self._worker is not None:
            # 系统窗口继续提供取消入口；绝不销毁正在执行文件操作的线程。
            self._worker.requestInterruption()

    def is_shutdown_done(self):
        return self._worker is None
