"""按等面积分格优先补足参考选区；核心不依赖 Qt 或原文件读取。"""
from __future__ import annotations

from math import floor, sqrt

import numpy as np
from PIL import Image, ImageFilter


def _overlaps_with_margin(box, other, margin=0.025):
    return (box[0] < other[2] + margin and box[2] > other[0] - margin
            and box[1] < other[3] + margin and box[3] > other[1] - margin)


def _grid_cells(count, *, portrait=False):
    """非平方数各行格数最多差一，行高按格数分配，使每格面积相等。"""
    rows = max(1, floor(sqrt(count) + .5))
    base, extra = divmod(count, rows)
    cells = []
    used = 0
    for row in range(rows):
        columns = base + (row < extra)
        top, bottom = used / count, (used + columns) / count
        for column in range(columns):
            box = (column / columns, top, (column + 1) / columns, bottom)
            cells.append((box[1], box[0], box[3], box[2]) if portrait else box)
        used += columns
    return tuple(cells)


def _corner_strength(pixels):
    """Shi–Tomasi：5×5 局部结构张量的最小特征值，拒绝单方向边缘。"""
    gy, gx = np.gradient(pixels)

    def mean5(values):
        padded = np.pad(values, ((2, 2), (2, 2)), mode='reflect')
        integral = np.pad(padded, ((1, 0), (1, 0))).cumsum(0, dtype=np.float64).cumsum(1)
        return (integral[5:, 5:] - integral[:-5, 5:] - integral[5:, :-5] + integral[:-5, :-5]) / 25

    xx, xy, yy = mean5(gx * gx), mean5(gx * gy), mean5(gy * gy)
    return np.maximum(0, .5 * (xx + yy - np.sqrt((xx - yy) ** 2 + 4 * xy ** 2)))


def _cell_candidates(pixels, gradients, strengths, peaks, cell, existing):
    height, width = pixels.shape
    left, top, right, bottom = cell
    # 40% 格宽/高留出同格补选的空间，同时满足不超过 16% 图幅及 60% 格幅。
    bw, bh = min(.16, .4 * (right-left)), min(.16, .4 * (bottom-top))
    pw, ph = floor(bw * width), floor(bh * height)
    if min(pw, ph) < 12:
        return []
    y, x = np.nonzero(peaks)
    x0, y0 = x - pw // 2, y - ph // 2
    boxes = np.column_stack((x0 / width, y0 / height, (x0+pw) / width, (y0+ph) / height))
    valid = ((boxes[:, 0] >= left) & (boxes[:, 1] >= top)
             & (boxes[:, 2] <= right) & (boxes[:, 3] <= bottom))
    for l, t, r, b in existing:
        valid &= ~((boxes[:, 0] < r+.025) & (boxes[:, 2] > l-.025)
                   & (boxes[:, 1] < b+.025) & (boxes[:, 3] > t-.025))
    indices = np.flatnonzero(valid)
    order = indices[np.argsort(-strengths[y[indices], x[indices]], kind='stable')]
    candidates, centers = [], []
    gx, gy = gradients
    for index in order:
        cx, cy = int(x[index]), int(y[index])
        # 候选也先做空间抑制，避免 64 个名额全部挤在同一强角点附近。
        if any(abs(cx-ox) < pw/3 and abs(cy-oy) < ph/3 for ox, oy in centers):
            continue
        ix, iy = int(x0[index]), int(y0[index])
        patch = pixels[iy:iy+ph, ix:ix+pw]
        if (float(patch.std()) < 10 or min(float(gx[iy:iy+ph, ix:ix+pw-1].mean()),
                                         float(gy[iy:iy+ph-1, ix:ix+pw].mean())) < 3.5
                or float(np.mean((patch < 5) | (patch > 250))) > .75):
            continue
        candidates.append((float(strengths[cy, cx]), tuple(float(v) for v in boxes[index])))
        centers.append((cx, cy))
        if len(candidates) == 64:
            break
    return candidates


def suggest_reference_regions(
    image: Image.Image,
    existing=(),
    *,
    target_count: int = 9,
) -> tuple[tuple[float, float, float, float], ...]:
    """返回补足到目标总数所需的新框；质量不足时允许少于目标，不修改已有框。"""
    if isinstance(target_count, bool) or not isinstance(target_count, int) or not 1 <= target_count <= 36:
        raise ValueError('自动选区目标数量必须为 1–36 的整数')
    existing = tuple(existing)
    needed = target_count - len(existing)
    if needed <= 0 or min(image.size) < 64:
        return ()
    scale = min(1, 768 / max(image.size))
    size = tuple(max(1, round(value * scale)) for value in image.size)
    if min(size) < 12:
        return ()
    # 直接缩小已解码预览，不复制整幅原图，也不在 GUI 中读取/解码文件。
    with image.resize(size, Image.Resampling.BILINEAR) as small, small.convert('L') as gray:
        pixels = np.asarray(gray, dtype=np.float32)
        with gray.filter(ImageFilter.GaussianBlur(1)) as smooth:
            strengths = _corner_strength(np.asarray(smooth, dtype=np.float32))
    neighbors = np.pad(strengths, 1, constant_values=-1)
    peaks = strengths >= max(1., .01 * float(strengths.max()))
    height, width = strengths.shape
    for dy in range(3):
        for dx in range(3):
            peaks &= strengths >= neighbors[dy:dy+height, dx:dx+width]
    gradients = np.abs(np.diff(pixels, axis=1)), np.abs(np.diff(pixels, axis=0))
    cells = _grid_cells(target_count, portrait=image.height > image.width)
    groups = [_cell_candidates(pixels, gradients, strengths, peaks, cell, existing) for cell in cells]
    chosen = []

    def available(box):
        return not any(_overlaps_with_margin(box, other) for other in chosen)

    # 先照顾尚未被已有选区中心覆盖的格子；已有选区不移动、不重新编号。
    for cell, candidates in zip(cells, groups):
        l, t, r, b = cell
        if any(l <= (box[0]+box[2])/2 < r and t <= (box[1]+box[3])/2 < b for box in existing):
            continue
        for _, box in candidates:
            if available(box):
                chosen.append(box)
                break
        if len(chosen) == needed:
            return tuple(chosen)

    candidates = [candidate for group in groups for candidate in group]
    while len(chosen) < needed:
        candidates = [candidate for candidate in candidates if available(candidate[1])]
        if not candidates:
            break
        anchors = (*existing, *chosen)

        def distributed_score(candidate):
            score, box = candidate
            if not anchors:
                return score
            cx, cy = (box[0]+box[2])/2, (box[1]+box[3])/2
            distance = min(np.hypot(cx-(other[0]+other[2])/2, cy-(other[1]+other[3])/2) for other in anchors)
            return score * (.5 + .5 * min(1., distance / .45))

        chosen.append(max(candidates, key=distributed_score)[1])
    return tuple(chosen)
