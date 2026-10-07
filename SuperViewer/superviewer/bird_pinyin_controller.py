# -*- coding: utf-8 -*-
"""照片/目录拼音补全；有界结果队列与分批界面更新。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import html
from pathlib import Path
import queue
import threading
import time

from .bird_identification import collect_paths
from .bird_identification_controller import BirdIDProgressDialog
from .bird_pinyin_update import PinyinUpdater
from .metadata_result_sync import MetadataResultSync
from .qt_compat import QThread, QMessageBox
try:
    from PyQt6.QtCore import QObject, QTimer
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QTimer


@dataclass(frozen=True)
class PinyinJob:
    inputs: tuple[str, ...]
    recursive: bool = False
    display_paths: tuple[tuple[str, str], ...] = ()


class PinyinWorker(QThread):
    def __init__(self, job):
        super().__init__()
        self.job = job
        self.results = queue.Queue(maxsize=128)
        self.cancelled = threading.Event()
        self.total = 0
        self.failure = ""

    def stop(self):
        self.cancelled.set()

    def run(self):
        try:
            paths = collect_paths(self.job.inputs, recursive=self.job.recursive, cancelled=self.cancelled.is_set)
            self.total = len(paths)
            if not paths and not self.cancelled.is_set():
                self.failure = "没有找到受支持的照片"
            updater = PinyinUpdater()
            for path in paths:
                if self.cancelled.is_set():
                    break
                result = updater.update(path, cancelled=self.cancelled.is_set)
                # 已提交结果必须送达；停止/关闭期间 GUI 定时器仍会排空队列。
                while True:
                    try:
                        self.results.put(result, timeout=.1)
                        break
                    except queue.Full:
                        continue
        except Exception as exc:
            self.failure = f"[BirdPinyin] {exc}"


class PinyinController(QObject):
    def __init__(self, window, files, directory_browser=None):
        super().__init__(window)
        self._main, self._files = window, files
        self._sync = MetadataResultSync(files)
        self._worker = self._dialog = None
        self._shutdown_requested = False
        self._finished_received = False
        self._counts = Counter()
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._drain)
        files.add_file_context_menu_extender(self.extend_file_menu)
        if directory_browser is not None:
            directory_browser.add_context_menu_extender(self.extend_directory_menu)

    @property
    def busy(self):
        return self._worker is not None

    def show_progress(self):
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()

    def _busy_menu(self, menu):
        menu.addAction("查看拼音更新进度", self.show_progress)
        menu.addAction("停止更新拼音", self.stop)

    def extend_file_menu(self, menu, paths):
        if self.busy:
            self._busy_menu(menu)
        elif paths:
            text = "更新拼音" if len(paths) == 1 else f"更新拼音（{len(paths)} 张）"
            menu.addAction(text, lambda: self.start_for_paths(list(paths)))

    def extend_directory_menu(self, menu, directory):
        sub = menu.addMenu("更新拼音")
        if self.busy:
            self._busy_menu(sub)
        else:
            for text, recursive in (("更新当前目录拼音", False), ("更新目录及子目录拼音", True)):
                sub.addAction(text, lambda _checked=False, r=recursive: self.start(PinyinJob((directory,), r)))

    def start_for_paths(self, paths):
        resolve = getattr(self._files, "_resolve_source_path_for_action", None)
        sources = []
        try:
            for path in paths:
                source = resolve(path) if callable(resolve) else path
                if not source:
                    raise ValueError(f"找不到照片原文件：{path}")
                sources.append(str(source))
        except Exception as exc:
            QMessageBox.information(self._main, "更新拼音", str(exc))
            return False
        return self.start(PinyinJob(tuple(sources), display_paths=tuple(zip(paths, sources))))

    def start(self, job):
        if self._shutdown_requested:
            return False
        if self.busy:
            self.show_progress()
            return False
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        self._counts, self._finished_received = Counter(), False
        self._dialog = BirdIDProgressDialog(self._main)
        self._dialog.setWindowTitle("更新鸟名拼音")
        self._dialog.label.setText("正在扫描照片；仅补全缺失或鸟名已变更的拼音…")
        self._dialog.cancel_requested.connect(self.stop)
        self._worker = worker = PinyinWorker(job)
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
        for result in batch:
            self._counts[result.status] += 1
        if batch and not self._shutdown_requested:
            self._sync.sync(batch, worker.job.display_paths)
            lines = [f"{Path(r.source).name}：{r.message}" for r in batch]
            self._dialog.details.append(html.escape("\n".join(lines)).replace("\n", "<br>"))
            done = sum(self._counts.values())
            self._dialog.bar.setRange(0, max(1, worker.total))
            self._dialog.bar.setValue(done)
            self._dialog.label.setText(f"已处理 {done}/{worker.total}")
            self._dialog.summary.setText(f"已更新 {self._counts['success']}，跳过 {self._counts['skipped']}，失败 {self._counts['failed']}")
        if self._finished_received and worker.results.empty():
            self._timer.stop()
            self._worker = None
            text = (f"更新未完成：{worker.failure}" if worker.failure else
                    "已停止，已写入的拼音保留。" if worker.cancelled.is_set() else "拼音更新完成。")
            self._dialog.finish(text)
            if self._shutdown_requested:
                self._dialog.close()
            worker.deleteLater()

    def stop(self):
        if self._worker is not None:
            self._worker.stop()

    def request_shutdown(self):
        self._shutdown_requested = True
        self.stop()

    def is_shutdown_done(self):
        return self._worker is None
