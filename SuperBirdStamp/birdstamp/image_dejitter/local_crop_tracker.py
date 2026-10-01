"""固定尺度鸟体局部分析；检测框仅定位搜索裁片，不直接产生补偿。"""
from dataclasses import replace
import numpy as np
from PIL import Image
from .subject_local_tracker import SubjectLocalTracker, LocalObservation
from .region_tracking_result import RegionTrackingResult, image_file_signature
from .bird_candidates import detect_bird_candidates, associate_target
from .matching_options import MatchingOptions


class LocalCropSubjectTracker(SubjectLocalTracker):
    def __init__(self, reference, regions, *, target, options=MatchingOptions(), detector=None):
        self.regions = tuple(tuple(r) for r in regions)
        self.target = tuple(target)
        self.reference_size = reference.size
        self.options = options
        self.detector = detector or detect_bird_candidates
        w,h = reference.size
        self.side = max(64, round(max((target[2]-target[0])*w, (target[3]-target[1])*h)*2))
        self.analysis_size = (min(1024,self.side),)*2
        self.source_per_analysis = np.array((self.side/self.analysis_size[0],)*2)
        self.origin = self._origin(target)
        x,y = self.origin
        local = tuple(((l*w-x)/self.side,(t*h-y)/self.side,(r*w-x)/self.side,(b*h-y)/self.side)
                      for l,t,r,b in regions)
        with self._crop(reference,self.origin) as crop:
            self.inner = SubjectLocalTracker(crop,local,options=options)

    def _origin(self, box):
        w,h = self.reference_size
        return (round((box[0]+box[2])*w/2-self.side/2), round((box[1]+box[3])*h/2-self.side/2))

    def _crop(self, image, origin):
        x,y = origin
        # 直接采样到有界图像；大鸟或越界窗口不分配巨大的补黑正方形。
        scale = self.side/self.analysis_size[0]
        return image.transform(self.analysis_size,Image.Transform.AFFINE,
                               (scale,0,x,0,scale,y),Image.Resampling.BICUBIC)

    def track(self, image, *, cancelled=lambda: False, source_path=None):
        if image.size != self.reference_size:
            return self._failed('源尺寸或方向不同，请分段设置新参考')
        try:
            from .bird_observation_cache import detect_cached
            birds = (detect_cached(source_path,image,cancelled=cancelled)
                     if source_path and self.detector is detect_bird_candidates else self.detector(image,cancelled=cancelled))
            target = associate_target(self.target,birds)
        except ValueError as exc:
            return self._failed(str(exc))
        origin = self._origin(target)
        with self._crop(image,origin) as crop:
            result = self.inner.track(crop,cancelled=cancelled)
        obs = result.observation
        scale = self.source_per_analysis
        points = tuple((p[0],p[1],p[2]*scale[0]+self.origin[0],p[3]*scale[1]+self.origin[1],
                        p[4]*scale[0]+origin[0],p[5]*scale[1]+origin[1],p[6],p[7]) for p in obs.points)
        delta = (tuple(np.array(obs.displacement)*scale+np.array(origin)-self.origin)
                 if obs.displacement is not None else None)
        w,h = image.size
        boxes = tuple((l+delta[0]/w,t+delta[1]/h,r+delta[0]/w,b+delta[1]/h)
                      for l,t,r,b in self.regions) if delta is not None else (None,)*len(self.regions)
        if any(box and not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1) for box in boxes):
            return self._failed('局部参考越界，请补关键帧')
        return replace(result,boxes=boxes,predicted_boxes=self.regions,
            observation=replace(obs,displacement=delta,points=points,source_size=image.size,
                source_per_analysis=tuple(scale),reference_origin=self.origin,moving_origin=origin))

    def _failed(self, message):
        return RegionTrackingResult((None,)*len(self.regions),error=message,predicted_boxes=self.regions,
            observation=LocalObservation('needs_keyframe',message,source_size=self.reference_size))

    def track_cached(self, path, image, *, cancelled):
        from . import subject_observation_cache as cache
        signature = image_file_signature(path)
        reference = getattr(self,'cache_reference',None)
        from .bird_observation_cache import detector_signature
        key = ('local-crop-v1',reference,signature,self.regions,self.target,self.side,self.analysis_size,detector_signature())
        if cancelled():
            raise InterruptedError('已取消局部主体跟踪')
        result = cache.get(key) if signature and reference else None
        if result is None:
            result = self.track(image,cancelled=cancelled,source_path=path)
            if signature and reference and not cancelled() and image_file_signature(path) == signature:
                cache.put(key,result)
        return result

    def rekeyframe(self, image, boxes):
        w,h = self.reference_size
        offsets = [((b[0]+b[2]-r[0]-r[2])/2,(b[1]+b[3]-r[1]-r[3])/2) for b,r in zip(boxes,self.regions)]
        dx,dy = np.median(offsets,axis=0)
        target = tuple(v+(dx if i%2 == 0 else dy) for i,v in enumerate(self.target))
        return LocalCropSubjectTracker(image,boxes,target=target,options=self.options,detector=self.detector)
