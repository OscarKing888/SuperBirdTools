# -*- coding: utf-8 -*-
"""拍摄地点编辑、剪贴板和后台批量保存。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import html
import os
import queue
import threading
import time

from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from app_common.shooting_location import shooting_location, write_shooting_location
from .file_context_menu import file_menu_group
from .bird_identification import _fingerprint
from .bird_identification_controller import BirdIDProgressDialog
from .metadata_result_sync import MetadataResultSync
from .qt_compat import QApplication, QMessageBox, QThread, QTimer
try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import QInputDialog
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject
    from PyQt5.QtWidgets import QInputDialog


@dataclass
class LocationResult:
    source: str
    message: str = ""
    updates: dict = field(default_factory=dict)
    saved_fingerprint: tuple | None = None


def save_location(path, text):
    source = os.path.normpath(os.path.abspath(path))
    with xmp_sidecar_write_lock(source):
        updates = write_shooting_location(source, text)
        stamp = _fingerprint(PhotoMetaDataXMP().sidecar_path_for(source))
    return LocationResult(source, updates=updates, saved_fingerprint=stamp)


class LocationWorker(QThread):
    def __init__(self, paths, text):
        super().__init__()
        self.paths, self.text = paths, text
        self.results = queue.Queue(maxsize=128)
        self.cancelled = threading.Event()

    def run(self):
        for path in self.paths:
            if self.cancelled.is_set():
                break
            try:
                result = save_location(path, self.text)
            except Exception as exc:
                result = LocationResult(path, str(exc))
            # 已写结果必须交付；关闭期间仍排空队列，直到真实 finished。
            self.results.put(result)


class ShootingLocationController(QObject):
    def __init__(self, window, files):
        super().__init__(window)
        self._main, self._files = window, files
        self._sync = MetadataResultSync(files)
        self._worker = self._dialog = None
        self._shutdown_requested = self._finished_received = False
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._drain)
        files.add_file_context_menu_extender(self.extend_file_menu)

    @property
    def busy(self):
        return self._worker is not None

    @file_menu_group("capture")
    def extend_file_menu(self, menu, paths):
        photos = [p for p in paths if Path(p).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS]
        if not photos:
            return
        sub = menu.addMenu("拍摄地点")
        edit = sub.addAction("修改拍摄地点…", lambda: self.edit(photos))
        copy = sub.addAction("复制拍摄地点", lambda: self.copy(photos[0]))
        paste = sub.addAction("粘贴拍摄地点", lambda: self.start(photos, QApplication.clipboard().text()))
        copy.setEnabled(len(photos) == 1)
        edit.setEnabled(self._worker is None)
        paste.setEnabled(self._worker is None and bool(QApplication.clipboard().text().strip()))

    def _source(self, path):
        source = self._files._resolve_source_path_for_action(path)
        if not source:
            raise FileNotFoundError(f"找不到照片原文件：{path}")
        return source

    def copy(self, path):
        try:
            # 只读侧车，不等待原图 EXIF；支持目录元数据尚未读完时复制。
            text = shooting_location(PhotoMetaDataXMP().read(self._source(path)))
            QApplication.clipboard().setText(text)
        except Exception as exc:
            QMessageBox.warning(self._main, "复制拍摄地点失败", str(exc))

    def edit(self, paths):
        current = shooting_location(self._files.cached_photo_metadata_for_path(paths[0])) if len(paths) == 1 else ""
        text, accepted = QInputDialog.getText(
            self._main, "修改拍摄地点", f"为 {len(paths)} 张照片设置拍摄地点（留空可清除）：", text=current,
        )
        if accepted:
            self.start(paths, text)

    def save_one(self, path, text):
        if self._shutdown_requested or self._worker is not None:
            raise RuntimeError("正在保存批量拍摄地点，请稍后再试。")
        source = self._source(path)
        result = save_location(source, text)
        self._sync.sync([result], [(path, source)])
        return True

    def start(self, paths, text):
        if self._shutdown_requested or self._worker is not None:
            return False
        if not self._files._file_writes_allowed("修改拍摄地点", warn=True):
            return False
        try:
            aliases = [(path, self._source(path)) for path in paths]
        except Exception as exc:
            QMessageBox.warning(self._main, "拍摄地点", str(exc))
            return False
        # RAW/JPEG 同侧车只提交一次，界面同步仍涵盖所有同名照片。
        unique = {}
        for _, source in aliases:
            key = os.path.normcase(os.path.abspath(PhotoMetaDataXMP().sidecar_path_for(source)))
            unique.setdefault(key, source)
        if not unique:
            return False
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        self._aliases = aliases
        self._done = self._failed = 0
        self._finished_received = False
        self._dialog = BirdIDProgressDialog(self._main)
        self._dialog.setWindowTitle("保存拍摄地点")
        self._dialog.label.setText("正在保存拍摄地点…")
        self._dialog.cancel_requested.connect(self.stop)
        self._worker = worker = LocationWorker(list(unique.values()), text)
        worker.finished.connect(lambda w=worker: self._finished(w))
        self._dialog.show()
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
        batch, started = [], time.monotonic()
        while len(batch) < 16 and time.monotonic() - started < .008:
            try:
                batch.append(worker.results.get_nowait())
            except queue.Empty:
                break
        self._done += len(batch)
        self._failed += sum(not result.updates for result in batch)
        if not self._shutdown_requested:
            self._sync.sync(batch, self._aliases)
            for result in batch:
                if result.message:
                    self._dialog.details.append(html.escape(f"{result.source}：{result.message}"))
            self._dialog.bar.setRange(0, len(worker.paths))
            self._dialog.bar.setValue(self._done)
            self._dialog.label.setText(f"已处理 {self._done}/{len(worker.paths)}")
            self._dialog.summary.setText(f"已保存 {self._done - self._failed}，失败 {self._failed}")
        if self._finished_received and worker.results.empty():
            self._timer.stop()
            self._worker = None
            self._dialog.finish("已停止，已保存的地点保留。" if worker.cancelled.is_set() else "拍摄地点保存完成。")
            if self._shutdown_requested:
                self._dialog.close()
            worker.deleteLater()

    def stop(self):
        if self._worker is not None:
            self._worker.cancelled.set()

    def request_shutdown(self):
        self._shutdown_requested = True
        self.stop()

    def is_shutdown_done(self):
        return self._worker is None
