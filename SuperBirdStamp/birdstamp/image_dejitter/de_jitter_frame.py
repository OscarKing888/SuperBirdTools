from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(slots=True)
class DeJitterFrame:
    """单帧去抖动上下文。

    ``region_patches`` 为该帧在各参考区（归一化坐标）位置抽取并缩放到统一尺寸的
    灰度 patch，顺序与 :attr:`DeJitterContext.reference_patches` 对齐；当不使用参考区
    策略时可为空。
    """

    source_width: int
    source_height: int
    center: tuple[float, float]
    center_norm: tuple[float, float]
    strength: int = 0
    source_path: Path | None = None
    region_patches: tuple[np.ndarray, ...] = ()
    is_reference: bool = False
    stable_center: tuple[float, float] | None = None
