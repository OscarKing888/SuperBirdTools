# -*- coding: utf-8 -*-
"""主要鸟名的后台提交、缓存同步和关闭生命周期。"""
from .metadata_result_sync import MetadataResultSync
from .per_bird_primary import set_primary_individual
from .qt_compat import QThread
try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import QMessageBox
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject
    from PyQt5.QtWidgets import QMessageBox


class PrimaryBirdWorker(QThread):
    def __init__(self, source, index, expected, aliases):
        super().__init__()
        self.source, self.index, self.expected, self.aliases = source, index, expected, aliases
        self.result = None

    def run(self):
        self.result = set_primary_individual(self.source, self.index, self.expected,
                                             cancelled=self.isInterruptionRequested)


class PrimaryBirdController(QObject):
    def __init__(self, window, files):
        super().__init__(window)
        self.window, self.files = window, files
        self.worker = None
        self.closing = False
        self.sync = MetadataResultSync(files)

    def bind(self, panel):
        panel.primary_allowed = self.allowed
        panel.primary_requested.connect(self.apply)

    def allowed(self):
        check = getattr(self.window, '_sidecar_writes_allowed', None)
        return not self.closing and self.worker is None and (not callable(check) or check())

    def apply(self, path, index, expected):
        if not self.allowed():
            return False
        try:
            source = self.files._resolve_source_path_for_action(path)
            if not source:
                raise ValueError('找不到照片原文件')
        except Exception as exc:
            QMessageBox.warning(self.window, '设置主要鸟名', str(exc))
            return False
        self.worker = worker = PrimaryBirdWorker(source, index, expected, [(path, source)])
        worker.finished.connect(lambda w=worker: self._finished(w))
        worker.start()
        return True

    def _finished(self, worker):
        if worker is not self.worker:
            return
        self.worker = None
        if not self.closing:
            result = worker.result
            if result.updates:
                self.sync.sync([result], worker.aliases)
            if result.status not in {'success', 'cancelled'}:
                QMessageBox.warning(self.window, '设置主要鸟名', result.message)
        worker.deleteLater()

    def request_shutdown(self):
        self.closing = True
        if self.worker is not None:
            self.worker.requestInterruption()

    def is_shutdown_done(self):
        return self.worker is None
