"""Model preview: one model's raw output on a photo, with its own parameters (no sharpness).

Used by the trace window's model preview docks. Qt-free: runs on a worker thread
and returns boxes / masks in full-resolution image pixels; :func:`render` draws them
on a display copy (the same 2400 px frame the trace uses, so trace boxes line up).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .image_source import AnalysisImage

Box = Tuple[float, float, float, float]
PREVIEW_INPUT_MAX = 2048  # long edge fed to a model: dozens of low-confidence masks stay bounded
SAM_CROP_MARGIN = 0.5     # SAM sees the prompts' region plus this share on every side
SAM_CROP_MIN = 256
PALETTE = [(0, 200, 255), (255, 120, 200), (120, 255, 120), (255, 200, 60), (160, 140, 255),
           (255, 255, 120), (80, 255, 220), (255, 160, 120)]


@dataclass(frozen=True)
class DetectorPreview:
    model: str = "auto"
    region: Optional[Box] = None  # image px; None = whole frame
    imgsz: int = 640
    min_conf: float = 0.10
    birds_only: bool = True
    lift: bool = True             # lift dark mid-tones first (detection only)


@dataclass(frozen=True)
class SamPreview:
    model: str = "sam2.1_t.pt"
    boxes: Tuple[Box, ...] = ()                         # image px; each its own object without points
    points: Tuple[Tuple[float, float, bool], ...] = ()  # (x, y, keep) image px; one object with <= 1 box


@dataclass
class PreviewItem:
    label: str
    confidence: Optional[float]
    box: Box                       # image px
    mask: Optional[np.ndarray] = None
    mask_box: Optional[Box] = None  # image px the mask covers (mask is resized onto it)


@dataclass
class PreviewResult:
    model: str
    device: str
    elapsed_s: float
    input_desc: str
    items: List[PreviewItem] = field(default_factory=list)
    gamma: Optional[float] = None


def display_image(image: AnalysisImage) -> Tuple[np.ndarray, float]:
    """The trace's display frame of ``image`` (same scale, so trace boxes line up)."""
    from .trace import DISPLAY_LONG_EDGE, _auto_exposure, _downscale

    small, scale = _downscale(image.rgb8, DISPLAY_LONG_EDGE)
    return _auto_exposure(small), scale


def _crop(image: AnalysisImage, region: Optional[Box]) -> Tuple[np.ndarray, Box, float]:
    """``(rgb crop at most PREVIEW_INPUT_MAX long, its box in image px, crop px per image px)``."""
    H, W = image.rgb8.shape[:2]
    if region is None:
        x1, y1, x2, y2 = 0, 0, W, H
    else:
        x1, y1 = max(0, int(region[0])), max(0, int(region[1]))
        x2, y2 = min(W, int(np.ceil(region[2]))), min(H, int(np.ceil(region[3])))
        if x2 - x1 < 16 or y2 - y1 < 16:
            raise ValueError("预览区域太小")
    crop = image.rgb8[y1:y2, x1:x2]
    scale = min(1.0, PREVIEW_INPUT_MAX / float(max(crop.shape[:2])))
    if scale < 1.0:
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))),
                          interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(crop), (float(x1), float(y1), float(x2), float(y2)), scale


def run_detector(image: AnalysisImage, params: DetectorPreview, *, models=None) -> PreviewResult:
    from .analyzer import LIFT_DARK_GAMMA, lift_midtones
    from .models import shared_models

    models = models or shared_models(params.model)
    t0 = time.perf_counter()
    crop, (cx1, cy1, cx2, cy2), s = _crop(image, params.region)
    bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
    gamma = None
    if params.lift:
        lifted, g = lift_midtones(bgr, bgr=True)
        if g < LIFT_DARK_GAMMA:
            bgr, gamma = lifted, g
    found = models.detect_objects(bgr, conf=params.min_conf, imgsz=params.imgsz, birds_only=params.birds_only)
    items = []
    for name, conf, box, mask in found:
        full = (box[0] / s + cx1, box[1] / s + cy1, box[2] / s + cx1, box[3] / s + cy1)
        items.append(PreviewItem(name, conf, full, None if mask is None else mask.astype(bool),
                                 None if mask is None else (cx1, cy1, cx2, cy2)))
    where = "全图" if params.region is None else f"区域 {int(cx2 - cx1)} × {int(cy2 - cy1)} px"
    return PreviewResult(str(getattr(models, "detector_name", params.model)), str(getattr(models, "device", "")),
                         time.perf_counter() - t0, f"{where}，网络输入 {params.imgsz} px", items, gamma)


def sam_region(image: AnalysisImage, params: SamPreview) -> Box:
    """The region SAM sees: the prompts' bounds plus SAM_CROP_MARGIN, at least SAM_CROP_MIN."""
    H, W = image.rgb8.shape[:2]
    xs = [v for b in params.boxes for v in (b[0], b[2])] + [p[0] for p in params.points]
    ys = [v for b in params.boxes for v in (b[1], b[3])] + [p[1] for p in params.points]
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    w, h = max(x2 - x1, SAM_CROP_MIN), max(y2 - y1, SAM_CROP_MIN)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    w, h = w * (1 + 2 * SAM_CROP_MARGIN), h * (1 + 2 * SAM_CROP_MARGIN)
    return (max(0.0, cx - w / 2), max(0.0, cy - h / 2), min(float(W), cx + w / 2), min(float(H), cy + h / 2))


def run_sam(image: AnalysisImage, params: SamPreview, *, refiner=None) -> PreviewResult:
    from .refine import shared_refiner

    if not params.boxes and not params.points:
        raise ValueError("请先画框、点选，或使用检测框")
    if params.points and len(params.boxes) > 1:
        raise ValueError("点选时只能配合一个框")
    refiner = refiner or shared_refiner(params.model)
    t0 = time.perf_counter()
    crop, (cx1, cy1, cx2, cy2), s = _crop(image, sam_region(image, params))

    def local(x, y):
        return (x - cx1) * s, (y - cy1) * s

    boxes = [(*local(b[0], b[1]), *local(b[2], b[3])) for b in params.boxes]
    points = [local(p[0], p[1]) for p in params.points]
    found = refiner.segment(crop, boxes=boxes or None, points=points or None,
                            labels=[1 if p[2] else 0 for p in params.points] or None)
    items = []
    for n, (mask, score) in enumerate(found):
        ys, xs = np.nonzero(mask)
        if ys.size:
            box = (xs.min() / s + cx1, ys.min() / s + cy1, (xs.max() + 1) / s + cx1, (ys.max() + 1) / s + cy1)
        else:
            box = (cx1, cy1, cx1, cy1)
        items.append(PreviewItem(f"对象 {n + 1}", score, box, mask, (cx1, cy1, cx2, cy2)))
    prompts = "，".join(p for p in (f"{len(params.boxes)} 个框" if params.boxes else "",
                                    f"{len(params.points)} 个点" if params.points else "") if p)
    return PreviewResult(params.model, str(getattr(refiner, "device", "")), time.perf_counter() - t0,
                         f"提示：{prompts}；区域 {int(cx2 - cx1)} × {int(cy2 - cy1)} px", items)


def render(display: np.ndarray, scale: float, items: Sequence[PreviewItem]):
    """``(image, rows)``: masks tinted and boxes drawn on a copy of ``display``; rows are
    ``trace.TraceBirdRow`` (display coords) for the hover list."""
    from .trace import TraceBirdRow, _line_w, _rect, hex_color

    img = display.copy()
    lw = _line_w(img)
    dh, dw = img.shape[:2]
    overlay, tinted = img.copy(), np.zeros((dh, dw), bool)
    rows = []
    for n, item in enumerate(items):
        color = PALETTE[n % len(PALETTE)]
        if item.mask is not None and item.mask_box is not None:
            mx1, my1, mx2, my2 = (int(round(v * scale)) for v in item.mask_box)
            mx1, my1, mx2, my2 = max(0, mx1), max(0, my1), min(dw, mx2), min(dh, my2)
            if mx2 > mx1 and my2 > my1:
                part = cv2.resize(item.mask.astype(np.uint8), (mx2 - mx1, my2 - my1),
                                  interpolation=cv2.INTER_NEAREST).astype(bool)
                region = overlay[my1:my2, mx1:mx2]
                region[part] = color
                tinted[my1:my2, mx1:mx2] |= part
        box = tuple(float(v) * scale for v in item.box)
        _rect(img, box, color, lw)
        conf = "" if item.confidence is None else f" {item.confidence:.2f}"
        text = f"#{n + 1} {item.label}" if item.label.isascii() else f"#{n + 1}"  # OpenCV draws ASCII only
        cv2.putText(img, f"{text}{conf}", (int(box[0]) + 2 * lw, max(12, int(box[1]) - 2 * lw)),
                    cv2.FONT_HERSHEY_SIMPLEX, max(0.4, lw * 0.45), color, max(1, lw // 2), cv2.LINE_AA)
        bw, bh = item.box[2] - item.box[0], item.box[3] - item.box[1]
        value = (("" if item.confidence is None else f"置信度 {item.confidence:.2f} · ")
                 + f"框 {int(bw)} × {int(bh)} px"
                 + ("" if item.mask is None else f" · 轮廓 {mask_area(item):,} px"))
        rows.append(TraceBirdRow(f"#{n + 1} {item.label}", value, hex_color(color), box))
    blended = cv2.addWeighted(overlay, 0.45, img, 0.55, 0)
    img[tinted] = blended[tinted]
    return img, rows


def mask_area(item: PreviewItem) -> int:
    """Mask area in image pixels (masks come from a downscaled crop)."""
    mx1, my1, mx2, my2 = item.mask_box
    return int(round(item.mask.sum() * (mx2 - mx1) * (my2 - my1) / max(1, item.mask.size)))
