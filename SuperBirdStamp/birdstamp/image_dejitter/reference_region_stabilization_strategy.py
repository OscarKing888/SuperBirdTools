from __future__ import annotations

import numpy as np

from .constants import STRATEGY_REFERENCE_REGION
from .de_jitter_context import DeJitterContext
from .de_jitter_frame import DeJitterFrame
from .de_jitter_strategy import DeJitterStrategy
from .dejitter_utils import clamp_percent, median_float
from .feature_aligner import FeatureAligner
from .normalized_box import NormalizedBox
from .numpy_phase_correlation_aligner import NumpyPhaseCorrelationAligner


class ReferenceRegionStabilizationStrategy(DeJitterStrategy):
    """以初始帧框选的特征参考区为锚点，按帧间平移补偿裁切中心。"""

    strategy_id = STRATEGY_REFERENCE_REGION
    label = "参考区特征对齐"
    requires_reference_regions = True
    supports_multiple_regions = True

    def stabilize(self, context: DeJitterContext) -> None:
        frames = context.frames
        if not frames:
            return
        aligner = context.aligner or NumpyPhaseCorrelationAligner()
        reference_patches = context.reference_patches
        regions = context.reference_regions
        if not reference_patches or not regions:
            return
        blend = clamp_percent(context.strength, 0) / 100.0
        if blend <= 0:
            blend = 1.0

        for frame in frames:
            displacement = self._estimate_frame_displacement(
                frame=frame,
                regions=regions,
                reference_patches=reference_patches,
                aligner=aligner,
                min_confidence=context.min_confidence,
            )
            raw_center = frame.center
            if displacement is None:
                frame.stable_center = raw_center
                continue
            base_center = context.reference_raw_center or raw_center
            target_x = float(base_center[0]) + displacement[0]
            target_y = float(base_center[1]) + displacement[1]
            stable_x = float(raw_center[0]) * (1.0 - blend) + target_x * blend
            stable_y = float(raw_center[1]) * (1.0 - blend) + target_y * blend
            frame.stable_center = (stable_x, stable_y)

    @staticmethod
    def _estimate_frame_displacement(
        *,
        frame: DeJitterFrame,
        regions: tuple[NormalizedBox, ...],
        reference_patches: tuple[np.ndarray, ...],
        aligner: FeatureAligner,
        min_confidence: float,
    ) -> tuple[float, float] | None:
        if not frame.region_patches:
            return None
        count = min(len(regions), len(reference_patches), len(frame.region_patches))
        if count <= 0:
            return None
        dxs: list[float] = []
        dys: list[float] = []
        for index in range(count):
            ref_patch = reference_patches[index]
            target_patch = frame.region_patches[index]
            if ref_patch is None or target_patch is None:
                continue
            patch_h, patch_w = np.asarray(ref_patch).shape[:2]
            if patch_h < 2 or patch_w < 2:
                continue
            dx_patch, dy_patch, confidence = aligner.estimate_translation(ref_patch, target_patch)
            if confidence < float(min_confidence):
                continue
            box = regions[index]
            region_px_w = max(1.0, (float(box[2]) - float(box[0])) * float(frame.source_width))
            region_px_h = max(1.0, (float(box[3]) - float(box[1])) * float(frame.source_height))
            scale_x = region_px_w / float(patch_w)
            scale_y = region_px_h / float(patch_h)
            disp_x = dx_patch * scale_x
            disp_y = dy_patch * scale_y
            disp_x = max(-region_px_w * 0.5, min(region_px_w * 0.5, disp_x))
            disp_y = max(-region_px_h * 0.5, min(region_px_h * 0.5, disp_y))
            dxs.append(disp_x)
            dys.append(disp_y)
        if not dxs:
            return None
        median_x = median_float(dxs)
        median_y = median_float(dys)
        if median_x is None or median_y is None:
            return None
        return (median_x, median_y)
