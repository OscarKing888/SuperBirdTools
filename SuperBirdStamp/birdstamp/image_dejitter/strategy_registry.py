from __future__ import annotations

from typing import Any

from .constants import DEFAULT_STRATEGY, STRATEGY_REFERENCE_REGION
from .de_jitter_strategy import DeJitterStrategy
from .reference_region_stabilization_strategy import ReferenceRegionStabilizationStrategy

_STRATEGY_REGISTRY: dict[str, type[DeJitterStrategy]] = {
    STRATEGY_REFERENCE_REGION: ReferenceRegionStabilizationStrategy,
}


def normalize_strategy_id(value: Any) -> str:
    strategy_id = str(value or "").strip().lower()
    if strategy_id in _STRATEGY_REGISTRY:
        return strategy_id
    return DEFAULT_STRATEGY


def resolve_dejitter_strategy(strategy_id: Any) -> DeJitterStrategy | None:
    strategy = _STRATEGY_REGISTRY.get(normalize_strategy_id(strategy_id))
    return strategy() if strategy is not None else None
