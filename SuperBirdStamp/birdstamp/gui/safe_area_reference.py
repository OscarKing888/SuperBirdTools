"""安全区参考图的有界后台解码；每次只运行一个任务，迟到结果不覆盖新选择。"""
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage


class ReferenceImageWorker(QThread):
    def __init__(self, orientation, path):
        super().__init__()
        self.orientation, self.path = orientation, path
        self.image, self.error = QImage(), ''

    def run(self):
        try:
            from PIL.ImageQt import ImageQt
            from birdstamp.decoders.image_decoder import decode_image_for_preview
            if not self.isInterruptionRequested():
                with decode_image_for_preview(Path(self.path), max_long_edge=1400, decoder='auto') as image:
                    if not self.isInterruptionRequested():
                        with image.convert('RGBA') as rgba:
                            self.image = ImageQt(rgba).copy()
        except Exception as exc:
            self.error = f'参考图读取失败：{exc}'


class ReferenceImageLoader(QObject):
    ready = pyqtSignal(str, str, QImage, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.paths = {}
        self.pending = {}
        self.versions = {}
        self.worker = None
        self.closed = False

    def set_paths(self, paths, *, force=None):
        if self.closed:
            return
        for orientation in ('portrait', 'landscape'):
            path = paths.get(orientation, '')
            if orientation != force and self.paths.get(orientation, '') == path:
                continue
            self.versions[orientation] = self.versions.get(orientation, 0)+1
            self.paths[orientation] = path
            self.pending.pop(orientation, None)
            self.ready.emit(orientation, path, QImage(), '正在读取参考图…' if path else '')
            if path:
                self.pending[orientation] = (path, self.versions[orientation])
        self._start_next()

    def _start_next(self):
        if self.closed or self.worker is not None or not self.pending:
            return
        orientation = next(iter(self.pending))
        path, version = self.pending.pop(orientation)
        self.worker = ReferenceImageWorker(orientation, path)
        self.worker.version = version
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        if not self.closed and self.versions.get(worker.orientation) == worker.version:
            self.ready.emit(worker.orientation, worker.path, worker.image, worker.error)
        worker.deleteLater()
        self._start_next()

    def close(self):
        self.closed = True
        self.pending.clear()
        if self.worker is not None:
            # QThread 必须等真实退出后才能释放，即使结果已经失效。
            self.worker.requestInterruption()
            self.worker.wait()
            self.worker.deleteLater()
            self.worker = None
