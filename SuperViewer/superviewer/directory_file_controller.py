"""目录新建、文件拖放和目录粘贴协调；系统拥有文件冲突与操作进度。"""
from __future__ import annotations

import os
from pathlib import Path
import uuid

from .native_file_transfer import create_child_directory, native_transfer, transfer_paths_with_sidecars, TransferResult
from .file_move_history import MoveHistory, completed_moves, is_sidecar, path_key, prepare_replay, snapshot_move

try:
    from PyQt6.QtCore import QMimeData, QObject, QThread, Qt, QUrl, pyqtSignal
    from PyQt6.QtWidgets import QAbstractItemView, QApplication, QInputDialog, QMessageBox
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QMimeData, QObject, QThread, Qt, QUrl, pyqtSignal
    from PyQt5.QtWidgets import QAbstractItemView, QApplication, QInputDialog, QMessageBox

_DRAG_MIME = "application/x-superviewer-file-drag"


class NativeTransferWorker(QThread):
    def __init__(self, sources, destination, copy, owner):
        super().__init__()
        self.sources, self.destination = tuple(sources), destination
        self.copy, self.owner = copy, owner
        self.paths = ()
        self.moved = []
        self.result = TransferResult()
        self.direction = ""
        self.inverse = ()
        self.remaining = ()
        self.untracked = ()
        self.destinations = [destination]
        self.targets = ()
        self.clipboard_revision = None
        self.clipboard_cut_complete = False

    def run(self):
        snapshots = ()
        try:
            self.paths = transfer_paths_with_sidecars(self.sources, self.destination, copy=self.copy)
            self.targets = tuple(str(Path(self.destination) / Path(p).name) for p in self.paths)
            if not self.copy:
                snapshots = snapshot_move(self.paths, self.destination, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.result = native_transfer(self.paths, self.destination, copy=self.copy, owner=self.owner)
        except InterruptedError:
            self.result = TransferResult(cancelled=True)
        except Exception as exc:
            self.result = TransferResult(error=str(exc))
        finally:
            # 取消/跳过可能只完成一部分；只移除确实不再存在的源文件，不猜测成功。
            if not self.copy:
                self.moved = [path for path in self.sources if path in self.paths and not os.path.exists(path)]
                self.inverse, self.untracked = completed_moves(snapshots)
                self.clipboard_cut_complete = (bool(self.paths) and len(self.moved) == len(self.sources)
                                               and all(not os.path.exists(path) for path in self.paths))


class MoveReplayWorker(NativeTransferWorker):
    def __init__(self, entries, direction):
        super().__init__([e.source for e in entries if not is_sidecar(e.source)], "", False, 0)
        self.remaining = tuple(entries)
        self.direction = direction
        self.destinations = list(dict.fromkeys(str(Path(e.target).parent) for e in entries))

    def run(self):
        snapshots = []
        try:
            self.remaining = prepare_replay(self.remaining)
            groups = {}
            for entry in self.remaining:
                groups.setdefault(str(Path(entry.target).parent), []).append(entry)
            for destination, entries in groups.items():
                if self.isInterruptionRequested():
                    self.result = TransferResult(cancelled=True)
                    break
                # 前一目录的系统窗口可停留很久，每批提交前再校验当前文件。
                if any(not e.identity.matches(e.source) or os.path.lexists(e.target) for e in entries):
                    raise ValueError("文件或目标位置已改变，剩余移动未执行，请检查后重试。")
                paths = tuple(e.source for e in entries)
                snapshots.extend(snapshot_move(paths, destination, self.isInterruptionRequested))
                self.paths += paths
                self.targets += tuple(e.target for e in entries)
                self.result = native_transfer(paths, destination, copy=False, owner=0)
                if self.result.error or self.result.cancelled:
                    break
        except InterruptedError:
            self.result = TransferResult(cancelled=True)
        except Exception as exc:
            self.result = TransferResult(error=str(exc))
        finally:
            self.inverse, self.untracked = completed_moves(snapshots)
            gone = {s.entry.source for s in snapshots if not os.path.lexists(s.entry.source)}
            self.remaining = tuple(e for e in self.remaining if e.source not in gone)
            self.moved = [path for path in self.sources if path in gone]


class DirectoryFileController(QObject):
    photos_moved = pyqtSignal(object)
    history_changed = pyqtSignal()

    def __init__(self, window, files, directories):
        super().__init__(window)
        self._main, self._files, self._directories = window, files, directories
        self._worker = None
        self._shutdown_requested = False
        self._drag_token = b""
        self._drag_sources = ()
        self._clipboard_revision = 0
        QApplication.clipboard().dataChanged.connect(self._clipboard_changed)
        self._history = MoveHistory()
        self.history_actions = window._move_history_actions
        self.history_actions.set_controller(self)
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
        paste = menu.addAction("粘贴到此文件夹", lambda: self.paste_to_directory(path))
        paste.setEnabled(self.can_paste_to_directory(path))
        paste.setStatusTip("将剪贴板文件和同名 XMP 粘贴到此文件夹；剪切的文件会移动。")
        self.history_actions.add_to_menu(menu)

    def _clipboard_changed(self):
        self._clipboard_revision += 1

    def can_paste_to_directory(self, destination):
        if self._shutdown_requested or self._worker is not None or not os.path.isdir(destination):
            return False
        if not self._files.file_writes_allowed():
            return False
        _, entries = self._files._clipboard_file_payload()
        sources = [entry["source"] for entry in entries]
        targets = [str(Path(destination) / Path(p).name) for p in sources]
        return bool(sources) and self._files.file_operation_paths_allowed(sources + targets)

    def paste_to_directory(self, destination):
        # 菜单打开后剪贴板、目标或运行状态可能变化，执行前重新检查。
        if not self.can_paste_to_directory(destination):
            return False
        action, entries = self._files._clipboard_file_payload()
        clipboard_revision = self._clipboard_revision
        if not entries:
            return False
        sources = tuple(entry["source"] for entry in entries)
        targets = [str(Path(destination) / Path(p).name) for p in sources]
        if not self._files._file_operation_paths_allowed(list(sources) + targets, "粘贴", warn=True):
            return False
        self._displays = {path: path for path in sources}
        self._scopes = {path: self._files._report_scope_path_key(self._files.get_report_row_for_path(path)) for path in sources}
        worker = NativeTransferWorker(sources, os.path.abspath(destination), action != "cut", 0)
        if action == "cut":
            # 保存编辑的守卫可能弹出对话框，代次必须与操作前的文件快照绑定。
            worker.clipboard_revision = clipboard_revision
        self._start(worker, "正在由系统粘贴文件…")
        return True

    @property
    def can_undo(self):
        return bool(self._history.undo) and self._worker is None and not self._shutdown_requested

    @property
    def can_redo(self):
        return bool(self._history.redo) and self._worker is None and not self._shutdown_requested

    def undo(self):
        if self.can_undo:
            self._replay("undo", self._history.undo[-1])

    def redo(self):
        if self.can_redo:
            self._replay("redo", self._history.redo[-1])

    def _replay(self, direction, entries):
        paths = [e.source for e in entries if not is_sidecar(e.source)]
        targets = [e.target for e in entries if not is_sidecar(e.target)]
        if not self._files._file_operation_paths_allowed(paths+targets, "撤销/重做移动", warn=True):
            return
        self._displays = {path: path for path in paths}
        self._scopes = {path: self._files._report_scope_path_key(self._files.get_report_row_for_path(path)) for path in paths}
        worker = MoveReplayWorker(entries, direction)
        self._start(worker, "正在撤销文件移动…" if direction == "undo" else "正在重做文件移动…")

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
        return (mime is not None and not self._shutdown_requested and self._worker is None and bool(self._drag_token)
                and bytes(mime.data(_DRAG_MIME)) == self._drag_token)

    def clear_drag(self):
        self._drag_token = b""
        self._drag_sources = ()
        self._directories._tree.clear_drop_target()

    def submit_drop(self, mime, destination, copy):
        if not self.accepts_drag(mime) or not os.path.isdir(destination):
            return False
        sources = self._drag_sources
        targets = [str(Path(destination)/Path(p).name) for p in sources]
        if not self._files._file_operation_paths_allowed(list(sources)+targets, "拖放移动/复制", warn=True):
            return False
        self._displays, self._scopes = dict(self._drag_displays), dict(self._drag_scopes)
        # 系统窗口不归属主窗口 HWND：应用关闭等待时隐藏主窗，系统取消入口仍须可见。
        worker = NativeTransferWorker(sources, os.path.abspath(destination), copy, 0)
        self._start(worker, "正在由系统复制文件…" if copy else "正在由系统移动文件…")
        return True

    def _start(self, worker, message):
        editor = getattr(self._main,"_photo_edit",None)
        if editor:
            editor.begin_file_operation()
        self._worker = worker
        worker.finished.connect(self._worker_finished)
        self._main.statusBar().showMessage(message)
        self.history_changed.emit()
        worker.start()

    def _worker_finished(self):
        self._finished(self.sender())

    def _finished(self, worker):
        if worker is not self._worker:
            return
        self._worker = None
        # 仅整批剪切完成且剪贴板未换内容时清除；失败/部分完成可继续粘贴。
        if (worker.clipboard_revision is not None
                and worker.clipboard_revision == self._clipboard_revision
                and worker.clipboard_cut_complete
                and not worker.result.error and not worker.result.cancelled):
            QApplication.clipboard().clear()
        editor = getattr(self._main,"_photo_edit",None)
        if editor:
            editor.end_file_operation([*worker.paths,*worker.targets])
        if not self._shutdown_requested:
            if worker.direction:
                self._history.finish(worker.direction, worker.inverse, worker.remaining)
            elif not worker.copy:
                self._history.record(worker.inverse, changed=bool(worker.moved or worker.untracked))
            from app_common.exif_io.writer import invalidate_metadata_cache
            invalidate_metadata_cache([*worker.paths, *(e.source for e in worker.inverse),
                                       *worker.targets])
            if worker.moved:
                self._files._delete_report_rows_for_paths(
                    [self._displays[path] for path in worker.moved], resolved_paths=worker.moved,
                    scope_keys=[self._scopes[path] for path in worker.moved])
                self._files.clear_tag_history()
                self.photos_moved.emit(worker.moved)
            if worker.inverse:
                # 只释放本次确实恢复的实际路径；报告仍从磁盘只读重载。
                restored = {path_key(e.source) for e in worker.inverse}
                tombstones = self._files._report_deleted_path_tombstones
                retained = {identity for identity in tombstones if identity[1] not in restored}
                if retained != tombstones:
                    self._files._report_deleted_path_tombstones = retained
                    self._files._report_full_cache = None
                    self._files._directory_scope_cache.clear()
            # 包含子目录、目标正位于当前根和取消后的部分完成都按实际磁盘刷新。
            directory = self._files.get_current_dir()
            if directory and worker.paths:
                touched = [*worker.destinations, *(str(Path(p).parent) for p in worker.paths)]
                if any(self._directories._is_same_or_parent_path(directory, path) for path in touched):
                    self._files.load_directory(directory, force_reload=True)
            result = worker.result
            message = result.error or ("系统文件操作已取消；已完成的文件保持系统处理结果。" if result.cancelled else "系统文件操作已完成。")
            if worker.direction and worker.remaining and not (result.error or result.cancelled):
                message = "部分文件未移动，可再次撤销/重做重试。"
            if worker.untracked:
                message += f" {len(worker.untracked)} 个覆盖、改名或无法确认去向的文件未加入撤销历史。"
            self._main.statusBar().showMessage(message, 8000)
            if result.error or worker.untracked:
                QMessageBox.warning(self._main, "文件移动/复制结果", message)
        self.history_changed.emit()
        worker.deleteLater()

    def request_shutdown(self):
        self._shutdown_requested = True
        self.history_changed.emit()
        self.clear_drag()
        if self._worker is not None:
            # 系统窗口继续提供取消入口；绝不销毁正在执行文件操作的线程。
            self._worker.requestInterruption()

    def is_shutdown_done(self):
        return self._worker is None
