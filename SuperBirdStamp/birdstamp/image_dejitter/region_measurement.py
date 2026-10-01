"""单个选区的一次真实图像测量。

二维纹理：LK（角点先剔除位于单向边缘上的点）＋双向核验＋最大共识；失败时只对该区
调用 NCC/ECC 独立配准。单向边缘：模板相关只取法向位置，沿线方向不作为证据。
输出的位移/法向偏移均以源像素计，并相对原参考（模板帧自身的位移记在 base）。
"""
from dataclasses import dataclass, replace
import math
import numpy as np

from .analysis_window import AnalysisWindow, region_window, window_rect, pyramid_levels

FB_LIMIT = 1.5          # 规范分析像素
CONSENSUS = 2.5
MIN_INLIERS = 8
SIGMA_FLOOR = .3
CORNER_RATIO = .05      # 角点局部结构张量比；更低的是电线/轮廓上的滑动点
EDGE_PEAK = .86
EDGE_UNIQUE = .06
EDGE_RIDGE = .03
EDGE_MAD = .5
EDGE_FB = .75
EDGE_ANGLE = 10.


@dataclass(frozen=True, slots=True, eq=False)
class RegionTemplate:
    index: int
    kind: str
    window: AnalysisWindow
    gray: np.ndarray
    rect: tuple                       # 选区在窗口中的分析像素矩形
    corners: np.ndarray               # (N,2) 窗口分析坐标
    normal: tuple
    margin: float                     # 源像素搜索余量
    base: tuple = (0., 0.)            # 模板帧相对原参考的位移（源像素）
    base_cov: tuple = (0., 0., 0., 0.)
    links: int = 0                    # 距原参考的关键帧链数
    source: str = ''                  # 模板来源照片（链式诊断）


@dataclass(frozen=True, slots=True)
class Measurement:
    index: int
    kind: str
    ok: bool
    reason: str = ''
    value: tuple = ()                 # 2d: (dx,dy)；1d: (m,)；源像素，相对原参考
    normal: tuple = (0., 1.)
    covariance: tuple = ()            # 2d: 2×2 展平；1d: (方差,)
    scale: float = 1.
    quality: float = 0.
    method: str = 'lk'
    inliers: int = 0
    metrics: tuple = ()               # (候选, 有效, 内点, 覆盖, dx, dy)，分析像素
    points: tuple = ()                # 与 LocalObservation.points 相同格式（不含编号）
    anchor: str = 'reference'
    links: int = 0
    origins: tuple = ()               # (参考窗口原点x,y, 当前窗口原点x,y)，源像素；仅诊断

    def row(self):
        """写入 LocalObservation.constraints；JSON 往返后可由 from_row 还原。"""
        return (self.index, self.kind, self.ok, self.reason, tuple(self.value), tuple(self.normal),
                tuple(self.covariance), self.scale, self.quality, self.method, self.inliers, self.anchor, self.links)

    @classmethod
    def from_row(cls, row):
        index, kind, ok, reason, value, normal, cov, scale, quality, method, inliers, anchor, links = row
        return cls(int(index), str(kind), bool(ok), str(reason), tuple(map(float, value)), tuple(map(float, normal)),
                   tuple(map(float, cov)), float(scale), float(quality), str(method), int(inliers),
                   anchor=str(anchor), links=int(links))


def consensus_translation(a, b, threshold=CONSENSUS):
    delta = np.asarray(b, dtype=float) - np.asarray(a, dtype=float)
    if not len(delta):
        return None, np.zeros(0, bool)
    if not np.isfinite(delta).all():
        raise ValueError('局部主体：对应点包含非有限值')
    counts = np.zeros(len(delta), dtype=int)
    for start in range(0, len(delta), 128):
        counts[start:start+128] = (np.linalg.norm(delta[start:start+128, None]-delta[None], axis=2) < threshold).sum(axis=1)
    selected = np.linalg.norm(delta-delta[counts.argmax()], axis=1) < threshold
    for _ in range(3):
        displacement = np.median(delta[selected], axis=0)
        updated = np.linalg.norm(delta-displacement, axis=1) < threshold
        if not updated.any():
            break
        selected = updated
    return np.median(delta[selected], axis=0), selected


def select_corners(gray, rect, *, exclude=lambda points: np.zeros(len(points), bool)):
    """选区内每格限额取角点，并剔除局部近乎单向的点（电线、长轮廓）。"""
    import cv2
    x0, y0, x1, y1 = rect
    mask = np.zeros(gray.shape, np.uint8)
    mask[y0:y1, x0:x1] = 255
    found = cv2.goodFeaturesToTrack(gray, maxCorners=560, qualityLevel=.006, minDistance=7, mask=mask, blockSize=5)
    if found is None:
        return np.empty((0, 2), np.float32)
    found = found[:, 0]
    eig = cv2.cornerEigenValsAndVecs(gray, 9, 3)
    lam = np.sort(eig[..., :2], axis=-1)
    xi, yi = np.clip(np.rint(found[:, 0]).astype(int), 0, gray.shape[1]-1), np.clip(np.rint(found[:, 1]).astype(int), 0, gray.shape[0]-1)
    ratio = lam[yi, xi, 0]/np.maximum(lam[yi, xi, 1], 1e-9)
    found = found[(ratio >= CORNER_RATIO) & ~exclude(found)]
    cells, selected = {}, []
    for point in found:
        cell = (min(3, int((point[0]-x0)*4/max(1, x1-x0))), min(3, int((point[1]-y0)*4/max(1, y1-y0))))
        if cells.get(cell, 0) < 9 and len(selected) < 140:
            selected.append(point)
            cells[cell] = cells.get(cell, 0)+1
    return np.asarray(selected, np.float32).reshape(-1, 2)


def make_template(image, box, *, index, kind, normal, scale, margin, base=(0., 0.), base_cov=(0., 0., 0., 0.),
                  links=0, source='', exclude=None):
    window = region_window(image.size, box, scale, margin)
    gray = window.gray(image)
    rect = window_rect(window, image.size, box)
    if rect[2]-rect[0] < 6 or rect[3]-rect[1] < 6:
        raise ValueError(f'选区 {index+1} 在分析尺度下过小')
    corners = (select_corners(gray, rect, **({} if exclude is None else dict(exclude=lambda p: exclude(window, p))))
               if kind == '2d' else np.empty((0, 2), np.float32))
    return RegionTemplate(index, kind, window, gray, rect, corners, tuple(map(float, normal)), float(margin),
                          tuple(map(float, base)), tuple(map(float, base_cov)), int(links), str(source))


def _check(cancelled):
    if cancelled():
        raise InterruptedError('局部主体跟踪已取消')


def measure(template, image, *, prior=(0., 0.), cancelled=lambda: False):
    """prior 为预计相对原参考的位移（源像素）；搜索窗口据此平移并夹回图内。"""
    _check(cancelled)
    shift = np.asarray(prior, dtype=float)-np.asarray(template.base)
    moving_window = template.window.placed(image.size, shift)
    moving = moving_window.gray(image)
    _check(cancelled)
    result = (_measure_edge(template, moving_window, moving) if template.kind == '1d'
              else _measure_texture(template, moving_window, moving, shift, cancelled))
    origins = (*(np.asarray(template.window.origin)-np.asarray(template.base)), *moving_window.origin)
    return replace(result, origins=tuple(map(float, origins)))


def _failed(template, reason, metrics=(), points=(), method='lk'):
    return Measurement(template.index, template.kind, False, reason, normal=template.normal,
                       scale=template.window.scale, method=method, metrics=metrics, points=points,
                       anchor=template.source or 'reference', links=template.links)


def _lk(template, moving, init, levels):
    """带初值的金字塔 LK；反向从当前点以相反初值独立回到参考，前后向误差作为可靠性。"""
    import cv2
    a = template.corners.reshape(-1, 1, 2).astype(np.float32)
    n = len(a)
    init = np.asarray(init, np.float32).reshape(1, 1, 2)
    kwargs = dict(winSize=(25, 25), maxLevel=levels, criteria=(3, 40, .005), flags=cv2.OPTFLOW_USE_INITIAL_FLOW)
    guess = a+init
    q, status, _ = cv2.calcOpticalFlowPyrLK(template.gray, moving, a, guess.copy(), **kwargs)
    if q is None or status is None:
        return guess[:, 0], np.zeros(n, bool), np.full(n, np.inf)
    finite = np.isfinite(q).all(axis=(1, 2))
    q = np.where(finite[:, None, None], q, guess).astype(np.float32)
    rev, reverse, _ = cv2.calcOpticalFlowPyrLK(moving, template.gray, q, (q-init).copy(), **kwargs)
    if rev is None or reverse is None:
        return q[:, 0], np.zeros(n, bool), np.full(n, np.inf)
    fb = np.linalg.norm(a[:, 0]-rev[:, 0], axis=1)
    h, w = moving.shape
    b = q[:, 0]
    valid = (finite & status[:, 0].astype(bool) & reverse[:, 0].astype(bool) & np.isfinite(fb) & (fb < FB_LIMIT)
             & (b[:, 0] >= 0) & (b[:, 0] < w) & (b[:, 1] >= 0) & (b[:, 1] < h))
    return b, valid, fb


def _coarse_init(template, moving):
    """1/4 分辨率模板相关粗定位，只作 LK 初值；是否成立由 LK 双向与共识决定。"""
    import cv2
    x0, y0, x1, y1 = template.rect
    factor = max(1, min(4, min(x1-x0, y1-y0)//16))
    patch = template.gray[y0:y1, x0:x1]
    small_patch = cv2.resize(patch, (max(1, patch.shape[1]//factor), max(1, patch.shape[0]//factor)), interpolation=cv2.INTER_AREA)
    small = cv2.resize(moving, (max(1, moving.shape[1]//factor), max(1, moving.shape[0]//factor)), interpolation=cv2.INTER_AREA)
    if small.shape[0] < small_patch.shape[0] or small.shape[1] < small_patch.shape[1] or small_patch.std() < 1:
        return None
    heat = cv2.matchTemplate(small, small_patch, cv2.TM_CCOEFF_NORMED)
    _, peak, _, (px, py) = cv2.minMaxLoc(heat)
    if not np.isfinite(peak) or peak < .5:
        return None
    return np.array((px*factor-x0, py*factor-y0), float)


def _sigma(scale, residual, count):
    mad = float(np.median(residual)) if len(residual) else 0.
    return scale*max(SIGMA_FLOOR, 1.4826*mad/math.sqrt(max(1, min(count, 25))))


def _measure_texture(template, window, moving, shift, cancelled):
    s = template.window.scale
    offset = (np.asarray(window.origin)-np.asarray(template.window.origin))/s   # 窗口原点差（分析像素）
    expected = shift/s-offset
    a = template.corners
    inits = [expected]
    coarse = _coarse_init(template, moving)
    if coarse is not None and np.linalg.norm(coarse-expected) > 2:
        inits.append(coarse)
    best = None
    for k, init in enumerate(inits):
        _check(cancelled)
        levels = pyramid_levels(template.margin, s) if k == 0 else 2
        b, valid, fb = _lk(template, moving, init, levels)
        delta, selected = consensus_translation(a[valid], b[valid])
        inliers = np.zeros(len(a), bool)
        inliers[np.flatnonzero(valid)[selected]] = True
        if best is None or inliers.sum() > best[3].sum():
            best = (b, valid, fb, inliers, delta)
    b, valid, fb, inliers, delta = best
    n = int(inliers.sum())
    x0, y0, x1, y1 = template.rect
    span = max(x1-x0, y1-y0)
    coverage = float(np.ptp(a[inliers], axis=0).max()/max(1, span)) if n else 0.
    good = (delta is not None and n >= MIN_INLIERS and valid.sum() >= .4*len(a)
            and n >= .5*valid.sum() and coverage >= .2)
    metrics = (len(a), int(valid.sum()), n, coverage,
               *((float(delta[0]), float(delta[1])) if delta is not None else (None, None)))
    base = np.asarray(template.base)
    def points(accepted):
        ref = template.window.to_source(a)-base
        mov = window.to_source(b) if len(b) else ref
        return tuple((0, template.index, *map(float, p), *map(float, q), bool(ok and accepted),
                      float(e) if np.isfinite(e) else None) for p, q, ok, e in zip(ref, mov, inliers, fb))
    if good:
        residual = np.linalg.norm((b-a)[inliers]-delta, axis=1)
        sigma = _sigma(s, residual, n)
        return _accepted(template, window, delta, sigma, n/max(1, len(a)), 'lk', n, metrics, points(True))
    from .local_registration import register_region
    try:
        _check(cancelled)
        found, quality, records = register_region(template.gray, moving, template.rect, cancelled=cancelled)
    except ValueError as exc:
        reason = f'选区 {template.index+1} 纹理、覆盖或可靠点不足（LK 内点 {n}/{len(a)}；独立配准：{exc}）'
        return _failed(template, reason, metrics, points(False))
    found = np.asarray(found, float)
    sub = tuple((0, template.index, *map(float, template.window.to_source((x, y))-base),
                 *map(float, window.to_source((mx, my))), True, float(e)) for x, y, mx, my, e in records)
    return _accepted(template, window, found, s*.5, float(quality), 'ncc_ecc', len(records),
                     (len(records), len(records), len(records), 1., float(found[0]), float(found[1])), sub)


def _accepted(template, window, delta, sigma, quality, method, inliers, metrics, points):
    s = template.window.scale
    local = np.asarray(delta, float)*s+np.asarray(window.origin)-np.asarray(template.window.origin)
    value = local+np.asarray(template.base)
    cov = np.eye(2)*sigma**2+np.asarray(template.base_cov).reshape(2, 2)
    return Measurement(template.index, '2d', True, '', tuple(map(float, value)), template.normal,
                       tuple(map(float, cov.ravel())), s, float(quality), method, int(inliers), metrics, points,
                       template.source or 'reference', template.links)


def _normal_position(heat, origin, normal, thickness):
    """热图 → 法向位置（分析像素）。

    电线模板会沿切向滑动，主脊很长；轻微倾斜/下垂使法向位置沿脊线性变化。
    因此对主脊做加权直线拟合 m = a + b·t：a 为预计切向位置（t=0）处的法向偏移，
    残差 MAD 衡量直线性，b 给出与参考方向的偏角。返回 dict 或带 reason 的 dict。
    """
    h, w = heat.shape
    ys, xs = np.mgrid[0:h, 0:w]
    dx, dy = xs-origin[0], ys-origin[1]
    n = np.asarray(normal)
    m = dx*n[0]+dy*n[1]
    t = -dx*n[1]+dy*n[0]
    finite = np.isfinite(heat)
    if not finite.any():
        return dict(reason='相关热图无效')
    heat = np.where(finite, heat, -1.)
    peak_index = np.unravel_index(int(heat.argmax()), heat.shape)
    peak = float(heat[peak_index])
    bins = np.rint(m).astype(int)
    low = bins.min()
    profile = np.full(bins.max()-low+1, -1.)
    np.maximum.at(profile, (bins-low).ravel(), heat.ravel())
    best = int(bins[peak_index])-low
    exclusion = max(3, int(round(.15*thickness)))
    rivals = np.concatenate((profile[:max(0, best-exclusion)], profile[best+exclusion+1:]))
    if len(rivals) and rivals.max() > peak-EDGE_UNIQUE:
        return dict(peak=peak, at=peak_index, reason='平行边缘重复，法向位置有歧义')
    # 主脊：法向距离在排除带内。每个切向分箱取脊顶（加权质心），再对脊顶做直线拟合，
    # 这样相关峰自身的宽度不会被误当成边缘弯曲。
    band = np.abs(bins-(best+low)) <= exclusion
    tb = np.rint(t).astype(int)
    crest_t, crest_m, crest_w = [], [], []
    for key in np.unique(tb[band]):
        cell = band & (tb == key)
        top = float(heat[cell].max())
        if top < peak-EDGE_RIDGE:
            continue
        near = cell & (heat >= top-.02)
        weight = heat[near]-(top-.02)+1e-3
        crest_t.append(float(key)); crest_m.append(float(np.average(m[near], weights=weight))); crest_w.append(top)
    crest_t, crest_m = np.asarray(crest_t), np.asarray(crest_m)
    if len(crest_t) >= 3 and np.ptp(crest_t) >= 5:
        slope, intercept = np.polyfit(crest_t, crest_m, 1)
    else:
        slope, intercept = 0., float(np.mean(crest_m)) if len(crest_m) else float(m[peak_index])
    residual = crest_m-(intercept+slope*crest_t) if len(crest_m) else np.zeros(1)
    mad = float(np.median(np.abs(residual-np.median(residual))))
    return dict(peak=peak, at=peak_index, intercept=float(intercept), slope=float(slope), mad=mad,
                angle=math.degrees(math.atan(abs(float(slope)))), t_peak=float(t[peak_index]), reason='')


def _measure_edge(template, window, moving):
    import cv2
    x0, y0, x1, y1 = template.rect
    patch = template.gray[y0:y1, x0:x1]
    n = np.asarray(template.normal)
    thickness = abs(n[0])*(x1-x0)+abs(n[1])*(y1-y0)
    if moving.shape[0] < patch.shape[0] or moving.shape[1] < patch.shape[1]:
        return _failed(template, f'选区 {template.index+1} 搜索窗口不足', method='edge_ncc')
    heat = cv2.matchTemplate(moving, patch, cv2.TM_CCOEFF_NORMED)
    fit = _normal_position(heat, (x0, y0), n, thickness)
    label = f'选区 {template.index+1}（单向边缘）'
    if fit['reason']:
        return _failed(template, f'{label}{fit["reason"]}', method='edge_ncc')
    if fit['peak'] < EDGE_PEAK:
        return _failed(template, f'{label}相关匹配质量不足（{fit["peak"]:.2f}）', method='edge_ncc')
    if fit['mad'] > EDGE_MAD:
        return _failed(template, f'{label}沿线法向位置不一致（边缘弯曲或局部运动）', method='edge_ncc')
    if fit['angle'] > EDGE_ANGLE:
        return _failed(template, f'{label}方向与参考不一致', method='edge_ncc')
    py, px = fit['at']
    moved = moving[py:py+patch.shape[0], px:px+patch.shape[1]]
    if moved.shape != patch.shape or moved.std() < 1:
        return _failed(template, f'{label}有效覆盖不足', method='edge_ncc')
    # 反向：从当前图峰值处取模板，在参考窗口内独立搜索；比较同一切向位置的法向偏移。
    back = _normal_position(cv2.matchTemplate(template.gray, moved, cv2.TM_CCOEFF_NORMED), (px, py), n, thickness)
    forward_at_peak = fit['intercept']+fit['slope']*fit['t_peak']
    fb = abs(forward_at_peak+back['intercept']) if not back['reason'] else math.inf
    if fb > EDGE_FB:
        return _failed(template, f'{label}正反向法向位置不一致', method='edge_ncc')
    m, mad = fit['intercept'], fit['mad']
    s = template.window.scale
    origin_shift = np.asarray(window.origin)-np.asarray(template.window.origin)
    value = m*s+float(n @ origin_shift)+float(n @ np.asarray(template.base))
    # 切向位置未知带来的法向不确定度：偏角 × 切向搜索半径的一半。
    along = abs(fit['slope'])*.5*template.margin/s
    variance = (s*max(SIGMA_FLOOR, mad, along))**2+float(n @ np.asarray(template.base_cov).reshape(2, 2) @ n)
    centre = np.array(((x0+x1)/2, (y0+y1)/2))
    ref = template.window.to_source(centre)-np.asarray(template.base)
    mov = template.window.to_source(centre)+n*(m*s+float(n @ origin_shift))
    point = ((0, template.index, *map(float, ref), *map(float, mov), True, float(fb)),)
    metrics = (1, 1, 1, 1., float(m*n[0]), float(m*n[1]))
    return Measurement(template.index, '1d', True, '', (float(value),), template.normal, (float(variance),), s,
                       float(fit['peak']), 'edge_ncc', 1, metrics, point, template.source or 'reference', template.links)
