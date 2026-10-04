"""Contrast-invariant blur-radius measurements (NumPy/OpenCV, no Torch).

The core estimator is the gradient re-blur ratio of Zhuo & Sim (2011),
"Defocus map estimation from a single image": at an edge blurred by a Gaussian of
radius ``s``, re-blurring by ``s0`` lowers the peak gradient by
``R = sqrt(s^2 + s0^2) / s``, so ``s = s0 / sqrt(R^2 - 1)``. The result is in
pixels and does not depend on edge contrast, exposure or plumage colour. A light
pre-smoothing ``sa`` suppresses sensor noise and is removed analytically.

Measuring on the strongest edges only keeps high-ISO noise from dominating, which
is the failure mode of Laplacian variance / Tenengrad on dark, noisy bird crops.

The reported radius includes pixel sampling and the discrete Sobel difference,
which add ~0.6-0.73 px in quadrature depending on edge orientation
(``measured ≈ hypot(true, 0.73)`` for axis-aligned edges), so a perfectly focused
image measures about 0.6-0.7 px. Thresholds in ``scoring`` are
calibrated on these measured values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

PRE_SIGMA = 1.0
REBLUR_SIGMA = 1.5
MIN_HEAD_EDGES = 20


@dataclass(frozen=True)
class EdgeEstimator:
    """How the blur radius is read from the strongest edges of a region.

    ``min_kept``: at least this many of the strongest above-noise edges are measured
    (all of them when fewer pass); ``quantile``: which quantile of their blur radii
    is reported.
    """

    key: str
    min_kept: int
    quantile: float


# Standard (default): median of the strongest 30 (or top 5%) edges; the verdict
# thresholds were calibrated on it.
ESTIMATOR_STANDARD = EdgeEstimator("standard", 30, 0.5)
# Dense: at least 60 edges, 40th percentile. On 274 shorebird heads measured at two
# plausible eye positions it flipped fewer verdicts below 300 px (17% vs 24%) with
# no overall shift (median -0.004 px vs standard), so the thresholds still apply.
ESTIMATOR_DENSE = EdgeEstimator("dense", 60, 0.4)
EDGE_ESTIMATORS = {e.key: e for e in (ESTIMATOR_STANDARD, ESTIMATOR_DENSE)}


def edge_estimator(key: Optional[str]) -> EdgeEstimator:
    return EDGE_ESTIMATORS.get(key or "", ESTIMATOR_STANDARD)
MIN_BODY_EDGES = 60
DIRECTION_BINS = 8
MIN_EDGES_PER_DIRECTION = 15
# Edges must rise this far above the estimated sensor noise (edge SNR). Pure noise
# peaks at ~0.6 x sigma_noise in mag0, and noise inflates the pre-reblur gradient
# more than the re-blurred one, so weak edges read too sharp: on a badly blurred
# ISO 6400 head the only edges left sit at ~3x noise and measured "sharp". Real
# head edges of sharp birds sit at >= 4.7x (labelled set), blurred-but-contrasty
# ones at 15x+.
NOISE_EDGE_FACTOR = 4.0


@dataclass
class EdgeSelection:
    """Which edge pixels a measurement used, kept so a trace can show exactly that.

    Masks are full-size booleans over the field; ``ys/xs/sigma`` are the measured
    (valid, step-like) edges and their blur radii.
    """

    candidates: np.ndarray        # Canny edges inside the region
    passed_noise: np.ndarray      # ... strong enough above the noise level
    selected: np.ndarray          # ... strongest fraction actually measured
    line_like: np.ndarray         # selected but rejected as thinner than a step edge
    ys: np.ndarray
    xs: np.ndarray
    sigma: np.ndarray
    noise_sigma: float
    threshold: float              # gradient magnitude needed to pass the noise test


@dataclass(frozen=True)
class EdgeBlurStats:
    sigma: Optional[float]
    p25: Optional[float]
    p75: Optional[float]
    edge_count: int


def _gaussian(img: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma) if sigma > 0 else img


def _gradients(img: np.ndarray):
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    return gx, gy


def estimate_noise_sigma(gray: np.ndarray) -> float:
    """Immerkær (1996) fast noise estimate; texture biases it upwards (stricter, never looser)."""
    if min(gray.shape[:2]) < 3:
        return 0.0
    kernel = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    response = cv2.filter2D(np.ascontiguousarray(gray, dtype=np.float32), -1, kernel)
    return float(np.sqrt(np.pi / 2.0) * np.mean(np.abs(response[1:-1, 1:-1])) / 6.0)


def _stats(samples: np.ndarray, quantile: float = 0.5) -> EdgeBlurStats:
    """Blur radius at ``quantile`` (median by default), quartiles and count; ``None`` below 8 samples."""
    if samples.size < 8:
        return EdgeBlurStats(None, None, None, int(samples.size))
    value = np.median(samples) if quantile == 0.5 else np.quantile(samples, quantile)
    return EdgeBlurStats(
        float(value), float(np.percentile(samples, 25)), float(np.percentile(samples, 75)),
        int(samples.size),
    )


edge_stats = _stats  # public alias: stats of a sample array (median / quartiles / count)


class EdgeBlurField:
    """Pre-computed gradient fields of one grayscale ROI, reused by all region queries."""

    def __init__(self, gray: np.ndarray, *, pre_sigma: float = PRE_SIGMA, reblur_sigma: float = REBLUR_SIGMA):
        gray = np.ascontiguousarray(gray, dtype=np.float32)
        self.pre_sigma = float(pre_sigma)
        self.reblur_sigma = float(reblur_sigma)
        g0 = _gaussian(gray, self.pre_sigma)
        g1 = _gaussian(gray, float(np.hypot(self.pre_sigma, self.reblur_sigma)))
        self.gx0, self.gy0 = _gradients(g0)
        gx1, gy1 = _gradients(g1)
        self.mag0 = np.hypot(self.gx0, self.gy0)
        self.mag1 = np.hypot(gx1, gy1)
        lo, hi = float(np.percentile(g0, 0.5)), float(np.percentile(g0, 99.5))
        scale = 255.0 / max(hi - lo, 1e-6)
        u8 = np.clip((g0 - lo) * scale, 0, 255).astype(np.uint8)
        self._u8 = u8
        self._canny_cache: dict = {}
        self.noise_sigma = estimate_noise_sigma(gray)

    def _edges(self, low: int, high: int) -> np.ndarray:
        key = (low, high)
        if key not in self._canny_cache:
            self._canny_cache[key] = cv2.Canny(self._u8, low, high, L2gradient=True).astype(bool)
        return self._canny_cache[key]

    def _sigma_and_valid(self, sel: np.ndarray):
        """Per-pixel blur radius for ``sel`` plus which pixels are valid step edges."""
        ratio = self.mag0[sel] / np.maximum(self.mag1[sel], 1e-9)
        total = self.reblur_sigma / np.sqrt(np.maximum(ratio ** 2 - 1.0, 1e-12))
        # A step edge already smoothed by pre_sigma cannot measure below it. Thinner
        # structures (eye-ring lines, catchlights, twigs) do and would read as
        # impossibly sharp, so they are not edges for this estimator.
        valid = (ratio > 1.02) & (total >= self.pre_sigma)
        sigma = np.sqrt(np.maximum(total ** 2 - self.pre_sigma ** 2, 0.0))
        return sigma, valid

    def _sigma_at(self, sel: np.ndarray) -> np.ndarray:
        sigma, valid = self._sigma_and_valid(sel)
        return sigma[valid]

    def select_strongest_edges(self, region: Optional[np.ndarray], *, top_fraction: float = 0.05,
                               min_edges: int = MIN_HEAD_EDGES,
                               min_kept: int = ESTIMATOR_STANDARD.min_kept) -> EdgeSelection:
        """The strongest above-noise edges inside ``region`` (``None`` = everywhere)."""
        candidates = self._edges(30, 90)
        if region is not None:
            candidates = candidates & region
        threshold = NOISE_EDGE_FACTOR * self.noise_sigma
        passed = candidates & (self.mag0 > threshold)
        selected = np.zeros_like(passed)
        n = int(passed.sum())
        if n >= min_edges:
            keep = max(top_fraction, min(1.0, float(min_kept) / n))
            thr = float(np.quantile(self.mag0[passed], 1.0 - keep))
            selected = passed & (self.mag0 >= thr)
        ys, xs = np.nonzero(selected)
        sigma, valid = self._sigma_and_valid(selected)
        line_like = np.zeros_like(selected)
        line_like[ys[~valid], xs[~valid]] = True
        return EdgeSelection(candidates, passed, selected, line_like, ys[valid], xs[valid],
                             sigma[valid].astype(np.float32), float(self.noise_sigma), float(threshold))

    def strongest_edge_samples(self, region: Optional[np.ndarray], *, top_fraction: float = 0.05,
                               min_edges: int = MIN_HEAD_EDGES) -> np.ndarray:
        """Blur radii at the strongest edges inside ``region`` (``None`` = everywhere)."""
        return self.select_strongest_edges(region, top_fraction=top_fraction, min_edges=min_edges).sigma

    def strongest_edge_blur(self, region: Optional[np.ndarray], *, top_fraction: float = 0.05,
                            min_edges: int = MIN_HEAD_EDGES) -> EdgeBlurStats:
        """Median blur radius over the strongest edges inside ``region`` (head use)."""
        return _stats(self.strongest_edge_samples(region, top_fraction=top_fraction, min_edges=min_edges))

    def body_blur(self, region: np.ndarray, *, min_edges: int = MIN_BODY_EDGES):
        """Median blur over all above-noise edges plus a directional max/min ratio.

        Motion blur widens edges perpendicular to the motion only, so the median
        per edge-orientation bin differs strongly between directions.
        Returns ``(EdgeBlurStats, motion_ratio_or_None)``.
        """
        stats, motion_ratio, _detail = self.body_blur_detail(region, min_edges=min_edges)
        return stats, motion_ratio

    def body_blur_detail(self, region: np.ndarray, *, min_edges: int = MIN_BODY_EDGES):
        """``body_blur`` plus ``(ys, xs, sigma, per_direction_medians)`` for traces."""
        empty = (np.empty(0, int), np.empty(0, int), np.empty(0, np.float32), [None] * DIRECTION_BINS)
        if not region.any():
            return EdgeBlurStats(None, None, None, 0), None, empty
        noise_floor = float(np.median(self.mag0[region]))
        edges = self._edges(20, 60) & region & (self.mag0 > 4.0 * noise_floor)
        n = int(edges.sum())
        if n < min_edges:
            return EdgeBlurStats(None, None, None, n), None, empty
        ys, xs = np.nonzero(edges)
        ratio = self.mag0[edges] / np.maximum(self.mag1[edges], 1e-9)
        ok = ratio > 1.02
        total = self.reblur_sigma / np.sqrt(np.maximum(ratio ** 2 - 1.0, 1e-6))
        ok = ok & (total >= self.pre_sigma)  # drop line-like responses, as in _sigma_at
        sig = np.sqrt(np.maximum(total ** 2 - self.pre_sigma ** 2, 0.0))
        theta = np.mod(np.arctan2(self.gy0[edges], self.gx0[edges]), np.pi)
        per_dir = []
        by_bin = []
        for k in range(DIRECTION_BINS):
            center = k * np.pi / DIRECTION_BINS
            dist = np.abs(np.angle(np.exp(2j * (theta - center)))) / 2.0
            sel = ok & (dist < np.pi / (2 * DIRECTION_BINS))
            if int(sel.sum()) >= MIN_EDGES_PER_DIRECTION:
                per_dir.append(float(np.median(sig[sel])))
                by_bin.append(per_dir[-1])
            else:
                by_bin.append(None)
        motion_ratio = None
        if len(per_dir) >= DIRECTION_BINS // 2:
            motion_ratio = float(max(per_dir) / max(min(per_dir), 0.3))
        detail = (ys[ok], xs[ok], sig[ok].astype(np.float32), by_bin)
        valid = sig[ok]
        if valid.size < 8:
            return EdgeBlurStats(None, None, None, int(valid.size)), motion_ratio, detail
        stats = EdgeBlurStats(
            float(np.median(valid)), float(np.percentile(valid, 25)), float(np.percentile(valid, 75)), int(valid.size)
        )
        return stats, motion_ratio, detail


FULL_IMAGE_TILE = 1024
MIN_TILE_SAMPLES = 8  # a tile with fewer measured edges has no blur value of its own
MF_MIN_TILES = 3  # manual focus: never decide on fewer of the sharpest tiles than this
# A tile competes for "sharpest" only on real detail: enough measured step edges, and most of its
# strongest edges are steps. On dark, noisy frames (night, 4 s) near-black tiles pass the 4x-noise
# gate on grain, most of which is then rejected as dot/line-like, and the rest reads σ ~0.4.
MF_TILE_MIN_EDGES = 15
MF_TILE_MIN_STEP_FRACTION = 0.5


@dataclass(frozen=True)
class TileOptions:
    """No-bird tiling (SuperViewer 设置 → 鸟清晰度, trace 参数 tab, CLI).

    ``full_tile``: tile side for the whole image (no focus point). Manual-focus photos
    (``mf_center``) measure the frame centre instead (``mf_center_percent`` of each
    side) in ``mf_tile`` tiles, and the sharpest ``mf_sharpest_percent`` of the
    measurable tiles (at least :data:`MF_MIN_TILES`) decide: with manual focus the
    sharpest part of the centre is the plane the photographer focused on.
    Limits match ``app_common.superviewer_user_options.BIRD_SHARPNESS_TILE_LIMITS``.
    """

    full_tile: int = FULL_IMAGE_TILE
    mf_center: bool = True
    mf_center_percent: int = 50
    mf_tile: int = 256
    mf_sharpest_percent: int = 10

    @classmethod
    def from_params(cls, params: Optional[dict]) -> "TileOptions":
        """From a flat options dict (other keys ignored, missing ones default)."""
        params = params or {}
        names = ("full_tile", "mf_center", "mf_center_percent", "mf_tile", "mf_sharpest_percent")
        return cls(**{name: params[name] for name in names if params.get(name) is not None}).normalized()

    def as_params(self) -> dict:
        o = self.normalized()
        return {"full_tile": o.full_tile, "mf_center": o.mf_center, "mf_center_percent": o.mf_center_percent,
                "mf_tile": o.mf_tile, "mf_sharpest_percent": o.mf_sharpest_percent}

    def normalized(self) -> "TileOptions":
        def clamp(value, low, high, default):
            try:
                return max(low, min(high, int(value)))
            except (TypeError, ValueError, OverflowError):
                return default

        return TileOptions(clamp(self.full_tile, 128, 4096, FULL_IMAGE_TILE), bool(self.mf_center),
                           clamp(self.mf_center_percent, 10, 100, 50), clamp(self.mf_tile, 32, 2048, 256),
                           clamp(self.mf_sharpest_percent, 1, 100, 10))

    def version_tag(self) -> str:
        """Suffix for the algorithm version; empty for the defaults, so results never mix."""
        o, d = self.normalized(), TileOptions()
        parts = [] if o.full_tile == d.full_tile else [f"t{o.full_tile}"]
        if not o.mf_center:
            parts.append("mf-off")
        elif (o.mf_center_percent, o.mf_tile, o.mf_sharpest_percent) != \
                (d.mf_center_percent, d.mf_tile, d.mf_sharpest_percent):
            parts.append(f"mf{o.mf_center_percent}-{o.mf_tile}-{o.mf_sharpest_percent}")
        return "-".join(parts)


TILE_MARGIN = 16  # px of real neighbours measured around a tile (blur/gradient support is ~6 px)


def _tile_samples(gray: np.ndarray, tile: int, top_fraction: float, cancelled):
    """``(x, y, w, h, samples, strongest)`` per tile: radii at its strongest edges and how many
    strongest edges were looked at (``samples`` are the step edges among them); ``None`` once cancelled.

    Each tile is measured with :data:`TILE_MARGIN` px of its real neighbours and keeps
    only the edges inside it: an edge cut by the tile border would otherwise be
    mirrored into a thin line and read as sharp, which small tiles (and "sharpest
    tiles") would pick up.
    """
    h, w = gray.shape[:2]
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            if cancelled():
                yield None
                return
            bw, bh = min(tile, w - x), min(tile, h - y)
            if min(bw, bh) < 32:
                continue
            x0, y0 = max(0, x - TILE_MARGIN), max(0, y - TILE_MARGIN)
            padded = gray[y0:min(h, y + tile + TILE_MARGIN), x0:min(w, x + tile + TILE_MARGIN)]
            inside = np.zeros(padded.shape[:2], bool)
            inside[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = True
            selection = EdgeBlurField(padded).select_strongest_edges(inside, top_fraction=top_fraction)
            yield x, y, bw, bh, selection.sigma, int(selection.selected.sum())


def full_image_blur(gray: np.ndarray, *, tile: int = FULL_IMAGE_TILE, top_fraction: float = 0.05,
                    cancelled=lambda: False, tile_report=None) -> EdgeBlurStats:
    """Whole-image blur radius, tile by tile so memory stays bounded on 25-60 MP frames.

    Each tile contributes the radii at its own strongest edges; the result is the
    median over all tiles' samples.
    """
    samples = []
    for item in _tile_samples(gray, tile, top_fraction, cancelled):
        if item is None:
            return EdgeBlurStats(None, None, None, 0)
        x, y, w, h, part, _strongest = item
        if part.size:
            samples.append(part)
        if tile_report is not None:
            tile_report(x, y, w, h, part)
    return _stats(np.concatenate(samples) if samples else np.empty(0, np.float32))


@dataclass(frozen=True)
class SharpestTiles:
    stats: EdgeBlurStats
    chosen: list       # (x, y, w, h) of the sharpest tiles, sharpest first
    candidates: list   # (x, y, w, h) of every tile on real detail (see MF_TILE_MIN_EDGES)


def tile_on_detail(samples: np.ndarray, strongest: int) -> bool:
    return samples.size >= MF_TILE_MIN_EDGES and samples.size >= MF_TILE_MIN_STEP_FRACTION * strongest


def sharpest_tiles_blur(gray: np.ndarray, *, tile: int, sharpest_percent: float, min_tiles: int = MF_MIN_TILES,
                        top_fraction: float = 0.05, cancelled=lambda: False, tile_report=None) -> SharpestTiles:
    """Blur radius of the sharpest tiles (manual focus, see :class:`TileOptions`).

    Tiles on real detail (:func:`tile_on_detail`) are ranked by their median radius;
    the sharpest ``sharpest_percent`` of them (at least ``min_tiles``) are pooled.
    """
    ranked = []
    for item in _tile_samples(gray, tile, top_fraction, cancelled):
        if item is None:
            return SharpestTiles(EdgeBlurStats(None, None, None, 0), [], [])
        x, y, w, h, part, strongest = item
        if tile_on_detail(part, strongest):
            ranked.append((float(np.median(part)), (x, y, w, h), part))
        if tile_report is not None:
            tile_report(x, y, w, h, part)
    ranked.sort(key=lambda r: r[0])
    count = min(len(ranked), max(min_tiles, int(np.ceil(len(ranked) * sharpest_percent / 100.0))))
    chosen = ranked[:count]
    samples = np.concatenate([part for _m, _box, part in chosen]) if chosen else np.empty(0, np.float32)
    return SharpestTiles(_stats(samples), [box for _m, box, _part in chosen], [box for _m, box, _part in ranked])


def edge_sigma_map(gray: np.ndarray, box, out_size, *, bounds=None, tile: int = FULL_IMAGE_TILE,
                   cancelled=lambda: False) -> Optional[np.ndarray]:
    """Blur radius of the above-noise step edges inside ``box`` (x1, y1, x2, y2), reduced to
    ``out_size`` (w, h) over that box: each output pixel holds the mean radius of the edge
    pixels it covers, NaN where there are none (float16). ``None`` once cancelled.

    Display-only "focus peaking" (trace viewer 焦平面): every edge that clears the
    measurement's 4x-noise gate, not only the strongest 5%, measured at full
    resolution tile by tile (bounded memory) with :data:`TILE_MARGIN` px of real
    neighbours. ``bounds`` (default: the whole image) is the real picture content;
    nothing outside it is read, so RAW padding never borders a "sharp" edge.
    """
    h, w = gray.shape[:2]
    bx1, by1, bx2, by2 = (0, 0, w, h) if bounds is None else (int(v) for v in bounds)
    x1, y1, x2, y2 = (int(v) for v in box)
    out_w, out_h = max(1, int(out_size[0])), max(1, int(out_size[1]))
    sx, sy = out_w / max(1, x2 - x1), out_h / max(1, y2 - y1)
    total = np.zeros(out_h * out_w, np.float64)
    count = np.zeros(out_h * out_w, np.float64)
    for ty in range(max(y1, by1), min(y2, by2), tile):
        for tx in range(max(x1, bx1), min(x2, bx2), tile):
            if cancelled():
                return None
            bw, bh = min(tile, x2 - tx, bx2 - tx), min(tile, y2 - ty, by2 - ty)
            if min(bw, bh) < 8:
                continue
            x0, y0 = max(bx1, tx - TILE_MARGIN), max(by1, ty - TILE_MARGIN)
            padded = gray[y0:min(by2, ty + bh + TILE_MARGIN), x0:min(bx2, tx + bw + TILE_MARGIN)]
            blur_field = EdgeBlurField(padded)
            inside = np.zeros(padded.shape[:2], bool)
            inside[ty - y0:ty - y0 + bh, tx - x0:tx - x0 + bw] = True
            edges = inside & blur_field._edges(30, 90) & (blur_field.mag0 > NOISE_EDGE_FACTOR * blur_field.noise_sigma)
            sigma, valid = blur_field._sigma_and_valid(edges)
            ys, xs = np.nonzero(edges)
            ys, xs, sigma = ys[valid] + (y0 - y1), xs[valid] + (x0 - x1), sigma[valid]
            index = (np.minimum((ys * sy).astype(np.int64), out_h - 1) * out_w
                     + np.minimum((xs * sx).astype(np.int64), out_w - 1))
            total += np.bincount(index, sigma, out_h * out_w)
            count += np.bincount(index, None, out_h * out_w)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (total / count).reshape(out_h, out_w).astype(np.float16)
