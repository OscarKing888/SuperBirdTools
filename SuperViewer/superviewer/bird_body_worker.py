# -*- coding: utf-8 -*-
"""One asynchronous bird-body cache/detection unit for BrowserWorkPool ANALYSIS."""
from __future__ import annotations

from dataclasses import dataclass
import os

from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.file_browser._work_action import WorkerAction
from app_common.log import get_logger

from .bird_body import (BirdBodyResult, cache_field, camera_box_from_raw,
                        default_detector, default_missed_bird_finder, load_detection_image,
                        result_from_metadata, source_fingerprint)

_log = get_logger("superviewer.bird_body")


@dataclass(frozen=True)
class BirdBodyOutcome:
    source_path: str
    result: BirdBodyResult | None = None
    cache_hit: bool = False
    written: bool = False
    cancelled: bool = False
    error: str = ""


class BirdBodyAction(WorkerAction):
    def __init__(self, source_path: str, *, cancelled=lambda: False,
                 detector=None, write_xmp=True, missed_bird_finder=None):
        """``missed_bird_finder`` (``.find(path, cancelled=)``) rechecks when no bird is
        found; it defaults to the shared finder only with the default detector."""
        super().__init__(cancelled=cancelled)
        self.source_path = os.path.normpath(os.fspath(source_path))
        self.detector = detector
        self.write_xmp = bool(write_xmp)
        if missed_bird_finder is None and detector is None:
            missed_bird_finder = default_missed_bird_finder()
        self.missed_bird_finder = missed_bird_finder

    def _find_missed_bird(self, path: str):
        """``(box, error)``; a failed recheck must not be cached as "no bird"."""
        # 伪装/遮挡的鸟：与鸟清晰度共用复检规则。
        try:
            return self.missed_bird_finder.find(path, cancelled=self.is_cancelled), ""
        except Exception as exc:
            if self.is_cancelled():
                return None, ""
            _log.warning("[bird.body] camouflaged-bird recheck failed path=%r: %s", path, exc)
            return None, f"伪装鸟复检失败：{exc}"

    def execute(self) -> BirdBodyOutcome:
        path = self.source_path
        if self.is_cancelled():
            return BirdBodyOutcome(path, cancelled=True)
        try:
            fingerprint = source_fingerprint(path)
            metadata = PhotoMetaDataXMP()
            cached = result_from_metadata(path, metadata.read(path), fingerprint)
            if self.is_cancelled():
                return BirdBodyOutcome(path, cancelled=True)
            if cached is not None:
                if source_fingerprint(path) != fingerprint:
                    return BirdBodyOutcome(path, cancelled=True)
                return BirdBodyOutcome(path, result=cached, cache_hit=True)
            image, crop = load_detection_image(path)
            try:
                if self.is_cancelled():
                    return BirdBodyOutcome(path, cancelled=True)
                box = (self.detector.detect(image) if self.detector is not None
                       else default_detector().detect(image, cancelled=self.is_cancelled))
                box = camera_box_from_raw(box, crop)
            finally:
                image.close()
            recheck_error = ""
            if box is None and self.missed_bird_finder is not None and not self.is_cancelled():
                box, recheck_error = self._find_missed_bird(path)
            if self.is_cancelled() or source_fingerprint(path) != fingerprint:
                return BirdBodyOutcome(path, cancelled=True)
            result = BirdBodyResult(box, fingerprint)
            if recheck_error:
                return BirdBodyOutcome(path, result=result, error=recheck_error)
            if not self.write_xmp:
                return BirdBodyOutcome(path, result=result)
            # 与用户编辑共用侧车锁；排队等待后重新验证，过期分析不能覆盖新源图缓存。
            with xmp_sidecar_write_lock(path):
                if self.is_cancelled() or source_fingerprint(path) != fingerprint:
                    return BirdBodyOutcome(path, cancelled=True)
                written = metadata.write(path, {cache_field(path): result.to_json()})
            error = "" if written else "鸟体框已计算，但 XMP 缓存写入失败"
            if error:
                _log.warning("[bird.body] %s path=%r", error, path)
            return BirdBodyOutcome(path, result=result, written=written, error=error)
        except Exception as exc:
            if self.is_cancelled():
                return BirdBodyOutcome(path, cancelled=True)
            _log.error("[bird.body] detection failed path=%r: %s", path, exc)
            return BirdBodyOutcome(path, error=f"鸟体识别失败：{exc}")
