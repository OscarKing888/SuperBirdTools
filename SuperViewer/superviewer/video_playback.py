# -*- coding: utf-8 -*-
"""Frame/time conversion for video transport, independent of Qt widgets."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FrameClock:
    duration_ms: int
    fps: float

    @property
    def available(self):
        return self.duration_ms > 0 and math.isfinite(self.fps) and self.fps > 0

    @property
    def last_frame(self):
        return max(0, math.ceil(self.duration_ms * self.fps / 1000 - 1e-6) - 1)

    def frame_at(self, milliseconds):
        return max(0, min(self.last_frame, math.floor(milliseconds * self.fps / 1000 + 1e-6)))

    def position(self, frame):
        # 从绝对帧号换算，避免 29.97/59.94 fps 逐次加减毫秒带来的累计偏移。
        # 严格落在目标帧内：Qt 定位恰好等于前帧 endTime 时可能仍显示前帧。
        frame = max(0, min(self.last_frame, frame))
        return min(self.duration_ms - 1, math.floor(frame * 1000 / self.fps) + 1) if frame else 0
