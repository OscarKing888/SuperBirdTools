"""人工局部的固定关键帧 LK 跟踪；不加载检测器，不估计缩放或旋转。

像素阈值统一作用于长边最多 2048 的分析图。坐标恢复使用实际双轴比例，
EXIF 方向由共用 decoder 统一。参考虚拟锚点固定，不随可见点质心漂移。
"""
from dataclasses import dataclass, replace
import numpy as np
from PIL import Image

from .matching_options import MatchingOptions
from .region_tracking_result import RegionTrackingResult

ALGORITHM_VERSION = 1


@dataclass(frozen=True, slots=True)
class LocalObservation:
    status: str
    reason: str = ''
    displacement: tuple | None = None  # reference -> source，源像素
    analysis_size: tuple = ()
    source_size: tuple = ()
    source_per_analysis: tuple = ()
    # (持续 ID, 区域, ref_x, ref_y, moving_x, moving_y, accepted, fb_error)，源像素
    points: tuple = ()
    region_metrics: tuple = ()  # (候选数, 有效数, 内点数, 覆盖率, dx, dy)
    algorithm_version: int = ALGORITHM_VERSION

    def __post_init__(self):
        for name in ('analysis_size','source_size','source_per_analysis'):
            object.__setattr__(self,name,tuple(getattr(self,name)))
        for name in ('points','region_metrics'):
            object.__setattr__(self,name,tuple(tuple(row) for row in getattr(self,name)))
        if self.displacement is not None:
            object.__setattr__(self,'displacement',tuple(self.displacement))

    def summary(self):
        count = sum(bool(p[6]) for p in self.points)
        label = {'tracked':'已跟踪','needs_keyframe':'需补关键帧','user_override':'人工关键帧','reference':'参考帧'}.get(self.status,self.status)
        return f'局部主体 · {label} · 内点 {count}/{len(self.points)}' + (f' · {self.reason}' if self.reason else '')


def consensus_translation(a, b, threshold=2.5):
    delta = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    if not len(delta):
        return None, np.zeros(0, bool)
    if not np.isfinite(delta).all():
        raise ValueError('局部主体：对应点包含非有限值')
    counts = np.zeros(len(delta), dtype=int)
    for start in range(0, len(delta), 128):
        counts[start:start+128] = (np.linalg.norm(delta[start:start+128, None] - delta[None], axis=2) < threshold).sum(axis=1)
    selected = np.linalg.norm(delta - delta[counts.argmax()], axis=1) < threshold
    for _ in range(3):
        displacement = np.median(delta[selected], axis=0)
        updated = np.linalg.norm(delta - displacement, axis=1) < threshold
        if not updated.any():
            break
        selected = updated
    return np.median(delta[selected], axis=0), selected


class SubjectLocalTracker:
    supports_recovery = False

    def __init__(self, reference, regions, *, options=MatchingOptions()):
        try:
            import cv2
        except ImportError as exc:
            raise ValueError('高级局部跟踪需要 OpenCV；请安装项目依赖或选择基本方法。') from exc
        self.options = options
        self.regions = tuple(tuple(float(v) for v in r) for r in regions)
        if not self.regions or len(self.regions) > 28:
            raise ValueError('局部主体：请选择 1–28 个局部参考区')
        for l,t,r,b in self.regions:
            if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
                raise ValueError('局部主体：参考区必须在图像内')
        self.reference_size = reference.size
        scale = min(1., 2048 / max(reference.size))
        self.analysis_size = tuple(max(1, round(v*scale)) for v in reference.size)
        self.source_per_analysis = np.array(reference.size) / np.array(self.analysis_size)
        self.reference = self._gray(reference)
        self.points = []
        occupied = np.zeros(self.reference.shape, np.uint8)
        w,h = self.analysis_size
        for l,t,r,b in self.regions:
            mask = np.zeros_like(occupied)
            x0,y0,x1,y1 = round(l*w),round(t*h),round(r*w),round(b*h)
            mask[y0:y1,x0:x1] = 255
            mask[occupied > 0] = 0  # 重叠区域的点不能重复投票。
            occupied |= mask
            candidates = cv2.goodFeaturesToTrack(self.reference, maxCorners=560, qualityLevel=.006,
                                                minDistance=7, mask=mask, blockSize=5)
            candidates = np.empty((0,2), np.float32) if candidates is None else candidates[:,0]
            # 每格限额，保留角点质量顺序，避免少数边缘垄断投票。
            cells, selected = {}, []
            for point in candidates:
                cell = (min(3,int((point[0]-x0)*4/max(1,x1-x0))), min(3,int((point[1]-y0)*4/max(1,y1-y0))))
                if cells.get(cell,0) < 9 and len(selected) < 140:
                    selected.append(point)
                    cells[cell] = cells.get(cell,0)+1
            self.points.append(np.asarray(selected, np.float32).reshape(-1,1,2))

    def _gray(self, image):
        with image.resize(self.analysis_size, Image.Resampling.LANCZOS) as small:
            with small.convert('L') as gray:
                return np.array(gray)

    def track(self, image, *, cancelled=lambda: False):
        import cv2
        def check():
            if cancelled():
                raise InterruptedError('局部主体跟踪已取消')
        check()
        if image.size != self.reference_size:
            return self._result(None, (), (), '源尺寸或方向不同，请分段设置新参考')
        moving = self._gray(image)
        w,h = self.analysis_size
        kwargs = dict(winSize=(25,25), maxLevel=min(4,max(0,int(np.log2(max(1,min(w,h)/32))))), criteria=(3,40,.005))
        records, metrics, deltas, all_valid, all_inliers = [], [], [], 0, 0
        reasons = []
        point_id = 0
        for index, points in enumerate(self.points):
            check()
            a = points[:,0]
            b = a.copy()
            valid = np.zeros(len(a), bool)
            fb = np.full(len(a), np.inf)
            if len(a):
                q, status, _ = cv2.calcOpticalFlowPyrLK(self.reference, moving, points, None, **kwargs)
                check()
                if q is not None and status is not None:
                    # OpenCV 的失败点可能非有限；反向跟踪只接收有限坐标。
                    finite = np.isfinite(q).all(axis=(1,2))
                    safe = np.where(finite[:,None,None], q, points)
                    rev, reverse_status, _ = cv2.calcOpticalFlowPyrLK(moving,self.reference,safe,None,**kwargs)
                    b = safe[:,0]
                    if rev is not None and reverse_status is not None:
                        fb = np.linalg.norm(a-rev[:,0],axis=1)
                        valid = (finite & status[:,0].astype(bool) & reverse_status[:,0].astype(bool)
                                 & np.isfinite(fb) & (fb < 1.5) & (np.linalg.norm(b-a,axis=1) < 100)
                                 & (b[:,0] >= 0) & (b[:,0] < w) & (b[:,1] >= 0) & (b[:,1] < h))
            delta, selected = consensus_translation(a[valid],b[valid])
            inliers = np.zeros(len(a),bool)
            inliers[np.flatnonzero(valid)[selected]] = True
            n = int(inliers.sum())
            # 归一化覆盖以区域长边投影衡量；窄腿也允许沿纵向分布。
            region = self.regions[index]
            span = max((region[2]-region[0])*w,(region[3]-region[1])*h)
            coverage = float(np.ptp(a[inliers],axis=0).max()/max(1,span)) if n else 0.
            good = delta is not None and n >= 5 and valid.sum() >= .4*len(a) and n >= .5*valid.sum() and coverage >= .2
            if not good:
                reasons.append(f'选区 {index+1} 纹理、覆盖或可靠点不足')
            else:
                deltas.append(delta)
            metrics.append((len(a),int(valid.sum()),n,coverage,*(tuple(float(v) for v in delta) if delta is not None else (None,None))))
            all_valid += int(valid.sum())
            all_inliers += n
            for j,(p,q) in enumerate(zip(a,b)):
                ps,qs = p*self.source_per_analysis,q*self.source_per_analysis
                records.append((point_id,index,*map(float,ps),*map(float,qs),bool(inliers[j] and good),float(fb[j]) if np.isfinite(fb[j]) else None))
                point_id += 1
        check()
        if len(deltas) > 1 and np.linalg.norm(np.array(deltas)[:,None]-np.array(deltas)[None],axis=2).max() > 3.5:
            reasons.append('参考局部运动冲突，可能有姿态变化；请修正匹配或设置新关键帧')
        if all_inliers < 12 or all_inliers < .5*all_valid:
            reasons.append('共同平移证据不足')
        if reasons:
            return self._result(None,records,metrics,'；'.join(reasons))
        # 各区域等权；角点较多的区域不会压过其它局部。
        delta = np.median(np.array(deltas),axis=0)*self.source_per_analysis
        return self._result(tuple(map(float,delta)),records,metrics,'')

    def _result(self, delta, points, metrics, reason):
        w,h = self.reference_size
        boxes = tuple((l+delta[0]/w,t+delta[1]/h,r+delta[0]/w,b+delta[1]/h) for l,t,r,b in self.regions) if delta else (None,)*len(self.regions)
        if delta and any(not (0 <= l < r <= 1 and 0 <= t < b <= 1) for l,t,r,b in boxes):
            delta,boxes,reason = None,(None,)*len(self.regions),'局部参考越界，请设置新关键帧'
        observation = LocalObservation('tracked' if delta else 'needs_keyframe',reason,delta,self.analysis_size,
                                       self.reference_size,tuple(map(float,self.source_per_analysis)),tuple(points),tuple(metrics))
        return RegionTrackingResult(boxes,error=reason,scores=(1. if delta else 0.,)*len(boxes),
                                    reasons=(reason,)*len(boxes),predicted_boxes=self.regions,observation=observation)

    def track_cached(self, path, image, *, cancelled):
        from . import subject_observation_cache as cache
        from .region_tracking_result import image_file_signature
        if cancelled():
            raise InterruptedError('局部主体跟踪已取消')
        reference_signature = getattr(self, 'cache_reference', None)
        signature = image_file_signature(path)
        key = (ALGORITHM_VERSION, reference_signature, signature, self.regions, self.reference_size, self.analysis_size)
        result = cache.get(key) if reference_signature and signature else None
        if result is None:
            result = self.track(image,cancelled=cancelled)
            if (reference_signature and signature and not cancelled()
                    and image_file_signature(path) == signature):
                cache.put(key,result)
        return result

    def recover(self, image, result, previous, following, *, cancelled=lambda: False):
        # 不借邻帧插值伪造图像观测；只能显式人工修正/补参考。
        return result

    def resolve_manual(self, result, manual_boxes, size):
        from .manual_region_matches import apply_manual_boxes
        result = apply_manual_boxes(result,manual_boxes)
        offsets = [((b[0]+b[2]-r[0]-r[2])*size[0]/2,(b[1]+b[3]-r[1]-r[3])*size[1]/2)
                   for r,b in zip(self.regions,result.boxes) if b is not None]
        if len(offsets) != len(self.regions):
            return replace(result,boxes=(None,)*len(self.regions),observation=None,error='请修正所有失败选区，或设置新的局部参考')
        d = np.array(offsets)/self.source_per_analysis
        if np.linalg.norm(d[:,None]-d[None],axis=2).max() > 3.5:
            return replace(result,boxes=(None,)*len(self.regions),observation=None,error='人工局部位置冲突，请确认各区域属于同一运动')
        delta = tuple(map(float,np.median(offsets,axis=0)))
        observation = LocalObservation('user_override',displacement=delta,source_size=size)
        return replace(result,error='',reasons=('',)*len(self.regions),observation=observation)
