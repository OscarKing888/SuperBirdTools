# -*- coding: utf-8 -*-
"""照片/目录右键降噪；控制器保有批次直到真实 QThread.finished。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import html
import os
from pathlib import Path
import threading
import traceback

from app_common.log import get_logger
from app_common.superviewer_user_options import get_runtime_user_options
from image_denoise.types import DenoiseCancelled, DenoiseOptions

from .file_context_menu import file_menu_group
from .qt_compat import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                        QPushButton, QTextEdit, QThread, QVBoxLayout, pyqtSignal)

try:
    from PyQt6.QtCore import QObject, QUrl
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import QProgressBar
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QUrl
    from PyQt5.QtGui import QDesktopServices
    from PyQt5.QtWidgets import QProgressBar

_log = get_logger("image_denoise.viewer")


def current_denoise_options() -> DenoiseOptions:
    options = get_runtime_user_options()
    return DenoiseOptions(**{key: options[f"denoise_{key}"] for key in
                            ("output_mode", "subdir", "output_directory", "format",
                             "strength", "device", "workers")})


@dataclass(frozen=True)
class DenoiseJob:
    title: str
    inputs: tuple[str, ...]
    recursive: bool = False
    options: DenoiseOptions | None = None


class DenoiseWorker(QThread):
    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int, str)
    tile_progress = pyqtSignal(str, int, int)
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, job, holder, pool=None):
        super().__init__()
        self.job, self._holder, self._pool = job, holder, pool
        self._cancel = threading.Event()
        self.results = []

    def stop(self):
        self._cancel.set()
        self.requestInterruption()

    def cancelled(self):
        return self._cancel.is_set() or self.isInterruptionRequested()

    def _publish_result(self, result):
        if result.status == "success" and result.destination:
            from image_denoise.preview import register_denoised_output

            register_denoised_output(result.source, result.destination)
            self._holder._preview_history.record(result.source, result.destination)
        self.result_ready.emit(result)

    def run(self):
        from image_denoise.batch import collect_image_paths, run_batch

        try:
            self.status_changed.emit("正在扫描照片…")
            paths = collect_image_paths(self.job.inputs, recursive=self.job.recursive,
                                        options=self.job.options, cancelled=self.cancelled)
            if self.cancelled():
                return
            if not paths:
                self.failed.emit("没有找到可降噪的照片。")
                return
            self.results = run_batch(paths, self.job.options, pool=self._pool,
                                     engine=(self._holder.engine(self.job.options.device)
                                             if self.job.options.strength else None),
                                     cancelled=self.cancelled, on_result=self._publish_result,
                                     on_progress=self.progress_changed.emit,
                                     on_status=self.status_changed.emit, on_tile=self.tile_progress.emit,
                                     serial=self._pool is None)
        except DenoiseCancelled:
            pass
        except Exception as exc:
            _log.error("[Denoise] job failed: %s", traceback.format_exc())
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def _summary(counts) -> str:
    labels = (("success", "成功"), ("failed", "失败"), ("skipped", "跳过"), ("cancelled", "取消"))
    return "，".join(f"{label} {counts[status]}" for status, label in labels if counts[status])


class DenoiseProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumWidth(560)
        self._running = True
        self.description = QLabel("NAFNet RGB 降噪；RAW 先渲染为 sRGB，原图保持不变。", self)
        self.description.setWordWrap(True)
        self.label = QLabel("正在准备…", self)
        self.label.setWordWrap(True)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)
        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        self.details = QTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(160)
        self.details.document().setMaximumBlockCount(1000)
        self.details.hide()
        self.output_dirs = QComboBox(self)
        self.output_dirs.setMinimumContentsLength(20)
        self.output_dirs.setEnabled(False)
        self.open_button = QPushButton("打开输出目录", self)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_output)
        self.button = QPushButton("停止", self)
        self.button.clicked.connect(self._on_button)
        row = QHBoxLayout()
        row.addWidget(self.output_dirs, 1)
        row.addWidget(self.open_button)
        row.addWidget(self.button)
        layout = QVBoxLayout(self)
        for widget in (self.description, self.label, self.bar, self.summary, self.details):
            layout.addWidget(widget)
        layout.addLayout(row)

    def _open_output(self):
        directory = self.output_dirs.currentData()
        if directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def _on_button(self):
        if self._running:
            self.button.setEnabled(False)
            self.button.setText("正在停止…")
            self.cancel_requested.emit()
        else:
            self.close()

    def closeEvent(self, event):
        if self._running:
            self.cancel_requested.emit()
        super().closeEvent(event)

    def set_progress(self, done, total, source):
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self.label.setText(f"已处理 {done}/{total}" + (f"：{os.path.basename(source)}" if source else ""))

    def add_result(self, result):
        if result.status == "success":
            directory = str(Path(result.destination).parent)
            if self.output_dirs.findData(directory) < 0:
                self.output_dirs.addItem(directory, directory)
            self.output_dirs.setEnabled(True)
            self.open_button.setEnabled(True)
        if result.error:
            self.details.show()
            self.details.append(html.escape(f"{result.source}：{result.error}"))

    def finish(self, text):
        self._running = False
        self.label.setText(text)
        self.button.setEnabled(True)
        self.button.setText("关闭")


class DenoiseController(QObject):
    output_ready = pyqtSignal(str, str)
    batch_finished = pyqtSignal()  # after the last output_ready of a batch (also stopped/failed)

    def __init__(self, main_window, file_list, dir_browser=None):
        super().__init__(main_window)
        from .denoise_preview_history import DenoisePreviewHistory
        from .paths_settings import _get_user_state_dir
        from image_denoise.preview import register_denoised_output

        self._preview_history = DenoisePreviewHistory(Path(_get_user_state_dir()) / "denoise_previews.json")
        for source, destination in self._preview_history.entries():
            register_denoised_output(source, destination)
        self._main, self._file_list = main_window, file_list
        self._worker = self._dialog = self._engine = None
        self._engine_device = None
        self._engine_lock = threading.Lock()
        self._shutdown_requested = False
        self._counts = Counter()
        self._failure = ""
        file_list.add_file_context_menu_extender(self.extend_file_menu)
        if dir_browser is not None:
            dir_browser.add_context_menu_extender(self.extend_directory_menu)

    @property
    def busy(self):
        return self._worker is not None

    def engine(self, device):
        with self._engine_lock:
            if self._engine is not None and device != self._engine_device:
                self._engine.close()
                self._engine = None
            if self._engine is None:
                from image_denoise.engine import DenoiseEngine

                self._engine = DenoiseEngine(device)
                self._engine_device = device
            return self._engine

    def _busy_menu(self, menu):
        show = menu.addAction("查看降噪进度")
        show.triggered.connect(lambda _checked=False: self.show_progress())
        stop = menu.addAction("停止降噪")
        stop.triggered.connect(lambda _checked=False: self.stop())

    def show_progress(self):
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()
            self._dialog.activateWindow()

    @file_menu_group("process")
    def extend_file_menu(self, menu, paths):
        if self.busy:
            self._busy_menu(menu)
            return
        if paths:
            label = "降噪" if len(paths) == 1 else f"批量降噪（{len(paths)} 张）"
            action = menu.addAction(label)
            action.setToolTip("NAFNet RGB 降噪；RAW 先渲染，原图保持不变。")
            action.triggered.connect(lambda _checked=False, p=list(paths): self.start_for_paths(p))

    def extend_directory_menu(self, menu, directory):
        sub = menu.addMenu("批量降噪")
        if self.busy:
            self._busy_menu(sub)
            return
        for label, recursive in (("降噪当前目录", False), ("降噪目录及子目录", True)):
            action = sub.addAction(label)
            action.triggered.connect(lambda _checked=False, r=recursive: self.start(
                DenoiseJob(f"批量降噪 - {Path(directory).name or directory}", (directory,), r)))

    def start_for_paths(self, paths):
        resolve = getattr(self._file_list, "_resolve_source_path_for_action", None)
        sources = []
        for path in paths:
            try:
                source = resolve(path) if callable(resolve) else path
                if not source:
                    raise ValueError("无法找到照片原文件")
            except Exception as exc:
                _log.error("[Denoise] source path resolution failed path=%r: %s", path, traceback.format_exc())
                QMessageBox.information(self._main, "批量降噪", f"无法解析照片原文件：{path}\n{exc}")
                return False
            sources.append(str(source))
        return self.start(DenoiseJob(f"批量降噪 - {len(sources)} 张", tuple(sources)))

    def start(self, job):
        if self._shutdown_requested:
            return False
        if self.busy:
            self.show_progress()
            return False
        options = job.options or current_denoise_options()
        if options.output_mode == "ask":
            initial = options.output_directory
            if not initial and job.inputs:
                source = Path(job.inputs[0])
                initial = str(source if source.is_dir() else source.parent)
            selected = QFileDialog.getExistingDirectory(self._main, "选择本批降噪输出目录", initial)
            if not selected:
                return False
            options = replace(options, output_directory=selected)
        try:
            from image_denoise.batch import validate_options

            validate_options(options)
        except ValueError as exc:
            QMessageBox.information(self._main, "批量降噪", str(exc))
            return False
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        pool_getter = getattr(self._file_list, "background_work_pool", None)
        pool = pool_getter() if callable(pool_getter) else None
        self._worker = worker = DenoiseWorker(replace(job, options=options), self, pool)
        self._dialog = dialog = DenoiseProgressDialog(self._main, job.title)
        self._counts, self._failure = Counter(), ""
        dialog.cancel_requested.connect(lambda w=worker: self._request_stop(w))
        worker.status_changed.connect(lambda text, w=worker: self._on_status(w, text))
        worker.progress_changed.connect(lambda d, t, p, w=worker: self._on_progress(w, d, t, p))
        worker.tile_progress.connect(lambda p, d, t, w=worker: self._on_tile(w, p, d, t))
        worker.result_ready.connect(lambda result, w=worker: self._on_result(w, result))
        worker.failed.connect(lambda message, w=worker: self._on_failure(w, message))
        worker.finished.connect(lambda w=worker: self._on_finished(w))
        _log.info("[Denoise] start inputs=%s recursive=%s options=%s", len(job.inputs), job.recursive, options)
        dialog.show()
        worker.start()
        return True

    def stop(self):
        if self._worker is not None:
            self._worker.stop()

    def _request_stop(self, worker):
        if worker is self._worker:
            worker.stop()

    def _on_status(self, worker, text):
        if worker is self._worker and not self._shutdown_requested and self._dialog is not None:
            self._dialog.label.setText(text)

    def _on_progress(self, worker, done, total, source):
        if worker is self._worker and not self._shutdown_requested and self._dialog is not None:
            self._dialog.set_progress(done, total, source)

    def _on_tile(self, worker, source, done, total):
        self._on_status(worker, f"正在降噪 {os.path.basename(source)}：{done}/{total} 块")

    def _on_result(self, worker, result):
        if worker is not self._worker or self._shutdown_requested:
            return
        self._counts[result.status] += 1
        _log.info("[Denoise] result source=%r destination=%r status=%s error=%s",
                  result.source, result.destination, result.status, result.error)
        if result.status == "success" and result.destination:
            self.output_ready.emit(result.source, result.destination)
        if self._dialog is not None:
            self._dialog.add_result(result)
            self._dialog.summary.setText(_summary(self._counts))

    def _on_failure(self, worker, message):
        if worker is self._worker:
            self._failure = message

    def _on_finished(self, worker):
        if worker is not self._worker:
            return
        self._worker = None
        if worker.results:
            self._counts = Counter(result.status for result in worker.results)
        if self._shutdown_requested:
            if self._dialog is not None:
                self._dialog._running = False
                self._dialog.close()
                self._dialog.deleteLater()
                self._dialog = None
            self._release_engine()
        elif self._dialog is not None:
            if self._failure:
                text = f"降噪未完成：{self._failure}"
            elif worker.cancelled():
                text = "已停止。已完成的成片保留。"
            elif self._counts["failed"]:
                text = "降噪结束，部分照片未能完成。" if self._counts["success"] else "降噪失败，请查看下方原因。"
            else:
                text = "降噪完成。"
            self._dialog.finish(text)
            self._dialog.summary.setText(_summary(self._counts))
        worker.deleteLater()
        if not self._shutdown_requested:
            self.batch_finished.emit()

    def request_shutdown(self):
        self._shutdown_requested = True
        if self._worker is not None:
            self._worker.stop()
        else:
            self._release_engine()

    def is_shutdown_done(self):
        return self._worker is None

    def _release_engine(self):
        with self._engine_lock:
            engine, self._engine = self._engine, None
            self._engine_device = None
        if engine is not None:
            engine.close()
