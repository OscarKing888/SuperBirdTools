"""从可靠参考区中选择一致的平移，不把离群区域计入补偿。"""
from dataclasses import replace
from math import atan2, hypot, radians
from statistics import median
from .matching_options import MatchingOptions


def region_offsets(regions, result, size, reference_size):
    width, height = size
    rw, rh = reference_size
    return {i: (((box[0]+box[2])*width-(region[0]+region[2])*rw)/2,
                ((box[1]+box[3])*height-(region[1]+region[3])*rh)/2)
            for i, (region, box) in enumerate(zip(regions, result.boxes)) if box is not None}


def _rotation_consistent_groups(regions, offsets, reference_size, tolerance, min_span, max_degrees):
    """小角度转动会使真实匹配的位移不同；用至少三区的刚性几何证据核验。

    这里只选择可靠区域；rigid_alignment 按输出模式拟合刚性变换或保留平移。
    两点即可拟合转动，因此必须有第三个实测匹配支持，且参考点不能集中在同一小块内。
    """
    if len(offsets) < 3 or max_degrees <= 0:
        return set()
    rw, rh = reference_size
    source = {i: complex((regions[i][0]+regions[i][2])*rw/2,
                         (regions[i][1]+regions[i][3])*rh/2) for i in offsets}
    target = {i: source[i] + complex(*offsets[i]) for i in offsets}
    groups = set()
    indices = list(offsets)
    for position, i in enumerate(indices):
        for j in indices[position+1:]:
            baseline = source[j] - source[i]
            if abs(baseline) < min_span:
                continue
            rotation = (target[j] - target[i]) / baseline
            if abs(rotation) < 1e-8:
                continue
            rotation /= abs(rotation)
            if abs(atan2(rotation.imag, rotation.real)) > radians(max_degrees):
                continue
            translation = (target[i]+target[j]-rotation*(source[i]+source[j]))/2
            group = tuple(k for k in indices
                          if abs(target[k]-rotation*source[k]-translation) <= tolerance)
            if len(group) < 3 or i not in group or j not in group:
                continue
            # 全组最小二乘刚性拟合，再检查所有成员，避免仅凭一对端点放行。
            center = sum(source[k] for k in group)/len(group)
            mapped = sum(target[k] for k in group)/len(group)
            covariance = sum((source[k]-center).conjugate()*(target[k]-mapped) for k in group)
            if abs(covariance) < 1e-8:
                continue
            rotation = covariance/abs(covariance)
            if abs(atan2(rotation.imag, rotation.real)) > radians(max_degrees):
                continue
            if all(abs(target[k]-mapped-rotation*(source[k]-center)) <= tolerance for k in group):
                groups.add(group)
    return groups


def select_translation(regions, result, size, reference_size, *, options=MatchingOptions()):
    offsets = region_offsets(regions, result, size, reference_size)
    if not offsets:
        return None
    tolerance = options.pixel_tolerance(size)
    groups = {tuple(i for i, q in offsets.items() if hypot(q[0]-p[0], q[1]-p[1]) <= tolerance)
              for p in offsets.values()}
    groups.update(_rotation_consistent_groups(regions, offsets, reference_size, tolerance,
                                              min(size)*.1, options.rotation_degrees))
    quality = lambda group: sum(result.scores[i] if i < len(result.scores) else 0 for i in group) / len(group)
    ranked = sorted(groups, key=lambda group: (-len(group), -quality(group), group))
    winner = ranked[0]
    # 一区足够，但同等支持的冲突候选需要有明显质量差，不能任意选一个方向。
    competing = [group for group in ranked[1:] if len(group) == len(winner) and not set(group) & set(winner)]
    if competing and quality(winner) - max(map(quality, competing)) < .08:
        return None
    dx = median(offsets[i][0] for i in winner)
    dy = median(offsets[i][1] for i in winner)
    return dx, dy, winner


def resolve_tracking_consensus(regions, result, size, reference_size, *, options=MatchingOptions()):
    translation = select_translation(regions, result, size, reference_size, options=options)
    if translation is None:
        reasons = tuple((result.reasons[i] if i < len(result.reasons) else '') or
                        ('纹理匹配通过，但几何偏差或冲突未消除，未确定位置' if box else '未匹配')
                        for i, box in enumerate(result.boxes))
        hint = ''
        if any(box is not None for box in result.boxes):
            hint = (f'。当前允许旋转 {options.rotation_degrees:g}°，位置容差 {options.tolerance_percent:g}%'
                    f'（约 {options.pixel_tolerance(size):.1f} 像素）。请确认选区属于同一运动；'
                    '若仅有轻微几何差异，可在“高级自定义”调整；重复纹理应重新框选。')
        return replace(result, boxes=tuple(None for _ in regions), reasons=reasons,
                       predicted_boxes=tuple(regions), error='；'.join(f'选区 {i+1}：{s}' for i, s in enumerate(reasons))+hint)
    dx, dy, accepted = translation
    width, height = size
    rw, rh = reference_size
    boxes, predictions, reasons = [], [], []
    for i, region in enumerate(regions):
        predicted = ((region[0]*rw+dx)/width, (region[1]*rh+dy)/height,
                     (region[2]*rw+dx)/width, (region[3]*rh+dy)/height)
        box = result.boxes[i] if i in accepted else None
        reason = ''
        if box is None:
            if predicted[0] < 0 or predicted[1] < 0 or predicted[2] > 1 or predicted[3] > 1:
                reason = '预计越界，未匹配'
            elif result.boxes[i] is not None:
                reason = '位移不一致，已排除'
            else:
                reason = (result.reasons[i] if i < len(result.reasons) else '') or '未匹配'
        boxes.append(box)
        predictions.append(predicted)
        reasons.append(reason)
    return replace(result, boxes=tuple(boxes), predicted_boxes=tuple(predictions), reasons=tuple(reasons),
                   error='；'.join(f'选区 {i+1}：{s}' for i, s in enumerate(reasons) if s))
