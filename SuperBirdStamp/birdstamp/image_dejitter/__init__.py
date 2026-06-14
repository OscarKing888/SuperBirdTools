"""Image de-jitter (crop center stabilization): one public class per module.

Module layout:
- ``normalized_box`` — ``NormalizedBox`` type alias
- ``constants`` — strategy ids and default tuning values
- ``dejitter_utils`` — shared numeric helpers
- ``de_jitter_frame`` — ``DeJitterFrame``
- ``de_jitter_context`` — ``DeJitterContext``
- ``feature_aligner`` — ``FeatureAligner`` ABC
- ``numpy_phase_correlation_aligner`` — ``NumpyPhaseCorrelationAligner``
- ``de_jitter_strategy`` — ``DeJitterStrategy`` ABC
- ``median_center_stabilization_strategy`` — ``MedianCenterStabilizationStrategy``
- ``reference_region_stabilization_strategy`` — ``ReferenceRegionStabilizationStrategy``
- ``strategy_registry`` — ``normalize_strategy_id``, ``resolve_dejitter_strategy``

External code should import from ``birdstamp.image_dejitter`` (this package).
"""
from __future__ import annotations

from .constants import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_PATCH_SIZE,
    DEFAULT_STRATEGY,
    STRATEGY_MEDIAN,
    STRATEGY_REFERENCE_REGION,
)
from .de_jitter_context import DeJitterContext
from .de_jitter_frame import DeJitterFrame
from .de_jitter_strategy import DeJitterStrategy
from .feature_aligner import FeatureAligner
from .median_center_stabilization_strategy import MedianCenterStabilizationStrategy
from .normalized_box import NormalizedBox
from .numpy_phase_correlation_aligner import NumpyPhaseCorrelationAligner
from .reference_region_stabilization_strategy import ReferenceRegionStabilizationStrategy
from .strategy_registry import normalize_strategy_id, resolve_dejitter_strategy

__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_PATCH_SIZE",
    "DEFAULT_STRATEGY",
    "STRATEGY_MEDIAN",
    "STRATEGY_REFERENCE_REGION",
    "DeJitterContext",
    "DeJitterFrame",
    "DeJitterStrategy",
    "FeatureAligner",
    "MedianCenterStabilizationStrategy",
    "NormalizedBox",
    "NumpyPhaseCorrelationAligner",
    "ReferenceRegionStabilizationStrategy",
    "normalize_strategy_id",
    "resolve_dejitter_strategy",
]
