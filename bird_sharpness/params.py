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

from .image_source import SOURCE_DENOISED, SOURCE_JPEG, SOURCE_RAW
from .metrics import EDGE_ESTIMATORS, ESTIMATOR_STANDARD, TileOptions

ENH_OFF, ENH_MANUAL, ENH_NOBIRD = "off", "manual", "nobird"
ENH_MODES = (ENH_OFF, ENH_MANUAL, ENH_NOBIRD)
SAM_SCOPE_RECHECKED, SAM_SCOPE_ALL = "rechecked", "all"
SAM_SCOPES = (SAM_SCOPE_RECHECKED, SAM_SCOPE_ALL)
FLOCK_MODES = ("auto", "always", "off")
# name: (default, minimum, maximum, version tag)
DETECTION_INTS = {
    "detect_long_edge": (1024, 640, 4096, "detedge"),
    "detect_imgsz": (640, 320, 2048, "deti"),
    "detect_conf_percent": (25, 5, 95, "detc"),
    "duplicate_box_percent": (70, 1, 100, "dupbox"),
    "duplicate_mask_percent": (70, 1, 100, "dupmask"),
}
# Which pixels of a bird are measured: its outline (segmentation / SAM mask, else the box core)
# or the whole box core regardless of masks.
PIXELS_OUTLINE, PIXELS_BOX = "outline", "box"
BIRD_PIXELS = (PIXELS_OUTLINE, PIXELS_BOX)
IMAGE_SOURCES = (SOURCE_RAW, SOURCE_JPEG, SOURCE_DENOISED)
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
    sam_scope: str = SAM_SCOPE_ALL           # all (default): every detected bird; rechecked: only birds the normal pass missed
    enhanced: EnhancedSearch = field(default_factory=EnhancedSearch)
    tiles: TileOptions = field(default_factory=TileOptions)
    # The measured pixels of each bird (PIXELS_OUTLINE / PIXELS_BOX) and whether everything else in
    # the bird's crop is painted letterbox grey (114) before the eye model and the edge measurement
    # run, like the model chain's cut-out. The grey meets the outline in a perfectly sharp
    # artificial edge; the head and body regions are shrunk inside the outline, which keeps that
    # edge out of the measurement, but the eye model then sees a cut-out instead of the scene.
    bird_pixels: str = PIXELS_OUTLINE
    grey_fill: bool = False
    # Detected birds whose box long side (full-resolution px) is below this are ignored before
    # measuring (fragments like a 34 x 30 px sliver of a cut-out); 0 = keep every bird. Applies to
    # every detection stage and to given birds. Note the flock pass exists to find 20-50 px birds.
    min_bird_side: int = 0
    # Which pixels are measured (image_source.SOURCE_*): the RAW decode the thresholds are calibrated
    # on (default here and in the CLI), the camera's embedded JPEG (SuperViewer's default user option)
    # or the denoised rendering (needs the analyzer's ``denoised_lookup``). Non-RAW sources tag the
    # version, so their results never pass for RAW ones.
    image_source: str = SOURCE_RAW

    # 首遍副本/网络输入与置信度；复检和鸟群补检沿用此置信度。
    detect_long_edge: int = 1024
    detect_imgsz: int = 640
    detect_conf_percent: int = 25
    duplicate_box_percent: int = 70
    duplicate_mask_percent: int = 70
    flock_mode: str = "auto"
    exclude_birds: bool = True  # 测量后排除弱且无眼的鸟，以及与有眼鸟重叠的无眼局部

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
                   enh, TileOptions.from_params(p), p.get("bird_pixels", d.bird_pixels),
                   p.get("grey_fill", d.grey_fill), p.get("min_bird_side", d.min_bird_side),
                   p.get("image_source", d.image_source),
                   **{name: p.get(name, spec[0]) for name, spec in DETECTION_INTS.items()},
                   flock_mode=p.get("flock_mode", "auto"), exclude_birds=p.get("exclude_birds", True)).normalized()

    def normalized(self) -> "AnalysisParams":
        return AnalysisParams(
            _clamp(self.max_birds, 0, 999, 0),
            self.edge_estimator if self.edge_estimator in EDGE_ESTIMATORS else ESTIMATOR_STANDARD.key,
            _model_name(self.detector, "auto", "auto"), _model_name(self.sam_model, "", ""),
            self.sam_scope if self.sam_scope in SAM_SCOPES else SAM_SCOPE_ALL,
            self.enhanced.normalized(), self.tiles.normalized(),
            self.bird_pixels if self.bird_pixels in BIRD_PIXELS else PIXELS_OUTLINE, bool(self.grey_fill),
            _clamp(self.min_bird_side, 0, 4096, 0),
            self.image_source if self.image_source in IMAGE_SOURCES else SOURCE_RAW,
            **{name: (_clamp(getattr(self, name), lo, hi, default) // 32 * 32 if name == "detect_imgsz"
                      else _clamp(getattr(self, name), lo, hi, default))
               for name, (default, lo, hi, tag) in DETECTION_INTS.items()},
            flock_mode=self.flock_mode if self.flock_mode in FLOCK_MODES else "auto",
            exclude_birds=bool(self.exclude_birds))

    def as_params(self) -> dict:
        o = self.normalized()
        e = o.enhanced
        return {"max_birds": o.max_birds, "edge_estimator": o.edge_estimator, "detector": o.detector,
                "sam_model": o.sam_model, "sam_scope": o.sam_scope, "enh_mode": e.mode,
                "enh_region_percent": e.region_percent, "enh_grid": e.grid, "enh_imgsz": e.imgsz,
                "enh_min_conf_percent": e.min_conf_percent, "enh_lift": e.lift, **o.tiles.as_params(),
                "bird_pixels": o.bird_pixels, "grey_fill": o.grey_fill, "min_bird_side": o.min_bird_side,
                "image_source": o.image_source, **{name: getattr(o, name) for name in DETECTION_INTS},
                "flock_mode": o.flock_mode, "exclude_birds": o.exclude_birds}

    def version_tags(self) -> List[str]:
        """Algorithm version suffixes for the non-default settings, in a fixed order."""
        o = self.normalized()
        tags = [] if o.edge_estimator == ESTIMATOR_STANDARD.key else [o.edge_estimator]
        detection_tags = [f"{tag}{getattr(o, name)}" for name, (default, lo, hi, tag) in DETECTION_INTS.items()
                          if getattr(o, name) != default]
        detection_tags += ([] if o.flock_mode == "auto" else [f"flock-{o.flock_mode}"])
        detection_tags += ([] if o.exclude_birds else ["keep-candidates"])
        for tag in (o.tiles.version_tag(), "" if o.detector == "auto" else o.detector[:-3], o.enhanced.version_tag(),
                    "" if not o.sam_model else f"{o.sam_model[:-3]}-{o.sam_scope}",
                    "" if o.bird_pixels == PIXELS_OUTLINE else o.bird_pixels, "grey" if o.grey_fill else "",
                    f"min{o.min_bird_side}" if o.min_bird_side else "", *detection_tags,
                    "" if o.image_source == SOURCE_RAW else o.image_source):
            if tag:
                tags.append(tag)
        return tags


def add_detection_arguments(parser):
    """Shared CLI controls for formal sharpness and per-bird identification."""
    tips = {"detect_long_edge": "首遍副本长边上限（不放大原图）", "detect_imgsz": "首遍网络输入（32 的倍数）",
            "detect_conf_percent": "检测置信度下限（百分比）", "duplicate_box_percent": "去重框重叠门槛（百分比）",
            "duplicate_mask_percent": "去重掩膜重叠门槛（百分比）"}
    for name, (default, low, high, tag) in DETECTION_INTS.items():
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=default,
                            help=f"{tips[name]}，默认 {default}，范围 {low}–{high}")
    parser.add_argument("--flock-mode", choices=FLOCK_MODES, default="auto",
                        help="鸟群 2048 px 补检：auto 自动（含全图复检后）/ always 总是 / off 关闭")
    parser.add_argument("--keep-bird-candidates", action="store_true",
                        help="关闭测量后的假鸟/局部排除，保留弱且看不到眼的候选（仍去重）")


def detection_params_from_args(args):
    return {**{name: getattr(args, name) for name in DETECTION_INTS},
            "flock_mode": args.flock_mode, "exclude_birds": not args.keep_bird_candidates}
