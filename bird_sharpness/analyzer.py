"""Bird sharpness analysis.

Pipeline (see ``docs/bird_sharpness.md``):

1. decode the full-resolution image (RAW via LibRaw);
2. detect every bird on a 1024 px copy (segmentation masks, or boxes from a
   plain detector); when none is confident, look again at a finer network input
   and next to the camera focus point for a camouflaged bird
   (:meth:`BirdSharpnessAnalyzer._recheck`);
3. per bird, measure only that bird's pixels: blur radius at the strongest edges
   of ``eye circle ∩ bird`` when the keypoint model finds an eye, the whole bird
   otherwise, plus body median/directional blur for motion; the bird with the
   best score decides the photo;
4. no bird: the camera focus box, at least 128 x 128 px;
5. no bird and no focus point: the whole image (tiled);
6. map the blur radius to a SuperPicky-compatible 0..1000 score and a verdict.
"""

from __future__ import annotations

import os
import traceback
import threading
import time
from dataclasses import asdict, dataclass, field as dc_field, replace
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

from app_common import bird_sharpness_fields as fields
from app_common.log import get_logger

from .focus import FocusProvider, default_focus_box, focus_window
from .image_source import AnalysisImage, load_analysis_image
from .metrics import EdgeBlurField, edge_stats, full_image_blur
from .models import (BIRD_CONFIDENCE_MIN, FOUND_FOCUS_WEAK, FOUND_FOCUS_ZOOM, FOUND_FULL, FOUND_FULL_FINE,
                     FOUND_FULL_LIFTED, BirdDetection, BirdSharpnessModels)
from .scoring import ALGORITHM_VERSION, VERDICT_ERROR, VERDICT_NO_BIRD, blank_head_sigma, classify, sigma_to_score

_log = get_logger("bird_sharpness")

DETECT_LONG_EDGE = 1024  # detection copy
DETECT_IMGSZ = 640  # first-pass network input (the size the YOLO models are trained at)
RECHECK_IMGSZ = 1024  # second pass when the first finds no bird: small, dark birds score higher
MAX_BIRDS = 8
CROP_PAD_RATIO = 0.15
EYE_VISIBLE_MIN = 0.5
BEAK_VISIBLE_MIN = 0.3
HEAD_RADIUS_BEAK_RATIO = 1.2
HEAD_RADIUS_BOX_RATIO = 0.15
HEAD_RADIUS_MIN_PX = 40
HEAD_MASK_DILATE_PX = 9
BODY_MASK_ERODE_PX = 15
BOX_INSET_RATIO = 0.08  # detection boxes include background; measure their core
# Progress stages reported through ``analyze(..., on_stage=...)``.
STAGE_DECODE = "decode"
STAGE_DETECT = "detect"
STAGE_MEASURE = "measure"


def _r3(value) -> Optional[float]:
    return None if value is None else round(float(value), 3)


@dataclass
class BirdMeasurement:
    """Sharpness values computed from one bird's own pixels."""

    verdict: str
    score: Optional[int]
    sigma: Optional[float]
    head_sigma: Optional[float]
    body_sigma: Optional[float]
    motion_ratio: Optional[float]
    eye_visibility: Optional[float]
    confidence: float
    box: Tuple[int, int, int, int]
    eye_xy: Optional[Tuple[float, float]] = None
    head_radius: Optional[float] = None
    head_edges: int = 0
    masked: bool = False
    found_by: str = FOUND_FULL
    index: int = 0  # detection number (trace "鸟 #index+1"), kept when false extras are dropped

    def rank(self) -> tuple:
        # Best bird: highest score, then smallest blur radius, then detector confidence.
        return (-1 if self.score is None else self.score, -(self.sigma if self.sigma is not None else 99.0),
                self.confidence)


@dataclass
class BirdSharpnessResult:
    path: str
    verdict: str
    score: Optional[int] = None
    sigma: Optional[float] = None
    region: str = ""
    bird_count: int = 0
    head_sigma: Optional[float] = None
    body_sigma: Optional[float] = None
    motion_ratio: Optional[float] = None
    eye_visibility: Optional[float] = None
    bird_confidence: Optional[float] = None
    bird_box: Optional[Tuple[int, int, int, int]] = None
    eye_xy: Optional[Tuple[float, float]] = None
    head_radius: Optional[float] = None
    head_edges: int = 0
    region_box: Optional[Tuple[int, int, int, int]] = None
    birds: List[dict] = dc_field(default_factory=list)
    image_long_edge: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    version: str = ALGORITHM_VERSION

    @property
    def ok(self) -> bool:
        return self.verdict != VERDICT_ERROR

    def to_dict(self) -> dict:
        return asdict(self)

    def to_xmp_fields(self) -> Dict[str, str]:
        """Sidecar assignments; empty strings remove stale values from an earlier run."""
        if not self.ok:
            return {}

        def num(value: Optional[float], fmt: str) -> str:
            return "" if value is None else fmt % value

        out = {
            fields.xmp_key(fields.FIELD_VERDICT): self.verdict,
            fields.xmp_key(fields.FIELD_SCORE): "" if self.score is None else str(int(self.score)),
            fields.xmp_key(fields.FIELD_SIGMA): num(self.sigma, "%.3f"),
            fields.xmp_key(fields.FIELD_REGION): self.region,
            fields.xmp_key(fields.FIELD_BIRD_COUNT): str(int(self.bird_count)),
            fields.xmp_key(fields.FIELD_HEAD_SIGMA): num(self.head_sigma, "%.3f"),
            fields.xmp_key(fields.FIELD_BODY_SIGMA): num(self.body_sigma, "%.3f"),
            fields.xmp_key(fields.FIELD_MOTION_RATIO): num(self.motion_ratio, "%.2f"),
            fields.xmp_key(fields.FIELD_EYE_VISIBILITY): num(self.eye_visibility, "%.2f"),
            fields.xmp_key(fields.FIELD_VERSION): self.version,
        }
        if self.score is not None:
            # SuperPicky's own sharpness slot, same "%06.2f" format it writes.
            out[fields.SHARPNESS_XMP_KEY] = "%06.2f" % float(self.score)
        return out


DUPLICATE_CONTAINMENT = 0.7


def dedupe_detections(detections: List[BirdDetection]) -> List[BirdDetection]:
    """Drop a detection when one of the two boxes lies mostly inside the other.

    Segmentation often returns one bird twice: the whole bird and a part of it
    (DSC04512: whole bird + upper half), in either confidence order; both would
    measure the same head and inflate the bird count. In the 2026-10-02 set this
    was 24 of 28 "multi-bird" frames. ``detections`` must be strongest first.
    """
    kept: List[BirdDetection] = []
    for det in detections:
        if not any(box_overlap(det.box, other.box) >= DUPLICATE_CONTAINMENT for other in kept):
            kept.append(det)
    return kept


# Extra birds that are not separate birds, decided after measuring (eye
# visibility comes from the keypoint model; without it nothing is excluded):
# * a false detection (a leaf, a pale trunk): weak, shows no eye and sits next to
#   a confident bird. DSC04392/04395 leaf at 0.26-0.27 next to a 0.94 bird,
#   DSC05567 trunk at 0.36 next to 0.68. It only matters when it outranks the
#   real bird, which is exactly the failure;
# * a part of another bird (a raised wing, a tail): its box lies mostly inside a
#   bird whose eye is visible while it shows none. DSC05008: wing box 0.55, 65 %
#   inside the 0.43 whole-bird box. Masks cannot tell: segmentation splits such a
#   bird into body and wing with ~2 % mask overlap. Across the set, overlapping
#   boxes were either >= 95 % (duplicates, merged above) or <= 11 % apart from it.
EXTRA_BIRD_CONFIDENCE_MAX = 0.4
EXTRA_BIRD_ANCHOR_MIN = 0.5
PART_OF_BIRD_OVERLAP = 0.5
EXCLUDED_FALSE = "false"
EXCLUDED_PART = "part"


@dataclass(frozen=True)
class Exclusion:
    """Why a measured bird does not count: ``reason`` relative to bird ``other``."""

    reason: str  # EXCLUDED_FALSE | EXCLUDED_PART
    other: int
    overlap: float = 0.0


def excluded_birds(birds: List["BirdMeasurement"]) -> Dict[int, Exclusion]:
    """Extra birds that do not count; the remaining set is never empty."""
    if len(birds) < 2:
        return {}

    def eyeless(b) -> bool:
        return b.eye_visibility is not None and b.eye_visibility < EYE_VISIBLE_MIN

    out: Dict[int, Exclusion] = {}
    for i, part in enumerate(birds):
        if not eyeless(part):
            continue
        owners = [(box_overlap(part.box, b.box), j) for j, b in enumerate(birds)
                  if j != i and b.eye_visibility is not None and b.eye_visibility >= EYE_VISIBLE_MIN]
        overlap, owner = max(owners, default=(0.0, -1))
        if overlap >= PART_OF_BIRD_OVERLAP:
            out[i] = Exclusion(EXCLUDED_PART, owner, round(overlap, 2))
    anchor = max(range(len(birds)), key=lambda i: birds[i].confidence)
    if birds[anchor].confidence >= EXTRA_BIRD_ANCHOR_MIN:
        for i, b in enumerate(birds):
            if i != anchor and i not in out and b.confidence < EXTRA_BIRD_CONFIDENCE_MAX and eyeless(b):
                out[i] = Exclusion(EXCLUDED_FALSE, anchor)
    return out


# Recheck for camouflaged birds (DSC05167: a nightjar on a branch at dusk). The
# first pass keeps its 640 px network input, which finds large or blurred birds
# best, so photos with a bird there are unaffected. When it finds none:
# 0. a dark frame (backlit dusk: bright sky, dark bird) is detected again with
#    its mid-tones lifted, like the camera JPEG's tone curve (LIFT_* below);
# then the 1024 px copy is detected again at 1024 px input: a confident bird there
# is taken as is; otherwise, with a camera focus point, one bird at the focus point
# is accepted on either
#   1. a weak (1024 px) candidate lying mostly on the focus box, or
#   2. a confident bird in a zoomed window around the focus point that a weak
#      candidate confirms at the same place.
# Zoomed windows alone are not trusted: dark leaves against the sky read as
# birds at 0.3-0.8 once magnified. Calibrated on the 2026-10-02 Century Park set
# (two disjoint samples of ~320 frames): 25 hidden birds found, every accepted
# one verified by eye; 1 of 590 random non-bird focus spots accepted (a pine cone).
# Mid-tone lift for detection only (never measured): gamma bringing the median
# luma to LIFT_MEDIAN_TARGET. A plain gain does nothing here because the sky is
# already bright. Lifting before the first pass instead would lose 4 and move 27
# of 329 found birds and add 4 false birds (branches, trunk knots) next to real
# ones; as a fallback it found 14 of 33 missed birds (all real) in a 362-frame
# sample of the 2026-10-02 set.
LIFT_MEDIAN_TARGET = 100.0
LIFT_GAMMA_MIN = 0.35
LIFT_DARK_GAMMA = 0.9  # lift only when the gamma would be below this (median luma < ~85)

FOCUS_CANDIDATE_CONFIDENCE = 0.05  # weakest candidate kept as evidence
FOCUS_WEAK_CONFIDENCE = 0.10
FOCUS_WEAK_OVERLAP = 0.5
FOCUS_ZOOM_DIVISORS = (6.0, 4.0, 2.5)  # window side = image long edge / divisor
FOCUS_ZOOM_IMGSZ = 640
FOCUS_ZOOM_OVERLAP = 0.2
FOCUS_ZOOM_AGREEMENT_IOU = 0.3

Box = Tuple[float, float, float, float]


def _area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: Box, b: Box) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def box_overlap(a: Box, b: Box) -> float:
    """Intersection over the smaller box: 1.0 when one box lies inside the other."""
    return _intersection(a, b) / max(1e-6, min(_area(a), _area(b)))


def box_iou(a: Box, b: Box) -> float:
    inter = _intersection(a, b)
    return inter / max(1e-6, _area(a) + _area(b) - inter)


def zoom_window(focus_px: Box, bounds: Tuple[int, int, int, int], side: float) -> Tuple[int, int, int, int]:
    """Square ``side`` px window centred on the focus box, shifted (not shrunk) inside ``bounds``."""
    bx1, by1, bx2, by2 = bounds
    cx, cy = (focus_px[0] + focus_px[2]) / 2.0, (focus_px[1] + focus_px[3]) / 2.0

    def span(center: float, lo: int, hi: int) -> Tuple[int, int]:
        size = int(min(side, hi - lo))
        start = int(round(min(max(lo, center - size / 2.0), hi - size)))
        return start, start + size

    x1, x2 = span(cx, bx1, bx2)
    y1, y2 = span(cy, by1, by2)
    return x1, y1, x2, y2


def lift_midtones(img: np.ndarray, *, bgr: bool = False) -> Tuple[np.ndarray, float]:
    """``(lifted copy, gamma)`` of a uint8 colour image; gamma 1.0 (same array) when not dark."""
    weights = np.array([0.114, 0.587, 0.299] if bgr else [0.299, 0.587, 0.114], np.float32)
    median = float(np.median(img.reshape(-1, 3)[::7].astype(np.float32) @ weights)) / 255.0
    median = min(max(median, 1e-3), 0.999)
    gamma = float(np.log(LIFT_MEDIAN_TARGET / 255.0) / np.log(median))
    gamma = min(1.0, max(LIFT_GAMMA_MIN, gamma))
    if gamma >= 1.0:
        return img, 1.0
    lut = np.clip(np.power(np.arange(256) / 255.0, gamma) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return lut[img], gamma


@dataclass
class Recheck:
    """What the no-bird recheck looked at and what it accepted (full-resolution boxes)."""

    focus_box: Optional[Box]
    lift_gamma: Optional[float] = None  # set when the dark-frame lifted pass ran
    candidates: List[Tuple[float, Box]] = dc_field(default_factory=list)  # weak candidates touching focus
    windows: List[dict] = dc_field(default_factory=list)  # {"box", "detections": [(conf, box, accepted)]}
    accepted: List[BirdDetection] = dc_field(default_factory=list)  # detection-image coordinates

    @property
    def source(self) -> str:
        return self.accepted[0].source if self.accepted else ""


@dataclass(frozen=True)
class MissedBird:
    """A bird found by :meth:`BirdSharpnessAnalyzer.find_missed_bird`.

    ``box`` is normalised to the decoded pixels (display orientation), which for
    RAW include the sensor margins outside ``camera_crop``.
    """

    box: Box
    camera_crop: Optional[Tuple[float, float, float, float]]
    source: str
    confidence: float


def valid_bounds(image: AnalysisImage) -> Tuple[int, int, int, int]:
    """Pixel bounds of real picture content: the camera frame inside RAW output.

    Some RAW outputs (e.g. Sony M-size) carry black padding outside the camera
    frame; its hard border would otherwise read as a perfectly sharp edge.
    """
    H, W = image.gray.shape[:2]
    crop = image.camera_crop
    if not crop:
        return 0, 0, W, H
    x1, y1 = int(np.ceil(crop[0] * W)), int(np.ceil(crop[1] * H))
    x2, y2 = int(np.floor(crop[2] * W)), int(np.floor(crop[3] * H))
    if x2 - x1 < 32 or y2 - y1 < 32:
        return 0, 0, W, H
    return x1, y1, x2, y2


def _resize_long_edge(img: np.ndarray, long_edge: int) -> Tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    scale = min(1.0, float(long_edge) / float(max(h, w)))
    if scale >= 1.0:
        return img, 1.0
    return cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA), scale


def _window_detection(det: BirdDetection, box_full: Box, window, scale: float, small_shape) -> BirdDetection:
    """A zoomed-window detection expressed like a whole-frame one (detection-image coordinates)."""
    mask = None
    if det.mask is not None:
        x1, y1, x2, y2 = window
        sh, sw = small_shape
        sx1, sy1 = int(round(x1 * scale)), int(round(y1 * scale))
        sx2, sy2 = min(sw, max(sx1 + 1, int(round(x2 * scale)))), min(sh, max(sy1 + 1, int(round(y2 * scale))))
        part = cv2.resize(np.asarray(det.mask, np.float32), (sx2 - sx1, sy2 - sy1), interpolation=cv2.INTER_AREA)
        mask = np.zeros((sh, sw), np.uint8)
        mask[sy1:sy2, sx1:sx2] = (part >= 0.5).astype(np.uint8)
    return BirdDetection(det.confidence, tuple(v * scale for v in box_full), mask, FOUND_FOCUS_ZOOM)


def _focus_part(det: BirdDetection, focus_px: Optional[Box], scale: float) -> BirdDetection:
    """Keep only the mask piece on the focus box (the largest without one), and fit the box to it.

    Recheck masks often carry stray patches of sky and branches next to the bird
    (DSC05167); their edges would be measured as the bird's.
    """
    if det.mask is None:
        return det
    mask = (np.asarray(det.mask) > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 2:
        return det
    h, w = mask.shape[:2]
    on_focus = np.zeros(count, np.int64)
    if focus_px is not None:
        fx1, fy1 = max(0, int(focus_px[0] * scale)), max(0, int(focus_px[1] * scale))
        fx2, fy2 = min(w, int(np.ceil(focus_px[2] * scale)) + 1), min(h, int(np.ceil(focus_px[3] * scale)) + 1)
        on_focus = np.bincount(labels[fy1:fy2, fx1:fx2].ravel(), minlength=count)
        on_focus[0] = 0
    best = int(np.argmax(on_focus)) if on_focus.max() > 0 else 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh = (int(v) for v in stats[best, :4])
    b = det.box
    box = (max(float(x), b[0]), max(float(y), b[1]), min(float(x + bw), b[2]), min(float(y + bh), b[3]))
    if box[2] <= box[0] or box[3] <= box[1]:
        box = (float(x), float(y), float(x + bw), float(y + bh))
    return replace(det, mask=(labels == best).astype(np.uint8), box=box)


class BirdSharpnessAnalyzer:
    """Reusable analyzer; keep one instance per worker so models load once.

    ``focus_provider(path, display_w, display_h)`` returns the camera focus box in
    normalised display coordinates (see :mod:`bird_sharpness.focus`).
    """

    def __init__(self, models: Optional[BirdSharpnessModels] = None, *,
                 focus_provider: Optional[FocusProvider] = None):
        self.models = models or BirdSharpnessModels()
        self.focus_provider = focus_provider or default_focus_box

    def load(self) -> None:
        self.models.load()

    def release(self) -> None:
        self.models.release()

    def analyze(self, path: str, *, on_stage: Optional[Callable[[str], None]] = None,
                cancelled: Callable[[], bool] = lambda: False, tracer=None) -> BirdSharpnessResult:
        """Analyse one photo. ``tracer`` (:class:`~bird_sharpness.trace.AnalysisTracer`)
        records every key step with the exact data used; ``None`` costs nothing."""
        t0 = time.perf_counter()
        try:
            result = self._analyze(path, on_stage or (lambda stage: None), cancelled, tracer)
        except Exception as exc:
            _log.error("[BirdSharpness] analysis failed path=%r: %s", path, traceback.format_exc())
            result = BirdSharpnessResult(path=path, verdict=VERDICT_ERROR, error=f"{type(exc).__name__}: {exc}")
        result.elapsed_s = round(time.perf_counter() - t0, 3)
        if tracer is not None and getattr(tracer, "trace", None) is not None and result.ok:
            tracer.result(result)
        return result

    # ── pipeline ──────────────────────────────────────────────────────────
    def _analyze(self, path: str, on_stage: Callable[[str], None], cancelled, tracer=None) -> BirdSharpnessResult:
        on_stage(STAGE_DECODE)
        t_decode = time.perf_counter()
        image = load_analysis_image(path)
        focus_px = _UNSET = object()
        if tracer is not None:
            focus_px = self._focus_box_px(path, image)
            tracer.decode(path, image, focus_px, decode_s=time.perf_counter() - t_decode)
        on_stage(STAGE_DETECT)
        H, W = image.gray.shape[:2]
        small, scale = _resize_long_edge(image.rgb8, DETECT_LONG_EDGE)
        small_bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
        detections = dedupe_detections(self.models.detect_birds(small_bgr, imgsz=DETECT_IMGSZ))[:MAX_BIRDS]
        if tracer is not None:
            tracer.detect(detections, scale, has_masks=bool(getattr(self.models, "has_masks", True)),
                          has_keypoints=bool(getattr(self.models, "has_keypoints", True)))
        if not detections and not cancelled():
            if focus_px is _UNSET:
                focus_px = self._focus_box_px(path, image)
            recheck = self._recheck(image, small_bgr, scale, focus_px, cancelled)
            detections = recheck.accepted
            if tracer is not None:
                tracer.recheck(recheck)
        on_stage(STAGE_MEASURE)
        if detections:
            birds = [self._measure_bird(image, det, scale, index=i, tracer=tracer) for i, det in enumerate(detections)]
            excluded = excluded_birds(birds)
            kept = [b for i, b in enumerate(birds) if i not in excluded]
            if tracer is not None:
                tracer.mark_best(birds.index(max(kept, key=BirdMeasurement.rank)), excluded=excluded)
            return self._bird_result(path, kept, max(H, W))
        return self._no_bird_result(path, image, cancelled, tracer,
                                    focus_px=None if focus_px is _UNSET else focus_px,
                                    focus_known=focus_px is not _UNSET)

    def _recheck(self, image: AnalysisImage, small_bgr, scale: float, focus_px: Optional[Box],
                 cancelled) -> Recheck:
        """Second look when the first pass found no bird (see the FOCUS_* notes above)."""
        H, W = image.gray.shape[:2]
        check = Recheck(None if focus_px is None else tuple(focus_px))
        lifted, gamma = lift_midtones(small_bgr, bgr=True)
        if gamma < LIFT_DARK_GAMMA:
            check.lift_gamma = gamma
            found = self.models.detect_birds(lifted, imgsz=DETECT_IMGSZ)
            if found:
                check.accepted = [_focus_part(replace(d, source=FOUND_FULL_LIFTED), focus_px, scale)
                                  for d in dedupe_detections(found)[:MAX_BIRDS]]
                return check
            if cancelled():
                return check
        candidates = self.models.detect_birds(small_bgr, conf=FOCUS_CANDIDATE_CONFIDENCE, imgsz=RECHECK_IMGSZ)
        confident = [replace(d, source=FOUND_FULL_FINE) for d in candidates if d.confidence >= BIRD_CONFIDENCE_MIN]
        if confident:
            check.accepted = dedupe_detections(confident)[:MAX_BIRDS]
            return check
        if focus_px is None:
            return check
        near = []
        for det in candidates:
            box = tuple(v / scale for v in det.box)
            if _intersection(box, focus_px) > 0:
                near.append((det, box))
        check.candidates = [(d.confidence, b) for d, b in near]
        # One bird at the focus point: a weak candidate split in head and body must not count twice.
        weak = [d for d, b in near
                if d.confidence >= FOCUS_WEAK_CONFIDENCE and box_overlap(b, focus_px) >= FOCUS_WEAK_OVERLAP]
        if weak:
            best = max(weak, key=lambda d: d.confidence)
            check.accepted = [_focus_part(replace(best, source=FOUND_FOCUS_WEAK), focus_px, scale)]
            return check
        anchors = [b for _, b in near if box_overlap(b, focus_px) >= FOCUS_ZOOM_OVERLAP]
        if not anchors:
            return check  # nothing bird-like at the focus point: skip the zoomed inferences
        bounds = valid_bounds(image)
        small_shape = small_bgr.shape[:2]
        for divisor in FOCUS_ZOOM_DIVISORS:
            if cancelled():
                break
            win = zoom_window(focus_px, bounds, max(H, W) / divisor)
            x1, y1, x2, y2 = win
            crop = cv2.cvtColor(np.ascontiguousarray(image.rgb8[y1:y2, x1:x2]), cv2.COLOR_RGB2BGR)
            found = []
            for det in self.models.detect_birds(crop, conf=BIRD_CONFIDENCE_MIN, imgsz=FOCUS_ZOOM_IMGSZ):
                box = (det.box[0] + x1, det.box[1] + y1, det.box[2] + x1, det.box[3] + y1)
                ok = (box_overlap(box, focus_px) >= FOCUS_ZOOM_OVERLAP
                      and any(box_iou(box, a) >= FOCUS_ZOOM_AGREEMENT_IOU for a in anchors))
                found.append((det, box, ok))
            check.windows.append({"box": win, "divisor": divisor,
                                  "detections": [(d.confidence, b, ok) for d, b, ok in found]})
            accepted = [(d, b) for d, b, ok in found if ok]
            if accepted:
                det, box = max(accepted, key=lambda item: item[0].confidence)
                check.accepted = [_focus_part(_window_detection(det, box, win, scale, small_shape), focus_px, scale)]
                break
        return check

    def find_missed_bird(self, path: str, *, cancelled: Callable[[], bool] = lambda: False) -> Optional[MissedBird]:
        """Run only the no-bird recheck, for callers with their own first pass.

        SuperViewer's preview bird box detects on a small embedded preview; when
        that finds nothing it asks here, so camouflaged birds get the same
        calibrated rules (full-resolution decode for the zoomed windows).
        """
        image = load_analysis_image(path)
        if cancelled():
            return None
        H, W = image.gray.shape[:2]
        small, scale = _resize_long_edge(image.rgb8, DETECT_LONG_EDGE)
        focus_px = self._focus_box_px(path, image)
        check = self._recheck(image, cv2.cvtColor(small, cv2.COLOR_RGB2BGR), scale, focus_px, cancelled)
        if not check.accepted or cancelled():
            return None
        det = max(check.accepted, key=lambda d: d.confidence * _area(d.box))
        x1, y1, x2, y2 = (v / scale for v in det.box)
        return MissedBird((x1 / W, y1 / H, x2 / W, y2 / H), image.camera_crop, det.source, float(det.confidence))

    def _bird_result(self, path: str, birds: List[BirdMeasurement], long_edge: int) -> BirdSharpnessResult:
        best = max(birds, key=BirdMeasurement.rank)
        return BirdSharpnessResult(
            path=path,
            verdict=best.verdict,
            score=best.score,
            sigma=best.sigma,
            region=fields.REGION_BIRD,
            bird_count=len(birds),
            head_sigma=best.head_sigma,
            body_sigma=best.body_sigma,
            motion_ratio=best.motion_ratio,
            eye_visibility=best.eye_visibility,
            bird_confidence=round(best.confidence, 3),
            bird_box=best.box,
            eye_xy=best.eye_xy,
            head_radius=best.head_radius,
            head_edges=best.head_edges,
            region_box=best.box,
            birds=[asdict(b) for b in birds],
            image_long_edge=long_edge,
        )

    def _measure_bird(self, image: AnalysisImage, det: BirdDetection, scale: float, *, index: int = 0,
                      tracer=None) -> BirdMeasurement:
        H, W = image.gray.shape[:2]
        bx1, by1, bx2, by2 = (v / scale for v in det.box)
        x1, y1 = max(0, int(bx1)), max(0, int(by1))
        x2, y2 = min(W, int(np.ceil(bx2))), min(H, int(np.ceil(by2)))
        bw, bh = max(1, x2 - x1), max(1, y2 - y1)
        pad = int(CROP_PAD_RATIO * max(bw, bh))
        X1, Y1, X2, Y2 = max(0, x1 - pad), max(0, y1 - pad), min(W, x2 + pad), min(H, y2 + pad)
        cw, ch = X2 - X1, Y2 - Y1

        # This bird's pixels in ROI coordinates: its segmentation mask, else the core of its box.
        if det.mask is not None:
            mh, mw = det.mask.shape[:2]
            sx, sy = mw / float(W), mh / float(H)
            mx1, my1 = int(np.floor(X1 * sx)), int(np.floor(Y1 * sy))
            mx2, my2 = max(mx1 + 1, int(np.ceil(X2 * sx))), max(my1 + 1, int(np.ceil(Y2 * sy)))
            mask = cv2.resize(det.mask[my1:my2, mx1:mx2], (cw, ch), interpolation=cv2.INTER_NEAREST)
            # Detection-resolution masks may spill into a neighbour; keep this bird's box only.
            clip = np.zeros_like(mask)
            clip[y1 - Y1:y2 - Y1, x1 - X1:x2 - X1] = 1
            mask = (mask & clip).astype(np.uint8)
        else:
            mask = np.zeros((ch, cw), np.uint8)
            ix, iy = int(BOX_INSET_RATIO * bw), int(BOX_INSET_RATIO * bh)
            mask[y1 - Y1 + iy:y2 - Y1 - iy, x1 - X1 + ix:x2 - X1 - ix] = 1
        bird_px = mask.astype(bool)

        field_ = EdgeBlurField(image.gray[Y1:Y2, X1:X2])
        body = cv2.erode(mask, np.ones((BODY_MASK_ERODE_PX, BODY_MASK_ERODE_PX), np.uint8)).astype(bool)
        if not body.any():
            body = bird_px
        body_stats, motion_ratio, body_detail = field_.body_blur_detail(body)

        keypoints = self.models.keypoints(np.ascontiguousarray(image.rgb8[Y1:Y2, X1:X2]))
        eye_vis = None
        eye_abs = None
        radius = None
        head_stats = None
        head = None
        selection = None
        trace_keypoints = None
        if keypoints is not None:
            coords, vis = keypoints
            pts = coords * np.array([cw, ch], np.float32)  # left eye, right eye, beak
            eye_idx = 0 if vis[0] >= vis[1] else 1
            eye_vis = float(vis[eye_idx])
            eye = pts[eye_idx]
            if vis[2] >= BEAK_VISIBLE_MIN:
                radius = HEAD_RADIUS_BEAK_RATIO * float(np.linalg.norm(eye - pts[2]))
            else:
                radius = HEAD_RADIUS_BOX_RATIO * max(bw, bh)
            radius = max(radius, HEAD_RADIUS_MIN_PX)
            trace_keypoints = (pts, vis, radius)
            if eye_vis >= EYE_VISIBLE_MIN:
                yy, xx = np.ogrid[:ch, :cw]
                dilated = cv2.dilate(mask, np.ones((HEAD_MASK_DILATE_PX, HEAD_MASK_DILATE_PX), np.uint8)).astype(bool)
                head = ((xx - eye[0]) ** 2 + (yy - eye[1]) ** 2 <= radius ** 2) & dilated
                selection = field_.select_strongest_edges(head)
                head_stats = edge_stats(selection.sigma)
            eye_abs = (round(float(eye[0] + X1), 1), round(float(eye[1] + Y1), 1))
            head_sigma = head_stats.sigma if head_stats is not None else None
            head_blank = head_stats is not None and head_sigma is None
            verdict, score = classify(head_sigma, body_stats.sigma, motion_ratio,
                                      eye_visible=eye_vis >= EYE_VISIBLE_MIN, head_blank=head_blank)
            if head_blank:
                sigma = blank_head_sigma(body_stats.sigma)
            else:
                sigma = head_sigma if head_sigma is not None else body_stats.sigma
        else:
            # No eye model: the whole bird's strongest edges stand in for the head.
            selection = field_.select_strongest_edges(bird_px)
            head_stats = edge_stats(selection.sigma)
            head_sigma = None
            sigma = head_stats.sigma
            verdict, score = classify(sigma, body_stats.sigma, motion_ratio, eye_visible=True,
                                      head_blank=sigma is None)
            if sigma is None:
                sigma = blank_head_sigma(body_stats.sigma)
        measurement = BirdMeasurement(
            verdict=verdict,
            score=score,
            sigma=_r3(sigma),
            head_sigma=_r3(head_sigma),
            body_sigma=_r3(body_stats.sigma),
            motion_ratio=None if motion_ratio is None else round(float(motion_ratio), 2),
            eye_visibility=None if eye_vis is None else round(eye_vis, 2),
            confidence=float(det.confidence),
            box=(x1, y1, x2, y2),
            eye_xy=eye_abs,
            head_radius=None if radius is None else round(float(radius), 1),
            head_edges=head_stats.edge_count if head_stats is not None else 0,
            masked=det.mask is not None,
            found_by=getattr(det, "source", FOUND_FULL),
            index=index,
        )
        if tracer is not None:
            tracer.bird(index, image, (X1, Y1, X2, Y2), bird_px, body, head, trace_keypoints, selection,
                        body_detail, measurement)
        return measurement

    def _focus_box_px(self, path: str, image: AnalysisImage) -> Optional[Tuple[float, float, float, float]]:
        """Camera focus box mapped onto the decoded pixels (RAW sensor margins included)."""
        H, W = image.gray.shape[:2]
        crop = image.camera_crop
        disp_w = max(1, int(round(W * (crop[2] - crop[0])))) if crop else W
        disp_h = max(1, int(round(H * (crop[3] - crop[1])))) if crop else H
        try:
            box = self.focus_provider(path, disp_w, disp_h)
        except Exception as exc:
            _log.debug("[BirdSharpness] focus provider failed path=%r: %s", path, exc)
            return None
        if not box:
            return None
        if crop:
            from app_common.raw_preview_geometry import map_camera_focus_box

            box = map_camera_focus_box(box, crop) or None
            if box is None:
                return None
        l, t, r, b = (float(v) for v in box)
        if not (0.0 <= l < r <= 1.0 and 0.0 <= t < b <= 1.0):
            return None
        return (l * W, t * H, r * W, b * H)

    def _no_bird_result(self, path: str, image: AnalysisImage, cancelled, tracer=None, *,
                        focus_px=None, focus_known: bool = False) -> BirdSharpnessResult:
        H, W = image.gray.shape[:2]
        vx1, vy1, vx2, vy2 = valid_bounds(image)
        region, region_box, stats = "", None, None
        if not focus_known:
            focus_px = self._focus_box_px(path, image)
        if focus_px is not None:
            # The window stays inside the camera frame (shifted, not shrunk).
            wx1, wy1, wx2, wy2 = focus_window(
                (focus_px[0] - vx1, focus_px[1] - vy1, focus_px[2] - vx1, focus_px[3] - vy1), vx2 - vx1, vy2 - vy1)
            fx1, fy1, fx2, fy2 = wx1 + vx1, wy1 + vy1, wx2 + vx1, wy2 + vy1
            selection = EdgeBlurField(image.gray[fy1:fy2, fx1:fx2]).select_strongest_edges(None)
            stats = edge_stats(selection.sigma)
            region, region_box = fields.REGION_FOCUS, (fx1, fy1, fx2, fy2)
            if tracer is not None:
                tracer.focus_window(image, region_box, selection, stats)
        if stats is None or stats.sigma is None:
            # No focus point (or nothing measurable there): default whole-image measurement.
            tiles = [] if tracer is not None else None
            stats = full_image_blur(
                image.gray[vy1:vy2, vx1:vx2], cancelled=cancelled,
                tile_report=None if tiles is None else
                (lambda x, y, w, h, part: tiles.append((x + vx1, y + vy1, w, h, part))))
            region, region_box = fields.REGION_FULL, (vx1, vy1, vx2, vy2)
            if tracer is not None:
                tracer.full_image(tiles, stats)
        return BirdSharpnessResult(
            path=path,
            verdict=VERDICT_NO_BIRD,
            score=sigma_to_score(stats.sigma),
            sigma=_r3(stats.sigma),
            region=region,
            bird_count=0,
            head_edges=stats.edge_count,
            region_box=region_box,
            image_long_edge=max(H, W),
        )


ProgressCallback = Callable[[int, int, BirdSharpnessResult], None]


def analyze_paths(
    paths: Iterable[str],
    *,
    analyzer: Optional[BirdSharpnessAnalyzer] = None,
    cancel_event: Optional[threading.Event] = None,
    on_result: Optional[ProgressCallback] = None,
    workers: int = 1,
) -> List[BirdSharpnessResult]:
    """Analyze files, ``workers`` at a time; results are returned in input order.

    ``on_result`` runs on the calling thread in completion order. Stops submitting
    new files once ``cancel_event`` is set and waits for the ones in flight.
    """
    items = [os.path.normpath(p) for p in paths]
    analyzer = analyzer or BirdSharpnessAnalyzer()
    total = len(items)
    by_index: Dict[int, BirdSharpnessResult] = {}

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def report(result: BirdSharpnessResult) -> None:
        if on_result is not None:
            on_result(len(by_index), total, result)

    if workers <= 1:
        for index, path in enumerate(items):
            if cancelled():
                break
            by_index[index] = analyzer.analyze(path)
            report(by_index[index])
    else:
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        analyzer.load()
        pending = {}
        next_index = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bird-sharpness") as executor:
            while pending or (next_index < total and not cancelled()):
                while next_index < total and len(pending) < workers * 2 and not cancelled():
                    pending[executor.submit(analyzer.analyze, items[next_index])] = next_index
                    next_index += 1
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    by_index[index] = future.result()
                    report(by_index[index])
    return [by_index[i] for i in sorted(by_index)]
