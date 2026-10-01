"""AK 鸟类关键点推理：RGB、1.25 倍正方形、MSRA 热图及翻转测试。"""
from dataclasses import dataclass
from functools import lru_cache
import threading
import numpy as np
from .model_store import model_path, verify_model, MODEL_ID

_LOCK = threading.RLock()
FLIP = [0, 2, 1, 3, 5, 4, 6, 8, 7, 10, 9, 12, 11, 13, 15, 14, 17, 16, 19, 18, 20, 21, 22]
PART_LABELS = {'auto': '自动推荐', 'head': '头部', 'torso': '躯干', 'legs': '腿部'}


@dataclass(frozen=True)
class PartCandidate:
    part: str
    regions: tuple
    confidence: float


def decode_heatmaps(heatmaps):
    """MMPose MSRAHeatmap 默认四分之一像素梯度修正。"""
    channels, h, w = heatmaps.shape
    flat = heatmaps.reshape(channels, -1)
    index = flat.argmax(axis=1)
    points = np.column_stack((index % w, index // w)).astype(float)
    scores = flat.max(axis=1)
    for k, (x, y) in enumerate(points.astype(int)):
        if 1 < x < w-1 and 1 < y < h-1:
            points[k] += .25 * np.sign([heatmaps[k, y, x+1]-heatmaps[k, y, x-1],
                                      heatmaps[k, y+1, x]-heatmaps[k, y-1, x]])
    return points / (w, h), scores


@lru_cache(maxsize=1)
def _load_model(path, signature):
    import torch
    from .hrnet import build_hrnet
    verify_model(path)
    model = build_hrnet()
    # 官方旧 checkpoint 元数据含 NumPy 对象；仅在完整固定 SHA-256 校验后反序列化。
    state = torch.load(path, map_location='cpu', weights_only=False)['state_dict']
    state = {k.removeprefix('backbone.').replace('head.final_layer.', 'final_layer.'): v for k, v in state.items()}
    model.load_state_dict(state, strict=True)
    model.eval()
    return model


def predict_parts(image, bird_box, *, path=None, device=None, cancelled=lambda: False):
    import cv2
    import torch
    from birdstamp.gui.editor_core import _preferred_bird_detect_device
    path = path or model_path()
    if not path.is_file():
        raise ValueError('尚未安装鸟体部位模型，请在选区组下载或导入。')
    if cancelled():
        raise InterruptedError('已取消部位识别')
    w, h = image.size
    l, t, r, b = np.array(bird_box) * (w, h, w, h)
    side = max(r-l, b-t) * 1.25
    origin = np.array(((l+r-side)/2, (t+b-side)/2))
    # 只复制鸟体附近源像素，不把整张高分辨率原图变成 NumPy 张量。
    bounds = (int(np.floor(origin[0])), int(np.floor(origin[1])),
              int(np.ceil(origin[0]+side)), int(np.ceil(origin[1]+side)))
    with image.crop(bounds).convert('RGB') as crop:
        matrix = np.array([[256/side, 0, (bounds[0]-origin[0])*256/side],
                           [0, 256/side, (bounds[1]-origin[1])*256/side]], np.float32)
        pixels = cv2.warpAffine(np.asarray(crop), matrix, (256, 256), flags=cv2.INTER_LINEAR)
    tensor = torch.from_numpy(((pixels.astype(np.float32)-[123.675,116.28,103.53]) /
                              [58.395,57.12,57.375]).astype(np.float32).transpose(2,0,1).copy())[None]
    device = device if device is not None else _preferred_bird_detect_device()
    device = f'cuda:{device}' if isinstance(device, int) else device
    with _LOCK, torch.inference_mode():
        stat = path.stat()
        model = _load_model(path, (stat.st_size, stat.st_mtime_ns))
        try:
            model.to(device)
            batch = torch.cat((tensor, tensor.flip(-1))).to(device)
            heat = model(batch).cpu().numpy()
        except (RuntimeError, NotImplementedError):
            if device == 'cpu':
                raise
            model.to('cpu')
            heat = model(torch.cat((tensor, tensor.flip(-1)))).numpy()
    if cancelled():
        raise InterruptedError('已取消部位识别')
    flipped = heat[1, FLIP, :, ::-1].copy()
    flipped[:, :, 1:] = flipped[:, :, :-1].copy()
    a, _ = decode_heatmaps(heat[0])
    z, _ = decode_heatmaps(flipped)
    points, scores = decode_heatmaps((heat[0]+flipped)/2)
    points = (points*side+origin) / (w, h)
    # 翻转一致性独立于峰值，避免把不可见关节的单个高峰当作可靠定位。
    reliable = (scores >= .45) & (np.linalg.norm(a-z, axis=1) <= .08)
    reliable &= (points[:,0] >= bird_box[0]) & (points[:,0] <= bird_box[2])
    reliable &= (points[:,1] >= bird_box[1]) & (points[:,1] <= bird_box[3])
    return dict(model=MODEL_ID, points=points.tolist(), scores=scores.tolist(), reliable=reliable.tolist())


def candidates_from_pose(pose, bird_box):
    p, scores, valid = np.array(pose['points']), np.array(pose['scores']), np.array(pose['reliable'])
    bw, bh = bird_box[2]-bird_box[0], bird_box[3]-bird_box[1]
    def region(indices, minimum, margin):
        ids = [i for i in indices if valid[i]]
        if len(ids) < minimum:
            return None
        a, z = p[ids].min(axis=0), p[ids].max(axis=0)
        pad = np.array((bw,bh))*margin
        a = np.maximum(a-pad, bird_box[:2]); z = np.minimum(z+pad, bird_box[2:])
        if min(z-a) <= 0:
            return None
        return tuple(map(float, (*a,*z))), float(scores[ids].min())
    result = []
    for part, indices, minimum, margin in [('head', range(7), 3, .035),
                                          ('torso', (7,8,13,14,15,20), 3, .025)]:
        candidate = region(indices, minimum, margin)
        if candidate:
            result.append(PartCandidate(part, (candidate[0],), candidate[1]))
    legs = [region(ids, 2, .025) for ids in ((16,18), (17,19))]
    legs = [v for v in legs if v]
    if legs:
        result.append(PartCandidate('legs', tuple(v[0] for v in legs), min(v[1] for v in legs)))
    return tuple(result)
