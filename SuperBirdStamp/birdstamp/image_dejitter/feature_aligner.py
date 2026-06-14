from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class FeatureAligner(ABC):
    """特征对齐后端抽象：估计 ``target`` 相对 ``ref`` 的平移。"""

    aligner_id = "aligner"

    @abstractmethod
    def estimate_translation(
        self,
        ref: np.ndarray,
        target: np.ndarray,
    ) -> tuple[float, float, float]:
        """返回 ``(dx, dy, confidence)``。

        约定：当 ``target`` 的内容相对 ``ref`` 向右/下平移了 ``(dx, dy)`` 像素时，
        返回的 ``dx``/``dy`` 为正。``confidence`` 越大表示匹配越可靠。
        """
        raise NotImplementedError
