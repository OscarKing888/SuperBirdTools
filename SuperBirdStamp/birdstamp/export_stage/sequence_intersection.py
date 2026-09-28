"""Common crop and complete sequence bounds in output-pixel coordinates."""
from dataclasses import replace

from birdstamp.image_dejitter.alignment_bounds import intersect_convex, largest_pixel_rectangle, outward_bounds
from birdstamp.image_dejitter.rigid_alignment import rectangle_points
from .video_export_cancelled_error import VideoExportCancelledError


def compute_intersection_box(sequence, *, cancelled=lambda: False):
    """Compute from accepted frame geometry only; no decoding or matching."""
    width, height = sequence.output_size
    bounds = (0, 0, width, height)
    polygon = rectangle_points(bounds)
    for key in sequence.jobs:
        if cancelled():
            raise VideoExportCancelledError('已取消共同范围计算。')
        size = sequence.source_sizes[key]
        if sequence.alignments:
            left, top = sequence.canvas_box[:2]
            footprint = tuple((x-left, y-top) for x, y in
                              sequence.alignments[key].footprint(size, safe=True))
            if not footprint:
                return None
            polygon = intersect_convex(polygon, footprint)
            if len(polygon) < 3:
                return None
        else:
            # Legacy integer crops locate the source origin in output coordinates.
            left, top = sequence.pixel_boxes[key][:2]
            bounds = (max(bounds[0], -left), max(bounds[1], -top),
                      min(bounds[2], size[0]-left), min(bounds[3], size[1]-top))
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                return None
    if not sequence.alignments:
        return bounds
    try:
        return largest_pixel_rectangle(polygon, cancelled=cancelled)
    except InterruptedError as exc:
        raise VideoExportCancelledError('已取消共同范围计算。') from exc


def normalized_intersection_box(sequence):
    return _normalized_box(sequence.intersection_box, sequence.output_size)


def compute_union_box(sequence, *, cancelled=lambda: False):
    """Smallest axis-aligned box containing every full aligned source footprint.

    Unlike the common crop, this includes interpolation edges and may extend
    outside an unpadded output canvas. No source pixels or metadata are read.
    """
    footprints = []
    for key in sequence.jobs:
        if cancelled():
            raise VideoExportCancelledError('已取消完整范围计算。')
        size = sequence.source_sizes[key]
        if sequence.alignments:
            left, top = sequence.canvas_box[:2]
            footprint = tuple((x-left, y-top) for x, y in sequence.alignments[key].footprint(size))
        else:
            left, top = sequence.pixel_boxes[key][:2]
            footprint = rectangle_points((-left, -top, size[0]-left, size[1]-top))
        footprints.append(footprint)
    return outward_bounds(footprints) if footprints else None


def normalized_union_box(sequence):
    return _normalized_box(sequence.union_box, sequence.output_size)


def _normalized_box(box, size):
    if box is None:
        return None
    width, height = size
    left, top, right, bottom = box
    return left/width, top/height, right/width, bottom/height


def intersection_export_sequence(sequence):
    """Derive a canvas without altering analysis/previews or resampling twice."""
    if sequence.intersection_box is None:
        raise ValueError('整组没有共同有效区域，无法仅导出最大交集范围；请取消此选项或调整照片范围。')
    left, top, right, bottom = sequence.intersection_box
    size = right-left, bottom-top
    boxes = {key: (box[0]+left, box[1]+top, box[0]+right, box[1]+bottom)
             for key, box in sequence.pixel_boxes.items()}
    canvas = sequence.canvas_box
    if canvas:
        canvas = (canvas[0]+left, canvas[1]+top, canvas[0]+right, canvas[1]+bottom)
    union = sequence.union_box
    if union is not None:
        union = (union[0]-left, union[1]-top, union[2]-left, union[3]-top)
    return replace(sequence, pixel_boxes=boxes, canvas_box=canvas, output_size=size,
                   intersection_box=(0, 0, *size), union_box=union)
