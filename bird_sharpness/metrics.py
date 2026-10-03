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
MIN_BODY_EDGES = 60
DIRECTION_BINS = 8
MIN_EDGES_PER_DIRECTION = 15
# Edges must rise this far above the estimated sensor noise. Pure noise peaks at
# ~0.6 x sigma_noise in mag0, and noise "edges" look perfectly sharp, so smooth
# regions (sky, bokeh) would otherwise report impossible sub-0.6 px radii.
NOISE_EDGE_FACTOR = 2.0


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


def _stats(samples: np.ndarray) -> EdgeBlurStats:
    if samples.size < 8:
        return EdgeBlurStats(None, None, None, int(samples.size))
    return EdgeBlurStats(
        float(np.median(samples)), float(np.percentile(samples, 25)), float(np.percentile(samples, 75)),
        int(samples.size),
    )


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

    def _sigma_at(self, sel: np.ndarray) -> np.ndarray:
        ratio = self.mag0[sel] / np.maximum(self.mag1[sel], 1e-9)
        ratio = ratio[ratio > 1.02]
        total = self.reblur_sigma / np.sqrt(ratio ** 2 - 1.0)
        # A step edge already smoothed by pre_sigma cannot measure below it. Thinner
        # structures (eye-ring lines, catchlights, twigs) do and would read as
        # impossibly sharp, so they are not edges for this estimator.
        total = total[total >= self.pre_sigma]
        return np.sqrt(total ** 2 - self.pre_sigma ** 2)

    def strongest_edge_samples(self, region: Optional[np.ndarray], *, top_fraction: float = 0.05,
                               min_edges: int = MIN_HEAD_EDGES) -> np.ndarray:
        """Blur radii at the strongest edges inside ``region`` (``None`` = everywhere)."""
        edges = self._edges(30, 90) & (self.mag0 > NOISE_EDGE_FACTOR * self.noise_sigma)
        if region is not None:
            edges = edges & region
        n = int(edges.sum())
        if n < min_edges:
            return np.empty(0, np.float32)
        keep = max(top_fraction, min(1.0, 30.0 / n))
        thr = float(np.quantile(self.mag0[edges], 1.0 - keep))
        return self._sigma_at(edges & (self.mag0 >= thr))

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
        if not region.any():
            return EdgeBlurStats(None, None, None, 0), None
        noise_floor = float(np.median(self.mag0[region]))
        edges = self._edges(20, 60) & region & (self.mag0 > 4.0 * noise_floor)
        n = int(edges.sum())
        if n < min_edges:
            return EdgeBlurStats(None, None, None, n), None
        ratio = self.mag0[edges] / np.maximum(self.mag1[edges], 1e-9)
        ok = ratio > 1.02
        total = self.reblur_sigma / np.sqrt(np.maximum(ratio ** 2 - 1.0, 1e-6))
        ok = ok & (total >= self.pre_sigma)  # drop line-like responses, as in _sigma_at
        sig = np.sqrt(np.maximum(total ** 2 - self.pre_sigma ** 2, 0.0))
        theta = np.mod(np.arctan2(self.gy0[edges], self.gx0[edges]), np.pi)
        per_dir = []
        for k in range(DIRECTION_BINS):
            center = k * np.pi / DIRECTION_BINS
            dist = np.abs(np.angle(np.exp(2j * (theta - center)))) / 2.0
            sel = ok & (dist < np.pi / (2 * DIRECTION_BINS))
            if int(sel.sum()) >= MIN_EDGES_PER_DIRECTION:
                per_dir.append(float(np.median(sig[sel])))
        motion_ratio = None
        if len(per_dir) >= DIRECTION_BINS // 2:
            motion_ratio = float(max(per_dir) / max(min(per_dir), 0.3))
        valid = sig[ok]
        if valid.size < 8:
            return EdgeBlurStats(None, None, None, int(valid.size)), motion_ratio
        stats = EdgeBlurStats(
            float(np.median(valid)), float(np.percentile(valid, 25)), float(np.percentile(valid, 75)), int(valid.size)
        )
        return stats, motion_ratio


FULL_IMAGE_TILE = 1024


def full_image_blur(gray: np.ndarray, *, tile: int = FULL_IMAGE_TILE, top_fraction: float = 0.05,
                    cancelled=lambda: False) -> EdgeBlurStats:
    """Whole-image blur radius, tile by tile so memory stays bounded on 25-60 MP frames.

    Each tile contributes the radii at its own strongest edges; the result is the
    median over all tiles' samples.
    """
    h, w = gray.shape[:2]
    samples = []
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            if cancelled():
                return EdgeBlurStats(None, None, None, 0)
            block = gray[y:y + tile, x:x + tile]
            if min(block.shape[:2]) < 32:
                continue
            part = EdgeBlurField(block).strongest_edge_samples(None, top_fraction=top_fraction)
            if part.size:
                samples.append(part)
    return _stats(np.concatenate(samples) if samples else np.empty(0, np.float32))
