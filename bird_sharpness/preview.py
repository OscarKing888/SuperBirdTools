"""Model preview: one model's raw output on a photo, with its own parameters (no sharpness).

Used by the trace window's model chain (``SuperViewer/superviewer/model_preview.py``).
Qt-free: runs on a worker thread and returns boxes / masks in full-resolution image
pixels; :func:`render` draws them on a display copy (the same 2400 px frame the trace
uses, so trace boxes line up).

A chain feeds one window's results to the next: :func:`run_detector_on` zooms the
detector into each input result (optionally keeping only the pixels inside its mask,
so SAM's cut-out can be checked by YOLO), and :func:`run_sam_on` segments each input
box as its own object. Or :func:`cutout` makes the input results' pixels a new image
(everything else :data:`MASK_FILL` grey, cropped to them) and :func:`run_detector_cutout` /
:func:`run_sam_cutout` run the model once on that image; results come back in photo pixels.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .image_source import AnalysisImage

Box = Tuple[float, float, float, float]
PREVIEW_INPUT_MAX = 2048  # long edge fed to a model: dozens of low-confidence masks stay bounded
SAM_CROP_MARGIN = 0.5     # SAM sees the prompts' region plus this share on every side
SAM_CROP_MIN = 256
CHAIN_MARGIN = 0.3        # a detector zooming into an input result sees its box plus this share per side
CHAIN_MIN_SIDE = 64
ANALYSIS_MARGIN = 0.3     # 测清晰度: the temporary image is the results' bounds plus this share per side
MASK_FILL = 114           # outside-the-mask fill: YOLO's own letterbox grey
MASK_FILL_GRAY = MASK_FILL / 255.0  # the same grey in the measured luminance (0..1)
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
    source: Optional[int] = None    # chain: 1-based number of the input result it came from


@dataclass
class PreviewResult:
    model: str
    device: str
    elapsed_s: float
    input_desc: str
    items: List[PreviewItem] = field(default_factory=list)
    gamma: Optional[float] = None
    cutout: Optional["Cutout"] = None  # the new image the model saw (cut-out runs)


@dataclass
class Cutout:
    """Input results' pixels as a new image: ``rgb`` is the photo inside ``region`` with
    everything outside the results (``mask`` False) :data:`MASK_FILL` grey."""

    rgb: np.ndarray
    mask: np.ndarray
    region: Box     # image px (integers)
    count: int      # input results cut out


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


def _detect_crop(models, crop: np.ndarray, crop_box: Box, s: float, params: DetectorPreview,
                 source: Optional[int] = None) -> Tuple[List[PreviewItem], Optional[float]]:
    from .analyzer import LIFT_DARK_GAMMA, lift_midtones

    bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
    gamma = None
    if params.lift:
        lifted, g = lift_midtones(bgr, bgr=True)
        if g < LIFT_DARK_GAMMA:
            bgr, gamma = lifted, g
    found = models.detect_objects(bgr, conf=params.min_conf, imgsz=params.imgsz, birds_only=params.birds_only)
    cx1, cy1 = crop_box[0], crop_box[1]
    items = []
    for name, conf, box, mask in found:
        full = (box[0] / s + cx1, box[1] / s + cy1, box[2] / s + cx1, box[3] / s + cy1)
        items.append(PreviewItem(name, conf, full, None if mask is None else mask.astype(bool),
                                 None if mask is None else crop_box, source))
    return items, gamma


def _model_name(models, params: DetectorPreview) -> str:
    return str(getattr(models, "detector_name", params.model))


def run_detector(image: AnalysisImage, params: DetectorPreview, *, models=None) -> PreviewResult:
    from .models import shared_models

    models = models or shared_models(params.model)
    t0 = time.perf_counter()
    crop, crop_box, s = _crop(image, params.region)
    items, gamma = _detect_crop(models, crop, crop_box, s, params)
    cx1, cy1, cx2, cy2 = crop_box
    where = "全图" if params.region is None else f"区域 {int(cx2 - cx1)} × {int(cy2 - cy1)} px"
    return PreviewResult(_model_name(models, params), str(getattr(models, "device", "")),
                         time.perf_counter() - t0, f"{where}，网络输入 {params.imgsz} px", items, gamma)


def expand_box(box: Box, margin: float, shape, min_side: float = CHAIN_MIN_SIDE) -> Box:
    """``box`` grown by ``margin`` of its size on every side (at least ``min_side``), inside the image."""
    H, W = shape[:2]
    w, h = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
    w, h = max(w * (1 + 2 * margin), min_side), max(h * (1 + 2 * margin), min_side)
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    return (max(0.0, cx - w / 2), max(0.0, cy - h / 2), min(float(W), cx + w / 2), min(float(H), cy + h / 2))


def mask_in(item: PreviewItem, region: Box, shape) -> Optional[np.ndarray]:
    """``item``'s mask resampled onto ``region`` (image px) at ``shape`` (h, w); None without a mask."""
    if item.mask is None or item.mask_box is None:
        return None
    mh, mw = item.mask.shape[:2]
    h, w = shape[:2]
    sx, sy = w / max(region[2] - region[0], 1e-6), h / max(region[3] - region[1], 1e-6)
    mx1, my1, mx2, my2 = item.mask_box
    ax, ay = (mx2 - mx1) / mw * sx, (my2 - my1) / mh * sy  # region px per mask px
    # pixel centres: mask pixel j spans [j, j+1) and lands on [j*a + b, (j+1)*a + b)
    affine = np.float32([[ax, 0, (mx1 - region[0]) * sx + 0.5 * ax - 0.5],
                         [0, ay, (my1 - region[1]) * sy + 0.5 * ay - 0.5]])
    return cv2.warpAffine(item.mask.astype(np.uint8), affine, (w, h), flags=cv2.INTER_NEAREST).astype(bool)


def run_detector_on(image: AnalysisImage, params: DetectorPreview, inputs: Sequence[PreviewItem], *,
                    margin: float = CHAIN_MARGIN, mask_only: bool = False, models=None) -> PreviewResult:
    """The detector zoomed into each input result (its box plus ``margin``).

    ``mask_only``: pixels outside the input's mask become :data:`MASK_FILL` grey, so the
    detector sees only what SAM (or a segmenting detector) cut out. Inputs without a
    mask are fed whole. Every output keeps ``source`` = the input's number.
    """
    from .models import shared_models

    if not inputs:
        raise ValueError("没有输入结果")
    models = models or shared_models(params.model)
    t0 = time.perf_counter()
    items: List[PreviewItem] = []
    gammas, masked = [], 0
    for k, item in enumerate(inputs, 1):
        crop, crop_box, s = _crop(image, expand_box(item.box, margin, image.rgb8.shape))
        if mask_only:
            keep = mask_in(item, crop_box, crop.shape)
            if keep is not None:
                crop = crop.copy()
                crop[~keep] = MASK_FILL
                masked += 1
        found, gamma = _detect_crop(models, crop, crop_box, s, params, source=k)
        items.extend(found)
        if gamma is not None:
            gammas.append(gamma)
    how = f"{len(inputs)} 个输入各自放大（框外扩 {round(margin * 100)}%）"
    if mask_only:
        how += f"，{masked} 个只留轮廓内像素" if masked else "，输入没有轮廓，按整框送入"
    return PreviewResult(_model_name(models, params), str(getattr(models, "device", "")),
                         time.perf_counter() - t0, f"{how}，网络输入 {params.imgsz} px", items,
                         min(gammas) if gammas else None)


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


def run_sam_on(image: AnalysisImage, model: str, inputs: Sequence[PreviewItem], *, refiner=None) -> PreviewResult:
    """SAM with each input result's box as its own prompt (each in its own crop, so small
    birds keep their resolution). Every output keeps ``source`` = the input's number."""
    from .refine import shared_refiner

    if not inputs:
        raise ValueError("没有输入结果")
    refiner = refiner or shared_refiner(model)
    t0 = time.perf_counter()
    items: List[PreviewItem] = []
    for k, item in enumerate(inputs, 1):
        for found in run_sam(image, SamPreview(model, (tuple(item.box),)), refiner=refiner).items:
            found.label, found.source = f"对象 {len(items) + 1}", k
            items.append(found)
    return PreviewResult(model, str(getattr(refiner, "device", "")), time.perf_counter() - t0,
                         f"{len(inputs)} 个输入框，每个单独作为一个对象", items)


def cutout(image: AnalysisImage, inputs: Sequence[PreviewItem], *, fill: int = MASK_FILL) -> Cutout:
    """The input results' pixels (masks; plain boxes for results without one) as a new image
    cropped to them, no margin."""
    if not inputs:
        raise ValueError("没有输入结果")
    H, W = image.rgb8.shape[:2]
    x1 = max(0, int(np.floor(min(i.box[0] for i in inputs))))
    y1 = max(0, int(np.floor(min(i.box[1] for i in inputs))))
    x2 = min(W, int(np.ceil(max(i.box[2] for i in inputs))))
    y2 = min(H, int(np.ceil(max(i.box[3] for i in inputs))))
    if x2 - x1 < 16 or y2 - y1 < 16:
        raise ValueError("抠出的区域太小")
    region = (float(x1), float(y1), float(x2), float(y2))
    keep = np.zeros((y2 - y1, x2 - x1), bool)
    for item in inputs:
        mask = mask_in(item, region, keep.shape)
        if mask is None:
            bx1, by1 = max(0, int(item.box[0]) - x1), max(0, int(item.box[1]) - y1)
            bx2, by2 = int(np.ceil(item.box[2])) - x1, int(np.ceil(item.box[3])) - y1
            keep[by1:by2, bx1:bx2] = True
        else:
            keep |= mask
    rgb = image.rgb8[y1:y2, x1:x2].copy()
    rgb[~keep] = fill
    return Cutout(rgb, keep, region, len(inputs))


def _on_cutout(result: PreviewResult, cut: Cutout) -> PreviewResult:
    """A run on ``cut.rgb``: results moved back to photo pixels, the cut-out attached."""
    dx, dy = cut.region[0], cut.region[1]
    for item in result.items:
        item.box = (item.box[0] + dx, item.box[1] + dy, item.box[2] + dx, item.box[3] + dy)
        if item.mask_box is not None:
            b = item.mask_box
            item.mask_box = (b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy)
    h, w = cut.rgb.shape[:2]
    result.input_desc = f"抠出 {cut.count} 个输入的像素为新图（{w} × {h} px）；{result.input_desc}"
    result.cutout = cut
    return result


def _local(box: Box, cut: Cutout) -> Box:
    dx, dy = cut.region[0], cut.region[1]
    return (box[0] - dx, box[1] - dy, box[2] - dx, box[3] - dy)


def run_detector_cutout(image: AnalysisImage, params: DetectorPreview, inputs: Sequence[PreviewItem], *,
                        models=None) -> PreviewResult:
    """The detector once on the cut-out (``params.region``, photo px, limits it to that part)."""
    cut = cutout(image, inputs)
    region = None
    if params.region is not None:
        x1, y1, x2, y2 = _local(params.region, cut)
        h, w = cut.rgb.shape[:2]
        region = (max(0.0, x1), max(0.0, y1), min(float(w), x2), min(float(h), y2))
        if region[2] - region[0] < 16 or region[3] - region[1] < 16:
            raise ValueError("当前视图不在抠出的图内")
    result = run_detector(SimpleNamespace(rgb8=cut.rgb), replace(params, region=region), models=models)
    return _on_cutout(result, cut)


def run_sam_cutout(image: AnalysisImage, params: SamPreview, inputs: Sequence[PreviewItem], *,
                   refiner=None) -> PreviewResult:
    """SAM once on the cut-out with the drawn prompts (photo px); none = the whole new image as one box."""
    cut = cutout(image, inputs)
    h, w = cut.rgb.shape[:2]
    boxes = tuple(_local(b, cut) for b in params.boxes)
    points = tuple((x - cut.region[0], y - cut.region[1], keep) for x, y, keep in params.points)
    if not boxes and not points:
        boxes = ((0.0, 0.0, float(w), float(h)),)
    result = run_sam(SimpleNamespace(rgb8=cut.rgb), SamPreview(params.model, boxes, points), refiner=refiner)
    return _on_cutout(result, cut)


def cutout_display(display: np.ndarray, scale: float, cut: Cutout) -> np.ndarray:
    """The trace display frame showing only the cut-out's pixels (the rest grey), so the new
    image keeps the photo's coordinates."""
    img = np.full_like(display, MASK_FILL)
    dh, dw = display.shape[:2]
    x1, y1, x2, y2 = (int(round(v * scale)) for v in cut.region)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(dw, x2), min(dh, y2)
    if x2 > x1 and y2 > y1:
        part = cv2.resize(cut.mask.astype(np.uint8), (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST).astype(bool)
        img[y1:y2, x1:x2][part] = display[y1:y2, x1:x2][part]
    return img


def analysis_input(image: AnalysisImage, inputs: Sequence[PreviewItem], label: str = "", *, fill: bool = False):
    """``(temporary image, GivenBirds, region)`` for measuring ``inputs`` as birds.

    The temporary image is the photo's own pixels (``rgb8`` and the measured ``gray``)
    around the results, and each result is a given bird: its mask (else its box) in the
    temporary image's pixels. Measuring then looks only at those pixels.

    ``fill``: everything outside the results becomes :data:`MASK_FILL` grey (``gray``
    :data:`MASK_FILL_GRAY`), like the cut-out the chain shows. For comparison only: the
    grey meets each outline in a perfectly sharp artificial edge, so where the measured
    region reaches the outline the result reads sharper.
    """
    from .analyzer import GivenBird, GivenBirds

    if not inputs:
        raise ValueError("这个窗口没有结果")
    union = (min(i.box[0] for i in inputs), min(i.box[1] for i in inputs),
             max(i.box[2] for i in inputs), max(i.box[3] for i in inputs))
    x1, y1, x2, y2 = expand_box(union, ANALYSIS_MARGIN, image.rgb8.shape)
    x1, y1, x2, y2 = int(x1), int(y1), int(np.ceil(x2)), int(np.ceil(y2))
    if x2 - x1 < 16 or y2 - y1 < 16:
        raise ValueError("结果区域太小")
    region = (float(x1), float(y1), float(x2), float(y2))
    # Always copies (a full-width slice would be a view): the fill must never touch the photo.
    crop = replace(image, rgb8=image.rgb8[y1:y2, x1:x2].copy(), gray=image.gray[y1:y2, x1:x2].copy(),
                   camera_crop=None)
    h, w = y2 - y1, x2 - x1
    birds = []
    keep = np.zeros((h, w), bool)
    for item in inputs:
        bx1, by1 = max(0.0, item.box[0] - x1), max(0.0, item.box[1] - y1)
        bx2, by2 = min(float(w), item.box[2] - x1), min(float(h), item.box[3] - y1)
        mask = mask_in(item, region, (h, w))
        birds.append(GivenBird((bx1, by1, bx2, by2), mask, 1.0 if item.confidence is None else float(item.confidence)))
        if fill:
            if mask is None:
                keep[int(by1):int(np.ceil(by2)), int(bx1):int(np.ceil(bx2))] = True
            else:
                keep |= mask
    if fill:
        crop.rgb8[~keep] = MASK_FILL
        crop.gray[~keep] = MASK_FILL_GRAY
    return crop, GivenBirds(birds, label, filled=fill), region


def items_from_boxes(boxes: Sequence[Box], label: str = "计算过程识别的鸟") -> List[PreviewItem]:
    """Plain boxes (e.g. the trace's detected birds, image px) as chain inputs."""
    return [PreviewItem(f"{label} {n}", None, tuple(float(v) for v in box)) for n, box in enumerate(boxes, 1)]


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
        if item.source is not None:
            value += f" · 来自输入 #{item.source}"
        rows.append(TraceBirdRow(f"#{n + 1} {item.label}", value, hex_color(color), box))
    blended = cv2.addWeighted(overlay, 0.45, img, 0.55, 0)
    img[tinted] = blended[tinted]
    return img, rows


def mask_area(item: PreviewItem) -> int:
    """Mask area in image pixels (masks come from a downscaled crop)."""
    mx1, my1, mx2, my2 = item.mask_box
    return int(round(item.mask.sum() * (mx2 - mx1) * (my2 - my1) / max(1, item.mask.size)))
