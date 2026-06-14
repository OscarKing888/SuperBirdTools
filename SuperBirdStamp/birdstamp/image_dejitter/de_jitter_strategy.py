from __future__ import annotations

from abc import ABC, abstractmethod

from .de_jitter_context import DeJitterContext


class DeJitterStrategy(ABC):
    strategy_id = "strategy"
    label = "去抖动策略"
    requires_reference_regions = False
    supports_multiple_regions = False

    @abstractmethod
    def stabilize(self, context: DeJitterContext) -> None:
        """就地把稳定后中心写入每个 frame 的 ``stable_center``。"""
        raise NotImplementedError
