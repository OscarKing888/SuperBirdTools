from __future__ import annotations

from .constants import STRATEGY_MEDIAN
from .de_jitter_context import DeJitterContext
from .de_jitter_strategy import DeJitterStrategy
from .dejitter_utils import clamp_percent, median_float


class MedianCenterStabilizationStrategy(DeJitterStrategy):
    """把每帧裁切中心向中位中心线性混合（保留原有防抖行为）。"""

    strategy_id = STRATEGY_MEDIAN
    label = "中位中心混合"
    requires_reference_regions = False
    supports_multiple_regions = False

    def stabilize(self, context: DeJitterContext) -> None:
        frames = context.frames
        if len(frames) <= 1:
            return
        blend = clamp_percent(context.strength, 0) / 100.0
        if blend <= 0:
            return
        center_x = median_float([float(frame.center_norm[0]) for frame in frames])
        center_y = median_float([float(frame.center_norm[1]) for frame in frames])
        if center_x is None or center_y is None:
            return
        for frame in frames:
            raw_x, raw_y = frame.center_norm
            stable_x = float(raw_x) * (1.0 - blend) + center_x * blend
            stable_y = float(raw_y) * (1.0 - blend) + center_y * blend
            frame.stable_center = (
                stable_x * float(frame.source_width),
                stable_y * float(frame.source_height),
            )
