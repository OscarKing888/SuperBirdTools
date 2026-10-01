"""按照片顺序建立只读目标轨迹；身份关联不产生稳定补偿。"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType

import numpy as np
from PIL import Image

from .bird_candidates import detect_bird_candidates, iou
from .bird_observation_cache import cached_observation, detect_cached, detector_signature
from .region_tracking_result import image_file_signature

TRAJECTORY_VERSION = 1


def _key(path):
    from birdstamp.gui.editor_utils import path_key
    return path_key(Path(path))


def appearance(image, box):
    """小型颜色分布只用于排除不相似目标，不用它代替位置/歧义核验。"""
    # 中央区域减少天空/电线进入颜色分布；仍保留较宽范围以适应鸟的姿态变化。
    l,t,r,b = box
    dx,dy = (r-l)*.1,(b-t)*.1
    bounds = ((l+dx)*image.width,(t+dy)*image.height,(r-dx)*image.width,(b-dy)*image.height)
    with image.resize((48,48),Image.Resampling.BILINEAR,box=bounds).convert('HSV') as crop:
        pixels = np.asarray(crop).reshape(-1,3)
        hist = np.histogramdd(pixels,bins=(8,4,4),range=((0,256),)*3)[0].ravel()
    return tuple(np.sqrt(hist/max(1,hist.sum())))


def _center(box):
    return np.array(((box[0]+box[2])/2,(box[1]+box[3])/2))


def refinement_allowed(previous, candidates):
    """仅对漏检/单框或包含关系的重复框重检；独立的多鸟竞争不能被一次漏检抹去。"""
    radius = max(.035,float(np.linalg.norm(np.array(previous[2:])-previous[:2]))*1.5)
    nearby = [b for b in candidates if np.linalg.norm(_center(b.box)-_center(previous)) <= radius*1.5]
    if len(nearby) <= 1:return True
    def contains(a,b):
        if a==b:return True
        # 两个大小相近的重叠鸟框仍是身份竞争，不按“包含”消除。
        if (b[2]-b[0])*(b[3]-b[1]) > .7*(a[2]-a[0])*(a[3]-a[1]):return False
        intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        return intersection/max(1e-12,(b[2]-b[0])*(b[3]-b[1])) >= .9
    return any(all(contains(a.box,b.box) for b in nearby) for a in nearby)


def _refined_observations(path, previous, cancelled):
    from birdstamp.decoders.image_decoder import decode_image
    from .bird_candidates import BirdCandidate
    def compute():
        with decode_image(path,decoder='auto') as image:
            w,h=image.size
            cx,cy=_center(previous)*image.size
            side=max((previous[2]-previous[0])*w,(previous[3]-previous[1])*h)*2.5
            bounds=(max(0,cx-side/2),max(0,cy-side/2),min(w,cx+side/2),min(h,cy+side/2))
            l,t,r,b=bounds
            scale=min(1.,1280/max(r-l,b-t))
            size=(max(1,round((r-l)*scale)),max(1,round((b-t)*scale)))
            with image.resize(size,Image.Resampling.LANCZOS,box=bounds) as crop:
                local=detect_bird_candidates(crop,tiled=False,cancelled=cancelled)
            birds=tuple(BirdCandidate(((l+c.box[0]*(r-l))/w,(t+c.box[1]*(b-t))/h,
                                      (l+c.box[2]*(r-l))/w,(t+c.box[3]*(b-t))/h),c.confidence) for c in local)
            return birds,tuple(appearance(image,c.box) for c in birds)
    return cached_observation(path,'target-refinement-v1',(tuple(previous),detector_signature()),compute,cancelled)


def associate_temporal(previous, velocity, candidates, descriptors, anchor, recent):
    """预测位置、外观和候选竞争共同决定身份；重叠/近似候选直接拒绝。"""
    center = _center(previous)
    diagonal = float(np.linalg.norm(np.array(previous[2:])-previous[:2]))
    radius = max(.035,diagonal*1.5)
    prediction = center + np.clip(velocity,-radius*.5,radius*.5)
    area = (previous[2]-previous[0])*(previous[3]-previous[1])
    ranked = []
    for bird, descriptor in zip(candidates,descriptors):
        box = bird.box
        distance = float(np.linalg.norm(_center(box)-prediction))
        ratio = (box[2]-box[0])*(box[3]-box[1])/max(area,1e-12)
        similarity = min(float(np.dot(descriptor,anchor)),float(np.dot(descriptor,recent)))
        if (distance <= radius and np.linalg.norm(_center(box)-center) <= radius*1.5
                and .2 <= ratio <= 5 and similarity >= .55):
            score = .6*distance/radius + .4*(1-similarity)
            ranked.append((score,bird,descriptor))
    ranked.sort(key=lambda item:item[0])
    if not ranked:
        raise ValueError('目标关联：位置或外观证据不足，请确认目标或补关键帧。')
    if len(ranked)>1 and (ranked[1][0]-ranked[0][0] < .15 or iou(ranked[0][1].box,ranked[1][1].box) > .2):
        raise ValueError('目标关联：多鸟竞争或重叠，身份有歧义，请确认目标。')
    return ranked[0][1].box,ranked[0][2]


@dataclass(frozen=True)
class TargetFrame:
    box: tuple | None
    signature: tuple | None
    error: str = ''


@dataclass(frozen=True)
class TargetTrajectory:
    frames: object
    signature: str

    def frame(self, path):
        frame = self.frames.get(_key(path))
        if frame is None or frame.signature != image_file_signature(Path(path)):
            return TargetFrame(None,None,'目标轨迹已失效，请重新分析。')
        return frame


def build_target_trajectory(reference, paths, target, *, cancelled=lambda:False,
                            progress=lambda text:None, detector=detect_bird_candidates):
    """补齐抽样间的每张检测；参考在中间时分别向两侧关联，失败后不跨越重识别。"""
    from birdstamp.decoders.image_decoder import decode_image
    reference = Path(reference)
    ordered = list(dict.fromkeys(Path(p) for p in paths))
    if reference not in ordered:
        ordered.insert(0,reference)
    signatures = {p:image_file_signature(p) for p in ordered}
    def check():
        if cancelled():raise InterruptedError('已取消目标鸟关联')
    def observations(path):
        def compute():
            with decode_image(path,decoder='auto') as image:
                birds = (detect_cached(path,image,cancelled=cancelled) if detector is detect_bird_candidates
                         else detector(image,cancelled=cancelled))
                return image.size,birds,tuple(appearance(image,b.box) for b in birds)
        if detector is not detect_bird_candidates:return compute()
        return cached_observation(path,'target-appearance-v1',detector_signature(),compute,cancelled)
    check()
    with decode_image(reference,decoder='auto') as image:
        size = image.size
        anchor = appearance(image,target)
    frames = {_key(reference):TargetFrame(tuple(target),signatures[reference])}
    reference_index = ordered.index(reference)
    completed = 1
    for direction in (ordered[reference_index+1:],list(reversed(ordered[:reference_index]))):
        previous,recent,velocity = tuple(target),anchor,np.zeros(2)
        failure = ''
        for path in direction:
            check()
            completed += 1
            progress(f'关联目标鸟 {completed}/{len(ordered)}：{path.name}')
            if failure:
                frames[_key(path)] = TargetFrame(None,signatures[path],failure)
                continue
            try:
                current_size,birds,descriptors = observations(path)
                if current_size != size:raise ValueError('目标关联：源尺寸或方向不同。')
                try:
                    box,descriptor = associate_temporal(previous,velocity,birds,descriptors,anchor,recent)
                except ValueError:
                    if detector is not detect_bird_candidates or not refinement_allowed(previous,birds):raise
                    # 高分辨率局部重检必须唯一且较可信，之后仍过同一身份门槛。
                    refined,features=_refined_observations(path,previous,cancelled)
                    if len(refined)!=1 or refined[0].confidence < .6:raise
                    box,descriptor=associate_temporal(previous,velocity,refined,features,anchor,recent)
                velocity = _center(box)-_center(previous)
                previous,recent = box,descriptor
                frames[_key(path)] = TargetFrame(box,signatures[path])
            except (ValueError,OSError) as exc:
                frames[_key(path)] = TargetFrame(None,signatures[path],str(exc))
                failure = f'目标关联中断于 {path.name}，未跨越失败帧重选鸟；请确认目标或补关键帧。'
    check()
    if any(image_file_signature(p)!=signature for p,signature in signatures.items()):
        raise ValueError('照片在目标关联期间变化，请重新分析。')
    signature = hashlib.sha256(repr((TRAJECTORY_VERSION,tuple(target),tuple(frames.items()),
                                    detector_signature())).encode('utf-8')).hexdigest()
    return TargetTrajectory(MappingProxyType(frames),signature)
