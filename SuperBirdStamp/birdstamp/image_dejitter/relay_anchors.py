"""接力参考：整组在某张照片跟踪失败后，在该照片新增一组选区继续匹配后续照片。

接力段内照片只匹配接力参考图上的选区；接力参考图再通过朝原参考图方向相邻、已对齐的
“衔接照片”接回原参考图坐标，因此整组仍输出到同一个画布。衔接照片必须真实匹配到接力选区，
不使用插值或预测位置代替。
"""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, sin
from pathlib import Path

from .manual_region_matches import normalize_match_box
from .region_tracking_result import image_file_signature
from .rigid_alignment import FrameAlignment, map_point

RELAY_ANCHORS_KEY = 'dejitter_relay_anchors'


def _path_key(path) -> str:
    from birdstamp.gui.editor_utils import path_key
    return path_key(Path(path))


def relay_regions(record) -> tuple:
    """记录中可用的归一化选区；无效框丢弃，不改变其余编号。"""
    if not isinstance(record, dict):
        return ()
    values = record.get('regions')
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(box for value in values if (box := normalize_match_box(value)) is not None)


def relay_record(path, regions) -> dict:
    """接力参考图记录；签名绑定原图内容，文件变化后选区不再生效。"""
    return dict(path=str(path), signature=image_file_signature(Path(path)),
                regions=tuple(tuple(float(v) for v in box) for box in regions))


def relay_settings_value(records) -> list:
    """有选区的记录按路径排序，保证分析签名稳定。"""
    usable = [dict(path=r['path'], signature=r.get('signature'), regions=[list(b) for b in relay_regions(r)])
              for r in records if isinstance(r, dict) and isinstance(r.get('path'), str) and relay_regions(r)]
    return sorted(usable, key=lambda r: _path_key(r['path']))


@dataclass(frozen=True, slots=True)
class RelayAnchor:
    """index/bridge 是照片列表位置；bridge 是朝原参考图方向相邻的衔接照片。"""

    index: int
    bridge: int
    path: Path
    regions: tuple

    @property
    def forward(self) -> bool:
        return self.bridge < self.index


def relay_candidate_bridge(index, count, reference_index):
    """可作为接力参考图时返回衔接照片位置，否则返回 None。"""
    if not 0 <= index < count or index == reference_index:
        return None
    if index > reference_index:
        return index - 1 if index - 1 >= 0 else None
    return index + 1 if index + 1 < count else None


def resolve_relay_anchors(records, paths, reference, *, allow_empty=False) -> tuple[RelayAnchor, ...]:
    """只返回列表内、原图未变化、位置可衔接的接力参考图；按列表顺序排列。"""
    paths = tuple(Path(p) for p in paths)
    keys = [_path_key(p) for p in paths]
    positions = {key: index for index, key in enumerate(keys)}
    reference_index = positions.get(_path_key(reference), -1) if reference else -1
    anchors = {}
    for record in records if isinstance(records, (list, tuple)) else ():
        if not isinstance(record, dict) or not isinstance(record.get('path'), str):
            continue
        index = positions.get(_path_key(record['path']))
        if index is None or index in anchors:
            continue
        regions = relay_regions(record)
        if not regions and not allow_empty:
            continue
        try:
            signature = tuple(record['signature']) if record.get('signature') is not None else None
        except TypeError:
            continue
        if signature is None or signature != image_file_signature(paths[index]):
            continue
        bridge = relay_candidate_bridge(index, len(paths), reference_index)
        if bridge is None:
            continue
        anchors[index] = RelayAnchor(index, bridge, paths[index], regions)
    return tuple(anchors[index] for index in sorted(anchors))


def relay_segments(count, reference_index, anchors) -> tuple:
    """每个列表位置所属的接力参考图（None 表示仍匹配原参考图）。

    参考图之后的照片使用其前方最近的接力参考图；参考图之前的照片使用其后方最近的接力参考图。
    """
    forward = sorted((a for a in anchors if a.forward), key=lambda a: a.index)
    backward = sorted((a for a in anchors if not a.forward), key=lambda a: a.index, reverse=True)
    owners = [None] * count
    for index in range(count):
        if index > reference_index:
            owners[index] = next((a for a in reversed(forward) if a.index <= index), None)
        elif index < reference_index:
            owners[index] = next((a for a in reversed(backward) if a.index >= index), None)
    return tuple(owners)


def compose(outer, inner):
    """先应用 inner，再应用 outer 的二维仿射矩阵 (a,b,c,d,e,f)。"""
    a, b, c, d, e, f = outer
    A, B, C, D, E, F = inner
    return (a*A+b*D, a*B+b*E, a*C+b*F+c, d*A+e*D, d*B+e*E, d*C+e*F+f)


def _orthonormal(matrix):
    # 多次相乘的浮点误差不能让刚性校验失败；重新由角度构造旋转部分。
    angle = atan2(matrix[3], matrix[0])
    c, s = cos(angle), sin(angle)
    if abs(s) <= 1e-12:
        return (1., 0., matrix[2], 0., 1., matrix[5]), 0.
    return (c, -s, matrix[2], s, c, matrix[5]), angle


def blended_alignment(matrix, size, strength, *, rigid, status, reason='', indices=()):
    """把整段 100% 变换按补偿强度插值；规则与原参考图的 estimate_alignment 一致。"""
    matrix, angle = _orthonormal(matrix)
    blend = max(0, min(100, float(strength))) / 100
    if not rigid or angle == 0:
        transform = (1., 0., float(round(matrix[2]*blend)), 0., 1., float(round(matrix[5]*blend)))
        if status == 'rigid':
            return FrameAlignment(transform, 0., 0., 'rigid', '', tuple(indices))
        return FrameAlignment(transform, status='fallback' if status == 'fallback' else 'translation',
                              reason=reason, region_indices=tuple(indices))
    w, h = size
    qx, qy = w/2, h/2
    px, py = map_point(matrix, (qx, qy))
    applied = angle * blend
    c, s = cos(applied), sin(applied)
    gx, gy = qx+blend*(px-qx), qy+blend*(py-qy)
    tx, ty = gx-(c*qx-s*qy), gy-(s*qx+c*qy)
    if abs(s) <= 1e-12:
        c, s, tx, ty = 1., 0., float(round(tx)), float(round(ty))
        applied = 0.
    transform = (c, -s, tx, s, c, ty)
    applied_degrees = degrees(atan2(s, c))
    if status == 'fallback':
        return FrameAlignment(transform, applied_degrees=applied_degrees, status='fallback',
                              reason=reason, region_indices=tuple(indices))
    return FrameAlignment(transform, -degrees(angle), applied_degrees, 'rigid', '', tuple(indices))
