"""同一 YOLO 实例的全部鸟候选；局部检测不改变原预览检测参数。"""
from dataclasses import dataclass
import threading

DETECTOR_LOCK = threading.RLock()


@dataclass(frozen=True)
class BirdCandidate:
    box: tuple
    confidence: float


def iou(a, b):
    area = max(0,min(a[2],b[2])-max(a[0],b[0])) * max(0,min(a[3],b[3])-max(a[1],b[1]))
    return area / max(1e-12, (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area)


def detect_bird_candidates(image, *, tiled=True, cancelled=lambda: False):
    from birdstamp.gui import editor_core as core
    from PIL import Image
    def detect(box):
        if cancelled():
            raise InterruptedError('已取消目标鸟识别')
        width,height=box[2]-box[0],box[3]-box[1]
        scale=min(1.,1280/max(width,height))
        size=(max(1,round(width*scale)),max(1,round(height*scale)))
        # 从源图直接采样；并行分析 84 MP 原片时不额外复制整幅 RGB 图。
        with image.resize(size,Image.Resampling.LANCZOS,box=box) as small, small.convert('RGB') as crop:
            with DETECTOR_LOCK:
                detector = core._load_bird_detector()
                if detector is None:
                    raise ValueError('YOLO：'+core.get_bird_detector_error_message())
                model, classes = detector
                kwargs = dict(source=crop, conf=core._BIRD_DETECT_CONFIDENCE, verbose=False)
                device = core._preferred_bird_detect_device()
                try:
                    results = model.predict(device=device, **kwargs)
                except Exception:
                    if device == 'cpu':
                        raise
                    results = model.predict(device='cpu', **kwargs)
            found = []
            for result in results:
                for b in result.boxes:
                    if int(b.cls.item()) not in classes:
                        continue
                    l,t,r,z = b.xyxy.cpu().numpy()[0]
                    sx,sy = (box[2]-box[0])/crop.width, (box[3]-box[1])/crop.height
                    rect = ((box[0]+l*sx)/image.width,(box[1]+t*sy)/image.height,
                            (box[0]+r*sx)/image.width,(box[1]+z*sy)/image.height)
                    rect = tuple(max(0.,min(1.,float(v))) for v in rect)
                    found.append(BirdCandidate(rect,float(b.conf.item())))
            return found
    found = detect((0,0,*image.size))
    if not found and tiled and max(image.size) > 1280:
        # 3×3 重叠裁片最多九次，不随高分辨率照片无限增加推理次数。
        for y in (0., .3, .6):
            for x in (0., .3, .6):
                found.extend(detect(tuple(round(v) for v in
                    (x*image.width,y*image.height,(x+.4)*image.width,(y+.4)*image.height))))
    kept = []
    for item in sorted(found, key=lambda v: -v.confidence):
        if not any(iou(item.box, previous.box) > .4 for previous in kept):
            kept.append(item)
    return tuple(kept)


def associate_target(reference_box, candidates):
    """只允许唯一的邻近候选；无证据不跳到另一只鸟。"""
    import math
    cx, cy = (reference_box[0]+reference_box[2])/2, (reference_box[1]+reference_box[3])/2
    ranked = sorted(candidates,key=lambda v:-iou(reference_box,v.box))
    if ranked and iou(reference_box,ranked[0].box) >= .15:
        if len(ranked)==1 or iou(reference_box,ranked[0].box)-iou(reference_box,ranked[1].box) >= .15:
            return ranked[0].box
        raise ValueError('目标鸟重叠或身份有歧义，请确认目标。')
    radius = max(.08, math.hypot(reference_box[2]-reference_box[0],reference_box[3]-reference_box[1])*.5)
    nearby = [v for v in candidates if math.hypot((v.box[0]+v.box[2])/2-cx,
                                                 (v.box[1]+v.box[3])/2-cy) <= radius]
    if len(nearby) != 1:
        raise ValueError('目标鸟丢失或身份有歧义，请确认目标或补关键帧。')
    return nearby[0].box
