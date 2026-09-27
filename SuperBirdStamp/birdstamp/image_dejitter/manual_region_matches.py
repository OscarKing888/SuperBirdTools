"""逐照片、逐编号的手动匹配；只对相同参考选区及未变化的原图生效。"""
from dataclasses import replace
from math import isfinite
from pathlib import Path

from .region_tracking_result import RegionTrackingResult, image_file_signature

MANUAL_MATCHES_KEY = 'dejitter_manual_matches'


def normalize_match_box(box):
    try:
        values = tuple(float(v) for v in box)
        if (len(values) == 4 and all(isfinite(v) and 0 <= v <= 1 for v in values)
                and values[2] - values[0] > 1e-4 and values[3] - values[1] > 1e-4):
            return values
    except (TypeError, ValueError):
        pass
    return None


def manual_match_record(path, reference, regions, boxes):
    return dict(path=str(path), reference=str(reference), regions=tuple(tuple(b) for b in regions),
                signature=image_file_signature(Path(path)), reference_signature=image_file_signature(Path(reference)),
                boxes=tuple(tuple(b) if b is not None else None for b in boxes))


def valid_manual_boxes(record, path, reference, regions):
    if not isinstance(record, dict) or not path or not reference:
        return ()
    try:
        signature, reference_signature = image_file_signature(Path(path)), image_file_signature(Path(reference))
        if (signature is None or reference_signature is None
                or tuple(record['signature']) != signature or tuple(record['reference_signature']) != reference_signature
                or tuple(tuple(b) for b in record['regions']) != tuple(tuple(b) for b in regions)
                or len(record['boxes']) != len(regions)):
            return ()
        return tuple(normalize_match_box(box) if box is not None else None for box in record['boxes'])
    except (KeyError, TypeError, ValueError):
        return ()


def apply_manual_boxes(result, boxes, *, signature=None):
    if not any(box is not None for box in boxes):
        return result
    count = len(boxes)
    result = result or RegionTrackingResult((None,) * count, signature)
    matched = tuple(box if box is not None else result.boxes[i] for i, box in enumerate(boxes))
    scores = tuple(1.0 if box is not None else result.scores[i] if i < len(result.scores) else 0.0
                   for i, box in enumerate(boxes))
    reasons = tuple('手动修正' if box is not None else result.reasons[i] if i < len(result.reasons) else ''
                    for i, box in enumerate(boxes))
    return replace(result, boxes=matched, scores=scores, reasons=reasons,
                   manual_indices=tuple(i for i, box in enumerate(boxes) if box is not None),
                   error='；'.join(f'选区 {i+1}：{reason}' for i, reason in enumerate(reasons)
                                  if matched[i] is None and reason))


def without_manual_boxes(result):
    """分析缓存不是手动修正的源数据；已撤销/失效的修正必须重新自动匹配。"""
    if result is None or not result.manual_indices:
        return result
    boxes = tuple(None if i in result.manual_indices else box for i, box in enumerate(result.boxes))
    reasons = tuple('待自动匹配' if i in result.manual_indices else
                    result.reasons[i] if i < len(result.reasons) else '' for i in range(len(boxes)))
    return replace(result, boxes=boxes, manual_indices=(), reasons=reasons,
                   error='；'.join(f'选区 {i+1}：{s}' for i, s in enumerate(reasons) if boxes[i] is None and s))


def editable_match_boxes(regions, result):
    """失败区保留原编号；把越界预测框移回原图范围，供用户定位修正。"""
    boxes = []
    for index, region in enumerate(regions):
        box = result.boxes[index] if result and index < len(result.boxes) else None
        if box is None:
            box = result.predicted_boxes[index] if result and index < len(result.predicted_boxes) else region
        width, height = min(1, box[2] - box[0]), min(1, box[3] - box[1])
        left, top = max(0, min(box[0], 1 - width)), max(0, min(box[1], 1 - height))
        boxes.append((left, top, left + width, top + height))
    return tuple(boxes)
