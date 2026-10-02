"""Bird sharpness analysis: segment the bird, locate the eye, measure head blur.

Pipeline (see ``docs/bird_sharpness.md``):

1. decode the full-resolution image (RAW via LibRaw);
2. YOLO-seg on a 1024 px copy -> bird box + mask;
3. CUB keypoint model on the bird crop -> eye / beak;
4. blur radius at the strongest edges of ``eye circle ∩ mask`` (primary signal);
5. median + directional blur over the whole body (fallback / motion detection);
6. map to a SuperPicky-compatible 0..1000 score and a verdict.
"""

from __future__ import annotations

import os
import traceback
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

from app_common import bird_sharpness_fields as fields
from app_common.log import get_logger

from .image_source import load_analysis_image
from .metrics import EdgeBlurField
from .models import BirdSharpnessModels
from .scoring import ALGORITHM_VERSION, VERDICT_ERROR, VERDICT_NO_BIRD, classify

_log = get_logger("bird_sharpness")

DETECT_LONG_EDGE = 1024
CROP_PAD_RATIO = 0.15
EYE_VISIBLE_MIN = 0.5
BEAK_VISIBLE_MIN = 0.3
HEAD_RADIUS_BEAK_RATIO = 1.2
HEAD_RADIUS_BOX_RATIO = 0.15
HEAD_RADIUS_MIN_PX = 40
HEAD_MASK_DILATE_PX = 9
BODY_MASK_ERODE_PX = 15


@dataclass
class BirdSharpnessResult:
    path: str
    verdict: str
    score: Optional[int] = None
    head_sigma: Optional[float] = None
    body_sigma: Optional[float] = None
    motion_ratio: Optional[float] = None
    eye_visibility: Optional[float] = None
    bird_confidence: Optional[float] = None
    bird_box: Optional[Tuple[int, int, int, int]] = None
    eye_xy: Optional[Tuple[float, float]] = None
    head_radius: Optional[float] = None
    head_edges: int = 0
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
    """Reusable analyzer; keep one instance per worker so models load once."""

    def __init__(self, models: Optional[BirdSharpnessModels] = None):
        self.models = models or BirdSharpnessModels()

    def load(self) -> None:
        self.models.load()

    def release(self) -> None:
        self.models.release()

    def analyze(self, path: str) -> BirdSharpnessResult:
        t0 = time.perf_counter()
        try:
            result = self._analyze(path)
        except Exception as exc:
            _log.error("[BirdSharpness] analysis failed path=%r: %s", path, traceback.format_exc())
            result = BirdSharpnessResult(path=path, verdict=VERDICT_ERROR, error=f"{type(exc).__name__}: {exc}")
        result.elapsed_s = round(time.perf_counter() - t0, 3)
        return result

    def _analyze(self, path: str) -> BirdSharpnessResult:
        image = load_analysis_image(path)
        H, W = image.gray.shape[:2]
        small, scale = _resize_long_edge(image.rgb8, DETECT_LONG_EDGE)
        det = self.models.segment(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
        if det is None or det.boxes is None or len(det.boxes) == 0 or det.masks is None:
            return BirdSharpnessResult(path=path, verdict=VERDICT_NO_BIRD, image_long_edge=max(H, W))

        confs = det.boxes.conf.cpu().numpy()
        best = int(np.argmax(confs))
        bx1, by1, bx2, by2 = (det.boxes.xyxy[best].cpu().numpy() / scale).tolist()
        x1, y1 = max(0, int(bx1)), max(0, int(by1))
        x2, y2 = min(W, int(np.ceil(bx2))), min(H, int(np.ceil(by2)))
        bw, bh = max(1, x2 - x1), max(1, y2 - y1)
        pad = int(CROP_PAD_RATIO * max(bw, bh))
        X1, Y1, X2, Y2 = max(0, x1 - pad), max(0, y1 - pad), min(W, x2 + pad), min(H, y2 + pad)

        # Mask at detection resolution -> ROI at full resolution.
        mask_small = det.masks.data[best].cpu().numpy().astype(np.uint8)
        mh, mw = mask_small.shape[:2]
        sx, sy = mw / float(W), mh / float(H)
        mx1, my1 = int(np.floor(X1 * sx)), int(np.floor(Y1 * sy))
        mx2, my2 = max(mx1 + 1, int(np.ceil(X2 * sx))), max(my1 + 1, int(np.ceil(Y2 * sy)))
        mask = cv2.resize(mask_small[my1:my2, mx1:mx2], (X2 - X1, Y2 - Y1), interpolation=cv2.INTER_NEAREST)

        coords, vis = self.models.keypoints(np.ascontiguousarray(image.rgb8[Y1:Y2, X1:X2]))
        cw, ch = X2 - X1, Y2 - Y1
        pts = coords * np.array([cw, ch], np.float32)  # ROI coordinates: left eye, right eye, beak
        eye_idx = 0 if vis[0] >= vis[1] else 1
        eye_vis = float(vis[eye_idx])
        eye = pts[eye_idx]
        if vis[2] >= BEAK_VISIBLE_MIN:
            radius = HEAD_RADIUS_BEAK_RATIO * float(np.linalg.norm(eye - pts[2]))
        else:
            radius = HEAD_RADIUS_BOX_RATIO * max(bw, bh)
        radius = max(radius, HEAD_RADIUS_MIN_PX)

        roi_gray = image.gray[Y1:Y2, X1:X2]
        field_ = EdgeBlurField(roi_gray)
        yy, xx = np.ogrid[:ch, :cw]
        dilated = cv2.dilate(mask, np.ones((HEAD_MASK_DILATE_PX, HEAD_MASK_DILATE_PX), np.uint8)).astype(bool)
        head = ((xx - eye[0]) ** 2 + (yy - eye[1]) ** 2 <= radius ** 2) & dilated
        body = cv2.erode(mask, np.ones((BODY_MASK_ERODE_PX, BODY_MASK_ERODE_PX), np.uint8)).astype(bool)

        eye_visible = eye_vis >= EYE_VISIBLE_MIN
        head_stats = field_.strongest_edge_blur(head) if eye_visible else None
        body_stats, motion_ratio = field_.body_blur(body)
        head_sigma = head_stats.sigma if head_stats is not None else None
        verdict, score = classify(head_sigma, body_stats.sigma, motion_ratio, eye_visible=eye_visible)

        def r3(v):
            return None if v is None else round(float(v), 3)

        return BirdSharpnessResult(
            path=path,
            verdict=verdict,
            score=score,
            head_sigma=r3(head_sigma),
            body_sigma=r3(body_stats.sigma),
            motion_ratio=None if motion_ratio is None else round(float(motion_ratio), 2),
            eye_visibility=round(eye_vis, 2),
            bird_confidence=round(float(confs[best]), 3),
            bird_box=(x1, y1, x2, y2),
            eye_xy=(round(float(eye[0] + X1), 1), round(float(eye[1] + Y1), 1)),
            head_radius=round(float(radius), 1),
            head_edges=head_stats.edge_count if head_stats is not None else 0,
            image_long_edge=max(H, W),
        )


ProgressCallback = Callable[[int, int, BirdSharpnessResult], None]


def analyze_paths(
    paths: Iterable[str],
    *,
    analyzer: Optional[BirdSharpnessAnalyzer] = None,
    cancel_event: Optional[threading.Event] = None,
    on_result: Optional[ProgressCallback] = None,
) -> List[BirdSharpnessResult]:
    """Analyze files in order; stops early when ``cancel_event`` is set."""
    items = [os.path.normpath(p) for p in paths]
    analyzer = analyzer or BirdSharpnessAnalyzer()
    results: List[BirdSharpnessResult] = []
    for index, path in enumerate(items, start=1):
        if cancel_event is not None and cancel_event.is_set():
            break
        result = analyzer.analyze(path)
        results.append(result)
        if on_result is not None:
            on_result(index, len(items), result)
    return results
