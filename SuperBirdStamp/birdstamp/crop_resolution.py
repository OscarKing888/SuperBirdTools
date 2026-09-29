"""Pixel-accurate crop guides, independent of Qt and viewport rendering."""
from __future__ import annotations

from dataclasses import dataclass
import math

Box = tuple[float, float, float, float]
DEFAULT_TIERS = (("480p", 480), ("720p", 720), ("1080p", 1080), ("1440p", 1440), ("4K", 2160))


def normalize_snap_options(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    tiers = []
    seen = set()
    for item in raw.get("tiers", []) if isinstance(raw.get("tiers"), list) else []:
        try:
            label, edge = str(item["label"]).strip(), int(item["short_edge"])
            if label and 1 <= edge <= 16384 and edge not in seen:
                tiers.append((label, edge))
                seen.add(edge)
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
    def distance(key, default):
        try:
            result = float(raw.get(key, default))
            return result if math.isfinite(result) and 1 <= result <= 64 else default
        except (TypeError, ValueError):
            return default
    enter = distance("enter_distance", 10.0)
    return {"tiers": tuple(sorted(tiers, key=lambda t: t[1])) or DEFAULT_TIERS,
            "enter_distance": enter, "leave_distance": max(enter, distance("leave_distance", 16.0))}


@dataclass(frozen=True)
class CropPixelContext:
    source_size: tuple[int, int]
    preview_size: tuple[int, int]
    outer_pad: tuple[int, int, int, int] = (0, 0, 0, 0)
    ratio: float | None = None
    source_key: str = ""

    @property
    def valid(self) -> bool:
        return (all(math.isfinite(v) and v > 0 for v in (*self.source_size, *self.preview_size))
                and all(math.isfinite(v) and v >= 0 for v in self.outer_pad)
                and (self.ratio is None or (math.isfinite(self.ratio) and self.ratio > 0)))

    def to_source(self, box: Box) -> Box:
        from birdstamp.gui.editor_core import crop_box_to_source
        return crop_box_to_source(box, self.preview_size, self.outer_pad)

    def to_preview(self, box: Box) -> Box:
        w, h = self.preview_size
        pt, pb, pl, pr = self.outer_pad
        l, t, r, b = box
        return ((l * w + pl) / (w + pl + pr), (t * h + pt) / (h + pt + pb),
                (r * w + pl) / (w + pl + pr), (b * h + pt) / (h + pt + pb))

    def pixel_box(self, box: Box) -> Box:
        l, t, r, b = self.to_source(box)
        w, h = self.source_size
        return l * w, t * h, r * w, b * h

    def crop_size(self, box: Box) -> tuple[int, int]:
        from birdstamp.gui.editor_core import _crop_plan_from_override, compute_crop_output_size
        plan, pad = _crop_plan_from_override(*self.source_size, self.to_source(box))
        return compute_crop_output_size(*self.source_size, plan, pad)

    def pixel_ratio(self, box: Box) -> float:
        l, t, r, b = self.pixel_box(box)
        return (r - l) / (b - t)


@dataclass(frozen=True)
class CropResolutionTarget:
    label: str
    size: tuple[int, int]
    box: Box


def tier_size(short_edge: int, ratio: float) -> tuple[int, int]:
    return ((max(1, round(short_edge * ratio)), short_edge) if ratio >= 1
            else (short_edge, max(1, round(short_edge / ratio))))


def handle_point(box: Box, handle: str) -> tuple[float, float]:
    l, t, r, b = box
    return (l if "w" in handle else r if "e" in handle else (l + r) / 2,
            t if "n" in handle else b if "s" in handle else (t + b) / 2)


def resolution_targets(context: CropPixelContext, start_box: Box, handle: str | None,
                       ratio: float, tiers=DEFAULT_TIERS, *, min_size=0.02, min_overlap=0.02
                       ) -> tuple[CropResolutionTarget, ...]:
    """Keep the opposite anchor, rounding its position by at most half a pixel.

    Integer left/top plus integer width/height avoid half-pixel banker rounding
    producing a different exported size when a centered dimension is odd.
    """
    if not context.valid or not math.isfinite(ratio) or ratio <= 0:
        return ()
    l, t, r, b = context.pixel_box(start_box)
    source_w, source_h = context.source_size
    targets = []
    handle = handle or ""
    for label, edge in tiers:
        w, h = tier_size(edge, ratio)
        left = round(r) - w if "w" in handle else round(l) if "e" in handle else round((l + r - w) / 2)
        top = round(b) - h if "n" in handle else round(t) if "s" in handle else round((t + b - h) / 2)
        box = context.to_preview((left / source_w, top / source_h,
                                  (left + w) / source_w, (top + h) / source_h))
        bl, bt, br, bb = box
        if (br - bl + 1e-12 < min_size or bb - bt + 1e-12 < min_size
                or br < min_overlap or bb < min_overlap or bl > 1 - min_overlap or bt > 1 - min_overlap):
            continue
        if context.crop_size(box) != (w, h):
            continue
        targets.append(CropResolutionTarget(label, (w, h), box))
    return tuple(targets)


def target_distance(box: Box, target: CropResolutionTarget, handle: str | None,
                    viewport_size: tuple[float, float]) -> float:
    if handle is None:
        # Centered idle guides: compare their lower-right extents.
        handle = "se"
    x, y = handle_point(box, handle)
    tx, ty = handle_point(target.box, handle)
    dx, dy = abs(x - tx) * viewport_size[0], abs(y - ty) * viewport_size[1]
    return dx if handle in ("e", "w") else dy if handle in ("n", "s") else math.hypot(dx, dy)


def choose_snap_target(box: Box, targets: tuple[CropResolutionTarget, ...], handle: str | None,
                       viewport_size: tuple[float, float], *, previous: CropResolutionTarget | None = None,
                       enter_distance=10.0, leave_distance=16.0
                       ) -> tuple[CropResolutionTarget | None, bool]:
    if not targets:
        return None, False
    if previous in targets and target_distance(box, previous, handle, viewport_size) <= leave_distance:
        return previous, True
    nearest = min(targets, key=lambda target: target_distance(box, target, handle, viewport_size))
    return nearest, handle is not None and target_distance(box, nearest, handle, viewport_size) <= enter_distance
