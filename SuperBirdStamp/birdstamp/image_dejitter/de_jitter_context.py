from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .constants import DEFAULT_MIN_CONFIDENCE
from .de_jitter_frame import DeJitterFrame
from .feature_aligner import FeatureAligner
from .normalized_box import NormalizedBox


@dataclass(slots=True)
class DeJitterContext:
    """去抖动策略运行所需的封装上下文。

    支持 0 / 1 / N 个参考区：``reference_regions`` 为空时只有中位混合等无参考策略
    可用；为多个时由支持多区域的策略聚合处理。
    """

    frames: list[DeJitterFrame]
    strength: int = 0
    reference_regions: tuple[NormalizedBox, ...] = ()
    reference_patches: tuple[np.ndarray, ...] = ()
    reference_raw_center: tuple[float, float] | None = None
    reference_source: Path | None = None
    aligner: FeatureAligner | None = None
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    extra: dict[str, Any] = field(default_factory=dict)
