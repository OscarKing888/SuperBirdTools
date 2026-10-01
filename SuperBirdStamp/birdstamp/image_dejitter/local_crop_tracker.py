"""目标鸟引导的局部分析；检测框只给出搜索先验，不直接产生补偿。

每区仍按自身尺寸取规范分析尺度（与整图跟踪器相同的测量与求解）；目标鸟轨迹
只决定搜索窗口的平移，因此检测框抖动不会进入稳定位移。
"""
import numpy as np
from .subject_local_tracker import SubjectLocalTracker, LocalObservation
from .region_tracking_result import RegionTrackingResult, image_file_signature
from .bird_candidates import detect_bird_candidates, associate_target
from .matching_options import MatchingOptions


def _centre(box, size):
    return np.array(((box[0]+box[2])*size[0]/2, (box[1]+box[3])*size[1]/2))


class LocalCropSubjectTracker(SubjectLocalTracker):
    def __init__(self, reference, regions, *, target, options=MatchingOptions(), detector=None, geometry=None):
        regions = tuple(tuple(r) for r in regions)
        from .manual_region_matches import normalize_match_box
        target = normalize_match_box(target)
        if target is None:
            raise ValueError('目标鸟框无效，请重新选择目标鸟')
        if not regions or any(not (0 <= l < r <= 1 and 0 <= t < b <= 1) for l,t,r,b in regions):
            raise ValueError('局部主体：参考区必须在原图内')
        # 人工移动/追加的框也必须属于当前目标，不能把远处背景塞进鸟体局部。
        bw,bh = target[2]-target[0],target[3]-target[1]
        outside = [str(i+1) for i,(l,t,r,b) in enumerate(regions)
                   if l < target[0]-bw*.5 or r > target[2]+bw*.5
                   or t < target[1]-bh*.5 or b > target[3]+bh*.5]
        if outside:
            raise ValueError('选区 '+ '、'.join(outside)+' 远离目标鸟，不能与鸟体局部混用。'
                             '请删除这些背景选区，或改用基本参考区匹配并只选择背景；若换了参考图，请重新选择目标鸟。')
        self.target = tuple(target)
        self.detector = detector or detect_bird_candidates
        self.target_trajectory = None
        super().__init__(reference,regions,options=options,geometry=geometry)

    def prepare_targets(self, reference, paths, *, cancelled, progress=lambda text:None):
        from .target_trajectory import build_target_trajectory
        self.target_trajectory = build_target_trajectory(reference,paths,self.target,
            cancelled=cancelled,progress=progress,detector=self.detector)

    def _target_box(self, image, *, cancelled, source_path=None, target_box=None):
        from .bird_observation_cache import detect_cached
        if target_box is not None:
            return target_box
        if self.target_trajectory is not None and source_path is not None:
            frame = self.target_trajectory.frame(source_path)
            if frame.box is None:
                raise ValueError(frame.error)
            return frame.box
        birds = (detect_cached(source_path,image,cancelled=cancelled)
                 if source_path and self.detector is detect_bird_candidates else self.detector(image,cancelled=cancelled))
        return associate_target(self.target,birds)

    def motion_prior(self, path, image=None, *, cancelled=lambda: False):
        if self.target_trajectory is None:
            return None
        frame = self.target_trajectory.frame(path)
        return None if frame.box is None else _centre(frame.box,self.reference_size)-_centre(self.target,self.reference_size)

    def identity_error(self, path):
        if self.target_trajectory is None:
            return ''
        frame = self.target_trajectory.frame(path)
        return '' if frame.box is not None else (frame.error or '目标鸟身份中断')

    def track(self, image, *, cancelled=lambda: False, source_path=None, target_box=None, prior=None):
        if image.size != self.reference_size:
            return self._failed('源尺寸或方向不同，请分段设置新参考')
        if prior is None:
            try:
                target = self._target_box(image,cancelled=cancelled,source_path=source_path,target_box=target_box)
            except ValueError as exc:
                return self._failed(str(exc))
            prior = _centre(target,image.size)-_centre(self.target,image.size)
        return super().track(image,cancelled=cancelled,prior=tuple(map(float,prior)))

    def _failed(self, message):
        return RegionTrackingResult((None,)*len(self.regions),error=message,predicted_boxes=self.regions,
            observation=LocalObservation('needs_keyframe',message,source_size=self.reference_size,
                                         region_kinds=self.geometry.kinds,region_scales=tuple(map(float,self.geometry.scales))))

    def track_cached(self, path, image, *, cancelled):
        from . import subject_observation_cache as cache
        signature = image_file_signature(path)
        reference = getattr(self,'cache_reference',None)
        from .bird_observation_cache import detector_signature
        key = ('local-crop-v4',*self.cache_key(),signature,self.target,detector_signature(),
               self.target_trajectory.signature if self.target_trajectory is not None else None)
        if cancelled():
            raise InterruptedError('已取消局部主体跟踪')
        result = cache.get(key) if signature and reference else None
        if result is None:
            result = self.track(image,cancelled=cancelled,source_path=path)
            if signature and reference and not cancelled() and image_file_signature(path) == signature:
                cache.put(key,result)
        return result

    def at_keyframe(self, image, boxes, target):
        return LocalCropSubjectTracker(image,boxes,target=target,options=self.options,
                                       detector=self.detector,geometry=self.geometry)

    def rekeyframe(self, image, boxes):
        offsets = [((b[0]+b[2]-r[0]-r[2])/2,(b[1]+b[3]-r[1]-r[3])/2) for b,r in zip(boxes,self.regions)]
        dx,dy = np.median(offsets,axis=0)
        target = tuple(v+(dx if i%2 == 0 else dy) for i,v in enumerate(self.target))
        return LocalCropSubjectTracker(image,boxes,target=target,options=self.options,detector=self.detector,
                                       geometry=self.geometry)
