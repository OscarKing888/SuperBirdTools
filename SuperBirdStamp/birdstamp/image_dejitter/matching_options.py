"""Independent reference matching settings, shared by workers, GUI and cache keys."""
from dataclasses import dataclass
from math import isfinite

MATCHING_KEYS = ('dejitter_match_mode', 'dejitter_match_rotation_deg', 'dejitter_match_tolerance_pct')
ROTATION_RANGE = (0.0, 5.0)
TOLERANCE_RANGE = (0.05, 1.0)


def _bounded(value, default, limits):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(limits[0], min(limits[1], number)) if isfinite(number) else default


def normalize_matching_settings(settings=None):
    settings = settings or {}
    return dict(zip(MATCHING_KEYS, (
        'custom' if settings.get(MATCHING_KEYS[0]) == 'custom' else 'auto',
        round(_bounded(settings.get(MATCHING_KEYS[1]), 2.0, ROTATION_RANGE), 1),
        round(_bounded(settings.get(MATCHING_KEYS[2]), .3, TOLERANCE_RANGE), 2),
    )))


@dataclass(frozen=True, slots=True)
class MatchingOptions:
    rotation_degrees: float = 2.0
    tolerance_percent: float = .3

    @classmethod
    def from_settings(cls, settings=None):
        normalized = normalize_matching_settings(settings)
        if normalized[MATCHING_KEYS[0]] == 'auto':
            return cls()
        return cls(normalized[MATCHING_KEYS[1]], normalized[MATCHING_KEYS[2]])

    def pixel_tolerance(self, size):
        return max(1, min(size)*self.tolerance_percent/100)
