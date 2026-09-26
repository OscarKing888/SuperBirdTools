from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True, slots=True)
class SearchTemplate:
    coarse: np.ndarray
    coarse_origin: tuple[int, int]
    fine: np.ndarray
    fine_origin: tuple[int, int]
    fine_size: tuple[int, int]


def _gray(image, size, box=None):
    with image.resize(size, Image.Resampling.BILINEAR, box=box) as resized:
        with resized.convert('L') as gray:
            return np.asarray(gray, dtype=np.float64)


def normalized_correlation(image, template):
    """有效区域 ZNCC：FFT 分子 + 积分图局部方差，不使用循环边界。"""
    h, w = image.shape
    th, tw = template.shape
    if th > h or tw > w or min(th, tw) < 4 or float(template.std()) < 1.0:
        return None
    centered = template - template.mean()
    energy = float(np.sum(centered * centered))
    shape = (1 << (h + th - 2).bit_length(), 1 << (w + tw - 2).bit_length())
    numerator = np.fft.irfft2(np.fft.rfft2(image, s=shape) * np.fft.rfft2(centered[::-1, ::-1], s=shape), s=shape)
    numerator = numerator[th - 1:h, tw - 1:w]

    def sums(values):
        integral = np.pad(values, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        return integral[th:, tw:] - integral[:-th, tw:] - integral[th:, :-tw] + integral[:-th, :-tw]

    total = sums(image)
    variance = np.maximum(0, sums(image * image) - total * total / (th * tw))
    denominator = np.sqrt(variance * energy)
    return np.clip(np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 1e-8), -1, 1)


def _peak(scores):
    y, x = np.unravel_index(int(np.argmax(scores)), scores.shape)
    # 对相关峰做抛物线细化，保留分数像素定位，最终裁切仍由整组策略取整。
    def offset(a, b, c):
        denominator = a - 2 * b + c
        return float(np.clip(.5 * (a - c) / denominator, -.5, .5)) if denominator < -1e-10 else 0.0
    dx = offset(scores[y, x-1], scores[y, x], scores[y, x+1]) if 0 < x < scores.shape[1]-1 else 0
    dy = offset(scores[y-1, x], scores[y, x], scores[y+1, x]) if 0 < y < scores.shape[0]-1 else 0
    return x, y, dx, dy, float(scores[y, x])


class RegionTemplateSearch:
    """同位置小块失配后的整图粗搜索与局部细化；模板始终来自固定参考图。"""

    def __init__(self, reference, regions):
        scale = min(1, 1024 / max(reference.size))
        self.size = (max(1, round(reference.width * scale)), max(1, round(reference.height * scale)))
        coarse = _gray(reference, self.size)
        self.templates = []
        for region in regions:
            l, t, r, b = region
            x, y = round(l * self.size[0]), round(t * self.size[1])
            right, bottom = round(r * self.size[0]), round(b * self.size[1])
            patch = coarse[y:bottom, x:right].copy()
            fine_scale = min(1, 384 / max((r-l)*reference.width, (b-t)*reference.height, 1))
            fine_size = (max(1, round(reference.width*fine_scale)), max(1, round(reference.height*fine_scale)))
            fx, fy = round(l*fine_size[0]), round(t*fine_size[1])
            fr, fb = round(r*fine_size[0]), round(b*fine_size[1])
            if min(fr-fx, fb-fy, patch.shape[0], patch.shape[1]) < 4:
                self.templates.append(None)
                continue
            native_box = (fx/fine_size[0]*reference.width, fy/fine_size[1]*reference.height,
                          fr/fine_size[0]*reference.width, fb/fine_size[1]*reference.height)
            fine = _gray(reference, (fr-fx, fb-fy), native_box)
            self.templates.append(SearchTemplate(patch, (x,y), fine, (fx,fy), fine_size))

    def search_image(self, image):
        return _gray(image, self.size)

    def locate(self, image, coarse, index, *, cancelled=lambda: False):
        template = self.templates[index]
        if template is None:
            return None, '选区过小或缺少纹理'
        if cancelled():
            raise InterruptedError('参考区搜索已取消')
        scores = normalized_correlation(coarse, template.coarse)
        if scores is None:
            return None, '选区缺少稳定纹理'
        x, y, ox, oy, score = _peak(scores)
        if score < .65:
            return None, f'全图搜索相似度不足（{score:.2f}）'
        # 排除同一峰邻域后检查第二候选，重复纹理不能凭最大值强行认定。
        th, tw = template.coarse.shape
        competitors = scores.copy()
        rx, ry = max(3, tw//2), max(3, th//2)
        competitors[max(0,y-ry):y+ry+1, max(0,x-rx):x+rx+1] = -1
        if score - float(competitors.max()) < .08:
            return None, '存在多个相似位置，无法唯一定位'
        if cancelled():
            raise InterruptedError('参考区搜索已取消')
        dx = (x+ox-template.coarse_origin[0])/self.size[0]
        dy = (y+oy-template.coarse_origin[1])/self.size[1]
        fw, fh = template.fine_size
        fx, fy = template.fine_origin
        th, tw = template.fine.shape
        radius_x, radius_y = max(4, int(np.ceil(4*fw/self.size[0]))), max(4, int(np.ceil(4*fh/self.size[1])))
        left, top = max(0, int(np.floor(fx+dx*fw))-radius_x), max(0, int(np.floor(fy+dy*fh))-radius_y)
        right, bottom = min(fw, int(np.ceil(fx+dx*fw))+tw+radius_x), min(fh, int(np.ceil(fy+dy*fh))+th+radius_y)
        if right-left < tw or bottom-top < th:
            return None, '匹配区域超出画面'
        fine = _gray(image, (right-left, bottom-top),
                     (left/fw*image.width, top/fh*image.height, right/fw*image.width, bottom/fh*image.height))
        scores = normalized_correlation(fine, template.fine)
        if scores is None:
            return None, '细化区域缺少纹理'
        x, y, ox, oy, refined = _peak(scores)
        if refined < .65:
            return None, f'细化匹配不可靠（{refined:.2f}）'
        return ((left+x+ox-fx)/fw, (top+y+oy-fy)/fh), ''

    def recover_occlusion(self, image, index, expected, *, cancelled=lambda: False):
        """仅在前后两帧均可靠时，核验预测附近的多个参考子块；不直接输出插值。"""
        template = self.templates[index]
        if template is None:
            return None
        fw, fh = template.fine_size
        fx, fy = template.fine_origin
        th, tw = template.fine.shape
        if min(th, tw) < 16:
            return None
        radius_x = max(8, round(tw / 4))
        radius_y = max(8, round(th / 2))
        predicted_x, predicted_y = fx + expected[0]*fw, fy + expected[1]*fh
        left, top = max(0, round(predicted_x)-radius_x), max(0, round(predicted_y)-radius_y)
        right, bottom = min(fw, round(predicted_x)+tw+radius_x), min(fh, round(predicted_y)+th+radius_y)
        if right-left < tw or bottom-top < th:
            return None
        target = _gray(image, (right-left, bottom-top),
                       (left/fw*image.width, top/fh*image.height, right/fw*image.width, bottom/fh*image.height))
        candidates = []
        for sy in (0, th//4, th//2):
            for sx in (0, tw//4, tw//2):
                if cancelled():
                    raise InterruptedError('遮挡核验已取消')
                part = template.fine[sy:sy+th//2, sx:sx+tw//2]
                scores = normalized_correlation(target, part)
                if scores is None:
                    continue
                # 每个子块只能在相同的原选区原点范围内移动。
                scores = scores[sy:sy+bottom-top-th+1, sx:sx+right-left-tw+1]
                x, y, ox, oy, score = _peak(scores)
                if score < .8:
                    continue
                competitors = scores.copy()
                rx, ry = max(3, tw//8), max(3, th//8)
                competitors[max(0,y-ry):y+ry+1, max(0,x-rx):x+rx+1] = -1
                if score - float(competitors.max()) < .04:
                    continue
                candidates.append((left+x+ox, top+y+oy, score))
        # 至少两个子块必须给出一致位移，其中至少一个非常清晰。只保留源图证据，不平滑代填。
        clusters = [[q for q in candidates if np.hypot(q[0]-p[0], q[1]-p[1]) <= 3]
                    for p in candidates]
        if not clusters:
            return None
        cluster = max(clusters, key=lambda group: (len(group), sum(p[2] for p in group)))
        if len(cluster) < 2 or max(p[2] for p in cluster) < .95:
            return None
        if len(cluster) <= len(candidates)//2:
            return None
        x, y = float(np.median([p[0] for p in cluster])), float(np.median([p[1] for p in cluster]))
        return ((x-fx)/fw, (y-fy)/fh)
