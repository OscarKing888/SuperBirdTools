"""人工局部的固定关键帧跟踪；不加载检测器，不估计缩放或旋转。

每个选区按自身尺寸换算到规范分析尺度（见 analysis_window），像素阈值因此对
任何分辨率的照片都代表相同的相对精度。选区先分为二维纹理 / 单向边缘 / 平坦：
单向边缘（电线、枝条）只提供法向约束，与二维区一起做孔径约束联合求解。
EXIF 方向由共用 decoder 统一，所有点与位移均以源像素记录。
"""
from dataclasses import dataclass, replace
import numpy as np

from .matching_options import MatchingOptions
from .region_tracking_result import RegionTrackingResult
from .analysis_window import region_scale, region_window, motion_margin
from .aperture import classify_texture, aperture_problems, direction_label
from .region_measurement import (Measurement, make_template, measure, consensus_translation,  # noqa: F401
                                 MIN_INLIERS)
from .translation_solver import solve_translation

ALGORITHM_VERSION = 2
MAX_REGIONS = 28


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
    reference_origin: tuple = (0., 0.)
    moving_origin: tuple = (0., 0.)
    method: str = 'lk'
    quality: float = 0.
    keyframe_paths: tuple = ()
    region_kinds: tuple = ()     # 每区 '2d' / '1d'
    region_scales: tuple = ()    # 每区源像素/分析像素
    constraints: tuple = ()      # 每区 Measurement.row()
    covariance: tuple = ()       # 位移后验协方差 2×2 展平（源像素²）
    constrained_by: tuple = ()   # ((方向, 后验标准差, (区号...)), ...)
    chain: tuple = ()            # (链数, 后验标准差源像素, 方向 'forward'/'reverse'/'both')

    def __post_init__(self):
        for name in ('analysis_size','source_size','source_per_analysis','reference_origin','moving_origin',
                     'keyframe_paths','region_kinds','region_scales','covariance','chain'):
            object.__setattr__(self,name,tuple(getattr(self,name)))
        for name in ('points','region_metrics'):
            object.__setattr__(self,name,tuple(tuple(row) for row in getattr(self,name)))
        object.__setattr__(self,'constraints',tuple(Measurement.from_row(row).row() for row in self.constraints))
        object.__setattr__(self,'constrained_by',tuple((str(l),None if s is None else float(s),tuple(int(i) for i in r))
                                                       for l,s,r in self.constrained_by))
        if self.displacement is not None:
            object.__setattr__(self,'displacement',tuple(self.displacement))

    def summary(self):
        count = sum(bool(p[6]) for p in self.points)
        label = {'tracked':'已跟踪','needs_keyframe':'需补关键帧','user_override':'人工关键帧','reference':'参考帧',
                 'keyframe_bridge':'双向分段核验','keyframe_chain':'链式关键帧'}.get(self.status,self.status)
        evidence = {'ncc_ecc':'子块','lk':'内点'}.get(self.method,'证据')
        text = f'局部主体 · {label} · {evidence} {count}/{len(self.points)}'
        if self.chain:
            text += f' · {self.chain[0]} 段链，σ≈{self.chain[1]:.1f} 源像素'
        if self.constrained_by and '1d' in self.region_kinds:
            text += ' · ' + ' · '.join(f'{d}←选区 {"、".join(str(i+1) for i in r)}' for d,_,r in self.constrained_by if r)
        return text + (f' · {self.reason}' if self.reason else '')


@dataclass(frozen=True, slots=True)
class SubjectGeometry:
    """固定的每区分析比例与纹理角色；关键帧重建时沿用，保证阈值尺度不变。"""
    kinds: tuple
    normals: tuple
    scales: tuple


def _source_box(box, size):
    return (box[0]*size[0], box[1]*size[1], box[2]*size[0], box[3]*size[1])


class SubjectLocalTracker:
    supports_recovery = False

    def __init__(self, reference, regions, *, options=MatchingOptions(), geometry=None):
        try:
            import cv2  # noqa: F401
        except ImportError as exc:
            raise ValueError('高级局部跟踪需要 OpenCV；请安装项目依赖或选择基本方法。') from exc
        self.options = options
        self.regions = tuple(tuple(float(v) for v in r) for r in regions)
        if not self.regions or len(self.regions) > MAX_REGIONS:
            raise ValueError(f'局部主体：请选择 1–{MAX_REGIONS} 个局部参考区')
        for l,t,r,b in self.regions:
            if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
                raise ValueError('局部主体：参考区必须在图像内')
        self.reference_size = reference.size
        self.margin = motion_margin(reference.size)
        if geometry is None:
            scales = tuple(region_scale(reference.size, box) for box in self.regions)
            textures = []
            for box,scale in zip(self.regions,scales):
                textures.append(classify_texture(region_window(reference.size,box,scale,0.).gray(reference)))
            problems = aperture_problems(textures)
            if problems:
                raise ValueError('局部主体：' + '；'.join(problems))
            geometry = SubjectGeometry(tuple(t.kind for t in textures),tuple(t.normal for t in textures),scales)
        self.geometry = geometry
        self.templates = tuple(self._template(reference,i,box) for i,box in enumerate(self.regions))
        sparse = [str(t.index+1) for t in self.templates if t.kind == '2d' and len(t.corners) < MIN_INLIERS]
        if sparse:
            raise ValueError(f'局部主体：选区 {"、".join(sparse)} 可跟踪角点不足，请改选有清晰细节的部位')
        self.analysis_size = tuple(t.window.size for t in self.templates)
        self.source_per_analysis = np.array((min(self.geometry.scales),)*2)

    # ---- 模板 ----
    def _template(self, image, index, box, *, base=(0.,0.), base_cov=(0.,0.,0.,0.), links=0, source=''):
        earlier = [_source_box(r,image.size) for r in self.regions[:index]]
        def exclude(window, points):
            # 重叠区域的点不能重复投票：位于更早选区内的角点归前者。
            src = window.to_source(points) - np.asarray(base)
            hit = np.zeros(len(points),bool)
            for l,t,r,b in earlier:
                hit |= (src[:,0] >= l) & (src[:,0] < r) & (src[:,1] >= t) & (src[:,1] < b)
            return hit
        return make_template(image,box,index=index,kind=self.geometry.kinds[index],normal=self.geometry.normals[index],
                             scale=self.geometry.scales[index],margin=self.margin,base=base,base_cov=base_cov,
                             links=links,source=source,exclude=exclude)

    def template_at(self, index, image, *, base, base_cov=(0.,0.,0.,0.), links=0, source=''):
        """以另一张已解出位移的照片为该区新关键帧；尺度与纹理角色不变。"""
        w,h = self.reference_size
        l,t,r,b = self.regions[index]
        box = (l+base[0]/w,t+base[1]/h,r+base[0]/w,b+base[1]/h)
        if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError(f'选区 {index+1} 在关键帧上越界')
        template = self._template(image,index,box,base=base,base_cov=base_cov,links=links,source=source)
        if template.kind == '2d' and len(template.corners) < MIN_INLIERS:
            raise ValueError(f'选区 {index+1} 在关键帧上可跟踪角点不足')
        return template

    # ---- 测量与求解 ----
    def measure_regions(self, image, templates=None, *, prior=(0.,0.), cancelled=lambda: False):
        return [measure(t,image,prior=prior,cancelled=cancelled) for t in (templates or self.templates)]

    def track(self, image, *, cancelled=lambda: False, prior=(0.,0.)):
        def check():
            if cancelled():
                raise InterruptedError('局部主体跟踪已取消')
        check()
        if image.size != self.reference_size:
            return self._result(None,(),(),'源尺寸或方向不同，请分段设置新参考')
        measurements = self.measure_regions(image,prior=prior,cancelled=cancelled)
        check()
        return self.solve_result(measurements)

    def solve_result(self, measurements, *, status='tracked', max_std=None, chain=(), keyframe_paths=()):
        measurements = sorted(measurements,key=lambda m:m.index)
        ok = [m for m in measurements if m.ok]
        failed = [m.index for m in measurements if not m.ok]
        reasons = [m.reason for m in measurements if not m.ok]
        solve = solve_translation(ok,failed=failed,max_std=max_std) if ok else None
        if solve is not None and not solve.ok and (not failed or '方向缺少约束' in solve.reason):
            reasons.append(solve.reason)
        if not ok and not reasons:
            reasons.append('共同平移证据不足')
        delta = solve.displacement if solve is not None and solve.ok and not failed else None
        points = tuple((i,*p[1:6],bool(p[6] and delta is not None),p[7])
                       for i,p in enumerate(p for m in measurements for p in m.points))
        methods = sorted({m.method for m in ok})
        origins = next((m.origins for m in measurements if m.origins),())
        extra = dict(constraints=tuple(m.row() for m in measurements),
                     covariance=solve.covariance if delta is not None else (),
                     constrained_by=solve.constrained_by if solve is not None else (),
                     method=methods[0] if len(methods) == 1 else ('+'.join(methods) or 'lk'),
                     quality=min((m.quality for m in ok),default=0.),chain=chain if delta is not None else (),
                     keyframe_paths=keyframe_paths if delta is not None else (),
                     reference_origin=origins[:2] or (0.,0.),moving_origin=origins[2:] or (0.,0.))
        result = self._result(delta,points,tuple(m.metrics for m in measurements),'；'.join(dict.fromkeys(reasons)),
                              status=status,**extra)
        if delta is not None:
            scores = tuple(m.quality for m in measurements)
            result = replace(result,scores=scores)
        return result

    def _result(self, delta, points, metrics, reason, *, status='tracked', **extra):
        w,h = self.reference_size
        boxes = tuple((l+delta[0]/w,t+delta[1]/h,r+delta[0]/w,b+delta[1]/h) for l,t,r,b in self.regions) if delta else (None,)*len(self.regions)
        if delta and any(not (0 <= l < r <= 1 and 0 <= t < b <= 1) for l,t,r,b in boxes):
            delta,boxes,reason = None,(None,)*len(self.regions),'局部参考越界，请设置新关键帧'
            extra.update(covariance=(),chain=(),keyframe_paths=())
        observation = LocalObservation(status if delta else 'needs_keyframe',reason,
                                       None if delta is None else tuple(map(float,delta)),
                                       self.templates[0].window.size if getattr(self,'templates',None) else (),
                                       self.reference_size,tuple(map(float,self.source_per_analysis)),tuple(points),tuple(metrics),
                                       region_kinds=self.geometry.kinds,region_scales=tuple(map(float,self.geometry.scales)),**extra)
        return RegionTrackingResult(boxes,error=reason,scores=(1. if delta else 0.,)*len(boxes),
                                    reasons=(reason,)*len(boxes),predicted_boxes=self.regions,observation=observation)

    def cache_key(self):
        return (ALGORITHM_VERSION,getattr(self,'cache_reference',None),self.regions,self.reference_size,self.geometry)

    def track_cached(self, path, image, *, cancelled):
        from . import subject_observation_cache as cache
        from .region_tracking_result import image_file_signature
        if cancelled():
            raise InterruptedError('局部主体跟踪已取消')
        reference_signature = getattr(self, 'cache_reference', None)
        signature = image_file_signature(path)
        key = (*self.cache_key(),signature)
        result = cache.get(key) if reference_signature and signature else None
        if result is None:
            result = self.track(image,cancelled=cancelled)
            if (reference_signature and signature and not cancelled()
                    and image_file_signature(path) == signature):
                cache.put(key,result)
        return result

    def motion_prior(self, path, image=None, *, cancelled=lambda: False):
        """链式核验用的额外运动先验（源像素）；整图跟踪器没有独立先验。"""
        return None

    def identity_error(self, path):
        """链式核验前的目标身份检查；整图跟踪器没有目标身份。"""
        return ''

    def recover(self, image, result, previous, following, *, cancelled=lambda: False):
        # 不借邻帧插值伪造图像观测；链式关键帧另由 subject_keyframes 用真实图像核验。
        return result

    def at_keyframe(self, image, boxes, target=None):
        return SubjectLocalTracker(image,boxes,options=self.options,geometry=self.geometry)

    def rekeyframe(self, image, boxes):
        return SubjectLocalTracker(image,boxes,options=self.options,geometry=self.geometry)

    def resolve_manual(self, result, manual_boxes, size):
        from .manual_region_matches import apply_manual_boxes
        result = apply_manual_boxes(result,manual_boxes)
        if any(b is None for b in result.boxes) or len(result.boxes) != len(self.regions):
            return replace(result,boxes=(None,)*len(self.regions),observation=None,error='请修正所有失败选区，或设置新的局部参考')
        measurements = []
        for i,(r,b) in enumerate(zip(self.regions,result.boxes)):
            offset = np.array(((b[0]+b[2]-r[0]-r[2])*size[0]/2,(b[1]+b[3]-r[1]-r[3])*size[1]/2))
            scale,normal = self.geometry.scales[i],np.array(self.geometry.normals[i])
            sigma = .5*scale
            if self.geometry.kinds[i] == '1d':
                # 沿电线拖动是任意的；只比较法向分量。
                measurements.append(Measurement(i,'1d',True,value=(float(normal @ offset),),normal=tuple(normal),
                                                covariance=(sigma**2,),scale=scale,method='manual'))
            else:
                measurements.append(Measurement(i,'2d',True,value=tuple(map(float,offset)),normal=tuple(normal),
                                                covariance=(sigma**2,0.,0.,sigma**2),scale=scale,method='manual',inliers=MIN_INLIERS*2))
        solve = solve_translation(measurements)
        if not solve.ok:
            error = ('人工局部位置冲突，请确认各区域属于同一运动' if '冲突' in solve.reason
                     else f'人工局部位置无法确定共同平移：{solve.reason}')
            return replace(result,boxes=(None,)*len(self.regions),observation=None,error=error)
        delta = tuple(map(float,solve.displacement))
        observation = LocalObservation('user_override',displacement=delta,source_size=size,
                                       region_kinds=self.geometry.kinds,region_scales=tuple(map(float,self.geometry.scales)))
        return replace(result,error='',reasons=('',)*len(self.regions),observation=observation)


def describe_constraints(observation):
    """诊断文本：各方向由哪些选区约束。"""
    return '；'.join(f'{d}方向 σ≈{s:.1f} 源像素 ← 选区 {"、".join(str(i+1) for i in r)}'
                     for d,s,r in observation.constrained_by if r and s is not None)


__all__ = ['SubjectLocalTracker','LocalObservation','SubjectGeometry','consensus_translation','direction_label',
           'describe_constraints','ALGORITHM_VERSION']
