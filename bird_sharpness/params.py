"""Every tunable of one sharpness analysis, in one place.

The pipeline is the same for batch detection and the trace (debug) window; only
where the parameters come from differs: SuperViewer's user options (设置 → 鸟清晰度)
for batch runs, the trace window's 参数 tab for one window, CLI flags. All of them
build an :class:`AnalysisParams` from the same flat dict (:meth:`AnalysisParams.as_params`).
Non-default values are tagged into the algorithm version (:meth:`version_tags`), so
"skip analysed" never mixes results of different settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .metrics import EDGE_ESTIMATORS, ESTIMATOR_STANDARD, TileOptions

ENH_OFF, ENH_MANUAL, ENH_NOBIRD = "off", "manual", "nobird"
ENH_MODES = (ENH_OFF, ENH_MANUAL, ENH_NOBIRD)
SAM_SCOPE_RECHECKED, SAM_SCOPE_ALL = "rechecked", "all"
SAM_SCOPES = (SAM_SCOPE_RECHECKED, SAM_SCOPE_ALL)
_MODEL_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.pt$")


def _clamp(value, low, high, default) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def _model_name(value, default: str, builtin: str) -> str:
    text = str(value if value is not None else default).strip()
    return text if text == builtin or _MODEL_FILE_RE.match(text) else default


@dataclass(frozen=True)
class EnhancedSearch:
    """Enhanced bird search when the normal passes find no bird.

    The centre region (``region_percent`` of each side, centred on the camera focus
    box when there is one) is covered by ``grid`` x ``grid`` overlapping windows,
    each detected at ``imgsz`` network input, so a small or half-hidden bird fills
    far more of the network's view (DSC05639: bird 0.01 on the whole frame, 0.67
    zoomed). Zoomed leaves also read as birds (0.3-0.8), so candidates are only
    taken at ``min_conf_percent``; the trace shows every candidate for tuning.
    ``mode``: off / manual (manual-focus photos only) / nobird (every no-bird photo).
    """

    mode: str = ENH_OFF
    # 70 % / 2 x 2 (windows ~0.4 of the frame): windows smaller than the bird only see slices of it
    # (DSC05639, a bird ~0.26 of the frame wide, behind leaves: 50 % / 3 x 3 took a twig at 0.65).
    region_percent: int = 70
    grid: int = 2
    imgsz: int = 1024
    min_conf_percent: int = 50
    lift: bool = True  # lift dark mid-tones for detection (never measured)

    def normalized(self) -> "EnhancedSearch":
        imgsz = _clamp(self.imgsz, 320, 2048, 1024)
        return EnhancedSearch(self.mode if self.mode in ENH_MODES else ENH_OFF,
                              _clamp(self.region_percent, 20, 100, 70), _clamp(self.grid, 1, 6, 2),
                              max(320, imgsz // 32 * 32), _clamp(self.min_conf_percent, 5, 95, 50), bool(self.lift))

    def version_tag(self) -> str:
        o = self.normalized()
        if o.mode == ENH_OFF:
            return ""
        return (f"enh-{o.mode}{o.region_percent}g{o.grid}i{o.imgsz}c{o.min_conf_percent}"
                + ("" if o.lift else "-nolift"))


@dataclass(frozen=True)
class AnalysisParams:
    max_birds: int = 0                       # 0 = every bird
    edge_estimator: str = ESTIMATOR_STANDARD.key
    detector: str = "auto"                   # model file name, "auto" = built-in choice
    sam_model: str = ""                      # SAM/SAM2 file name, "" = no refinement
    sam_scope: str = SAM_SCOPE_RECHECKED     # rechecked: birds the normal pass missed; all: every bird
    enhanced: EnhancedSearch = field(default_factory=EnhancedSearch)
    tiles: TileOptions = field(default_factory=TileOptions)

    @classmethod
    def from_params(cls, params: Optional[dict]) -> "AnalysisParams":
        """From a flat dict (:meth:`as_params` names; unknown keys ignored, missing ones default)."""
        p = params or {}
        d = cls()
        enh = EnhancedSearch(p.get("enh_mode", d.enhanced.mode), p.get("enh_region_percent", d.enhanced.region_percent),
                             p.get("enh_grid", d.enhanced.grid), p.get("enh_imgsz", d.enhanced.imgsz),
                             p.get("enh_min_conf_percent", d.enhanced.min_conf_percent),
                             p.get("enh_lift", d.enhanced.lift))
        return cls(p.get("max_birds", d.max_birds), p.get("edge_estimator", d.edge_estimator),
                   p.get("detector", d.detector), p.get("sam_model", d.sam_model), p.get("sam_scope", d.sam_scope),
                   enh, TileOptions.from_params(p)).normalized()

    def normalized(self) -> "AnalysisParams":
        return AnalysisParams(
            _clamp(self.max_birds, 0, 999, 0),
            self.edge_estimator if self.edge_estimator in EDGE_ESTIMATORS else ESTIMATOR_STANDARD.key,
            _model_name(self.detector, "auto", "auto"), _model_name(self.sam_model, "", ""),
            self.sam_scope if self.sam_scope in SAM_SCOPES else SAM_SCOPE_RECHECKED,
            self.enhanced.normalized(), self.tiles.normalized())

    def as_params(self) -> dict:
        o = self.normalized()
        e = o.enhanced
        return {"max_birds": o.max_birds, "edge_estimator": o.edge_estimator, "detector": o.detector,
                "sam_model": o.sam_model, "sam_scope": o.sam_scope, "enh_mode": e.mode,
                "enh_region_percent": e.region_percent, "enh_grid": e.grid, "enh_imgsz": e.imgsz,
                "enh_min_conf_percent": e.min_conf_percent, "enh_lift": e.lift, **o.tiles.as_params()}

    def version_tags(self) -> List[str]:
        """Algorithm version suffixes for the non-default settings, in a fixed order."""
        o = self.normalized()
        tags = [] if o.edge_estimator == ESTIMATOR_STANDARD.key else [o.edge_estimator]
        for tag in (o.tiles.version_tag(), "" if o.detector == "auto" else o.detector[:-3], o.enhanced.version_tag(),
                    "" if not o.sam_model else f"{o.sam_model[:-3]}-{o.sam_scope}"):
            if tag:
                tags.append(tag)
        return tags
