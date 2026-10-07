# -*- coding: utf-8 -*-
"""稀有度快捷菜单与后台批量保存。"""
from __future__ import annotations

from pathlib import Path
import html
import os
import queue
import threading
import time

from app_common.bird_rarity import RARITY_LEVELS, rarity_metadata, rarity_score
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .bird_identification_controller import BirdIDProgressDialog
from .metadata_result_sync import MetadataResultSync
from .rarity_badge import rarity_badge_style
from .rarity_edit import RarityResult, save_rarity
from .qt_compat import QMessageBox, QThread, QTimer
try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import QInputDialog, QMenu
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject
    from PyQt5.QtWidgets import QInputDialog, QMenu


class RarityWorker(QThread):
    def __init__(self, paths, score):
        super().__init__()
        self.paths, self.score = paths, score
        self.results = queue.Queue(maxsize=128)
        self.cancelled = threading.Event()

    def run(self):
        for path in self.paths:
            if self.cancelled.is_set():
                break
            try:
                result = save_rarity(path, self.score, cancelled=self.cancelled.is_set)
            except Exception as exc:
                result = RarityResult(path, str(exc))
            # 已写结果必须交付；关闭期间仍排空队列，直到真实 finished。
            self.results.put(result)


class RarityController(QObject):
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

    def extend_file_menu(self, menu, paths):
        photos = [p for p in paths if Path(p).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS]
        if not photos:
            return
        title = "修改稀有度" if len(photos) == 1 else f"修改稀有度（{len(photos)} 张）"
        sub = menu.addMenu(title)
        self.populate_menu(sub, photos)

    def populate_menu(self, menu, paths):
        allowed = not self.busy and not self._shutdown_requested and self._files._file_writes_allowed("修改稀有度")
        # 一键选档写入该档下界；精确分数通过自定义入口设置。
        for (level, _range, _text, _color), score in zip(RARITY_LEVELS, (0, 8, 25, 50, 75)):
            label = rarity_badge_style(score, level=level)[0]
            action = menu.addAction(f"{label}（{score}）", lambda _checked=False, s=score: self.start(paths, s))
            action.setToolTip(f"将所选照片的稀有度设为 {score}/100")
            action.setEnabled(allowed)
        menu.addSeparator()
        action = menu.addAction("自定义分数…", lambda: self.edit(paths))
        action.setEnabled(allowed)

    def show_for_path(self, path, anchor):
        menu = QMenu(anchor)
        self.populate_menu(menu, [path])
        try:
            (menu.exec if hasattr(menu, "exec") else menu.exec_)(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        finally:
            menu.deleteLater()

    def _source(self, path):
        source = self._files._resolve_source_path_for_action(path)
        if not source:
            raise FileNotFoundError(f"找不到照片原文件：{path}")
        return source

    def edit(self, paths):
        current = rarity_metadata(self._files.cached_photo_metadata_for_path(paths[0]))[0] if len(paths) == 1 else None
        score, accepted = QInputDialog.getDouble(
            self._main, "修改稀有度", f"为 {len(paths)} 张照片设置稀有度（0–100，越高越稀有）：",
            current if current is not None else 0, 0, 100, 2,
        )
        if accepted:
            self.start(paths, score)

    def start(self, paths, score):
        if rarity_score(score) is None:
            return False
        if self._shutdown_requested or self._worker is not None:
            return False
        if not self._files._file_writes_allowed("修改稀有度", warn=True):
            return False
        try:
            aliases = [(path, self._source(path)) for path in paths]
        except Exception as exc:
            QMessageBox.warning(self._main, "稀有度", str(exc))
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
        self._dialog.setWindowTitle("保存稀有度")
        self._dialog.label.setText("正在保存稀有度…")
        self._dialog.cancel_requested.connect(self.stop)
        self._worker = worker = RarityWorker(list(unique.values()), score)
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
            self._dialog.finish("已停止，已保存的稀有度保留。" if worker.cancelled.is_set() else "稀有度保存完成。")
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
