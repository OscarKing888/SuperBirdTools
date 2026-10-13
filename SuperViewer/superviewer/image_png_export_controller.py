"""图片右键 PNG 导出；串行后台处理、可取消及真实线程完成后释放。"""
from __future__ import annotations

import html
import os
from pathlib import Path
import queue
import threading

from .bird_identification_controller import BirdIDProgressDialog
from .image_png_export import export_image_png, is_exportable_image
from .qt_compat import QFileDialog, QThread, QTimer

try:
    from PyQt6.QtCore import QObject
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject


class PngExportWorker(QThread):
    def __init__(self, paths, destination):
        super().__init__()
        self.paths, self.destination = paths, destination
        self.cancelled = threading.Event()
        self.results = queue.Queue(maxsize=32)
        self._lock = threading.Lock()
        self._progress = (0, "")

    def progress(self):
        with self._lock:
            return self._progress

    def run(self):
        for index, path in enumerate(self.paths, 1):
            if self.cancelled.is_set():
                break
            with self._lock:
                self._progress = (index, path)
            result = export_image_png(path, self.destination, cancelled=self.cancelled.is_set)
            # 关闭期间 GUI 继续排空有界队列，不丢失已导出的结果。
            self.results.put(result)


class ImagePngExportController(QObject):
    def __init__(self, window, files):
        super().__init__(window)
        self._main, self._files = window, files
        self._worker = self._dialog = None
        self._shutdown_requested = self._finished_received = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._drain)
        files.add_file_context_menu_extender(self.extend_file_menu)

    def extend_file_menu(self, menu, paths):
        images = [path for path in paths if is_exportable_image(path)]
        if not images:
            return
        title = "导出为 PNG…" if len(images) == 1 else f"导出为 PNG（{len(images)} 张）…"
        action = menu.addAction(title, lambda: self.choose_destination(images))
        action.setToolTip("按原尺寸导出；RAW 使用最大可用内嵌 JPG；同名自动编号")
        action.setEnabled(self._worker is None and not self._shutdown_requested)
        if self._worker is not None:
            menu.addAction("查看 PNG 导出进度…", self.show_progress)

    def _sources(self, paths):
        sources = {}
        for path in paths:
            if is_exportable_image(path):
                source = self._files._resolve_source_path_for_action(path) or path
                sources.setdefault(os.path.normcase(os.path.abspath(source)), source)
        return list(sources.values())

    def choose_destination(self, paths):
        if self._worker is not None or self._shutdown_requested:
            return
        sources = self._sources(paths)
        if not sources:
            return
        destination = QFileDialog.getExistingDirectory(
            self._main, "选择 PNG 输出目录（同名自动编号）", str(Path(sources[0]).parent))
        if destination:
            self.start(sources, destination)

    def start(self, paths, destination=None):
        if self._worker is not None or self._shutdown_requested:
            return False
        sources = self._sources(paths)
        if not sources:
            return False
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        self._done = self._failed = self._saved = 0
        self._finished_received = False
        self._dialog = BirdIDProgressDialog(self._main)
        self._dialog.setWindowTitle("导出为 PNG")
        self._dialog.label.setText("准备导出图片…")
        self._dialog.summary.setWordWrap(True)
        self._dialog.summary.setText("原尺寸导出，RAW 使用内嵌 JPG；同名自动编号。")
        self._dialog.bar.setRange(0, len(sources))
        self._dialog.details.append(html.escape(f"输出目录：{destination or '各图片所在目录'}"))
        self._dialog.cancel_requested.connect(self.stop)
        self._dialog.rejected.connect(self.stop)
        worker = self._worker = PngExportWorker(sources, destination)
        worker.finished.connect(lambda w=worker: self._finished(w))
        self.show_progress()
        self._timer.start()
        worker.start()
        return True

    def show_progress(self):
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()

    def _finished(self, worker):
        if worker is self._worker:
            self._finished_received = True
            self._drain()

    def _drain(self):
        worker = self._worker
        if worker is None:
            return
        for _ in range(16):
            try:
                result = worker.results.get_nowait()
            except queue.Empty:
                break
            self._done += 1
            self._saved += bool(result.output_path)
            self._failed += bool(result.error)
            state = result.error or ("已取消" if result.cancelled else
                                     f"完成：{result.width} × {result.height}\n{result.output_path}")
            self._dialog.details.append(html.escape(f"{result.source}\n{state}").replace("\n", "<br>"))
        index, path = worker.progress()
        self._dialog.bar.setValue(self._done)
        if not self._shutdown_requested:
            self._dialog.label.setText(f"正在导出 {index}/{len(worker.paths)}：{Path(path).name}")
            self._dialog.summary.setText(f"已保存 {self._saved} 张，失败 {self._failed} 张。取消后保留已完成的 PNG。")
        if self._finished_received and worker.results.empty():
            self._timer.stop()
            self._worker = None
            state = "已取消，已完成的 PNG 保留。" if worker.cancelled.is_set() else "导出结束。"
            self._dialog.finish(f"{state}已保存 {self._saved} 张，失败 {self._failed} 张。")
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
