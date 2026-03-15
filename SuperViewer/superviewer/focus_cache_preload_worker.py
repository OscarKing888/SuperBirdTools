# -*- coding: utf-8 -*-
"""后台线程：批量预加载多张图片的焦点框。"""

from __future__ import annotations

import os

from app_common.log import get_logger

from .focus_preview_loader import _load_focus_box_for_preview
from .qt_compat import QThread, pyqtSignal

_log = get_logger("focus_cache_preload_worker")

FOCUS_PRELOAD_BATCH_SIZE = 12


class FocusCachePreloadWorker(QThread):
    focus_batch_ready = pyqtSignal(int, object)  # (token, list[(source_path, focus_box, used_path)])

    def __init__(self, token: int, tasks: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._token = int(token)
        self._tasks = [
            (
                os.path.normpath(source_path) if source_path else "",
                os.path.normpath(load_path) if load_path else "",
            )
            for source_path, load_path in (tasks or [])
        ]

    @property
    def token(self) -> int:
        return self._token

    def run(self) -> None:
        batch: list[tuple[str, tuple[float, float, float, float], str]] = []
        _log.info("[FocusCachePreloadWorker.run] START token=%s tasks=%s", self._token, len(self._tasks))
        for source_path, load_path in self._tasks:
            if self.isInterruptionRequested():
                _log.info("[FocusCachePreloadWorker.run] interrupted token=%s", self._token)
                return
            if not source_path or not load_path or not os.path.isfile(load_path):
                continue
            try:
                focus_box = _load_focus_box_for_preview(
                    load_path,
                    1,
                    1,
                    allow_report_db_fallback=False,
                )
            except Exception:
                _log.exception("[FocusCachePreloadWorker.run] preload failed source=%r load=%r", source_path, load_path)
                continue
            if not focus_box:
                continue
            batch.append((source_path, focus_box, load_path))
            if len(batch) >= FOCUS_PRELOAD_BATCH_SIZE:
                _log.info(
                    "[FocusCachePreloadWorker.run] emit token=%s batch=%s",
                    self._token,
                    len(batch),
                )
                self.focus_batch_ready.emit(self._token, list(batch))
                batch.clear()
        if batch:
            _log.info(
                "[FocusCachePreloadWorker.run] emit final token=%s batch=%s",
                self._token,
                len(batch),
            )
            self.focus_batch_ready.emit(self._token, list(batch))
        _log.info("[FocusCachePreloadWorker.run] END token=%s", self._token)
