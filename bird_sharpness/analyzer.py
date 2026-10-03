"""Bird sharpness analysis.

Pipeline (see ``docs/bird_sharpness.md``):

1. decode the full-resolution image (RAW via LibRaw);
2. detect every bird on a 1024 px copy (segmentation masks, or boxes from a
   plain detector);
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
from dataclasses import asdict, dataclass, field as dc_field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

from app_common import bird_sharpness_fields as fields
from app_common.log import get_logger

from .focus import FocusProvider, default_focus_box, focus_window
from .image_source import AnalysisImage, load_analysis_image
from .metrics import EdgeBlurField, full_image_blur
from .models import BirdDetection, BirdSharpnessModels
from .scoring import ALGORITHM_VERSION, VERDICT_ERROR, VERDICT_NO_BIRD, blank_head_sigma, classify, sigma_to_score

_log = get_logger("bird_sharpness")

DETECT_LONG_EDGE = 1024
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


def _resize_long_edge(img: np.ndarray, long_edge: int) -> Tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    scale = min(1.0, float(long_edge) / float(max(h, w)))
    if scale >= 1.0:
        return img, 1.0
    return cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA), scale


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
                cancelled: Callable[[], bool] = lambda: False) -> BirdSharpnessResult:
        t0 = time.perf_counter()
        try:
            result = self._analyze(path, on_stage or (lambda stage: None), cancelled)
        except Exception as exc:
            _log.error("[BirdSharpness] analysis failed path=%r: %s", path, traceback.format_exc())
            result = BirdSharpnessResult(path=path, verdict=VERDICT_ERROR, error=f"{type(exc).__name__}: {exc}")
        result.elapsed_s = round(time.perf_counter() - t0, 3)
        return result

    # ── pipeline ──────────────────────────────────────────────────────────
    def _analyze(self, path: str, on_stage: Callable[[str], None], cancelled) -> BirdSharpnessResult:
        on_stage(STAGE_DECODE)
        image = load_analysis_image(path)
        on_stage(STAGE_DETECT)
        H, W = image.gray.shape[:2]
        small, scale = _resize_long_edge(image.rgb8, DETECT_LONG_EDGE)
        detections = self.models.detect_birds(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))[:MAX_BIRDS]
        on_stage(STAGE_MEASURE)
        if detections:
            birds = [self._measure_bird(image, det, scale) for det in detections]
            return self._bird_result(path, birds, max(H, W))
        return self._no_bird_result(path, image, cancelled)

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

    def _measure_bird(self, image: AnalysisImage, det: BirdDetection, scale: float) -> BirdMeasurement:
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
        body_stats, motion_ratio = field_.body_blur(body)

        keypoints = self.models.keypoints(np.ascontiguousarray(image.rgb8[Y1:Y2, X1:X2]))
        eye_vis = None
        eye_abs = None
        radius = None
        head_stats = None
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
            if eye_vis >= EYE_VISIBLE_MIN:
                yy, xx = np.ogrid[:ch, :cw]
                dilated = cv2.dilate(mask, np.ones((HEAD_MASK_DILATE_PX, HEAD_MASK_DILATE_PX), np.uint8)).astype(bool)
                head = ((xx - eye[0]) ** 2 + (yy - eye[1]) ** 2 <= radius ** 2) & dilated
                head_stats = field_.strongest_edge_blur(head)
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
            head_stats = field_.strongest_edge_blur(bird_px)
            head_sigma = None
            sigma = head_stats.sigma
            verdict, score = classify(sigma, body_stats.sigma, motion_ratio, eye_visible=True,
                                      head_blank=sigma is None)
            if sigma is None:
                sigma = blank_head_sigma(body_stats.sigma)
        return BirdMeasurement(
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
        )

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

    def _no_bird_result(self, path: str, image: AnalysisImage, cancelled) -> BirdSharpnessResult:
        H, W = image.gray.shape[:2]
        region, region_box, stats = "", None, None
        focus_px = self._focus_box_px(path, image)
        if focus_px is not None:
            fx1, fy1, fx2, fy2 = focus_window(focus_px, W, H)
            stats = EdgeBlurField(image.gray[fy1:fy2, fx1:fx2]).strongest_edge_blur(None)
            region, region_box = fields.REGION_FOCUS, (fx1, fy1, fx2, fy2)
        if stats is None or stats.sigma is None:
            # No focus point (or nothing measurable there): default whole-image measurement.
            stats = full_image_blur(image.gray, cancelled=cancelled)
            region, region_box = fields.REGION_FULL, (0, 0, W, H)
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
