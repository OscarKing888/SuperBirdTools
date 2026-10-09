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
   Optional (``params.AnalysisParams``): another detector, an enhanced search in
   zoomed windows when still no bird is found, SAM2 mask refinement;
5. no bird, no focus point, manual focus: the sharpest small tiles of the frame
   centre (the focal plane, not a bird; see ``metrics.TileOptions``);
   otherwise the whole image (tiled);
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

from .focus import FocusProvider, ManualFocusProvider, default_focus_box, default_manual_focus, focus_window
from .image_source import SOURCE_RAW, AnalysisImage, load_analysis_image, source_loader
from .metrics import (ESTIMATOR_STANDARD, EdgeBlurField, EdgeEstimator, TileOptions,
                      edge_estimator as get_edge_estimator, edge_stats, full_image_blur, sharpest_tiles_blur,
                      specular_highlights)
from .models import (BIRD_CONFIDENCE_MIN, FOUND_ENHANCED, FOUND_FOCUS_WEAK, FOUND_FOCUS_ZOOM, FOUND_FULL, FOUND_GIVEN,
                     FOUND_FULL_FINE, FOUND_FULL_LIFTED, FOUND_FULL_SMALL, BirdDetection, BirdSharpnessModels,
                     shared_models)
from .params import ENH_MANUAL, ENH_NOBIRD, ENH_OFF, PIXELS_BOX, SAM_SCOPE_ALL, AnalysisParams
from .preview import MASK_FILL, MASK_FILL_GRAY
from .scoring import ALGORITHM_VERSION, VERDICT_ERROR, VERDICT_NO_BIRD, blank_head_sigma, classify, sigma_to_score
from .timing import STAGE_DECODE, STAGE_DETECT, STAGE_MEASURE, STAGE_RECHECK, StageClock

_log = get_logger("bird_sharpness")

DETECT_LONG_EDGE = 1024  # detection copy
DETECT_IMGSZ = 640  # first-pass network input (the size the YOLO models are trained at)
RECHECK_IMGSZ = 1024  # second pass when the first finds no bird: small, dark birds score higher
# Birds measured per photo; 0 = no limit. Apps may set a limit (SuperViewer user option);
# birds touching the camera focus box are measured first when the limit cuts.
DEFAULT_MAX_BIRDS = 0
CROP_PAD_RATIO = 0.15
EYE_VISIBLE_MIN = 0.5
BEAK_VISIBLE_MIN = 0.3
HEAD_RADIUS_BEAK_RATIO = 1.2
HEAD_RADIUS_BOX_RATIO = 0.15
HEAD_RADIUS_MIN_PX = 40
# Mirror check for the eye/beak model. The bird is located again in the mirrored
# crop; where both runs put the eye within EYE_MIRROR_MAX of the bird's size the
# two are averaged, otherwise the head position is not trusted and the whole bird
# is measured. Of 484 birds (2026-10 shorebirds, Century Park cuckoo-hawks) about
# 20% disagreed, half of those with the eye on the body or another bird
# (flight, head down, crowds); all checked agreeing ones were on the head, down
# to ~200 px birds. Small size alone is not the cause: well-placed eyes stay
# within ~2% of the bird when it is shrunk to 150 px.
EYE_MIRROR_MAX = 0.10
BEAK_MIRROR_MAX = 0.15
# Small birds: the head σ is the median over the head circle and six variants
# (centre shifted ±20% of the radius each way, radius ×0.8 and ×1.25), so a few
# pixels of eye error move it less. On 116 shorebird heads (two plausible eye
# positions each: the crop's and its mirror's) this cut the typical σ difference
# by ~20–50% without shifting σ, but did not reduce verdict flips below 300 px:
# the remaining spread comes from the few (~30) edges a small head has.
SMALL_BIRD_SIDE = 450  # bird box long side, px
HEAD_SAMPLE_SHIFT = 0.2
HEAD_SAMPLE_VARIANTS = ((0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (-1.0, 0.0, 1.0), (0.0, 1.0, 1.0), (0.0, -1.0, 1.0),
                        (0.0, 0.0, 0.8), (0.0, 0.0, 1.25))
# Head and whole-bird measurements use the mask shrunk by about one detection-mask
# pixel (masks come from the 1024 px detection copy, so their outline is that
# coarse): a dilated mask let background bark cracks at the outline supply the
# head's sharpest edges (DSC06285: 13 of 38). Silhouette edges are lost with it;
# the feathers, eye and beak inside remain.
HEAD_MASK_ERODE_MIN_PX = 4
HEAD_MASK_ERODE_MAX_PX = 12
BODY_MASK_ERODE_PX = 15
BOX_INSET_RATIO = 0.08  # detection boxes include background; measure their core
# Progress stages reported through ``analyze(..., on_stage=...)`` are the timed stages of :mod:`.timing`.


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
    head_samples: Optional[List[float]] = None  # small birds: head σ of each circle variant (median used)
    eye_mirror_gap: Optional[float] = None  # eye distance between crop and mirrored crop / bird size
    eye_reliable: Optional[bool] = None
    index: int = 0  # detection number (trace "鸟 #index+1"), kept when false extras are dropped
    refined_by: str = ""  # SAM model whose mask replaced the detector's ("" = detector mask/box)
    grey_filled: bool = False  # the crop was painted grey outside the bird before measuring (params.grey_fill)

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
    stage_s: Dict[str, float] = dc_field(default_factory=dict)  # seconds per stage of this analysis (timing.STAGES)
    error: str = ""
    version: str = ALGORITHM_VERSION
    edge_estimator: str = ESTIMATOR_STANDARD.key
    detector: str = ""   # detector file actually used (JSON / trace only; the version carries non-defaults)
    sam_model: str = ""  # SAM model used for refinement, "" = none

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


def detection_mask_overlap(a: BirdDetection, b: BirdDetection) -> Optional[float]:
    """Intersection / smaller silhouette, or None without two usable masks.

    Masks cover the same whole frame, but the flock pass keeps first-pass masks
    at their original resolution. Compare at the smaller resolution without
    mutating either detection or changing the pixels used for measurement.
    """
    if a.mask is None or b.mask is None:
        return None
    ma, mb = np.asarray(a.mask), np.asarray(b.mask)
    if ma.ndim != 2 or mb.ndim != 2 or not ma.size or not mb.size:
        return None
    shape = (min(ma.shape[0], mb.shape[0]), min(ma.shape[1], mb.shape[1]))
    masks = []
    for mask in (ma, mb):
        mask = (mask > 0).astype(np.uint8)
        if mask.shape != shape:
            mask = cv2.resize(mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
        masks.append(mask.astype(bool))
    smaller = min(np.count_nonzero(masks[0]), np.count_nonzero(masks[1]))
    if not smaller:
        return None
    return float(np.count_nonzero(masks[0] & masks[1]) / smaller)


def dedupe_detections(detections: List[BirdDetection]) -> List[BirdDetection]:
    """Merge contained duplicates, retaining separate silhouettes of a flock.

    Segmentation often returns one bird twice: the whole bird and a part of it
    (DSC04512: whole bird + upper half), in either confidence order; both would
    measure the same head and inflate the bird count. In the 2026-10-02 set this
    was 24 of 28 "multi-bird" frames. With segmentation, also require silhouette
    containment: DSC00462's small bird is inside the large bird's box but shares
    none of its pixels. Without usable masks retain the box-only fallback.
    Input order determines priority (normal detection: confidence x box area;
    flock pass: first-pass birds first; enhanced search: uncut whole birds first).
    """
    def duplicate(det, other):
        if box_overlap(det.box, other.box) < DUPLICATE_CONTAINMENT:
            return False
        overlap = detection_mask_overlap(det, other)
        return overlap is None or overlap >= DUPLICATE_CONTAINMENT

    kept: List[BirdDetection] = []
    for det in detections:
        if not any(duplicate(det, other) for other in kept):
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


@dataclass
class GivenBird:
    """A bird handed to :meth:`BirdSharpnessAnalyzer.analyze` (``given``), in the loaded image's
    pixels: its box and, when known, its pixels (bool/uint8 mask of the whole image)."""

    box: Box
    mask: Optional[np.ndarray] = None
    confidence: float = 1.0


@dataclass
class GivenBirds:
    birds: List[GivenBird]
    label: str = ""  # shown as the step's 识别模型 (e.g. which model chain window)
    filled: bool = False  # the temporary image is grey outside the birds (comparison only, reads sharper)


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


# Enhanced bird search (params.EnhancedSearch): zoomed overlapping windows over the
# centre region when every other pass found no bird. Candidates down to
# ENH_CANDIDATE_CONFIDENCE are recorded for the trace; only those at the user's
# threshold are taken.
ENH_CANDIDATE_CONFIDENCE = 0.10
ENH_WINDOW_OVERLAP = 0.25


@dataclass
class EnhancedPass:
    """What the enhanced search looked at (full-resolution boxes) and what it took."""

    region: Tuple[int, int, int, int]
    min_conf: float
    imgsz: int
    gamma: Optional[float] = None  # set when dark mid-tones were lifted
    manual: Optional[bool] = None  # manual focus (when the mode asked)
    windows: List[dict] = dc_field(default_factory=list)  # {"box", "detections": [(conf, box, accepted)]}
    accepted: List[BirdDetection] = dc_field(default_factory=list)  # detection-image coordinates


def _cut_by_window(box, window, bounds, margin: float = 3.0) -> bool:
    """Whether ``box`` touches a border of ``window`` that is not also the picture's edge."""
    x1, y1, x2, y2 = window
    bx1, by1, bx2, by2 = bounds
    return ((box[0] <= x1 + margin and x1 > bx1) or (box[1] <= y1 + margin and y1 > by1)
            or (box[2] >= x2 - margin and x2 < bx2) or (box[3] >= y2 - margin and y2 < by2))


def enhanced_windows(region, grid: int, overlap: float = ENH_WINDOW_OVERLAP) -> List[Tuple[int, int, int, int]]:
    """``grid`` x ``grid`` windows covering ``region`` with ``overlap`` between neighbours."""
    x1, y1, x2, y2 = region
    rw, rh = x2 - x1, y2 - y1
    grid = max(1, int(grid))
    ww = rw / (1.0 + (grid - 1) * (1.0 - overlap))
    wh = rh / (1.0 + (grid - 1) * (1.0 - overlap))
    out = []
    for j in range(grid):
        for i in range(grid):
            wx = x1 + (0.0 if grid == 1 else i * (rw - ww) / (grid - 1))
            wy = y1 + (0.0 if grid == 1 else j * (rh - wh) / (grid - 1))
            out.append((int(round(wx)), int(round(wy)), int(round(wx + ww)), int(round(wy + wh))))
    return out


# Small birds (flocks): the first pass sees the 1024 px copy through a 640 px
# network input, so a shorebird 20-50 px long there is ~15-30 px to the network
# and most of a flock is missed (DSC00925: 13 of ~60 found; 59 at 2048 px).
# When any first-pass bird is shorter than FLOCK_BIRD_SIDE (in the 1024 px copy),
# a 2048 px copy is detected at 2048 px input and merged with the first pass.
# Calibration: never triggered on 326 bird frames of the 2026-10-02 set (perched
# hawks, median 232 px); triggered on 173 of 195 shorebird-flock frames.
FLOCK_BIRD_SIDE = 64  # px in the 1024 px detection copy (not SMALL_BIRD_SIDE, full-res px)
SMALL_DETECT_LONG_EDGE = 2048
SMALL_DETECT_IMGSZ = 2048


def _scaled_detection(det: BirdDetection, factor: float) -> BirdDetection:
    """A first-pass detection with its box in the high-resolution copy's pixels.

    The mask keeps its own resolution: measurement maps masks by their shape, so
    first-pass birds measure exactly as without the high-resolution pass.
    """
    return replace(det, box=tuple(v * factor for v in det.box))


def has_small_birds(detections: List[BirdDetection]) -> bool:
    return any(max(d.box[2] - d.box[0], d.box[3] - d.box[1]) < FLOCK_BIRD_SIDE for d in detections)


def prefer_focus_birds(detections: List[BirdDetection], focus_px: Optional[Box], scale: float) -> List[BirdDetection]:
    """Birds touching the camera focus box first, the rest in their original order.

    Used before the max_birds cut: detections are ranked by confidence x area,
    so the small bird the photographer focused on would otherwise be the one
    dropped from a flock (DSC00859: 9 birds, the focused one ranked last).
    """
    if focus_px is None:
        return list(detections)
    on_focus = [_intersection(tuple(v / scale for v in d.box), focus_px) > 0 for d in detections]
    return ([d for d, hit in zip(detections, on_focus) if hit]
            + [d for d, hit in zip(detections, on_focus) if not hit])


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


def _no_denoised_lookup(_path: str) -> None:
    """Without the app's denoise output settings no denoised rendering can be found."""
    return None


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


@dataclass
class HeadKeypoints:
    """Eye/beak model output for one bird crop, checked against its mirror image."""

    pts: np.ndarray  # (3, 2) left eye, right eye, beak in crop pixels (the used eye/beak averaged)
    vis: np.ndarray  # (3,) visibility
    eye_index: int
    mirror_pts: Optional[np.ndarray] = None  # same, from the mirrored crop mapped back
    eye_gap: Optional[float] = None  # / bird size
    beak_gap: Optional[float] = None

    @property
    def eye(self) -> np.ndarray:
        return self.pts[self.eye_index]

    @property
    def eye_visibility(self) -> float:
        return float(self.vis[self.eye_index])

    @property
    def eye_reliable(self) -> bool:
        return self.eye_gap is None or self.eye_gap <= EYE_MIRROR_MAX

    @property
    def beak_usable(self) -> bool:
        return self.vis[2] >= BEAK_VISIBLE_MIN and (self.beak_gap is None or self.beak_gap <= BEAK_MIRROR_MAX)


def locate_head(models, crop: np.ndarray, bird_size: float) -> Optional[HeadKeypoints]:
    """Eye and beak in ``crop``, located on the crop and on its mirror image (see EYE_MIRROR_MAX)."""
    found = models.keypoints(np.ascontiguousarray(crop))
    if found is None:
        return None
    h, w = crop.shape[:2]
    coords, vis = found
    pts = np.asarray(coords, np.float32) * np.array([w, h], np.float32)
    vis = np.asarray(vis, np.float32).copy()
    eye_index = 0 if vis[0] >= vis[1] else 1
    mirrored = models.keypoints(np.ascontiguousarray(crop[:, ::-1]))
    if mirrored is None:
        return HeadKeypoints(pts, vis, eye_index)
    mcoords, mvis = mirrored
    mpts = np.asarray(mcoords, np.float32) * np.array([w, h], np.float32)
    mpts[:, 0] = w - mpts[:, 0]
    mpts, mvis = mpts[[1, 0, 2]], np.asarray(mvis, np.float32)[[1, 0, 2]]  # the bird's left eye is now on the right
    meye = 0 if mvis[0] >= mvis[1] else 1
    size = max(1.0, float(bird_size))
    eye_gap = float(np.linalg.norm(pts[eye_index] - mpts[meye])) / size
    beak_gap = float(np.linalg.norm(pts[2] - mpts[2])) / size
    head = HeadKeypoints(pts.copy(), vis.copy(), eye_index, mpts, round(eye_gap, 3), round(beak_gap, 3))
    if head.eye_reliable:
        head.pts[eye_index] = (pts[eye_index] + mpts[meye]) / 2.0
        head.vis[eye_index] = (vis[eye_index] + mvis[meye]) / 2.0
    if beak_gap <= BEAK_MIRROR_MAX:
        head.pts[2] = (pts[2] + mpts[2]) / 2.0
        head.vis[2] = (vis[2] + mvis[2]) / 2.0
    return head


class BirdSharpnessAnalyzer:
    """Reusable analyzer; keep one instance per worker so models load once.

    ``focus_provider(path, display_w, display_h)`` returns the camera focus box in
    normalised display coordinates (see :mod:`bird_sharpness.focus`).
    """

    def __init__(self, models: Optional[BirdSharpnessModels] = None, *,
                 focus_provider: Optional[FocusProvider] = None, max_birds: Optional[int] = None,
                 edge_estimator: Optional[str] = None, tile_options: Optional[TileOptions] = None,
                 manual_focus_provider: Optional[ManualFocusProvider] = None,
                 params: Optional[AnalysisParams] = None, refiner_provider=None,
                 denoised_lookup: Optional[Callable[[str], object]] = None):
        """``params`` holds every option (see :mod:`bird_sharpness.params`); ``max_birds``,
        ``edge_estimator`` and ``tile_options`` override it. Without ``models`` the
        process-wide models of ``params.detector`` are used (shared with other analyzers).
        ``denoised_lookup(path)`` finds a photo's denoised rendering for ``params.image_source``
        ``denoised`` (see :func:`bird_sharpness.image_source.source_loader`)."""
        base = params or AnalysisParams()
        overrides = {k: v for k, v in (("max_birds", max_birds), ("edge_estimator", edge_estimator),
                                       ("tiles", tile_options)) if v is not None}
        self._params = replace(base, **overrides).normalized()
        self._explicit_models = models is not None
        self.models = models if models is not None else shared_models(self._params.detector)
        self.focus_provider = focus_provider or default_focus_box
        self.manual_focus_provider = manual_focus_provider or default_manual_focus
        if refiner_provider is None:
            from .refine import shared_refiner as refiner_provider
        self.refiner_provider = refiner_provider  # SAM model name -> refiner with .mask(rgb, box)
        self.denoised_lookup = denoised_lookup

    # Options are read per photo, so apps may change them between jobs.
    @property
    def params(self) -> AnalysisParams:
        return self._params

    @params.setter
    def params(self, value: Optional[AnalysisParams]) -> None:
        value = (value or AnalysisParams()).normalized()
        if not self._explicit_models and value.detector != self._params.detector:
            self.models = shared_models(value.detector)
        self._params = value

    @property
    def max_birds(self) -> int:  # 0 = measure every bird
        return self._params.max_birds

    @max_birds.setter
    def max_birds(self, value: int) -> None:
        self.params = replace(self._params, max_birds=value)

    @property
    def edge_estimator(self) -> str:  # metrics.EDGE_ESTIMATORS key
        return self._params.edge_estimator

    @edge_estimator.setter
    def edge_estimator(self, value: str) -> None:
        self.params = replace(self._params, edge_estimator=value)

    @property
    def tile_options(self) -> TileOptions:  # no-bird tiling
        return self._params.tiles

    @tile_options.setter
    def tile_options(self, value: TileOptions) -> None:
        self.params = replace(self._params, tiles=value or TileOptions())

    def with_options(self, *, params: Optional[AnalysisParams] = None, max_birds: Optional[int] = None,
                     edge_estimator: Optional[str] = None,
                     tile_options: Optional[TileOptions] = None) -> "BirdSharpnessAnalyzer":
        """A sibling with its own options (e.g. one trace window), sharing the focus
        providers and the loaded models (same detector), leaving this analyzer untouched."""
        return BirdSharpnessAnalyzer(
            self.models if self._explicit_models else None, focus_provider=self.focus_provider,
            manual_focus_provider=self.manual_focus_provider, refiner_provider=self.refiner_provider,
            denoised_lookup=self.denoised_lookup, params=params or self._params, max_birds=max_birds,
            edge_estimator=edge_estimator, tile_options=tile_options)

    @property
    def estimator(self) -> EdgeEstimator:
        return get_edge_estimator(self.edge_estimator)

    @property
    def version(self) -> str:
        """Algorithm version written to XMP; non-default options (dense estimator, tiling) are
        tagged so "skip analysed" never mixes their results with the defaults."""
        return "-".join([ALGORITHM_VERSION, *self._params.version_tags()])

    def _select(self, field_: EdgeBlurField, region):
        return field_.select_strongest_edges(region, min_kept=self.estimator.min_kept)

    def _edge_stats(self, samples):
        return edge_stats(samples, self.estimator.quantile)

    def _drop_small(self, detections: List[BirdDetection], scale: float) -> Tuple[List[BirdDetection], int]:
        """``(kept, ignored)``: birds whose box long side is below ``params.min_bird_side`` full-resolution
        px are ignored (``scale``: detection px per full-resolution px)."""
        side = int(self._params.min_bird_side or 0)
        if side <= 0:
            return detections, 0
        kept = [d for d in detections
                if max(d.box[2] - d.box[0], d.box[3] - d.box[1]) / max(scale, 1e-9) >= side]
        return kept, len(detections) - len(kept)

    def _limit(self, detections: List[BirdDetection]) -> List[BirdDetection]:
        limit = int(self.max_birds or 0)
        return detections[:limit] if limit > 0 else detections

    def image_loader(self) -> Callable[[str], AnalysisImage]:
        """Decoder of ``params.image_source`` (RAW decode, embedded JPEG, denoised rendering)."""
        source = self._params.image_source
        if source == SOURCE_RAW:
            return load_analysis_image
        return source_loader(source, denoised_lookup=self.denoised_lookup or _no_denoised_lookup)

    def load(self) -> None:
        self.models.load()

    def release(self) -> None:
        self.models.release()

    def analyze(self, path: str, *, on_stage: Optional[Callable[[str], None]] = None,
                cancelled: Callable[[], bool] = lambda: False, tracer=None,
                image_loader: Optional[Callable[[str], AnalysisImage]] = None,
                given: Optional["GivenBirds"] = None) -> BirdSharpnessResult:
        """Analyse one photo. ``tracer`` (:class:`~bird_sharpness.trace.AnalysisTracer`)
        records every key step with the exact data used; ``None`` costs nothing.
        ``image_loader(path)`` replaces the decode of ``params.image_source`` (:meth:`image_loader`;
        see :mod:`bird_sharpness.image_source`); focus metadata still comes from ``path``.
        ``given``: measure these birds (in the loaded image's pixels) instead of detecting;
        the image is not the photo's frame, so no focus box or manual-focus lookup."""
        t0 = time.perf_counter()
        clock = StageClock()
        report = on_stage or (lambda stage: None)

        def staged(stage: str) -> None:  # every stage change is also a timing boundary
            clock.enter(stage)
            report(stage)

        try:
            result = self._analyze(path, staged, cancelled, tracer, image_loader, given)
        except Exception as exc:
            _log.error("[BirdSharpness] analysis failed path=%r: %s", path, traceback.format_exc())
            result = BirdSharpnessResult(path=path, verdict=VERDICT_ERROR, error=f"{type(exc).__name__}: {exc}")
        result.stage_s = clock.finish()
        result.elapsed_s = round(time.perf_counter() - t0, 3)
        if tracer is not None and getattr(tracer, "trace", None) is not None and result.ok:
            tracer.result(result)
        return result

    # ── pipeline ──────────────────────────────────────────────────────────
    def _analyze(self, path: str, on_stage: Callable[[str], None], cancelled, tracer=None,
                 image_loader=None, given: Optional["GivenBirds"] = None) -> BirdSharpnessResult:
        on_stage(STAGE_DECODE)
        t_decode = time.perf_counter()
        image = (image_loader or self.image_loader())(path)
        if given is not None:
            return self._analyze_given(path, image, given, on_stage, tracer, time.perf_counter() - t_decode)
        focus_px = _UNSET = object()
        if tracer is not None:
            focus_px = self._focus_box_px(path, image)
            tracer.decode(path, image, focus_px, decode_s=time.perf_counter() - t_decode)
        on_stage(STAGE_DETECT)
        H, W = image.gray.shape[:2]
        small, scale = _resize_long_edge(image.rgb8, DETECT_LONG_EDGE)
        small_bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
        detections = dedupe_detections(self.models.detect_birds(small_bgr, imgsz=DETECT_IMGSZ))
        small_pass = None
        if detections and has_small_birds(detections) and not cancelled():
            detections, scale, small_pass = self._small_bird_pass(image, detections, scale)
        detections, ignored_small = self._drop_small(detections, scale)
        limit = int(self.max_birds or 0)
        unmeasured = max(0, len(detections) - limit) if limit > 0 else 0
        if unmeasured:
            if focus_px is _UNSET:
                focus_px = self._focus_box_px(path, image)
            detections = self._limit(prefer_focus_birds(detections, focus_px, scale))
        if tracer is not None:
            tracer.detect(detections, scale, has_masks=bool(getattr(self.models, "has_masks", True)),
                          has_keypoints=bool(getattr(self.models, "has_keypoints", True)), unmeasured=unmeasured,
                          limit=limit, small_pass=small_pass,
                          detector=str(getattr(self.models, "detector_name", "") or ""), ignored_small=ignored_small)
        if not detections and not cancelled():
            on_stage(STAGE_RECHECK)
            if focus_px is _UNSET:
                focus_px = self._focus_box_px(path, image)
            recheck = self._recheck(image, small_bgr, scale, focus_px, cancelled)
            detections, _ignored = self._drop_small(recheck.accepted, scale)
            if tracer is not None:
                tracer.recheck(recheck)
        manual_known: List[bool] = []

        def manual() -> bool:  # looked up once, only when needed
            if not manual_known:
                manual_known.append(self._manual_focus(path))
            return manual_known[0]

        enh = self._params.enhanced
        if not detections and not cancelled() and enh.mode != ENH_OFF and (enh.mode == ENH_NOBIRD or manual()):
            on_stage(STAGE_RECHECK)
            if focus_px is _UNSET:
                focus_px = self._focus_box_px(path, image)
            found = self._enhanced_search(image, small_bgr, scale, focus_px, cancelled)
            found.manual = manual_known[0] if manual_known else None
            detections, _ignored = self._drop_small(found.accepted, scale)
            if tracer is not None:
                tracer.enhanced(found)
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
                                    focus_known=focus_px is not _UNSET, manual=manual)

    def _analyze_given(self, path: str, image: AnalysisImage, given: "GivenBirds", on_stage, tracer,
                       decode_s: float) -> BirdSharpnessResult:
        """Measure the caller's birds as they are: no detection, rechecks, focus box or tiles."""
        if tracer is not None:
            tracer.decode(path, image, None, decode_s=decode_s)
        H, W = image.gray.shape[:2]
        detections = [BirdDetection(float(b.confidence), tuple(float(v) for v in b.box),
                                    None if b.mask is None else b.mask.astype(np.uint8), FOUND_GIVEN)
                      for b in given.birds]
        if not detections:
            raise ValueError("没有给定的鸟")
        detections, ignored_small = self._drop_small(detections, 1.0)
        if not detections:
            raise ValueError(f"给定的 {ignored_small} 只鸟的框长边都小于 {self._params.min_bird_side} px（设置「忽略小鸟」）")
        on_stage(STAGE_DETECT)
        if tracer is not None:
            tracer.detect(detections, 1.0, has_masks=any(d.mask is not None for d in detections),
                          has_keypoints=bool(getattr(self.models, "has_keypoints", True)), detector=given.label,
                          ignored_small=ignored_small)
        on_stage(STAGE_MEASURE)
        birds = [self._measure_bird(image, det, 1.0, index=i, tracer=tracer) for i, det in enumerate(detections)]
        excluded = excluded_birds(birds)
        kept = [b for i, b in enumerate(birds) if i not in excluded]
        if tracer is not None:
            tracer.mark_best(birds.index(max(kept, key=BirdMeasurement.rank)), excluded=excluded)
        return self._bird_result(path, kept, max(H, W))

    def _small_bird_pass(self, image: AnalysisImage, first: List[BirdDetection], scale: float):
        """Detect again on a 2048 px copy and merge (see FLOCK_BIRD_SIDE).

        Returns ``(detections, scale of the 2048 px copy, (first, found, added))``.
        First-pass birds stay exactly as they were (so their measurements do not
        change); the high-resolution pass only adds the birds it missed.
        """
        fine, fine_scale = _resize_long_edge(image.rgb8, SMALL_DETECT_LONG_EDGE)
        if fine_scale <= scale:
            return first, scale, None
        found = [replace(d, source=FOUND_FULL_SMALL) for d in
                 self.models.detect_birds(cv2.cvtColor(fine, cv2.COLOR_RGB2BGR), imgsz=SMALL_DETECT_IMGSZ)]
        factor = fine_scale / scale
        kept = [_scaled_detection(d, factor) for d in first]
        merged = dedupe_detections([*kept, *found])
        return merged, fine_scale, (len(first), len(found), len(merged) - len(first))

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
                                  for d in self._limit(dedupe_detections(found))]
                return check
            if cancelled():
                return check
        candidates = self.models.detect_birds(small_bgr, conf=FOCUS_CANDIDATE_CONFIDENCE, imgsz=RECHECK_IMGSZ)
        confident = [replace(d, source=FOUND_FULL_FINE) for d in candidates if d.confidence >= BIRD_CONFIDENCE_MIN]
        if confident:
            check.accepted = self._limit(dedupe_detections(confident))
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

    def _enhanced_search(self, image: AnalysisImage, small_bgr, scale: float, focus_px: Optional[Box],
                         cancelled) -> EnhancedPass:
        """Zoomed overlapping windows over the centre region (see :class:`params.EnhancedSearch`)."""
        o = self._params.enhanced
        vx1, vy1, vx2, vy2 = valid_bounds(image)
        rw = max(32, int(round((vx2 - vx1) * o.region_percent / 100.0)))
        rh = max(32, int(round((vy2 - vy1) * o.region_percent / 100.0)))
        if focus_px is not None:
            cx, cy = (focus_px[0] + focus_px[2]) / 2.0, (focus_px[1] + focus_px[3]) / 2.0
        else:
            cx, cy = (vx1 + vx2) / 2.0, (vy1 + vy2) / 2.0
        rx1 = int(round(min(max(vx1, cx - rw / 2.0), vx2 - rw)))
        ry1 = int(round(min(max(vy1, cy - rh / 2.0), vy2 - rh)))
        found = EnhancedPass((rx1, ry1, rx1 + rw, ry1 + rh), o.min_conf_percent / 100.0, o.imgsz)
        lut = None
        if o.lift:
            _lifted, gamma = lift_midtones(small_bgr, bgr=True)
            if gamma < LIFT_DARK_GAMMA:
                found.gamma = gamma
                lut = np.clip(np.power(np.arange(256) / 255.0, gamma) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        accepted = []
        for win in enhanced_windows(found.region, o.grid):
            if cancelled():
                break
            x1, y1, x2, y2 = win
            crop = cv2.cvtColor(np.ascontiguousarray(image.rgb8[y1:y2, x1:x2]), cv2.COLOR_RGB2BGR)
            if lut is not None:
                crop = lut[crop]
            seen = []
            for det in self.models.detect_birds(crop, conf=ENH_CANDIDATE_CONFIDENCE, imgsz=o.imgsz):
                box = (det.box[0] + x1, det.box[1] + y1, det.box[2] + x1, det.box[3] + y1)
                ok = det.confidence >= found.min_conf
                seen.append((det.confidence, box, ok))
                if ok:
                    cut = _cut_by_window(box, win, (vx1, vy1, vx2, vy2))
                    accepted.append((cut, replace(_window_detection(det, box, win, scale, small_bgr.shape[:2]),
                                                  source=FOUND_ENHANCED)))
            found.windows.append({"box": win, "detections": seen})
        # A bird cut by a window border is seen whole by an overlapping window: uncut and larger views
        # first, so merging (containment) keeps the whole bird rather than a slice of it, whose
        # confidence can be the higher one.
        accepted.sort(key=lambda item: (item[0], -_area(item[1].box), -item[1].confidence))
        found.accepted = [_focus_part(d, focus_px, scale)
                          for d in self._limit(dedupe_detections([d for _cut, d in accepted]))]
        return found

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
            version=self.version,
            edge_estimator=self.estimator.key,
            detector=str(getattr(self.models, "detector_name", "") or ""),
            sam_model=self._params.sam_model,
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

        # This bird's pixels in ROI coordinates: its segmentation mask, else the core of its box
        # (always the box core when the options measure the whole box).
        use_mask = det.mask is not None and self._params.bird_pixels != PIXELS_BOX
        if use_mask:
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
        detector_px, refined_by = None, ""
        if self._sam_applies(det):
            refined = self._sam_mask(image.rgb8[Y1:Y2, X1:X2], (x1 - X1, y1 - Y1, x2 - X1, y2 - Y1), mask)
            if refined is not None:
                detector_px, refined_by = mask.astype(bool), self._params.sam_model
                mask = refined.astype(np.uint8)
        bird_px = mask.astype(bool)
        # Optionally paint everything but the bird letterbox grey (the model chain's cut-out):
        # the eye model and the edge measurement then see the cut-out, never the photo.
        roi_gray, roi_rgb = image.gray[Y1:Y2, X1:X2], image.rgb8[Y1:Y2, X1:X2]
        grey_filled = bool(self._params.grey_fill)
        if grey_filled:
            roi_gray, roi_rgb = roi_gray.copy(), roi_rgb.copy()
            roi_gray[~bird_px] = MASK_FILL_GRAY
            roi_rgb[~bird_px] = MASK_FILL
        # Head / whole-bird edges come from the mask core with specular highlights cut out.
        mask_px = max(1.0, W / float(det.mask.shape[1])) if use_mask else 1.0
        erode_px = int(min(HEAD_MASK_ERODE_MAX_PX, max(HEAD_MASK_ERODE_MIN_PX, round(mask_px))))
        head_scale = max(HEAD_RADIUS_MIN_PX, HEAD_RADIUS_BOX_RATIO * max(bw, bh))
        glare = specular_highlights(roi_gray, head_scale)
        core = cv2.erode(mask, np.ones((2 * erode_px + 1, 2 * erode_px + 1), np.uint8)).astype(bool) & ~glare
        if not core.any():
            core = bird_px & ~glare

        # Sensor noise is always estimated on the photo's pixels: a grey-filled crop is mostly flat.
        field_ = EdgeBlurField(roi_gray, noise_source=image.gray[Y1:Y2, X1:X2] if grey_filled else None)
        body = cv2.erode(mask, np.ones((BODY_MASK_ERODE_PX, BODY_MASK_ERODE_PX), np.uint8)).astype(bool)
        if not body.any():
            body = bird_px
        body_stats, motion_ratio, body_detail = field_.body_blur_detail(body)

        keypoints = locate_head(self.models, roi_rgb, max(bw, bh))
        head_samples = None
        eye_vis = None
        eye_abs = None
        radius = None
        head_stats = None
        head = None
        selection = None
        trace_keypoints = None
        if keypoints is not None and keypoints.eye_visibility >= EYE_VISIBLE_MIN and not keypoints.eye_reliable:
            # The eye model and its mirror run disagree: the head position is a guess, so
            # measure the whole bird as without an eye model (eye visible, no score cap).
            eye_vis = keypoints.eye_visibility
            trace_keypoints = (keypoints, None)
            selection = self._select(field_, core)
            head_stats = self._edge_stats(selection.sigma)
            head_sigma = None
            sigma = head_stats.sigma
            verdict, score = classify(sigma, body_stats.sigma, motion_ratio, eye_visible=True,
                                      head_blank=sigma is None)
            if sigma is None:
                sigma = blank_head_sigma(body_stats.sigma)
        elif keypoints is not None:
            pts, vis = keypoints.pts, keypoints.vis  # left eye, right eye, beak
            eye_vis = keypoints.eye_visibility
            eye = keypoints.eye
            if keypoints.beak_usable:
                radius = HEAD_RADIUS_BEAK_RATIO * float(np.linalg.norm(eye - pts[2]))
            else:
                radius = HEAD_RADIUS_BOX_RATIO * max(bw, bh)
            radius = max(radius, HEAD_RADIUS_MIN_PX)
            trace_keypoints = (keypoints, radius)
            if eye_vis >= EYE_VISIBLE_MIN:
                yy, xx = np.ogrid[:ch, :cw]
                head = ((xx - eye[0]) ** 2 + (yy - eye[1]) ** 2 <= radius ** 2) & core
                selection = self._select(field_, head)
                head_stats = self._edge_stats(selection.sigma)
                if head_stats.sigma is not None and max(bw, bh) < SMALL_BIRD_SIDE:
                    head_samples = [float(head_stats.sigma)]
                    for dx, dy, scale_r in HEAD_SAMPLE_VARIANTS[1:]:
                        cx = eye[0] + dx * HEAD_SAMPLE_SHIFT * radius
                        cy = eye[1] + dy * HEAD_SAMPLE_SHIFT * radius
                        variant = ((xx - cx) ** 2 + (yy - cy) ** 2 <= (radius * scale_r) ** 2) & core
                        value = self._edge_stats(self._select(field_, variant).sigma).sigma
                        if value is not None:
                            head_samples.append(float(value))
            eye_abs = (round(float(eye[0] + X1), 1), round(float(eye[1] + Y1), 1))
            head_sigma = head_stats.sigma if head_stats is not None else None
            if head_samples:
                head_sigma = float(np.median(head_samples))
            head_blank = head_stats is not None and head_sigma is None
            verdict, score = classify(head_sigma, body_stats.sigma, motion_ratio,
                                      eye_visible=eye_vis >= EYE_VISIBLE_MIN, head_blank=head_blank)
            if head_blank:
                sigma = blank_head_sigma(body_stats.sigma)
            else:
                sigma = head_sigma if head_sigma is not None else body_stats.sigma
        else:
            # No eye model: the whole bird's strongest edges stand in for the head.
            selection = self._select(field_, core)
            head_stats = self._edge_stats(selection.sigma)
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
            masked=use_mask,
            found_by=getattr(det, "source", FOUND_FULL),
            head_samples=None if not head_samples else [round(v, 3) for v in head_samples],
            eye_mirror_gap=None if keypoints is None else keypoints.eye_gap,
            eye_reliable=None if keypoints is None else keypoints.eye_reliable,
            index=index,
            refined_by=refined_by,
            grey_filled=grey_filled,
        )
        if tracer is not None:
            tracer.bird(index, image, (X1, Y1, X2, Y2), bird_px, body, head, trace_keypoints, selection,
                        body_detail, measurement, detector_px=detector_px, rgb_roi=roi_rgb if grey_filled else None)
        return measurement

    def _sam_applies(self, det: BirdDetection) -> bool:
        p = self._params
        if p.bird_pixels == PIXELS_BOX:  # the whole box is measured: no outline to refine
            return False
        return bool(p.sam_model) and (p.sam_scope == SAM_SCOPE_ALL
                                      or getattr(det, "source", FOUND_FULL) not in (FOUND_FULL, FOUND_FULL_SMALL))

    def _sam_mask(self, rgb_roi: np.ndarray, box, detector_mask: np.ndarray) -> Optional[np.ndarray]:
        """SAM's mask of the bird in ``box`` (ROI px), kept inside the box + 10 %; ``None`` when
        it keeps less than ``refine.MIN_KEEP_FRACTION`` of the detector's mask (wrong object)."""
        from .refine import MIN_KEEP_FRACTION

        refined = self.refiner_provider(self._params.sam_model).mask(rgb_roi, box)
        if refined is None:
            return None
        x1, y1, x2, y2 = box
        mx, my = int(0.1 * (x2 - x1)), int(0.1 * (y2 - y1))
        keep = np.zeros(refined.shape, bool)
        keep[max(0, y1 - my):y2 + my, max(0, x1 - mx):x2 + mx] = True
        refined = refined & keep
        if refined.sum() < MIN_KEEP_FRACTION * max(1, int(np.count_nonzero(detector_mask))):
            _log.info("[BirdSharpness] SAM mask too small (%d px vs detector %d px); keeping the detector's",
                      int(refined.sum()), int(np.count_nonzero(detector_mask)))
            return None
        return refined

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

    def _manual_focus(self, path: str) -> bool:
        try:
            return bool(self.manual_focus_provider(path))
        except Exception as exc:
            _log.debug("[BirdSharpness] manual focus provider failed path=%r: %s", path, exc)
            return False

    def _no_bird_result(self, path: str, image: AnalysisImage, cancelled, tracer=None, *,
                        focus_px=None, focus_known: bool = False, manual=None) -> BirdSharpnessResult:
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
            selection = self._select(EdgeBlurField(image.gray[fy1:fy2, fx1:fx2]), None)
            stats = self._edge_stats(selection.sigma)
            region, region_box = fields.REGION_FOCUS, (fx1, fy1, fx2, fy2)
            if tracer is not None:
                tracer.focus_window(image, region_box, selection, stats)
        options = self.tile_options.normalized()
        if (stats is None or stats.sigma is None) and options.mf_center and (
                manual() if manual is not None else self._manual_focus(path)):
            # Manual focus: the sharpest part of the frame centre is the focused plane.
            cw = max(32, int(round((vx2 - vx1) * options.mf_center_percent / 100.0)))
            ch = max(32, int(round((vy2 - vy1) * options.mf_center_percent / 100.0)))
            cx1, cy1 = vx1 + (vx2 - vx1 - cw) // 2, vy1 + (vy2 - vy1 - ch) // 2
            center = (cx1, cy1, cx1 + cw, cy1 + ch)
            tiles = [] if tracer is not None else None
            sharpest = sharpest_tiles_blur(
                image.gray[center[1]:center[3], center[0]:center[2]], tile=options.mf_tile,
                sharpest_percent=options.mf_sharpest_percent, cancelled=cancelled,
                tile_report=None if tiles is None else
                (lambda x, y, w, h, part: tiles.append((x + cx1, y + cy1, w, h, part))))
            mf_stats = sharpest.stats
            if tracer is not None:
                shift = lambda boxes: [(x + cx1, y + cy1, w, h) for x, y, w, h in boxes]  # noqa: E731
                tracer.manual_center(center, tiles, shift(sharpest.chosen), shift(sharpest.candidates), mf_stats,
                                     options)
            if mf_stats.sigma is not None:
                stats, region, region_box = mf_stats, fields.REGION_MANUAL, center
        if stats is None or stats.sigma is None:
            # No focus point (or nothing measurable there): default whole-image measurement.
            tiles = [] if tracer is not None else None
            stats = full_image_blur(
                image.gray[vy1:vy2, vx1:vx2], tile=options.full_tile, cancelled=cancelled,
                tile_report=None if tiles is None else
                (lambda x, y, w, h, part: tiles.append((x + vx1, y + vy1, w, h, part))))
            region, region_box = fields.REGION_FULL, (vx1, vy1, vx2, vy2)
            if tracer is not None:
                tracer.full_image(tiles, stats, tile=options.full_tile)
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
            version=self.version,
            edge_estimator=self.estimator.key,
            detector=str(getattr(self.models, "detector_name", "") or ""),
            sam_model=self._params.sam_model,
        )


ProgressCallback = Callable[[int, int, BirdSharpnessResult], None]


def analyze_paths(
    paths: Iterable[str],
    *,
    analyzer: Optional[BirdSharpnessAnalyzer] = None,
    cancel_event: Optional[threading.Event] = None,
    on_result: Optional[ProgressCallback] = None,
    workers: int = 1,
    image_loader: Optional[Callable[[str], AnalysisImage]] = None,
) -> List[BirdSharpnessResult]:
    """Analyze files, ``workers`` at a time; results are returned in input order.

    ``on_result`` runs on the calling thread in completion order. Stops submitting
    new files once ``cancel_event`` is set and waits for the ones in flight.
    """
    items = [os.path.normpath(p) for p in paths]
    extra = {} if image_loader is None else {"image_loader": image_loader}
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
            by_index[index] = analyzer.analyze(path, **extra)
            report(by_index[index])
    else:
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        analyzer.load()
        pending = {}
        next_index = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bird-sharpness") as executor:
            while pending or (next_index < total and not cancelled()):
                while next_index < total and len(pending) < workers * 2 and not cancelled():
                    pending[executor.submit(analyzer.analyze, items[next_index], **extra)] = next_index
                    next_index += 1
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    by_index[index] = future.result()
                    report(by_index[index])
    return [by_index[i] for i in sorted(by_index)]
