"""序列识别策略边界；识别证据与输出运动规划分离。"""
from dataclasses import dataclass
from typing import Protocol
from abc import ABC, abstractmethod

METHOD_KEY = 'dejitter_recognition_method'
MODE_KEY = 'dejitter_subject_mode'
WINDOW_KEY = 'dejitter_subject_window'
VERSION_KEY = 'dejitter_subject_version'
RECOMMENDATION_KEY = 'dejitter_region_recommendation'
SUBJECT_KEYS = (METHOD_KEY, MODE_KEY, WINDOW_KEY, VERSION_KEY, RECOMMENDATION_KEY)
METHOD_CHOICES = (('基本：参考区匹配', 'reference_region'), ('高级：局部主体跟踪', 'subject_local'))


@dataclass(frozen=True, slots=True)
class SubjectSettings:
    version: int = 1
    method: str = 'reference_region'
    mode: str = 'lock'
    window: int = 5

    @classmethod
    def from_settings(cls, settings=None):
        settings = settings or {}
        # 未知版本安全回到基本方法，不把未来的配置误解释为当前算法。
        version = settings.get(VERSION_KEY, 1)
        method = settings.get(METHOD_KEY, 'reference_region') if version == 1 else 'reference_region'
        if method not in tuple(value for _, value in METHOD_CHOICES):
            method = 'reference_region'
        try:
            window = max(3, min(31, int(settings.get(WINDOW_KEY, 5)))) | 1
        except (ValueError, TypeError, OverflowError):
            window = 5
        return cls(method=method, mode='follow' if settings.get(MODE_KEY) == 'follow' else 'lock', window=window)

    def as_settings(self):
        return {METHOD_KEY:self.method, MODE_KEY:self.mode, WINDOW_KEY:self.window, VERSION_KEY:self.version}


class RegionTracker(Protocol):
    regions: tuple
    reference_size: tuple

    def track(self, image, *, cancelled): ...
    def recover(self, image, result, previous, following, *, cancelled): ...


class RecognitionStrategy(ABC):
    @abstractmethod
    def create_tracker(self, image, regions, *, options, settings=None) -> RegionTracker:
        """创建只读关键帧模板；识别返回观测，不决定输出变换。"""
        raise NotImplementedError


class ReferenceRegionRecognition(RecognitionStrategy):
    def create_tracker(self, image, regions, *, options, settings=None) -> RegionTracker:
        from .reference_region_tracker import ReferenceRegionTracker
        return ReferenceRegionTracker(image, regions, options=options)


class SubjectLocalRecognition(RecognitionStrategy):
    def create_tracker(self, image, regions, *, options, settings=None) -> RegionTracker:
        from .subject_local_tracker import SubjectLocalTracker
        meta = (settings or {}).get(RECOMMENDATION_KEY) or {}
        if meta.get('local_analysis') and meta.get('target'):
            from .local_crop_tracker import LocalCropSubjectTracker
            return LocalCropSubjectTracker(image, regions, target=meta['target'], options=options)
        return SubjectLocalTracker(image, regions, options=options)


RECOGNITION_STRATEGIES = {'reference_region':ReferenceRegionRecognition(), 'subject_local':SubjectLocalRecognition()}


def recognition_strategy(settings):
    return RECOGNITION_STRATEGIES[SubjectSettings.from_settings(settings).method]
