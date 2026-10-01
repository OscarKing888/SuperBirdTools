from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app_common.log import get_logger
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.image_dejitter.reference_region_tracker import ReferenceRegionTracker
from birdstamp.image_dejitter.matching_options import MatchingOptions
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult, image_file_signature
from .editor_utils import path_key

_log = get_logger("reference_tracking")


class EditorReferenceTrackingWorker(QThread):
    """只接收不可变输入；整批结果一次交付，QThread.finished 才表示生命周期结束。"""

    resultsReady = pyqtSignal(int, object)
    progressChanged = pyqtSignal(int, int, int)
    failed = pyqtSignal(int, str)

    def __init__(self, *, token: int, reference: Path, regions: tuple, paths: tuple[Path, ...],
                 options=MatchingOptions(), method="reference_region", recommendation="", parent=None):
        super().__init__(parent)
        self.token = token
        self.reference = reference
        self.regions = regions
        self.paths = paths
        self.options = options
        self.method = method
        self.recommendation = recommendation  # 冻结的推荐元数据 JSON；目标鸟/局部裁片与导出一致
        self.reference_signature = image_file_signature(reference)

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            if self.reference_signature is None:
                raise ValueError(f"无法读取参考照片：{self.reference}")
            from birdstamp.image_dejitter.recognition import recognition_strategy, METHOD_KEY, RECOMMENDATION_KEY
            settings = {METHOD_KEY: self.method}
            if self.recommendation:
                settings[RECOMMENDATION_KEY] = json.loads(self.recommendation)
            with decode_image(self.reference, decoder="auto") as image:
                tracker = recognition_strategy(settings).create_tracker(
                    image, self.regions, options=self.options, settings=settings)
            if image_file_signature(self.reference) != self.reference_signature:
                raise ValueError("参考照片已变化，请重新预处理。")
            tracker.cache_reference = self.reference_signature
            if hasattr(tracker, "prepare_targets"):
                # 与导出相同：先建立只读目标鸟轨迹，再逐张跟踪。
                tracker.prepare_targets(self.reference, list(self.paths), cancelled=self.isInterruptionRequested)
            results = {}
            for index, path in enumerate(self.paths, 1):
                if self.isInterruptionRequested():
                    return
                signature = image_file_signature(path)
                try:
                    if signature is None:
                        raise ValueError("照片不存在或无法读取")
                    if path_key(path) == path_key(self.reference):
                        result = RegionTrackingResult(self.regions, signature)
                    else:
                        with decode_image(path, decoder="auto") as image:
                            result = (tracker.track_cached(path, image, cancelled=self.isInterruptionRequested)
                                      if hasattr(tracker, "track_cached")
                                      else tracker.track(image, cancelled=self.isInterruptionRequested))
                            result = replace(result, signature=signature)
                    if image_file_signature(path) != signature:
                        raise ValueError("照片在预处理期间已变化")
                except InterruptedError:
                    return
                except Exception as exc:
                    _log.warning("参考区跟踪失败 path=%s: %s", path, exc)
                    result = RegionTrackingResult((None,) * len(self.regions), signature, str(exc))
                results[path_key(path)] = result
                self.progressChanged.emit(self.token, index, len(self.paths))
            if self.isInterruptionRequested():
                return
            if hasattr(tracker, "geometry"):
                # 与导出相同：固定参考失配的片段再做链式关键帧核验。
                from types import SimpleNamespace
                from birdstamp.image_dejitter.subject_keyframes import chain_keyframe_segments
                try:
                    results = chain_keyframe_segments([SimpleNamespace(path=path) for path in self.paths], tracker,
                                                      results, cancelled=self.isInterruptionRequested)
                except InterruptedError:
                    return
            if image_file_signature(self.reference) != self.reference_signature:
                raise ValueError("参考照片已变化，请重新预处理。")
            self.resultsReady.emit(self.token, results)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(self.token, str(exc))
