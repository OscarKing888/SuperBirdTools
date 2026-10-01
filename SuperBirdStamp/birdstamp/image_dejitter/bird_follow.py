"""两段式稳定的第二段：背景对齐去掉相机抖动后，画框平滑地跟随目标鸟。

第一段（参考区匹配）给出每张照片到参考图的刚性/平移变换，这是逐帧精确的相机抖动。
第二段把目标鸟轨迹框中心映射到“背景已稳定”的参考坐标里，得到鸟相对背景的运动，
再对它做稳健的局部线性趋势拟合，只把趋势加回画框位移：

    输出画框 = 背景对齐 + 鸟相对运动的平滑趋势

因此相机抖动逐帧完全去除，鸟的快速动作（低头、转身、展翅）原样保留，缓慢的
走动/漂移由画框跟随。检测框只进入经过平滑的构图路径，不作为逐帧补偿；某张照片
目标鸟丢失时，该帧路径取邻帧趋势并在计划中标明，窗口内证据不足则明确失败。
"""
import numpy as np

from .rigid_alignment import FrameAlignment, map_point

MIN_OBSERVATIONS = 2      # 每个平滑窗口内至少需要的鸟位置观测
HUBER_FLOOR = 2.          # 稳健权重的最小尺度（源像素），避免完美数据时尺度为零
FOLLOW_TRAJECTORY_VERSION = 1
MAX_DETECTION_GAP = 2     # 构图跟随允许的连续漏检张数；超过后不再重新关联（不跨越换鸟）


class FollowError(ValueError):
    """携带出错照片键，供管线转换为 SequencePhotoError。"""

    def __init__(self, key, message):
        super().__init__(message)
        self.key = key


def _huber_line(x, y):
    """加权局部线性拟合在 x=0 处的值；Huber 权重抑制个别检测框跳变（展翅、转身）。"""
    design = np.column_stack((np.ones(len(x)), x))
    weights = np.ones(len(x))
    for _ in range(6):
        root = np.sqrt(weights)[:, None]
        coefficients = np.linalg.lstsq(design*root, y*root, rcond=None)[0]
        residual = np.linalg.norm(y-design @ coefficients, axis=1)
        limit = 1.345*max(HUBER_FLOOR, 1.4826*float(np.median(residual)))
        weights = np.where(residual <= limit, 1., limit/np.maximum(residual, 1e-9))
    return coefficients[0]


def robust_trend(times, values, window):
    """逐帧局部线性回归（Huber 重加权），只用有观测的帧；返回 (趋势, 窗口内观测数)。

    values 为 (N,2)，缺失为 NaN。窗口与 subject_sequence.smooth_path 相同：两端不越界、
    不外推趋势；少于 3 张照片时趋势为零（等同仅背景稳定）。
    """
    values = np.asarray(values, dtype=float)
    times = np.asarray(times, dtype=float)
    n = len(values)
    trend = np.zeros_like(values)
    observed = np.isfinite(values).all(axis=1)
    support = np.zeros(n, dtype=int)
    if n < 3:
        support[:] = observed.sum()
        return trend, support
    for i, t in enumerate(times):
        start = max(0, min(i-window//2, n-window))
        end = min(n, start+window)
        idx = np.arange(start, end)[observed[start:end]]
        support[i] = len(idx)
        if len(idx) < MIN_OBSERVATIONS:
            trend[i] = np.nan
        elif np.ptp(times[idx]) == 0:
            trend[i] = values[idx].mean(axis=0)
        else:
            trend[i] = _huber_line(times[idx]-t, values[idx])
    return trend, support


def bird_positions(alignments, centres):
    """鸟在“背景已稳定”的参考坐标中的位置；缺失为 None。"""
    return {key: (map_point(alignments[key].source_to_reference, centre) if centre is not None else None)
            for key, centre in centres.items() if key in alignments}


def follow_offsets(keys, alignments, centres, times, window, *, reference_key):
    """返回 ({键: 画框跟随位移 (dx,dy)}, {键: (状态, 观测, 趋势)})，单位为参考坐标源像素。"""
    positions = bird_positions(alignments, centres)
    anchor = positions.get(reference_key)
    if anchor is None:
        raise FollowError(reference_key, '参考图中没有目标鸟位置，无法跟随；请重新选择目标鸟。')
    values = np.array([(np.subtract(positions[k], anchor) if positions.get(k) is not None else (np.nan, np.nan))
                       for k in keys], dtype=float)
    trend, support = robust_trend(times, values, window)
    gaps = [k for k, row in zip(keys, trend) if not np.isfinite(row).all()]
    if gaps:
        missing = sum(1 for k in keys if positions.get(k) is None)
        raise FollowError(gaps[0], f'目标鸟在此附近连续丢失（整组 {missing} 张无检测），平滑窗口内少于 '
                                   f'{MIN_OBSERVATIONS} 张可用位置，无法确定跟随路径；请加大跟随窗口、确认目标鸟或分段分析。')
    offsets, plans = {}, {}
    for key, value, row in zip(keys, values, trend):
        offsets[key] = (float(row[0]), float(row[1]))
        status = 'bird_follow' if np.isfinite(value).all() else 'bird_follow_trend'
        plans[key] = (status, tuple(map(float, value)) if np.isfinite(value).all() else (), tuple(map(float, row)))
    return offsets, plans


def shifted_alignment(alignment, offset):
    """输出坐标整体平移 -offset；无旋转时保持整数位移，仍走无重采样的整像素裁切。"""
    a, b, c, d, e, f = alignment.source_to_reference
    dx, dy = offset
    if not alignment.rotated:
        dx, dy = round(dx), round(dy)
    return FrameAlignment((a, b, c-dx, d, e, f-dy), alignment.measured_degrees, alignment.applied_degrees,
                          alignment.status, alignment.reason, alignment.region_indices)


def target_centres(trajectory, paths, keys, size):
    """轨迹框中心（源像素）；身份失败的照片为 None。"""
    w, h = size
    result = {}
    for path, key in zip(paths, keys):
        frame = trajectory.frame(path)
        box = frame.box
        result[key] = None if box is None else ((box[0]+box[2])*w/2, (box[1]+box[3])*h/2)
    return result


detector = None   # 测试可替换；None 表示应用现有 YOLO 实例与缓存


def prepare_follow_centres(reference, paths, settings, size, *, cancelled, progress=lambda text: None):
    """目标鸟：优先使用已选择的目标框；未选择时参考图必须恰好识别到一只鸟。"""
    from birdstamp.decoders.image_decoder import decode_image
    from birdstamp.gui.editor_utils import path_key
    from .bird_candidates import detect_bird_candidates
    from .bird_observation_cache import detect_cached
    from .manual_region_matches import normalize_match_box
    from .target_trajectory import build_target_trajectory
    reference_key = path_key(reference)
    target = normalize_match_box((settings.get('dejitter_region_recommendation') or {}).get('target'))
    chosen = detector or detect_bird_candidates
    if target is None:
        progress('识别参考图中的目标鸟…')
        with decode_image(reference, decoder='auto') as image:
            birds = detect_cached(reference, image, cancelled=cancelled) if chosen is detect_bird_candidates \
                else chosen(image, cancelled=cancelled)
        if len(birds) != 1:
            raise FollowError(reference_key, ('参考图中识别到多只鸟，请点击“选择目标鸟”确认要跟随哪一只。' if birds else
                                              '参考图中没有识别到鸟，无法跟随；请关闭“跟随目标鸟”或更换参考图。'))
        target = birds[0].box
    try:
        trajectory = build_target_trajectory(reference, paths, target, cancelled=cancelled, progress=progress,
                                             detector=chosen, max_gap=MAX_DETECTION_GAP)
    except ValueError as exc:
        raise FollowError(reference_key, str(exc)) from exc
    keys = [path_key(p) for p in paths]
    if reference_key not in keys:
        paths, keys = [reference, *paths], [reference_key, *keys]
    return target_centres(trajectory, paths, keys, size)
