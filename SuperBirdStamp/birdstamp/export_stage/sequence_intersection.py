"""Maximum common rectangle in output pixels, shared by overlays and export."""
from dataclasses import replace

from birdstamp.image_dejitter.alignment_bounds import intersect_convex, largest_pixel_rectangle
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
    if sequence.intersection_box is None:
        return None
    width, height = sequence.output_size
    left, top, right, bottom = sequence.intersection_box
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
    return replace(sequence, pixel_boxes=boxes, canvas_box=canvas, output_size=size,
                   intersection_box=(0, 0, *size))
