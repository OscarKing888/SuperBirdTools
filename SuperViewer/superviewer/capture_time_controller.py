"""右键从同名 RAW 更新拍摄时间或整份 XMP；后台执行，仅失败时展示报告。"""
from __future__ import annotations

from pathlib import Path
import os
import queue
import threading
import time

from app_common.exif_io.exiftool_runner import exiftool_worker_session, exiftool_read_request
from app_common.exif_io.xmp_sidecar import same_stem_key
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .file_context_menu import file_menu_group
from .capture_time_update import TARGET_EXTENSIONS, CaptureTimeResult, update_capture_times
from .raw_xmp_update import update_all_information
from .metadata_result_sync import MetadataResultSync
from .qt_compat import QThread
try:
    from PyQt6.QtCore import QObject, QTimer
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QFileDialog
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QTimer
    from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QFileDialog


class CaptureTimeWorker(QThread):
    def __init__(self, aliases, directory, *, all_information=False, refresh_paths=()):
        super().__init__()
        self.aliases, self.directory = aliases, directory
        self.all_information = all_information
        self.refresh_paths = list(refresh_paths)
        self.cancelled = threading.Event()
        self.results = queue.Queue(maxsize=128)

    def stop(self):
        self.cancelled.set()

    def run(self):
        delivered = set()
        paths = [source for _, source in self.aliases if source]
        try:
            for display, source in self.aliases:
                if source is None:
                    self.results.put(CaptureTimeResult(display, '无法解析所选照片的实际文件'))
            with exiftool_worker_session(), exiftool_read_request(self.cancelled.is_set, timeout=20):
                update = update_all_information if self.all_information else update_capture_times
                if self.all_information:
                    from app_common.exif_io.photo_meta import PhotoMetaDataProxy
                    from app_common.file_browser._workers import MetadataLoader
                    reader = PhotoMetaDataProxy()
                    normalizer = MetadataLoader([], reader)
                    siblings = {}
                    for path in self.refresh_paths:
                        path = os.path.normpath(os.path.abspath(path))
                        siblings.setdefault(same_stem_key(path), []).append(path)
                    refreshed_key, metadata_by_path = None, {}
                for result in update(paths, self.directory, cancelled=self.cancelled.is_set):
                    if self.all_information and result.updates:
                        key = (same_stem_key(result.source), result.saved_fingerprint)
                        if key != refreshed_key:
                            metadata_by_path = {}
                            # 共用 XMP 的图像可能尺寸/格式不同，各自读取原图回退信息。
                            for path in dict.fromkeys([result.source, *siblings.get(key[0], [])]):
                                metadata = reader.read(path)
                                metadata_by_path[path] = {**metadata, **normalizer._parse_rec(metadata)}
                            refreshed_key = key
                        result.metadata_by_path = metadata_by_path
                        result.updates = metadata_by_path[result.source]
                    delivered.add(result.source)
                    # 已提交结果必须交付；关窗期间 GUI 仍排空队列。
                    self.results.put(result)
        except Exception as exc:
            for path in dict.fromkeys(paths):
                if path not in delivered:
                    self.results.put(CaptureTimeResult(path, f'更新失败：{exc}'))


class CaptureTimeReport(QDialog):
    def __init__(self, parent, succeeded, failures, *, operation='更新拍摄时间'):
        super().__init__(parent)
        self.setWindowTitle(operation + '：失败报告')
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'已更新 {succeeded} 个文件；{len(failures)} 个文件未更新。'))
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlainText('\n\n'.join(f'{r.source}\n原因：{r.message}' for r in failures))
        layout.addWidget(self.details)
        close = QPushButton('关闭')
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class CaptureTimeController(QObject):
    def __init__(self, window, files):
        super().__init__(window)
        self._main, self._files = window, files
        self._sync = MetadataResultSync(files)
        self._worker = self._report = None
        self._shutdown_requested = False
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._drain)
        files.add_file_context_menu_extender(self.extend_file_menu)

    @property
    def busy(self):
        return self._worker is not None

    @file_menu_group("capture")
    def extend_file_menu(self, menu, paths):
        for all_information, extensions, operation in (
            (False, TARGET_EXTENSIONS, '更新拍摄时间'),
            (True, SUPPORTED_IMAGE_EXTENSIONS, '更新所有信息'),
        ):
            photos = [p for p in paths if Path(p).suffix.lower() in extensions]
            if not photos:
                continue
            running = self.busy and self._worker.all_information == all_information
            label = f'正在{operation}…' if running else f'从同名 RAW {operation}…'
            action = menu.addAction(label, lambda checked=False, p=photos, full=all_information:
                                    self.choose_directory(p, all_information=full))
            if all_information:
                action.setToolTip('递归查找同名 RAW，将其整份 XMP 复制到选中图像旁；覆盖已有 XMP 的全部信息')
            action.setEnabled(not self.busy and not self._shutdown_requested and
                              self._files._file_writes_allowed(operation))

    def choose_directory(self, paths, *, all_information=False):
        title = '选择 RAW 目录（包含子目录）'
        if all_information:
            title += ' — 复制 XMP 并覆盖所有信息'
        directory = QFileDialog.getExistingDirectory(self._main, title, str(Path(paths[0]).parent))
        if directory:
            self.start(paths, directory, all_information=all_information)

    def start(self, paths, directory, *, all_information=False):
        operation = '更新所有信息' if all_information else '更新拍摄时间'
        if self.busy or self._shutdown_requested or not self._files._file_writes_allowed(operation, warn=True):
            return False
        aliases = []
        for path in dict.fromkeys(paths):
            if Path(path).suffix.lower() not in (SUPPORTED_IMAGE_EXTENSIONS if all_information else TARGET_EXTENSIONS):
                continue
            try:
                source = self._files._resolve_source_path_for_action(path)
            except Exception:
                source = None
            aliases.append((path, str(Path(source).absolute()) if source else None))
        if not aliases:
            return False
        if self._report is not None:
            self._report.close()
            self._report.deleteLater()
            self._report = None
        self._succeeded, self._failures, self._finished_received = 0, [], False
        self._display_paths_by_source = {}
        for display, source in aliases:
            key = os.path.normcase(os.path.abspath(source or display))
            self._display_paths_by_source.setdefault(key, []).append(display)
        refresh_paths = [*getattr(self._files, '_all_files', ()), *(s for _, s in aliases if s)] if all_information else ()
        self._worker = worker = CaptureTimeWorker(aliases, directory, all_information=all_information,
                                                refresh_paths=refresh_paths)
        worker.finished.connect(lambda w=worker: self._finished(w))
        self._timer.start()
        worker.start()
        return True

    def _finished(self, worker):
        if worker is self._worker:
            self._finished_received = True
            self._drain()

    def _drain(self):
        worker = self._worker
        if worker is None:
            return
        batch, began = [], time.monotonic()
        while len(batch) < 16 and time.monotonic() - began < .008:
            try:
                batch.append(worker.results.get_nowait())
            except queue.Empty:
                break
        self._succeeded += sum(bool(r.updates) for r in batch)
        self._failures.extend(r for r in batch if not r.updates)
        if not self._shutdown_requested:
            self._sync.sync(batch, [(d, s) for d, s in worker.aliases if s], replace=worker.all_information)
            # 仅本轮已返回结果的照片取消选择；未匹配 RAW 的保留，便于换目录重试。
            deselected = []
            for result in batch:
                if not result.missing_raw:
                    key = os.path.normcase(os.path.abspath(result.source))
                    deselected.extend(self._display_paths_by_source.get(key, ()))
            if deselected:
                self._files.deselect_display_paths_silently(deselected)
        if self._finished_received and worker.results.empty():
            self._timer.stop()
            self._worker = None
            if self._failures and not self._shutdown_requested:
                operation = '更新所有信息' if worker.all_information else '更新拍摄时间'
                self._report = CaptureTimeReport(self._main, self._succeeded, self._failures, operation=operation)
                self._report.show()
            worker.deleteLater()

    def request_shutdown(self):
        self._shutdown_requested = True
        if self._worker is not None:
            self._worker.stop()
        if self._report is not None:
            self._report.close()

    def is_shutdown_done(self):
        return self._worker is None
