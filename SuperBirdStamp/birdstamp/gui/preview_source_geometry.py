"""相机预览坐标与 RAW/降噪像素坐标互转；不修改持久化的参考区定义。"""
from __future__ import annotations

import math

from app_common.raw_preview_geometry import map_camera_focus_box


def _camera_crop(camera_crop_box):
    # 复用焦点映射的范围校验，无效几何自然回退为恒等变换。
    return map_camera_focus_box((0.0, 0.0, 1.0, 1.0), camera_crop_box)


def camera_to_preview_box(box, camera_crop_box):
    return map_camera_focus_box(box, camera_crop_box)


def camera_to_preview_point(point, camera_crop_box):
    left, top, right, bottom = _camera_crop(camera_crop_box)
    return left + point[0] * (right - left), top + point[1] * (bottom - top)


def preview_to_camera_point(point, camera_crop_box):
    left, top, right, bottom = _camera_crop(camera_crop_box)
    return (point[0] - left) / (right - left), (point[1] - top) / (bottom - top)


def preview_to_camera_box(box, camera_crop_box, *, clip=True):
    """将画布编辑写回相机坐标；仅位于传感器外圈的框不能成为参考区。"""
    if box is None:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in box)
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            return None
        left, top, right, bottom = _camera_crop(camera_crop_box)
        values = ((x0 - left) / (right - left), (y0 - top) / (bottom - top),
                  (x1 - left) / (right - left), (y1 - top) / (bottom - top))
        if clip:
            values = tuple(max(0.0, min(1.0, value)) for value in values)
        return values if values[0] < values[2] and values[1] < values[3] else None
    except (TypeError, ValueError):
        return None


def _preview_shape(shape, camera_crop_box):
    if shape and isinstance(shape[0], (tuple, list)):
        return tuple(camera_to_preview_point(point, camera_crop_box) for point in shape)
    return camera_to_preview_box(shape, camera_crop_box)


def transform_source_overlays(state, camera_crop_box):
    """只转换原图参考/诊断叠加；焦点和鸟体由各自的像素来源处理。"""
    if camera_crop_box is None:
        return
    state.reference_regions = tuple(camera_to_preview_box(box, camera_crop_box)
                                    for box in state.reference_regions)
    state.reference_diagnostics = tuple((_preview_shape(shape, camera_crop_box), label, matched)
                                        for shape, label, matched in state.reference_diagnostics)
    state.subject_points = tuple((*camera_to_preview_point((x, y), camera_crop_box),
                                 *camera_to_preview_point((qx, qy), camera_crop_box), accepted)
                                for x, y, qx, qy, accepted in state.subject_points)
    state.crop_effect_box = camera_to_preview_box(state.crop_effect_box, camera_crop_box)
    state.alignment_crop_box = camera_to_preview_box(state.alignment_crop_box, camera_crop_box)
    state.crop_polygon = tuple(camera_to_preview_point(point, camera_crop_box) for point in state.crop_polygon)
