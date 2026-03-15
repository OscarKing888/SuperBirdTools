# -*- coding: utf-8 -*-
"""后台线程：加载单张图片的焦点框并发出 signal。"""

from __future__ import annotations

import os

from app_common.log import get_logger

from .focus_preview_loader import (
    _load_focus_box_for_preview,
    _load_focus_box_from_report_db,
    _resolve_focus_report_fallback_ref_size,
)
from .qt_compat import QThread, pyqtSignal

_log = get_logger("focus_box_loader")


class FocusBoxLoader(QThread):
    focus_loaded = pyqtSignal(int, object, str)  # (request_id, focus_box_or_none, used_path)

    def __init__(
        self,
        request_id: int,
        photo_cache_key: str,
        preview_path: str,
        source_path: str,
        width: int,
        height: int,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._photo_cache_key = str(photo_cache_key or "")
        self._preview_path = os.path.normpath(preview_path) if preview_path else ""
        self._source_path = os.path.normpath(source_path) if source_path else ""
        self._width = int(width)
        self._height = int(height)
        self._result_size_independent = False

    @property
    def request_id(self) -> int:
        return self._request_id

    @property
    def photo_cache_key(self) -> str:
        return self._photo_cache_key

    @property
    def preview_size_key(self) -> tuple[int, int]:
        return (self._width, self._height)

    @property
    def result_size_independent(self) -> bool:
        return self._result_size_independent

    def run(self) -> None:
        candidates: list[tuple[str, str]] = []
        if self._source_path and os.path.isfile(self._source_path):
            candidates.append(("source", self._source_path))
        if self._preview_path and os.path.isfile(self._preview_path):
            same_as_source = (
                self._source_path
                and os.path.normcase(self._preview_path) == os.path.normcase(self._source_path)
            )
            if not same_as_source:
                candidates.append(("preview", self._preview_path))

        seen_candidate_paths: set[str] = set()
        deduped_candidates: list[tuple[str, str]] = []
        for label, candidate_path in candidates:
            norm_candidate = os.path.normcase(candidate_path)
            if not candidate_path or norm_candidate in seen_candidate_paths:
                continue
            seen_candidate_paths.add(norm_candidate)
            deduped_candidates.append((label, candidate_path))
        candidates = deduped_candidates
        _log.info(
            "[FocusBoxLoader.run] START request_id=%s preview=%r source=%r candidates=%s",
            self._request_id,
            self._preview_path,
            self._source_path,
            [(label, path) for label, path in candidates],
        )
        for label, candidate_path in candidates:
            if self.isInterruptionRequested():
                _log.info("[FocusBoxLoader.run] interrupted request_id=%s", self._request_id)
                return
            focus_box = _load_focus_box_for_preview(
                candidate_path,
                self._width,
                self._height,
                allow_report_db_fallback=False,
            )
            _log.info(
                "[FocusBoxLoader.run] tried request_id=%s label=%s path=%r focus_box=%r",
                self._request_id,
                label,
                candidate_path,
                focus_box,
            )
            if focus_box:
                self._result_size_independent = True
                self.focus_loaded.emit(self._request_id, focus_box, candidate_path)
                return

        fallback_used_path = self._source_path or self._preview_path
        fallback_box = None
        if fallback_used_path and os.path.isfile(fallback_used_path):
            fallback_ref_size, fallback_size_independent = _resolve_focus_report_fallback_ref_size(
                fallback_used_path,
                fallback=(self._width, self._height),
            )
            fallback_box = _load_focus_box_from_report_db(
                fallback_used_path,
                self._width,
                self._height,
                ref_size=fallback_ref_size,
            )
            _log.info(
                "[FocusBoxLoader.run] report fallback request_id=%s path=%r ref_size=%s focus_box=%r",
                self._request_id,
                fallback_used_path,
                fallback_ref_size,
                fallback_box,
            )
            self._result_size_independent = bool(fallback_size_independent)
        self.focus_loaded.emit(self._request_id, fallback_box, fallback_used_path)
