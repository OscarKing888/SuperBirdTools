"""Direction-balanced head blur evidence, independent of body/wing motion.

The strongest gradients favour edges parallel to a motion streak. Fit the
squared blur of *all* above-noise head edges to a 2-D covariance instead:
sigma(theta)^2 = a + b*cos(2*theta) + c*sin(2*theta). Each populated direction
has one vote, so one long narrow edge cannot outweigh the blurred directions.
This is supplementary evidence: isotropic heads retain the calibrated score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .metrics import DIRECTION_BINS, EdgeBlurField, MIN_EDGES_PER_DIRECTION, SIGMA_FLOOR_PX
from .scoring import MOTION_RATIO_MIN, SIGMA_USABLE_MAX

MIN_HEAD_DIRECTIONS = 3
MAX_DIRECTION_CONDITION = 6.0
MAX_DIRECTION_RESIDUAL = 0.35
MIN_CONSTRAINED_DIRECTIONS = 6


@dataclass(frozen=True)
class HeadDirection:
    bins: Tuple[Optional[float], ...]
    counts: Tuple[int, ...]
    major: Optional[float] = None
    minor: Optional[float] = None
    ratio: Optional[float] = None

    @property
    def motion(self) -> bool:
        return (self.major is not None and self.major > SIGMA_USABLE_MAX
                and self.ratio is not None and self.ratio >= MOTION_RATIO_MIN)


def head_direction(field: EdgeBlurField, region: np.ndarray) -> HeadDirection:
    """Measure only an explicitly located head, never the whole bird fallback.

    Require enough samples per direction, a well-conditioned orientation fit,
    and agreement with directional blur. Sparse/one-direction texture cannot
    establish motion; no evidence is different from an isotropic sharp head.
    """
    selection = field.select_strongest_edges(region, top_fraction=1.0)
    theta = np.mod(np.arctan2(field.gy0[selection.ys, selection.xs],
                            field.gx0[selection.ys, selection.xs]), np.pi)
    bins, counts = [], []
    for k in range(DIRECTION_BINS):
        center = k * np.pi / DIRECTION_BINS
        distance = np.abs(np.angle(np.exp(2j * (theta - center)))) / 2.0
        values = selection.sigma[distance < np.pi / (2 * DIRECTION_BINS)]
        counts.append(int(values.size))
        bins.append(float(np.median(values)) if values.size >= MIN_EDGES_PER_DIRECTION else None)
    empty = HeadDirection(tuple(bins), tuple(counts))
    indices = np.array([k for k, value in enumerate(bins) if value is not None])
    if indices.size < MIN_HEAD_DIRECTIONS:
        return empty
    angle = indices * np.pi / DIRECTION_BINS
    design = np.column_stack((np.ones(indices.size), np.cos(2 * angle), np.sin(2 * angle)))
    if np.linalg.cond(design) > MAX_DIRECTION_CONDITION:
        return empty
    values = np.array([bins[k] for k in indices]) ** 2
    fit = np.linalg.lstsq(design, values, rcond=None)[0]
    a, b, c = fit
    amplitude = float(np.hypot(b, c))
    if a <= 0:
        return empty
    if a - amplitude < 0:
        # A box-shaped motion kernel is not Gaussian: its squared edge response
        # can fit a slightly negative minor axis. With broad direction coverage
        # project onto a physical covariance, then validate the *projected* fit.
        # Three directions fit exactly and cannot justify this correction.
        if indices.size < MIN_CONSTRAINED_DIRECTIONS:
            return empty
        a = amplitude + SIGMA_FLOOR_PX ** 2
        fit = np.array([a, b, c])
    if np.sqrt(np.mean((design @ fit - values) ** 2)) / a > MAX_DIRECTION_RESIDUAL:
        return empty
    minor = float(np.sqrt(max(SIGMA_FLOOR_PX ** 2, a - amplitude)))
    # Do not extrapolate beyond the broadest direction actually measured.
    major = min(float(np.sqrt(max(0.0, a + amplitude))), max(v for v in bins if v is not None))
    return HeadDirection(tuple(bins), tuple(counts), major, minor, major / minor)
