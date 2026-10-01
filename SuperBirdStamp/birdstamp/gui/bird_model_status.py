"""独立拥有模型校验线程；文件变化重新校验，界面线程不读取权重内容。"""
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from birdstamp.image_dejitter.bird_parts import model_store


class _ModelCheckWorker(QThread):
    ready = pyqtSignal(object)

    def __init__(self, path, parent):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            result = model_store.inspect_model(self.path, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(result)
        except InterruptedError:
            pass


class BirdModelStatus(QObject):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = model_store.ModelStatus('checking', ())
        self.worker = None
        self._signature = None
        self._shutdown = False
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QTimer.singleShot(0, self.refresh)

    def refresh(self, *, force=False):
        if force:
            self._signature = None
        if self._shutdown or self.worker is not None:
            return
        path = model_store.model_path()
        signature = model_store.model_file_signature(path)
        if signature == self._signature:
            return
        self._signature = signature
        self.status = model_store.ModelStatus('checking', signature)
        self.changed.emit()
        self.worker = _ModelCheckWorker(path, self)
        self.worker.ready.connect(self._ready)
        self.worker.finished.connect(self._finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _ready(self, result):
        if self._shutdown or self.sender() is not self.worker:
            return
        signature = model_store.model_file_signature(model_store.model_path())
        if result.state == 'changed' or result.signature != signature:
            self._signature = None
            return
        self.status = result
        self.changed.emit()

    def _finished(self):
        if self.sender() is self.worker:
            self.worker = None
        self.refresh()

    def shutdown(self):
        self._shutdown = True
        self.timer.stop()
        if self.worker is not None:
            self.worker.requestInterruption()
        return self.worker is None
