# -*- coding: utf-8 -*-
"""识鸟结果的可见区缩略图，复用文件列表的加载器、缓存和工作池。"""
from collections import OrderedDict
import os

from app_common.file_browser._thumbnail import ThumbnailLoader, ThumbnailMemoryCache
try:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QTimer, pyqtSignal


class BirdIDThumbnails(QObject):
    changed = pyqtSignal(str)
    SIZE = 256
    MAX_IMAGES = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._token = 0
        self._visible = ()
        self._images = OrderedDict()
        self._incomplete = set()
        self._shutdown = False
        self._context = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._load_visible)

    def configure(self, file_list):
        """在 GUI 线程快照本批报告上下文；后续切目录不改变识别结果的图片来源。"""
        self.set_visible([])
        self._images.clear()
        self._incomplete.clear()
        cache = getattr(file_list, '_thumb_memory_cache', None)
        pool = getattr(file_list, '_get_browser_work_pool', None)
        self._context = dict(
            thumb_cache=cache if cache is not None else ThumbnailMemoryCache(max_bytes=16 * 1024 * 1024),
            work_pool=pool() if callable(pool) else None,
            report_cache=(getattr(file_list, '_report_full_cache', None)
                          or getattr(file_list, '_report_cache', None) or {}).copy(),
            current_dir=getattr(file_list, '_current_dir', '') if getattr(file_list, '_use_preview_cache', True) else '',
        )

    def image(self, path):
        """绘制时只查内存，不访问磁盘，也不解码原图。"""
        return self._images.get(os.path.normpath(path))

    def failed(self, path):
        key = os.path.normpath(path)
        return key in self._images and self._images[key] is None

    def set_visible(self, paths):
        if self._shutdown:
            return
        visible = tuple(dict.fromkeys(os.path.normpath(path) for path in paths))
        if visible == self._visible:
            return
        self._visible = visible
        self._token += 1
        self._timer.stop()
        if self._worker is not None:
            self._worker.stop()
        for path in visible:
            if path in self._images:
                self._images.move_to_end(path)
        if visible:
            self._timer.start()

    def _load_visible(self):
        if self._shutdown or self._worker is not None:
            return
        paths = [p for p in self._visible if p not in self._images or p in self._incomplete]
        if not paths:
            return
        worker = self._worker = ThumbnailLoader(self.SIZE, self._token, parent=self, **self._context)
        token = self._token
        worker.enqueue(paths, priority=ThumbnailLoader.PRIORITY_VISIBLE)
        worker.set_desired_paths(paths)
        worker.thumbnail_ready.connect(self._ready)
        worker.finished.connect(lambda w=worker, t=token, p=tuple(paths): self._finished(w, t, p))
        worker.start()

    def _remember(self, path, image, *, complete=True):
        if complete:
            self._incomplete.discard(path)
        else:
            self._incomplete.add(path)
        self._images[path] = image
        self._images.move_to_end(path)
        while len(self._images) > self.MAX_IMAGES:
            removed, _ = self._images.popitem(last=False)
            self._incomplete.discard(removed)
        self.changed.emit(path)

    def _ready(self, token, path, image):
        if self._shutdown or token != self._token or path not in self._visible:
            return
        if image is not None and not image.isNull():
            self._remember(path, image, complete=False)

    def _finished(self, worker, token, paths):
        if worker is not self._worker:
            return
        self._worker = None
        if not self._shutdown and token == self._token:
            for path in paths:
                self._incomplete.discard(path)
                if path in self._visible and path not in self._images:
                    self._remember(path, None)
        worker.deleteLater()
        if not self._shutdown:
            self._timer.start()

    def request_shutdown(self):
        self._shutdown = True
        self._token += 1
        self._visible = ()
        self._timer.stop()
        if self._worker is not None:
            self._worker.stop()

    def is_shutdown_done(self):
        return self._worker is None
