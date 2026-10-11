"""背景选区推荐的核心：先找整组一致的运动层，再按分格配额挑选。

逐候选独立核验后再按质量贪心、最后从末尾删冲突，会保留最先挑中的那一层运动（可能是近景少数层），
并把占多数的主背景整组删掉。这里改为：
- 候选按目标数量等面积分格，每格取若干同尺寸强角点小框，另加多尺度宽轮廓大框（虚化背景常只有大框可靠）；
- 所有候选在每张抽样帧上的实测位移一起参与 RANSAC，求在全部抽样帧上都成立的最大一致集；
  有人工选区时先比包含的人工选区数，人工选区代表用户想稳定的那层运动；
- 整组找不到一致层时，退到按列表距离排序的最长一致前缀，报告选区可覆盖到哪里，供接力。
核心只处理数值，不解码文件、不依赖 Qt。
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, radians

import numpy as np

from .auto_regions import _overlaps_with_margin, cell_candidates

POOL_SIZE = 96
MIN_PER_CELL = 3
MAX_PAIR_HYPOTHESES = 2048
REGION_MARGIN = .025


@dataclass(frozen=True, slots=True)
class Candidate:
    box: tuple
    cell: int
    strength: float
    large: bool = False
    manual: bool = False


def _center(box):
    return (box[0]+box[2])/2, (box[1]+box[3])/2


def _cell_of(cells, box):
    cx, cy = _center(box)
    return next((i for i, (l, t, r, b) in enumerate(cells) if l <= cx < r and t <= cy < b), len(cells)-1)


def candidate_pool(image, *, avoid=(), existing=(), target_count=9, large=()):
    """人工选区在前（manual=True），随后是分格小框和后备大框；返回 (格子, 候选)。"""
    cells, groups = cell_candidates(image, (*avoid, *existing), target_count=target_count)
    per_cell = max(MIN_PER_CELL, ceil(POOL_SIZE / len(cells)))
    pool = [Candidate(tuple(box), _cell_of(cells, box), 0., manual=True) for box in existing]
    for index, group in enumerate(groups):
        pool.extend(Candidate(box, index, strength) for strength, box in group[:per_cell])
    pool.extend(Candidate(tuple(box), _cell_of(cells, box), 0., large=True) for box in large)
    return cells, tuple(pool)


def draft_regions(cells, pool, existing=()):
    """立即可显示的草稿：未被已有选区覆盖的每格最强小框，与最终挑选的第一轮一致。"""
    chosen = []
    for index, cell in enumerate(cells):
        if any(cell[0] <= _center(box)[0] < cell[2] and cell[1] <= _center(box)[1] < cell[3] for box in existing):
            continue
        for candidate in pool:
            if candidate.cell == index and not candidate.manual and not candidate.large and not any(
                    _overlaps_with_margin(candidate.box, other, REGION_MARGIN) for other in (*existing, *chosen)):
                chosen.append(candidate.box)
                break
    return tuple(chosen)


def pixel_offsets(pool, boxes, size):
    """实测框相对原位置的像素位移（复数）；未匹配为 NaN。"""
    width, height = size
    values = np.full(len(pool), np.nan, dtype=complex)
    for index, (candidate, box) in enumerate(zip(pool, boxes)):
        if box is not None:
            region = candidate.box
            values[index] = complex((box[0]+box[2]-region[0]-region[2])/2*width,
                                    (box[1]+box[3]-region[1]-region[3])/2*height)
    return values


def _residuals(rotation, translation, src, dst):
    """(H,S) 假设 × (S,m) 观测 → 每个假设下各点在已测抽样中的最大残差 (H,m)；未测（NaN）的抽样不计。"""
    predicted = rotation[:, :, None]*src[None, None, :] + translation[:, :, None]
    error = np.abs(dst[None, :, :]-predicted)
    return np.where(np.isnan(error), -1., error).max(axis=1)


def _refit(src, dst, mask, max_degrees):
    """用一致集逐帧最小二乘重拟合；转角超限时退回平移中位数。"""
    rotation, translation = [], []
    for frame in dst:
        measured = mask & ~np.isnan(frame)
        a, b = src[measured], frame[measured]
        r = 1+0j
        if max_degrees > 0 and a.size >= 3:
            covariance = np.sum(np.conj(a-a.mean())*(b-b.mean()))
            if abs(covariance) > 1e-8 and abs(np.angle(covariance)) <= radians(max_degrees):
                r = covariance/abs(covariance)
        offset = b-r*a
        rotation.append(r)
        translation.append(complex(np.median(offset.real), np.median(offset.imag)))
    return np.array([rotation]), np.array([translation])


def consistent_layer(centers, offsets, *, tolerance, max_degrees=0., min_span=0., required=(), seed=0):
    """全部抽样帧上同一刚性（或平移）运动的最大一致集。

    centers: (n,) 参考图像素中心（复数）；offsets: (S,n) 像素位移，NaN 为未匹配。
    排序依据：包含的 required 数 → 成员数 → 平均残差。两点总能拟合转动，刚性假设至少要第三个成员支持。
    假设只由全部抽样都匹配的候选生成；required（人工选区）只要在已匹配的抽样里符合即算属于该运动，
    出画或被遮挡的抽样不计，它代表用户想稳定的那层运动。
    """
    offsets = np.asarray(offsets, dtype=complex).reshape(-1, len(centers))
    mask = np.zeros(len(centers), dtype=bool)
    if offsets.shape[0] == 0:
        mask[:] = True
        return mask
    measured = ~np.isnan(offsets)
    complete = measured.all(axis=0)
    wanted = np.zeros(len(centers), dtype=bool)
    wanted[np.asarray(tuple(required), dtype=int)] = True
    valid = np.flatnonzero(complete | (wanted & measured.any(axis=0)))
    seeds = np.flatnonzero(complete[valid])
    if seeds.size == 0:
        return mask
    src = np.asarray(centers, dtype=complex)[valid]
    dst = src[None, :]+offsets[:, valid]
    required = wanted[valid]
    hypotheses = [(np.ones((seeds.size, offsets.shape[0]), dtype=complex), offsets[:, valid[seeds]].T, 1)]
    if max_degrees > 0 and seeds.size >= 3:
        first, second = np.triu_indices(seeds.size, 1)
        first, second = seeds[first], seeds[second]
        keep = np.abs(src[second]-src[first]) >= min_span
        first, second = first[keep], second[keep]
        if first.size > MAX_PAIR_HYPOTHESES:
            picked = np.random.default_rng(seed).choice(first.size, MAX_PAIR_HYPOTHESES, replace=False)
            first, second = first[picked], second[picked]
        if first.size:
            rotation = (dst[:, second]-dst[:, first])/(src[second]-src[first])
            rotation = (rotation/np.maximum(np.abs(rotation), 1e-12)).T
            ok = (np.abs(np.angle(rotation)) <= radians(max_degrees)).all(axis=1)
            rotation, first, second = rotation[ok], first[ok], second[ok]
            translation = (dst[:, first].T+dst[:, second].T-rotation*(src[first]+src[second])[:, None])/2
            hypotheses.append((rotation, translation, 3))
    best_key, best = None, None
    for rotation, translation, minimum in hypotheses:
        for start in range(0, len(rotation), 256):
            residual = _residuals(rotation[start:start+256], translation[start:start+256], src, dst)
            inside = residual <= tolerance
            counts = inside.sum(axis=1)
            counts[counts < minimum] = 0
            spread = np.where(inside, residual, 0).sum(axis=1)/np.maximum(counts, 1)
            keys = np.lexsort((spread, -counts, -(inside & required).sum(axis=1)))
            top = keys[0]
            key = (int((inside[top] & required).sum()), int(counts[top]), -float(spread[top]))
            if counts[top] and (best_key is None or key > best_key):
                best_key, best = key, inside[top]
    if best is None:
        return mask
    refit = _residuals(*_refit(src, dst, best, max_degrees), src, dst)[0] <= tolerance
    if (int((refit & required).sum()), int(refit.sum())) >= best_key[:2]:
        best = refit
    mask[valid[best]] = True
    return mask


def covered_layer(centers, offsets, *, needed, tolerance, max_degrees=0., min_span=0., required=(), selectable=None):
    """返回 (可覆盖的抽样数 k, 一致集)：k 从全部抽样往前缩，直到一致集里可选候选足够。

    抽样须按与参考图的列表距离排好序；k 为 0 表示连最近的抽样都找不到一致运动。
    先按人工选区所在的运动层找；那一层没有足够的可选纹理（例如人工框在鸟身上）时，退回占多数的运动层。
    """
    offsets = np.asarray(offsets, dtype=complex).reshape(-1, len(centers))
    selectable = np.ones(len(centers), dtype=bool) if selectable is None else np.asarray(selectable, dtype=bool)
    enough = max(1, min(needed, 3))
    for prefer in ((tuple(required), ()) if len(required) else ((),)):
        for count in range(offsets.shape[0], 0, -1):
            mask = consistent_layer(centers, offsets[:count], tolerance=tolerance, max_degrees=max_degrees,
                                    min_span=min_span, required=prefer)
            if int((mask & selectable).sum()) >= enough:
                return count, mask
    return 0, np.zeros(len(centers), dtype=bool)


def select_regions(cells, pool, eligible, quality, *, existing=(), needed):
    """一致集内按格配额挑选：先补未被已有选区覆盖的格子，再按质量和与已选的距离补足。

    小框与大框按中心所在格同等竞争，以抽样中最差的匹配质量排序：虚化背景上大框通常更可靠，
    纹理丰富处小角点框更准；不按尺寸预设优先级。
    """
    chosen = []
    anchors = tuple(existing)

    def free(box):
        return not any(_overlaps_with_margin(box, other, REGION_MARGIN) for other in (*anchors, *chosen))

    ranked = sorted((i for i, ok in enumerate(eligible) if ok and not pool[i].manual),
                    key=lambda i: (-round(quality[i], 2), -pool[i].strength))
    for index, cell in enumerate(cells):
        if len(chosen) >= needed:
            return tuple(chosen)
        if any(cell[0] <= _center(box)[0] < cell[2] and cell[1] <= _center(box)[1] < cell[3]
               for box in (*anchors, *chosen)):
            continue
        pick = next((i for i in ranked if pool[i].cell == index and free(pool[i].box)), None)
        if pick is not None:
            chosen.append(pool[pick].box)
    while len(chosen) < needed:
        options = [i for i in ranked if free(pool[i].box)]
        if not options:
            break
        others = (*anchors, *chosen)

        def score(i):
            if not others:
                return quality[i]
            cx, cy = _center(pool[i].box)
            distance = min(np.hypot(cx-_center(o)[0], cy-_center(o)[1]) for o in others)
            return quality[i]*(.5+.5*min(1., distance/.45))

        chosen.append(pool[max(options, key=score)].box)
    return tuple(chosen)
