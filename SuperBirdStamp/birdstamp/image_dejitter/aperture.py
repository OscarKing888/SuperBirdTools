"""选区纹理分类：二维纹理、单向边缘（电线/枝条）或平坦。

单向边缘只能测量法向位移（孔径问题）：沿线方向的匹配会任意滑动。
这里在创建跟踪器时就判定，并给出“约束哪个方向”的明确说明。
"""
from dataclasses import dataclass
import math
import numpy as np
from PIL import Image

FLAT_STD = 3.
FLAT_STRENGTH = 2.
ONE_D_RATIO = .02
TWO_D_RATIO = .05
PARALLEL_DEGREES = 20.


@dataclass(frozen=True, slots=True)
class RegionTexture:
    kind: str           # '2d' | '1d' | 'flat'
    ratio: float        # 结构张量 λmin/λmax
    normal: tuple       # 单位法向（主梯度方向），源坐标轴
    strength: float     # sqrt(λmax)，灰度/像素


def structure_tensor(gray):
    import cv2
    pixels = cv2.GaussianBlur(np.asarray(gray, dtype=np.float32), (0, 0), 1.)
    gy, gx = np.gradient(pixels)
    return np.array([[np.mean(gx*gx), np.mean(gx*gy)], [np.mean(gx*gy), np.mean(gy*gy)]])


def classify_texture(gray):
    gray = np.asarray(gray)
    if gray.size == 0 or min(gray.shape) < 6:
        return RegionTexture('flat', 0., (0., 1.), 0.)
    values, vectors = np.linalg.eigh(structure_tensor(gray))
    low, high = max(0., float(values[0])), max(0., float(values[1]))
    ratio = low/high if high > 1e-9 else 0.
    normal = vectors[:, 1]
    if normal[1] < 0 or (abs(normal[1]) < 1e-12 and normal[0] < 0):
        normal = -normal
    strength = math.sqrt(high)
    if float(gray.std()) < FLAT_STD or strength < FLAT_STRENGTH:
        kind = 'flat'
    elif ratio < ONE_D_RATIO:
        kind = '1d'
    else:
        kind = '2d'
    return RegionTexture(kind, ratio, tuple(map(float, normal)), strength)


def classify_regions(image, regions, *, max_side=256):
    """GUI 即时提示用：每区缩略到 ≤max_side 后分类，不复制整幅原图。"""
    result = []
    W, H = image.size
    for l, t, r, b in regions:
        box = (l*W, t*H, r*W, b*H)
        scale = max(1., max(box[2]-box[0], box[3]-box[1])/max_side)
        size = (max(1, round((box[2]-box[0])/scale)), max(1, round((box[3]-box[1])/scale)))
        with image.resize(size, Image.Resampling.LANCZOS, box=box) as small, small.convert('L') as gray:
            result.append(classify_texture(np.array(gray)))
    return tuple(result)


def direction_label(vector):
    angle = math.degrees(math.atan2(vector[1], vector[0])) % 180
    if angle < 12 or angle > 168:
        return '水平'
    if 78 < angle < 102:
        return '竖直'
    return f'约 {round(angle)}° 方向'


def _names(indices):
    return '、'.join(str(i+1) for i in indices)


def _spread(normals):
    """法向两两最大夹角（0–90°）。"""
    best = 0.
    for i, a in enumerate(normals):
        for b in normals[i+1:]:
            cosine = min(1., abs(float(np.dot(a, b))))
            best = max(best, math.degrees(math.acos(cosine)))
    return best


def aperture_problems(textures):
    """创建跟踪器时必须拒绝的组合；返回中文说明列表。"""
    problems = []
    flat = [i for i, t in enumerate(textures) if t.kind == 'flat']
    if flat:
        problems.append(f'选区 {_names(flat)} 纹理不足（近乎平坦），无法跟踪；请改选有清晰细节的部位')
    edges = [i for i, t in enumerate(textures) if t.kind == '1d']
    if edges and not any(t.kind == '2d' for t in textures):
        normals = [np.array(textures[i].normal) for i in edges]
        if _spread(normals) < PARALLEL_DEGREES:
            label = direction_label(np.mean([n*np.sign(n @ normals[0]) for n in normals], axis=0))
            many = '都是近乎平行的单向边缘' if len(edges) > 1 else '是单向边缘'
            problems.append(f'选区 {_names(edges)} {many}（电线/枝条），只能约束{label}方向；'
                            '请至少加入一个有二维纹理的局部（如鸟体），或加入方向明显不同的边缘')
    return problems


def texture_summary(textures):
    """“选区 1：二维纹理；选区 2–4：单向边缘，仅约束竖直方向”。"""
    parts = []
    for kind, text in (('2d', '二维纹理'), ('flat', '近乎平坦，无法跟踪')):
        indices = [i for i, t in enumerate(textures) if t.kind == kind]
        if indices:
            parts.append(f'选区 {_names(indices)}：{text}')
    groups = {}
    for i, t in enumerate(textures):
        if t.kind == '1d':
            groups.setdefault(direction_label(t.normal), []).append(i)
    for label, indices in groups.items():
        parts.append(f'选区 {_names(indices)}：单向边缘，仅约束{label}方向')
    return '；'.join(parts)
