"""右键更新 PNG/JPEG 拍摄时间；后台执行，仅失败时展示结果报告。"""
from __future__ import annotations

from pathlib import Path
import os
import queue
import threading
import time

from app_common.exif_io.exiftool_runner import exiftool_worker_session, exiftool_read_request
from .capture_time_update import TARGET_EXTENSIONS, CaptureTimeResult, update_capture_times
from .metadata_result_sync import MetadataResultSync
from .qt_compat import QThread
try:
    from PyQt6.QtCore import QObject, QTimer
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QFileDialog
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QTimer
    from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QFileDialog


class CaptureTimeWorker(QThread):
    def __init__(self, aliases, directory):
        super().__init__()
        self.aliases, self.directory = aliases, directory
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
                for result in update_capture_times(paths, self.directory, cancelled=self.cancelled.is_set):
                    delivered.add(result.source)
                    # 已提交结果必须交付；关窗期间 GUI 仍排空队列。
                    self.results.put(result)
        except Exception as exc:
            for path in dict.fromkeys(paths):
                if path not in delivered:
                    self.results.put(CaptureTimeResult(path, f'更新失败：{exc}'))


class CaptureTimeReport(QDialog):
    def __init__(self, parent, succeeded, failures):
        super().__init__(parent)
        self.setWindowTitle('更新拍摄时间：失败报告')
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

    def extend_file_menu(self, menu, paths):
        photos = [p for p in paths if Path(p).suffix.lower() in TARGET_EXTENSIONS]
        if not photos:
            return
        label = '正在更新拍摄时间…' if self.busy else '从同名 RAW 更新拍摄时间…'
        action = menu.addAction(label, lambda: self.choose_directory(photos))
        action.setEnabled(not self.busy and not self._shutdown_requested and
                          self._files._file_writes_allowed('更新拍摄时间'))

    def choose_directory(self, paths):
        directory = QFileDialog.getExistingDirectory(self._main, '选择 RAW 目录（包含子目录）', str(Path(paths[0]).parent))
        if directory:
            self.start(paths, directory)

    def start(self, paths, directory):
        if self.busy or self._shutdown_requested or not self._files._file_writes_allowed('更新拍摄时间', warn=True):
            return False
        aliases = []
        for path in dict.fromkeys(paths):
            if Path(path).suffix.lower() not in TARGET_EXTENSIONS:
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
        self._worker = worker = CaptureTimeWorker(aliases, directory)
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
            self._sync.sync(batch, [(d, s) for d, s in worker.aliases if s])
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
                self._report = CaptureTimeReport(self._main, self._succeeded, self._failures)
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
