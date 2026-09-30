"""Experimental, non-generative subject translation from user-selected local regions.

Input images/masks share the same ORIGINAL analysis pixel coordinate system.
A bird detector is only an upstream target selector: no detector/weights are loaded.
No background, scale, rotation, or deformation model is inferred here.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping, Sequence
import numpy as np


@dataclass(frozen=True)
class Options:
    max_corners: int = 140
    quality: float = 0.006
    min_distance: float = 7.0
    fb_threshold: float = 1.5
    consensus_threshold: float = 2.5
    max_displacement: float = 100.0
    min_region_points: int = 5
    min_total_points: int = 12
    min_track_fraction: float = 0.4
    min_consensus_fraction: float = 0.5
    max_region_disagreement: float = 3.5

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.max_corners > 1000:
            raise ValueError("max_corners is capped at 1000 per region")
        if not 0 < self.quality <= 1:
            raise ValueError("quality must be in (0, 1]")
        if max(self.min_track_fraction, self.min_consensus_fraction) > 1:
            raise ValueError("fractions must not exceed 1")
        for name in ('max_corners', 'min_region_points', 'min_total_points'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")


@dataclass
class RegionTrack:
    reference: np.ndarray
    moving: np.ndarray
    valid: np.ndarray
    inliers: np.ndarray
    fb_error: np.ndarray
    displacement: np.ndarray | None


def consensus_translation(a: np.ndarray, b: np.ndarray,
                          threshold: float) -> tuple[np.ndarray | None, np.ndarray]:
    """Deterministic maximum-consensus translation, then componentwise median.

    This is a bounded experiment (<=1000 points), not an unbounded dense solver.
    It estimates subject displacement, NOT true camera motion.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 2:
        raise ValueError("point arrays must be finite Nx2 arrays of equal shape")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("nonfinite point coordinates")
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("threshold must be positive")
    if len(a) > 4000:
        raise ValueError("too many points for the experimental consensus solver")
    if len(a) == 0:
        return None, np.zeros(0, dtype=bool)
    delta = b - a
    # Compute counts in bounded-memory chunks rather than an NxNx2 tensor.
    counts = np.empty(len(delta), dtype=int)
    for start in range(0, len(delta), 128):
        distance = np.linalg.norm(delta[start:start+128, None] - delta[None], axis=2)
        counts[start:start+128] = (distance < threshold).sum(axis=1)
    inliers = np.linalg.norm(delta - delta[int(counts.argmax())], axis=1) < threshold
    for _ in range(3):
        displacement = np.median(delta[inliers], axis=0)
        updated = np.linalg.norm(delta - displacement, axis=1) < threshold
        if not updated.any():
            break
        inliers = updated
    return np.median(delta[inliers], axis=0), inliers


def _empty_track() -> RegionTrack:
    return RegionTrack(np.empty((0, 2), np.float32), np.empty((0, 2), np.float32),
                       np.zeros(0, bool), np.zeros(0, bool), np.empty(0), None)


def track_region(reference: np.ndarray, moving: np.ndarray, mask: np.ndarray,
                 options: Options) -> RegionTrack:
    import cv2  # Optional dependency; importing this module doesn't load Qt/Torch.
    points = cv2.goodFeaturesToTrack(
        reference, maxCorners=options.max_corners, qualityLevel=options.quality,
        minDistance=options.min_distance, mask=mask, blockSize=5)
    if points is None:
        return _empty_track()
    level = min(4, max(0, int(np.floor(np.log2(max(1, min(reference.shape) / 64))))))
    kwargs = dict(winSize=(25, 25), maxLevel=level, criteria=(3, 40, 0.005))
    q, status, _ = cv2.calcOpticalFlowPyrLK(reference, moving, points, None, **kwargs)
    if q is None or status is None:
        return _empty_track()
    r, reverse_status, _ = cv2.calcOpticalFlowPyrLK(moving, reference, q, None, **kwargs)
    if r is None or reverse_status is None:
        return _empty_track()
    a, b = points[:, 0], q[:, 0]
    fb = np.linalg.norm(a - r[:, 0], axis=1)
    h, w = reference.shape
    valid = (status[:, 0].astype(bool) & reverse_status[:, 0].astype(bool)
             & np.isfinite(b).all(axis=1) & np.isfinite(fb)
             & (fb < options.fb_threshold)
             & (np.linalg.norm(b-a, axis=1) < options.max_displacement)
             & (b[:, 0] >= 0) & (b[:, 0] < w) & (b[:, 1] >= 0) & (b[:, 1] < h))
    displacement, selected = consensus_translation(a[valid], b[valid], options.consensus_threshold)
    inliers = np.zeros(len(a), dtype=bool)
    inliers[np.flatnonzero(valid)[selected]] = True
    return RegionTrack(a, b, valid, inliers, fb, displacement)


def analyze_pair(reference: np.ndarray, moving: np.ndarray,
                 masks: Mapping[str, np.ndarray], fit_regions: Sequence[str],
                 options: Options | None = None) -> tuple[dict, dict[str, RegionTrack]]:
    """Return a JSON-compatible report and actual feature tracks.

    fit_regions are explicitly chosen by the caller (e.g. left_leg/right_leg).
    An empty/ambiguous region refuses the transform; it never falls back to water.
    Report displacement is reference -> moving. The correction is its negative.
    Median residual is fit consistency, NOT independent ground-truth accuracy.
    """
    options = options or Options()
    options.validate()
    if (reference.ndim != 2 or moving.ndim != 2 or reference.shape != moving.shape
            or reference.dtype != np.uint8 or moving.dtype != np.uint8):
        raise ValueError("images must be same-size uint8 grayscale arrays")
    if not fit_regions or any(key not in masks for key in fit_regions):
        raise ValueError("fit_regions must select existing masks")
    if len(set(fit_regions)) != len(fit_regions):
        raise ValueError("duplicate fit regions")
    if len(fit_regions) * options.max_corners > 4000:
        raise ValueError("too many anchor regions for the experimental solver")
    occupied = np.zeros(reference.shape, dtype=bool)
    for key, mask in masks.items():
        if mask.shape != reference.shape or mask.dtype != np.uint8:
            raise ValueError(f"{key}: mask must be same-size uint8")
        if key in fit_regions:
            if (occupied & (mask > 0)).any():
                raise ValueError("anchor masks must not overlap (avoid duplicate votes)")
            occupied |= mask > 0
    tracks = {key: track_region(reference, moving, mask, options) for key, mask in masks.items()}
    report = dict(status="needs_keyframe", correction=None, displacement=None,
                  model="translation_only", scale_applied=1.0, rotation_applied_degrees=0.0,
                  selected_regions=list(fit_regions), options=asdict(options), regions={},
                  coordinate_system="input analysis pixels", reason="")
    for key, track in tracks.items():
        report["regions"][key] = dict(
            candidates=len(track.reference), valid_tracks=int(track.valid.sum()),
            local_inliers=int(track.inliers.sum()),
            displacement=None if track.displacement is None else track.displacement.tolist())
    anchors = [tracks[key] for key in fit_regions]
    for key, track in zip(fit_regions, anchors):
        if (track.displacement is None or int(track.inliers.sum()) < options.min_region_points
                or track.valid.sum() < options.min_track_fraction * len(track.valid)):
            report['reason'] = f"insufficient reliable points in {key}"
            return report, tracks
    displacements = np.array([track.displacement for track in anchors])
    disagreement = float(np.linalg.norm(displacements[:, None]-displacements[None], axis=2).max())
    report['region_disagreement_px'] = disagreement
    if disagreement > options.max_region_disagreement:
        report['reason'] = "selected regions disagree: pose change, wrong match, or stepping"
        return report, tracks
    a = np.concatenate([track.reference[track.valid] for track in anchors])
    b = np.concatenate([track.moving[track.valid] for track in anchors])
    displacement, inliers = consensus_translation(a, b, options.consensus_threshold)
    count = int(inliers.sum())
    report.update(valid_anchor_tracks=len(a), anchor_inliers=count)
    if count < options.min_total_points or count < options.min_consensus_fraction * len(a):
        report['reason'] = "insufficient common translation consensus"
        return report, tracks
    residual = np.linalg.norm((b-a)-displacement, axis=1)
    report.update(status="ok", reason="accepted selected-anchor consensus",
                  displacement=displacement.tolist(), correction=(-displacement).tolist(),
                  median_inlier_residual_px=float(np.median(residual[inliers])),
                  median_all_valid_residual_px=float(np.median(residual)),
                  median_before_displacement_px=float(np.median(np.linalg.norm(b-a, axis=1))))
    return report, tracks


def warp_translation(image: np.ndarray, correction: Sequence[float]) -> np.ndarray:
    """Single resampling, zero border, no generative filling. Crop separately."""
    import cv2
    correction = np.asarray(correction, dtype=np.float64)
    if correction.shape != (2,) or not np.isfinite(correction).all():
        raise ValueError("correction must be finite (dx, dy)")
    return cv2.warpAffine(image, np.array([[1, 0, correction[0]], [0, 1, correction[1]]]),
                          (image.shape[1], image.shape[0]), flags=cv2.INTER_LANCZOS4,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)
