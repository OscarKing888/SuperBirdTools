"""Video context action and owned, cancellable PNG export worker."""
from __future__ import annotations

import html
import os
from pathlib import Path
import queue
import threading

from app_common.video import is_video
from app_common.superviewer_user_options import get_runtime_user_options
from .bird_identification_controller import BirdIDProgressDialog
from .qt_compat import QFileDialog, QMessageBox, QThread, QTimer
from .video_frame_export import export_video_frames
try:
    from PyQt6.QtCore import QObject
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject


class FrameExportWorker(QThread):
    def __init__(self, paths, destination, suffix):
        super().__init__()
        self.paths, self.destination = paths, destination
        self.suffix = suffix
        self.cancelled = threading.Event()
        self.results = queue.Queue(maxsize=64)
        self._lock = threading.Lock()
        self._progress = (0, "", 0, "")

    def progress(self):
        with self._lock:
            return self._progress

    def run(self):
        for index, path in enumerate(self.paths, 1):
            if self.cancelled.is_set():
                break

            def report(frames, directory):
                with self._lock:
                    self._progress = (index, path, frames, directory)

            report(0, "")
            result = export_video_frames(path, self.destination,
                                         suffix=self.suffix,
                                         cancelled=self.cancelled.is_set, on_progress=report)
            self.results.put(result)


class VideoFrameExportController(QObject):
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
        videos = [path for path in paths if is_video(path)]
        if not videos:
            return
        title = "提取全部帧为 PNG…"
        if len(videos) > 1:
            title = f"提取全部帧为 PNG（{len(videos)} 个视频）…"
        action = menu.addAction(title, lambda: self.choose_destination(videos))
        action.setToolTip("按原分辨率逐帧保存；每个视频创建独立文件夹")
        action.setEnabled(self._worker is None and not self._shutdown_requested)
        if self._worker is not None:
            menu.addAction("查看视频提取进度…", self.show_progress)

    def choose_destination(self, paths):
        if self._worker is not None or self._shutdown_requested:
            return
        options = get_runtime_user_options()
        mode = options["video_frame_output_mode"]
        destination = None
        if mode == "fixed":
            destination = options["video_frame_output_directory"]
            if not destination or not Path(destination).expanduser().is_absolute():
                QMessageBox.warning(self._main, "视频处理", "请在用户选项 → 视频处理中设置固定输出根目录。")
                return
            destination = str(Path(destination).expanduser())
        elif mode == "ask":
            destination = QFileDialog.getExistingDirectory(
                self._main, "选择 PNG 帧输出目录（每个视频创建独立文件夹）",
                str(Path(paths[0]).parent),
            )
            if not destination:
                return
        self.start(paths, destination, suffix=options["video_frame_suffix"])

    def start(self, paths, destination=None, *, suffix="_Frames"):
        if self._worker is not None or self._shutdown_requested:
            return False
        sources = {}
        for path in paths:
            if is_video(path):
                source = self._files._resolve_source_path_for_action(path) or path
                sources.setdefault(os.path.normcase(os.path.abspath(source)), source)
        if not sources:
            return False
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        self._done = self._failed = self._frames = 0
        self._finished_received = False
        self._dialog = BirdIDProgressDialog(self._main)
        self._dialog.setWindowTitle("提取全部帧为 PNG")
        self._dialog.label.setText("准备提取视频帧…")
        self._dialog.summary.setWordWrap(True)
        self._dialog.summary.setText("原分辨率逐帧提取；取消后保留已完成的 PNG。")
        self._dialog.details.append(html.escape(f"输出父目录：{destination or '每个视频所在目录'}；目录名后缀：{suffix}"))
        self._dialog.cancel_requested.connect(self.stop)
        self._dialog.rejected.connect(self.stop)  # Escape also cancels the task.
        worker = self._worker = FrameExportWorker(list(sources.values()), destination, suffix)
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
            self._frames += result.frames
            self._failed += bool(result.error)
            state = result.error or ("已取消" if result.cancelled else "完成")
            text = f"{result.source}\n{state}，保留 {result.frames} 帧\n输出：{result.output_dir or '未创建'}"
            self._dialog.details.append(html.escape(text).replace("\n", "<br>"))
        index, path, frames, directory = worker.progress()
        if not self._shutdown_requested:
            self._dialog.label.setText(f"正在提取 {index}/{len(worker.paths)}：{Path(path).name}\n已提取 {frames} 帧")
            self._dialog.label.setToolTip(directory)
            self._dialog.summary.setText(f"已处理 {self._done}/{len(worker.paths)} 个视频，失败 {self._failed} 个。")
        if self._finished_received and worker.results.empty():
            self._timer.stop()
            self._worker = None
            self._dialog.bar.setRange(0, len(worker.paths))
            self._dialog.bar.setValue(self._done)
            state = "已取消，已完成的 PNG 保留。" if worker.cancelled.is_set() else "提取结束。"
            self._dialog.finish(f"{state}共保存 {self._frames} 帧，失败 {self._failed} 个视频。")
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
