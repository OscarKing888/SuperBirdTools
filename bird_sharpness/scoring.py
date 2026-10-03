"""Map measured blur radii to SuperPicky-compatible sharpness scores and verdicts.

This module has no third-party dependencies so UI code can format/label values
without importing OpenCV or Torch.

The SuperPicky sharpness scale is 0..1000 with these meaningful gates
(``core/rating_quota.py`` / ``core/rating_engine.py`` in SuperPicky):

* ``< 100``  -> rejected as blurry
* ``>= 300`` -> eligible for 3 stars (V2 quota)
* ``>= 400`` -> 3-star sharpness in the V1 engine

The blur radius ``sigma`` (pixels at full analysis resolution, Gaussian-equivalent)
is mapped piecewise-linearly so that those gates line up with what a 100% view
shows: ``sigma <= 0.85`` crisp, ``0.85..1.05`` usable, ``> 1.05`` soft and
``>= 1.55`` clearly blurred (v2: noise-level and line-like edges excluded, which
lifts every radius by ~0.05-0.1 px versus v1's 0.80/1.00/1.50; v3: edges must
reach 4x the noise level, and a visible head without any is "clearly blurred"). Calibrated on a Sony ILCE-1M2 ISO 2500-3200 burst
(LibRaw LINEAR demosaic, analysis at full output resolution); sigma is in pixels
of that resolution, so the same thresholds describe what a 100% view shows.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from app_common.bird_sharpness_fields import (
    VERDICT_ERROR,
    VERDICT_MOTION,
    VERDICT_NO_BIRD,
    VERDICT_NO_EYE,
    VERDICT_SHARP,
    VERDICT_SOFT,
    VERDICT_STYLES,
    VERDICT_USABLE,
)

ALGORITHM_VERSION = "sbt-blur-v5"

# (sigma_px, score) anchors, sigma ascending / score descending.
SCORE_ANCHORS: Tuple[Tuple[float, float], ...] = (
    (0.50, 1000.0),
    (0.85, 500.0),
    (1.05, 300.0),
    (1.55, 100.0),
    (2.55, 0.0),
)

SIGMA_SHARP_MAX = 0.85
SIGMA_USABLE_MAX = 1.05
SIGMA_BLURRED_MIN = 1.55
# Directional blur ratio (max/min over edge orientations) that marks motion blur.
MOTION_RATIO_MIN = 1.5
# Without a visible eye the head cannot be confirmed sharp: cap below "usable".
NO_EYE_SCORE_CAP = 299


def verdict_label(verdict: Optional[str]) -> str:
    style = VERDICT_STYLES.get(str(verdict or "").strip())
    return style.label if style else ""


def sigma_to_score(sigma: Optional[float], anchors: Sequence[Tuple[float, float]] = SCORE_ANCHORS) -> Optional[int]:
    """Return a 0..1000 score for a blur radius, or ``None`` when unknown."""
    if sigma is None:
        return None
    try:
        s = float(sigma)
    except (TypeError, ValueError):
        return None
    if s != s:  # NaN
        return None
    if s <= anchors[0][0]:
        return int(round(anchors[0][1]))
    for (s0, v0), (s1, v1) in zip(anchors, anchors[1:]):
        if s <= s1:
            t = (s - s0) / (s1 - s0)
            return int(round(v0 + (v1 - v0) * t))
    return int(round(anchors[-1][1]))


def blank_head_sigma(body_sigma: Optional[float]) -> float:
    """Blur radius reported for a visible head that has no edge above the noise.

    A feathered head with an eye always has edges unless it is badly blurred, so
    it counts as at least "clearly blurred" (or the body's radius if worse).
    """
    return max(float(body_sigma or 0.0), SIGMA_BLURRED_MIN)


def classify(
    head_sigma: Optional[float],
    body_sigma: Optional[float],
    motion_ratio: Optional[float],
    *,
    eye_visible: bool,
    head_blank: bool = False,
) -> Tuple[str, Optional[int]]:
    """Return ``(verdict, score)`` from the measured blur values.

    ``head_blank``: the eye is visible but the head had no measurable edge.
    """
    motion_like = (
        body_sigma is not None
        and body_sigma >= SIGMA_BLURRED_MIN
        and motion_ratio is not None
        and motion_ratio >= MOTION_RATIO_MIN
    )
    if eye_visible and head_sigma is None and head_blank:
        sigma = blank_head_sigma(body_sigma)
        return (VERDICT_MOTION if motion_like else VERDICT_SOFT), sigma_to_score(sigma)
    if eye_visible and head_sigma is not None:
        score = sigma_to_score(head_sigma)
        if head_sigma <= SIGMA_SHARP_MAX:
            return VERDICT_SHARP, score
        if head_sigma <= SIGMA_USABLE_MAX:
            return VERDICT_USABLE, score
        return (VERDICT_MOTION if motion_like else VERDICT_SOFT), score
    if body_sigma is None:
        return VERDICT_NO_EYE, None
    score = sigma_to_score(body_sigma)
    if score is not None:
        score = min(score, NO_EYE_SCORE_CAP)
    if motion_like:
        return VERDICT_MOTION, score
    if body_sigma >= SIGMA_BLURRED_MIN:
        return VERDICT_SOFT, score
    return VERDICT_NO_EYE, score
