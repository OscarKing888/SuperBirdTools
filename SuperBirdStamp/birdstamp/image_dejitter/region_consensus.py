"""从可靠参考区中选择一致的平移，不把离群区域计入补偿。"""
from dataclasses import replace
from math import hypot
from statistics import median


def region_offsets(regions, result, size, reference_size):
    width, height = size
    rw, rh = reference_size
    return {i: (((box[0]+box[2])*width-(region[0]+region[2])*rw)/2,
                ((box[1]+box[3])*height-(region[1]+region[3])*rh)/2)
            for i, (region, box) in enumerate(zip(regions, result.boxes)) if box is not None}


def select_translation(regions, result, size, reference_size):
    offsets = region_offsets(regions, result, size, reference_size)
    if not offsets:
        return None
    manual = {i: offsets[i] for i in result.manual_indices if i in offsets}
    candidates = manual or offsets
    tolerance = max(1, min(size) * .003)
    groups = {tuple(i for i, q in candidates.items() if hypot(q[0]-p[0], q[1]-p[1]) <= tolerance)
              for p in candidates.values()}
    quality = lambda group: sum(result.scores[i] if i < len(result.scores) else 0 for i in group) / len(group)
    ranked = sorted(groups, key=lambda group: (-len(group), -quality(group), group))
    winner = ranked[0]
    # 一区足够，但同等支持的冲突候选需要有明显质量差，不能任意选一个方向。
    competing = [group for group in ranked[1:] if len(group) == len(winner) and not set(group) & set(winner)]
    if competing and quality(winner) - max(map(quality, competing)) < .08:
        return None
    dx = median(offsets[i][0] for i in winner)
    dy = median(offsets[i][1] for i in winner)
    if manual:
        # 人工位置确定平移，自动匹配只能补充与其一致的证据，不能把修正投票掉。
        winner = tuple(i for i, point in offsets.items() if hypot(point[0]-dx, point[1]-dy) <= tolerance)
    return dx, dy, winner


def resolve_tracking_consensus(regions, result, size, reference_size):
    translation = select_translation(regions, result, size, reference_size)
    if translation is None:
        reasons = tuple('匹配方向冲突，未确定位置' if box is not None else
                        (result.reasons[i] if i < len(result.reasons) else '') or '未匹配'
                        for i, box in enumerate(result.boxes))
        return replace(result, boxes=tuple(None for _ in regions), reasons=reasons,
                       predicted_boxes=tuple(regions), error='；'.join(f'选区 {i+1}：{s}' for i, s in enumerate(reasons)))
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
