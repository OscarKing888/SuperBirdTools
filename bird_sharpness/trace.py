"""Step-by-step record of one sharpness analysis, for the visual trace viewer and CLI export.

The analyzer calls an :class:`AnalysisTracer` at each key step with the exact
masks and edge selections it used, so what is shown is what was computed. The
tracer renders RGB step images (OpenCV, no Qt) and keeps per-step metrics and
chart data; charts and text are drawn by the viewer. No tracer -> no cost.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from app_common import bird_sharpness_fields as fields

from .scoring import (
    SCORE_ANCHORS,
    SIGMA_BLURRED_MIN,
    SIGMA_SHARP_MAX,
    SIGMA_USABLE_MAX,
    verdict_label,
)

DISPLAY_LONG_EDGE = 2400
ROI_LONG_EDGE = 2400

# Birds not found on the whole frame are flagged in the conclusion.
FOUND_LABELS = {"full_lifted": "（提亮复检）", "full_fine": "（复检）", "focus_weak": "（焦点复检）", "focus_zoom": "（焦点放大复检）"}

# Colours (RGB) shared by step images and the viewer legend.
C_SHARP = (46, 157, 79)
C_USABLE = (201, 154, 6)
C_SOFT = (214, 69, 69)
C_FOCUS = (235, 64, 52)
C_WINDOW = (255, 214, 10)
C_CROP = (80, 200, 255)
C_MASK = (255, 255, 255)
C_HEAD = (0, 220, 255)
C_EYE = (255, 235, 59)
C_BEAK = (255, 140, 0)
C_REJECT_NOISE = (90, 104, 130)
C_REJECT_LINE = (220, 80, 220)
C_WEAK = (175, 175, 175)
BIRD_COLORS = [(0, 200, 255), (255, 120, 200), (120, 255, 120), (255, 200, 60), (160, 140, 255),
               (255, 255, 120), (80, 255, 220), (255, 160, 120)]

STEP_DECODE = "decode"
STEP_DETECT = "detect"
STEP_RECHECK = "recheck"
STEP_BIRDS = "birds"
STEP_BIRD = "bird"
STEP_HEAD = "head"
STEP_EDGES = "edges"
STEP_DISTRIBUTION = "distribution"
STEP_FOCUS = "focus"
STEP_TILES = "tiles"
STEP_RESULT = "result"


def hex_color(rgb: Sequence[int]) -> str:
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb[:3])


def sigma_color(sigma: float) -> Tuple[int, int, int]:
    """Green (sharp) -> amber (usable) -> red (blurred), on the verdict thresholds."""
    stops = [(SIGMA_SHARP_MAX - 0.15, C_SHARP), (SIGMA_SHARP_MAX, C_SHARP), (SIGMA_USABLE_MAX, C_USABLE),
             (SIGMA_BLURRED_MIN, C_SOFT), (SIGMA_BLURRED_MIN + 0.8, (120, 20, 20))]
    if sigma <= stops[0][0]:
        return stops[0][1]
    for (s0, c0), (s1, c1) in zip(stops, stops[1:]):
        if sigma <= s1:
            t = 0.0 if s1 == s0 else (sigma - s0) / (s1 - s0)
            return tuple(int(round(a + (b - a) * t)) for a, b in zip(c0, c1))
    return stops[-1][1]


def _verdict_rgb(verdict: str) -> Tuple[int, int, int]:
    style = fields.VERDICT_STYLES.get(verdict)
    if style is None:
        return (140, 143, 152)
    h = style.color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _fmt(value, fmt: str = "%.2f", none: str = "—") -> str:
    return none if value is None else fmt % value


@dataclass
class TraceChart:
    kind: str  # "histogram" | "directions" | "score_curve"
    title: str
    data: dict


@dataclass
class TraceStep:
    key: str
    title: str
    description: str
    image: np.ndarray                      # RGB uint8
    frame: str                             # coordinate frame id; equal frames can sync zoom/pan
    metrics: List[Tuple[str, str]] = field(default_factory=list)
    charts: List[TraceChart] = field(default_factory=list)
    legend: List[Tuple[str, str]] = field(default_factory=list)  # (hex colour, label)
    focus_rect: Optional[Tuple[int, int, int, int]] = None  # region worth zooming to, image coords
    bird: Optional[int] = None  # bird index for per-bird steps
    # metric label -> box (x1, y1, x2, y2) in image coords, highlighted when the row is hovered
    highlights: Dict[str, Tuple[float, float, float, float]] = field(default_factory=dict)

    def to_json(self) -> dict:
        def clean(value):
            if isinstance(value, np.ndarray):
                return [round(float(v), 4) for v in value.tolist()]
            if isinstance(value, (list, tuple)):
                return [clean(v) for v in value]
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, (np.floating, np.integer)):
                return value.item()
            return value

        return {"key": self.key, "title": self.title, "description": self.description, "frame": self.frame,
                "focus_rect": self.focus_rect, "bird": self.bird, "highlights": clean(self.highlights),
                "metrics": self.metrics, "legend": self.legend,
                "charts": [{"kind": c.kind, "title": c.title, "data": clean(c.data)} for c in self.charts]}


@dataclass
class TraceBird:
    index: int
    label: str
    best: bool
    steps: List[TraceStep] = field(default_factory=list)
    excluded: bool = False  # dropped as a false extra bird; measured but not used


@dataclass
class AnalysisTrace:
    path: str
    common: List[TraceStep] = field(default_factory=list)
    birds: List[TraceBird] = field(default_factory=list)
    region_steps: List[TraceStep] = field(default_factory=list)  # no-bird path
    final: List[TraceStep] = field(default_factory=list)
    result: Optional[object] = None  # BirdSharpnessResult

    def best_bird_index(self) -> int:
        for i, bird in enumerate(self.birds):
            if bird.best:
                return i
        return 0

    def steps_all(self) -> List[TraceStep]:
        """Every bird's steps one after another (bird #1, bird #2, ...)."""
        middle = [step for bird in self.birds for step in bird.steps] if self.birds else self.region_steps
        return [*self.common, *middle, *self.final]

    def steps_for(self, bird_index: Optional[int] = None) -> List[TraceStep]:
        middle = self.region_steps
        if self.birds:
            index = self.best_bird_index() if bird_index is None else max(0, min(bird_index, len(self.birds) - 1))
            middle = self.birds[index].steps
        return [*self.common, *middle, *self.final]

    def export(self, directory: str) -> List[str]:
        """Write every step image (PNG) and ``trace.json``; returns the written paths."""
        os.makedirs(directory, exist_ok=True)
        written, manifest = [], {"path": self.path, "result": getattr(self.result, "to_dict", lambda: {})(),
                                 "steps": []}
        groups = [("", self.common)]
        groups += [(f"bird{b.index + 1}_", b.steps) for b in self.birds] or [("", self.region_steps)]
        groups.append(("", self.final))
        n = 0
        for prefix, steps in groups:
            for step in steps:
                n += 1
                name = f"{n:02d}_{prefix}{step.key}.png"
                out = os.path.join(directory, name)
                ok, buf = cv2.imencode(".png", cv2.cvtColor(step.image, cv2.COLOR_RGB2BGR))
                if ok:
                    with open(out, "wb") as fh:  # cv2.imwrite cannot open non-ASCII paths on Windows
                        fh.write(buf.tobytes())
                    written.append(out)
                manifest["steps"].append({"file": name, **step.to_json()})
        manifest_path = os.path.join(directory, "trace.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1, default=str)
        written.append(manifest_path)
        return written


# ── drawing helpers ──────────────────────────────────────────────────────────

def _auto_exposure(img: np.ndarray) -> np.ndarray:
    """Display-only brightening so dark, under-exposed frames stay readable (never used to measure)."""
    lum = img if img.ndim == 2 else img.mean(axis=2)
    hi = float(np.percentile(lum, 99.0))
    if hi <= 1.0:
        return img
    gain = min(4.0, 235.0 / hi)
    if gain <= 1.05:
        return img
    return np.clip(img.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def _bbox(mask: np.ndarray, margin: float = 0.6) -> Optional[Tuple[int, int, int, int]]:
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    x1, x2, y1, y2 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    mx, my = int((x2 - x1) * margin) + 8, int((y2 - y1) * margin) + 8
    h, w = mask.shape[:2]
    return max(0, x1 - mx), max(0, y1 - my), min(w, x2 + mx), min(h, y2 + my)


def _to_rgb8(gray_or_rgb: np.ndarray) -> np.ndarray:
    if gray_or_rgb.ndim == 2:
        g = np.clip(gray_or_rgb * 255.0, 0, 255).astype(np.uint8) if gray_or_rgb.dtype != np.uint8 else gray_or_rgb
        return np.repeat(g[..., None], 3, axis=2)
    return np.ascontiguousarray(gray_or_rgb)


def _downscale(img: np.ndarray, long_edge: int) -> Tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    scale = min(1.0, long_edge / float(max(h, w)))
    if scale >= 1.0:
        return img.copy(), 1.0
    return cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA), scale


def _line_w(img: np.ndarray, base: float = 2.0) -> int:
    return max(1, int(round(base * max(img.shape[:2]) / 1200.0)))


def _rect(img, box, color, width, dashed=False):
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    if not dashed:
        cv2.rectangle(img, (x1, y1), (x2, y2), color, width, cv2.LINE_AA)
        return
    dash = max(6, width * 5)
    for (ax, ay), (bx, by) in (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))):
        length = int(np.hypot(bx - ax, by - ay))
        for t in range(0, length, dash * 2):
            t2 = min(length, t + dash)
            p = (int(ax + (bx - ax) * t / max(length, 1)), int(ay + (by - ay) * t / max(length, 1)))
            q = (int(ax + (bx - ax) * t2 / max(length, 1)), int(ay + (by - ay) * t2 / max(length, 1)))
            cv2.line(img, p, q, color, width, cv2.LINE_AA)


def _dim(img: np.ndarray, keep: Optional[np.ndarray], factor: float = 0.35) -> np.ndarray:
    out = img.astype(np.float32)
    if keep is None:
        out *= factor
    else:
        out[~keep] *= factor
    return np.clip(out, 0, 255).astype(np.uint8)


def _contour(img: np.ndarray, mask: np.ndarray, color, width: int) -> None:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(img, contours, -1, color, width, cv2.LINE_AA)


def _paint_points(img: np.ndarray, ys: np.ndarray, xs: np.ndarray, colors, radius: int) -> None:
    if radius <= 0:
        img[ys, xs] = colors
        return
    for y, x, c in zip(ys.tolist(), xs.tolist(), colors):
        cv2.circle(img, (x, y), radius, tuple(int(v) for v in c), -1, cv2.LINE_AA)


def _expand(box, factor: float, shape) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = box
    cx, cy, w, h = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) * factor, (y2 - y1) * factor
    H, W = shape[:2]
    return max(0, cx - w / 2), max(0, cy - h / 2), min(W, cx + w / 2), min(H, cy + h / 2)


def _histogram(series: List[dict], median: Optional[float], title: str) -> TraceChart:
    return TraceChart("histogram", title, {
        "series": series, "median": median,
        "bands": [SIGMA_SHARP_MAX, SIGMA_USABLE_MAX, SIGMA_BLURRED_MIN],
        "band_colors": [hex_color(C_SHARP), hex_color(C_USABLE), hex_color(C_SOFT)],
    })


EDGE_LEGEND = [
    (hex_color(C_REJECT_NOISE), "低于 4×噪声，舍弃"),
    (hex_color(C_WEAK), "高于噪声但非最强，未测"),
    (hex_color(C_REJECT_LINE), "线状结构（细枝/高光），舍弃"),
    (hex_color(C_SHARP), "实测边缘：清晰"),
    (hex_color(C_USABLE), "实测边缘：可用"),
    (hex_color(C_SOFT), "实测边缘：模糊"),
]


class AnalysisTracer:
    """Collects :class:`TraceStep` objects while the analyzer runs."""

    def __init__(self, *, display_long_edge: int = DISPLAY_LONG_EDGE, roi_long_edge: int = ROI_LONG_EDGE):
        self.display_long_edge = display_long_edge
        self.roi_long_edge = roi_long_edge
        self.trace: Optional[AnalysisTrace] = None
        self._overview: Optional[np.ndarray] = None
        self._scale = 1.0
        self._focus_px = None
        self._image_shape = (0, 0)
        self._bird_boxes: List[Tuple[int, int, int, int]] = []
        self._det_scale = 1.0
        self._measurements: Dict[int, object] = {}

    # ── shared frame ──
    def _display(self, box):
        return tuple(v * self._scale for v in box)

    def decode(self, path: str, image, focus_px, *, decode_s: float) -> None:
        self.trace = AnalysisTrace(path=path)
        H, W = image.gray.shape[:2]
        self._image_shape = (H, W)
        self._focus_px = focus_px
        overview, self._scale = _downscale(image.rgb8, self.display_long_edge)
        overview = _auto_exposure(overview)
        self._overview = overview
        img = overview.copy()
        lw = _line_w(img)
        if image.camera_crop:
            l, t, r, b = image.camera_crop
            _rect(img, (l * W * self._scale, t * H * self._scale, r * W * self._scale, b * H * self._scale),
                  C_CROP, lw, dashed=True)
        if focus_px is not None:
            _rect(img, self._display(focus_px), C_FOCUS, lw + 1)
        legend = [(hex_color(C_FOCUS), "相机焦点框")]
        if image.camera_crop:
            legend.insert(0, (hex_color(C_CROP), "相机 JPEG 画幅（RAW 输出含传感器边缘）"))
        from .image_source import SOURCE_DENOISED, SOURCE_JPEG

        source = getattr(image, "source", "")
        if source == SOURCE_JPEG:
            kind = "相机内嵌 JPEG（机内锐化、降噪、8 位压缩）"
            desc = ("本次按「相机 JPEG」计算：测的是相机内嵌的全尺寸 JPEG。机内锐化会让边缘看起来更锐、降噪会抹掉细节，"
                    "阈值是按 RAW 解码标定的，结果仅供对比。JPEG 已是相机画幅，焦点框直接对应。")
        elif source == SOURCE_DENOISED:
            kind = "降噪成片（NAFNet，RAW 渲染后降噪）"
            desc = ("本次按「降噪成片」计算：测的是降噪后的图像。降噪会改变噪声和细小边缘，阈值是按 RAW 解码标定的，"
                    "结果仅供对比。")
        else:
            kind = "RAW（LibRaw 全分辨率解码）" if image.is_raw else "位图"
            desc = ("清晰度以全分辨率像素计（100% 观看）。RAW 不用内嵌预览（相机 JPEG 经过机内锐化、降噪，有的还很小），"
                    "而用 LibRaw 解码；焦点框按相机画幅映射到解码像素上。")
        metrics = [("图像来源", kind), ("分辨率", f"{W} × {H}"),
                   ("解码耗时", f"{decode_s:.2f} s"),
                   ("焦点", "无" if focus_px is None else
                    f"{int(focus_px[2] - focus_px[0])} × {int(focus_px[3] - focus_px[1])} px")]
        self.trace.common.append(TraceStep(
            STEP_DECODE, "解码全分辨率", desc, img, "full", metrics, legend=legend))
        source_path = getattr(image, "source_path", "")
        if source_path and os.path.normcase(source_path) != os.path.normcase(path):
            metrics.append(("图像文件", os.path.basename(source_path)))

    def detect(self, detections, scale_to_full: float, *, has_masks: bool, has_keypoints: bool,
               unmeasured: int = 0, limit: int = 0, small_pass=None) -> None:
        """``small_pass``: ``(first-pass birds, high-resolution birds, added)`` when the
        small-bird pass ran (see ``analyzer.FLOCK_BIRD_SIDE``)."""
        img = _dim(self._overview, None, 0.55)
        lw = _line_w(img)
        H, W = self._image_shape
        sh, sw = img.shape[:2]
        rows = []
        highlights = {}
        self._bird_boxes = []
        self._det_scale = scale_to_full
        for i, det in enumerate(detections):
            color = BIRD_COLORS[i % len(BIRD_COLORS)]
            x1, y1, x2, y2 = (v / scale_to_full for v in det.box)
            self._bird_boxes.append((int(x1), int(y1), int(x2), int(y2)))
            if det.mask is not None:
                mask = cv2.resize(det.mask.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST).astype(bool)
                tint = np.array(color, np.float32)
                img[mask] = np.clip(img[mask] * 0.45 + tint * 0.55, 0, 255).astype(np.uint8)
            _rect(img, self._display((x1, y1, x2, y2)), color, lw)
            cv2.circle(img, (int(x1 * self._scale) + 6 * lw, int(y1 * self._scale) + 6 * lw), 5 * lw, color, -1,
                       cv2.LINE_AA)
            rows.append((f"鸟 #{i + 1}", f"置信度 {det.confidence:.2f}，框 {int(x2 - x1)} × {int(y2 - y1)} px"))
            highlights[rows[-1][0]] = self._display((x1, y1, x2, y2))
        if self._focus_px is not None:
            _rect(img, self._display(self._focus_px), C_FOCUS, lw)
        metrics = [("识别模型", "分割（像素掩膜）" if has_masks else "检测（鸟框）"),
                   ("鸟眼模型", "有" if has_keypoints else "无（按整只鸟计算，准确度低）"),
                   ("鸟数", str(len(detections))), *rows]
        if unmeasured:
            metrics.insert(3, ("未测量", f"另有 {unmeasured} 只（超过上限 {limit} 只；焦点框上的鸟优先测量）"))
        if small_pass is not None:
            from . import analyzer as A

            first, found, added = small_pass
            metrics.insert(3, ("小鸟高分辨率补检",
                               f"首遍 {first} 只（有鸟框 < {A.FLOCK_BIRD_SIDE} px），{A.SMALL_DETECT_LONG_EDGE} px 再识别"
                               f"找到 {found} 只，合并后新增 {added} 只"))
        legend = [(hex_color(BIRD_COLORS[i % len(BIRD_COLORS)]), f"鸟 #{i + 1}") for i in range(len(detections))]
        legend.append((hex_color(C_FOCUS), "相机焦点框"))
        desc = ("在 1024 px 副本上找出全部鸟（置信度 ≥ 0.25）。每只鸟后续只用自己的像素单独计算一组清晰度，"
                "最后取最好的一只。" + ("鸟很小（鸟群）：首遍在 1024 px 副本上看到的鸟太小、漏得多，"
                                     "又在 2048 px 副本上用 2048 px 输入识别一次并合并。" if small_pass else "")
                if detections else
                "全图没有置信度 ≥ 0.25 的鸟：下一步复检伪装或被遮挡的鸟；仍没有时有焦点用焦点区域，没有焦点用全图。")
        self.trace.common.append(TraceStep(STEP_DETECT, "鸟体识别", desc, img, "full", metrics, legend=legend,
                                           highlights=highlights))

    def recheck(self, check) -> None:
        """Step for :meth:`BirdSharpnessAnalyzer._recheck` (first pass found no bird)."""
        from . import analyzer as A
        from .models import FOUND_FOCUS_WEAK, FOUND_FULL_FINE, FOUND_FULL_LIFTED

        img = _dim(self._overview, None, 0.55)
        lw = _line_w(img)
        font = max(0.4, lw * 0.45)

        def label(box, text, color):
            x1, y1 = (int(v) for v in self._display(box)[:2])
            cv2.putText(img, text, (x1 + 2 * lw, max(12, y1 - 2 * lw)), cv2.FONT_HERSHEY_SIMPLEX, font, color,
                        max(1, lw // 2), cv2.LINE_AA)

        for window in check.windows:
            _rect(img, self._display(window["box"]), C_CROP, max(1, lw // 2), dashed=True)
        for conf, box in check.candidates:
            _rect(img, self._display(box), C_WEAK, max(1, lw // 2))
            label(box, f"{conf:.2f}", C_WEAK)
        for window in check.windows:
            for conf, box, ok in window["detections"]:
                if not ok:
                    _rect(img, self._display(box), C_REJECT_LINE, max(1, lw // 2), dashed=True)
        accepted_full = []
        for i, det in enumerate(check.accepted):
            box = tuple(v / self._det_scale for v in det.box)
            accepted_full.append(tuple(int(v) for v in box))
            color = BIRD_COLORS[i % len(BIRD_COLORS)]
            _rect(img, self._display(box), color, lw + 1)
            label(box, f"#{i + 1} {det.confidence:.2f}", color)
        if check.focus_box is not None:
            _rect(img, self._display(check.focus_box), C_FOCUS, lw + 1)
        self._bird_boxes = accepted_full

        lifted = check.source == FOUND_FULL_LIFTED
        fine = check.source == FOUND_FULL_FINE
        rows = [("暗部提亮后再识别",
                 "不需要（画面不暗）" if check.lift_gamma is None else
                 f"γ {check.lift_gamma:.2f}：" + (f"找到 {len(check.accepted)} 只，直接采纳" if lifted else "仍没有鸟"))]
        if not lifted:
            rows.append((f"全图 {A.RECHECK_IMGSZ} px 输入再识别",
                         f"找到 {len(check.accepted)} 只（置信度 ≥ {A.BIRD_CONFIDENCE_MIN:.2f}），直接采纳" if fine else
                         "仍没有置信度 ≥ %.2f 的鸟" % A.BIRD_CONFIDENCE_MIN))
        fine = fine or lifted
        if not fine and check.focus_box is None:
            rows.append(("焦点处复检", "跳过（没有相机焦点）"))
        elif not fine:
            weak_best = max((c for c, _ in check.candidates), default=None)
            rows.append(("焦点处弱候选", f"{len(check.candidates)} 个" +
                         ("" if weak_best is None else f"，最高置信度 {weak_best:.2f}")))
            weak_ok = check.source == FOUND_FOCUS_WEAK
            rows.append(("规则一：弱候选压在焦点上",
                         f"置信度 ≥ {A.FOCUS_WEAK_CONFIDENCE:.2f} 且重叠 ≥ {A.FOCUS_WEAK_OVERLAP:.0%}："
                         + ("通过" if weak_ok else "未通过")))
            if weak_ok:
                rows.append(("规则二：放大复检", "不需要"))
            elif not check.windows:
                rows.append(("规则二：放大复检", "跳过（焦点处没有任何弱候选可作印证）"))
            for window in check.windows:
                x1, y1, x2, y2 = window["box"]
                dets = window["detections"]
                best = max((c for c, _, _ in dets), default=None)
                passed = any(ok for _, _, ok in dets)
                rows.append((f"放大窗口 {x2 - x1} px",
                             "无鸟" if best is None else
                             f"最高 {best:.2f}，" + ("与弱候选位置一致：通过" if passed else "与弱候选位置不一致：不采信")))
        rows.append(("结果", f"找到 {len(check.accepted)} 只鸟" if check.accepted else
                     ("仍判为无鸟，改用焦点区域" if check.focus_box is not None else "仍判为无鸟，改用全图")))
        legend = [(hex_color(C_FOCUS), "相机焦点框"), (hex_color(C_WEAK), "焦点处弱候选（< 0.25）"),
                  (hex_color(C_CROP), "放大窗口"), (hex_color(C_REJECT_LINE), "放大后误认（未被印证，舍弃）")]
        if check.accepted:
            legend.append((hex_color(BIRD_COLORS[0]), "采纳的鸟"))
        if check.windows:
            zoom_to = self._display(check.windows[-1]["box"])
        elif check.focus_box is not None:
            zoom_to = _expand(self._display(check.focus_box), 4.0, img.shape)
        else:
            zoom_to = None
        self.trace.common.append(TraceStep(
            STEP_RECHECK, "复检",
            f"第一遍（{A.DETECT_IMGSZ} px 网络输入）没有鸟时复检伪装或被枝叶遮挡的鸟：画面暗（逆光、暮色）时先把暗部"
            "提亮（类似相机 JPEG 的影调，仅用于识别、不参与测量）再识别；再用 "
            f"{A.RECHECK_IMGSZ} px 输入把全图识别一次，小而暗的鸟置信度会更高；仍没有时，相机焦点往往就落在鸟上，于是"
            f"①焦点处的弱候选（≥ {A.FOCUS_WEAK_CONFIDENCE:.2f}）大部分压在焦点框上即采纳；"
            "②否则以焦点为中心放大（长边 1/6、1/4、1/2.5）重新识别，放大后的鸟还必须与弱候选位置一致——"
            "放大后的暗色树叶也常被认成鸟，单凭放大结果不采信。焦点处只采纳一只鸟。",
            img, "full", rows, legend=legend,
            focus_rect=None if zoom_to is None else tuple(int(v) for v in _expand(zoom_to, 1.3, img.shape))))

    # ── per bird ──
    def bird(self, index: int, image, roi, mask: np.ndarray, body: np.ndarray, head: Optional[np.ndarray],
             keypoints, selection, body_detail, measurement) -> None:
        X1, Y1, X2, Y2 = roi
        crop = image.rgb8[Y1:Y2, X1:X2]
        crop_small, s = _downscale(crop, self.roi_long_edge)
        crop_small = _auto_exposure(crop_small)
        frame = f"bird{index}"
        sh, sw = crop_small.shape[:2]

        def small(mask_full):
            return cv2.resize(mask_full.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST).astype(bool)

        m_small, body_small = small(mask), small(body)
        lw = _line_w(crop_small)
        steps: List[TraceStep] = []

        # ③ bird pixels
        img = _dim(crop_small, m_small, 0.3)
        _contour(img, m_small, C_MASK, lw)
        _contour(img, body_small, BIRD_COLORS[index % len(BIRD_COLORS)], lw)
        bw, bh = measurement.box[2] - measurement.box[0], measurement.box[3] - measurement.box[1]
        steps.append(TraceStep(
            STEP_BIRD, f"鸟 #{index + 1} 的像素",
            "只计算这只鸟自己的像素：白线为鸟体轮廓（分割掩膜，或检测框内缩 8%），彩线为向内收缩 15 px 的身体区域"
            "（测身体模糊与运动方向）。轮廓外压暗部分不参与计算。",
            img, frame,
            [("鸟框", f"{bw} × {bh} px"), ("裁切（含 15% 外扩）", f"{X2 - X1} × {Y2 - Y1} px"),
             ("鸟体像素", f"{int(mask.sum()):,}"), ("身体区域像素", f"{int(body.sum()):,}"),
             ("像素来源", "分割掩膜" if measurement.masked else "检测框内核")],
            legend=[(hex_color(C_MASK), "鸟体轮廓"), (hex_color(BIRD_COLORS[index % len(BIRD_COLORS)]), "身体区域")]))

        # ④ head
        measure_mask = head if head is not None else mask
        if keypoints is not None:
            img = _dim(crop_small, m_small, 0.45)
            if head is not None:
                h_small = small(head)
                img[h_small] = np.clip(img[h_small].astype(np.float32) * 0.6 + np.array(C_HEAD) * 0.4, 0, 255)
                _contour(img, h_small, C_HEAD, lw)
            from .analyzer import EYE_MIRROR_MAX

            head_kp, radius = keypoints
            pts, vis = head_kp.pts, head_kp.vis
            r = max(3, 4 * lw)
            if head_kp.mirror_pts is not None:  # mirror run: hollow rings, so disagreement is visible
                for (px, py), color in ((head_kp.mirror_pts[head_kp.eye_index], C_EYE), (head_kp.mirror_pts[2], C_BEAK)):
                    cv2.circle(img, (int(px * s), int(py * s)), r + 2 * lw, color, max(1, lw // 2), cv2.LINE_AA)
            for (px, py), color, v in ((pts[0], C_EYE, vis[0]), (pts[1], C_EYE, vis[1]), (pts[2], C_BEAK, vis[2])):
                c = (int(px * s), int(py * s))
                if v >= 0.3:
                    cv2.circle(img, c, r, color, -1, cv2.LINE_AA)
                else:
                    cv2.circle(img, c, r, color, lw, cv2.LINE_AA)
            eye_ok = measurement.eye_visibility is not None and measurement.eye_visibility >= 0.5
            if eye_ok and not head_kp.eye_reliable:
                desc = (f"原图与镜像图定位的眼相距 {head_kp.eye_gap:.0%} 鸟身长（> {EYE_MIRROR_MAX:.0%}）："
                        "飞行、低头、与别的鸟重叠时眼常被定到身体或别的鸟上，头部位置不可信，改按整只鸟计算（不封顶）。")
            elif eye_ok:
                desc = ("关键点模型定位眼和喙（原图与镜像图各定位一次取平均，空心圈为镜像结果）；"
                        "头部区域 = 以眼为圆心、半径 1.2×眼喙距的圆 ∩ 鸟体（青色）。"
                        "头部清晰才算鸟清晰，翅膀/尾羽运动和遮挡不计入。")
            else:
                desc = "眼睛不可见（可见度 < 0.5）：无法确认头部，改用身体模糊，分数封顶 299。"
            mirror = ("—" if head_kp.eye_gap is None else
                      f"眼差 {head_kp.eye_gap:.0%}，喙差 {head_kp.beak_gap:.0%} 鸟身长："
                      + ("一致" if head_kp.eye_reliable else "不一致"))
            steps.append(TraceStep(
                STEP_HEAD, "头部定位", desc, img, frame,
                [("眼可见度", _fmt(float(max(vis[0], vis[1])))), ("喙可见度", _fmt(float(vis[2]))),
                 ("镜像复核", mirror),
                 ("头部半径", _fmt(radius, "%.0f px")),
                 ("头部像素", "—" if head is None else f"{int(head.sum()):,}")],
                legend=[(hex_color(C_EYE), "眼（实心 = 可见，空心圈 = 镜像结果）"), (hex_color(C_BEAK), "喙"),
                        (hex_color(C_HEAD), "头部测量区域")]))

        # ⑤ edges
        zoom = _bbox(small(measure_mask), 0.6 if head is not None else 0.05)
        if steps and head is not None and keypoints is not None:
            steps[-1].focus_rect = zoom
        gray = cv2.cvtColor(crop_small, cv2.COLOR_RGB2GRAY)
        img = _dim(_to_rgb8(gray), None, 0.7)
        steps.append(self._edge_step(img, s, selection, measure_mask, small(measure_mask), frame,
                                     "头部" if head is not None else "鸟体"))
        steps[-1].focus_rect = zoom

        # ⑥ distribution
        heat = _dim(crop_small, None, 0.5)
        _contour(heat, small(measure_mask), C_HEAD, lw)
        if selection is not None and selection.sigma.size:
            colors = [sigma_color(v) for v in selection.sigma.tolist()]
            _paint_points(heat, (selection.ys * s).astype(int).clip(0, sh - 1),
                          (selection.xs * s).astype(int).clip(0, sw - 1), colors, max(2, 2 * lw))
        by, bx, bsig, by_bin = body_detail
        series = [{"name": "头部" if head is not None else "鸟体", "color": hex_color(C_HEAD),
                   "values": selection.sigma if selection is not None else np.empty(0)}]
        if bsig.size:
            series.append({"name": "身体", "color": "#a0a0a0", "values": bsig})
        charts = [_histogram(series, measurement.sigma, "模糊半径分布（px）")]
        charts.append(TraceChart("directions", "身体边缘 8 方向模糊（运动模糊检测）",
                                 {"bins": by_bin, "ratio": measurement.motion_ratio, "threshold": 1.5}))
        steps.append(TraceStep(
            STEP_DISTRIBUTION, "模糊分布",
            "每个实测边缘点按模糊半径着色（绿=清晰，黄=可用，红=模糊）。取中位数作为这只鸟的值；"
            "身体边缘按 8 个方向统计，方向间差异大（≥1.5 倍）且身体模糊时判为运动模糊。",
            heat, frame,
            [("测量值（中位数）", _fmt(measurement.sigma, "%.3f px")),
             (f"{'头部' if head is not None else '鸟体'}实测点",
              (f"{selection.sigma.size}" if selection is not None and selection.sigma.size >= 8 else
               f"{0 if selection is None else selection.sigma.size}（不足 8 点：严重模糊，按 ≥1.55 px 计）")
              if (head is not None or keypoints is None or measurement.eye_reliable is False)
              else "—（眼不可见，用身体）"),
             ("头部 σ", _fmt(measurement.head_sigma, "%.3f")
              + (f"（小鸟：{len(measurement.head_samples)} 个头部圆取中位数："
                 + " / ".join("%.2f" % v for v in measurement.head_samples) + "）"
                 if measurement.head_samples else "")),
             ("身体 σ", _fmt(measurement.body_sigma, "%.3f")),
             ("方向比", _fmt(measurement.motion_ratio)), ("判定", verdict_label(measurement.verdict)),
             ("分数", _fmt(measurement.score, "%d"))],
            charts, legend=[(hex_color(C_SHARP), "清晰"), (hex_color(C_USABLE), "可用"), (hex_color(C_SOFT), "模糊")],
            focus_rect=zoom))
        for step in steps:
            step.bird = index
        self._measurements[index] = measurement
        self.trace.birds.append(TraceBird(index, f"鸟 #{index + 1}", False, steps))

    def _edge_step(self, base: np.ndarray, s: float, selection, region_full: np.ndarray,
                   region_small: np.ndarray, frame: str, region_name: str) -> TraceStep:
        sh, sw = base.shape[:2]
        lw = _line_w(base)
        img = base.copy()

        def paint(mask_full, color):
            ys, xs = np.nonzero(mask_full)
            if ys.size:
                img[(ys * s).astype(int).clip(0, sh - 1), (xs * s).astype(int).clip(0, sw - 1)] = color

        counts = {"candidates": 0, "passed": 0, "selected": 0, "line": 0, "measured": 0}
        if selection is not None:
            paint(selection.candidates & ~selection.passed_noise, C_REJECT_NOISE)
            paint(selection.passed_noise & ~selection.selected, C_WEAK)
            paint(selection.line_like, C_REJECT_LINE)
            if selection.sigma.size:
                colors = [sigma_color(v) for v in selection.sigma.tolist()]
                _paint_points(img, (selection.ys * s).astype(int).clip(0, sh - 1),
                              (selection.xs * s).astype(int).clip(0, sw - 1), colors, max(1, lw))
            counts = {"candidates": int(selection.candidates.sum()), "passed": int(selection.passed_noise.sum()),
                      "selected": int(selection.selected.sum()), "line": int(selection.line_like.sum()),
                      "measured": int(selection.sigma.size)}
        _contour(img, region_small, C_HEAD, max(1, lw // 2))
        noise = selection.noise_sigma if selection is not None else None
        blank = selection is not None and selection.sigma.size < 8
        desc = (f"{region_name}内的 Canny 边缘按去向着色。只有强度 ≥ 4×传感器噪声的边缘参与，取其中最强的 5%；"
                "比预平滑还“锐”的线状结构（细枝、眼圈高光）会被误测成近乎 0 px，舍弃。")
        if blank:
            desc += f"\n\n{region_name}内没有足够的可测边缘：说明严重模糊，按“明显模糊”（≥ 1.55 px）计。"
        return TraceStep(
            STEP_EDGES, "边缘筛选", desc, img, frame,
            [("噪声 σ（Immerkær）", _fmt(noise, "%.4f")),
             ("通过阈值（4×噪声）", _fmt(selection.threshold if selection is not None else None, "%.4f")),
             ("候选边缘", f"{counts['candidates']:,}"), ("高于噪声", f"{counts['passed']:,}"),
             ("最强 5%", f"{counts['selected']:,}"), ("线状舍弃", f"{counts['line']:,}"),
             ("实测点", f"{counts['measured']:,}")],
            legend=EDGE_LEGEND)

    def mark_best(self, best_index: int, excluded=None) -> None:
        """``excluded``: {bird index: analyzer.Exclusion} of measured birds that do not count."""
        excluded = dict(excluded or {})
        for bird in self.trace.birds:
            bird.best = bird.index == best_index
            bird.excluded = bird.index in excluded
            if bird.best:
                bird.label = f"鸟 #{bird.index + 1}（最佳）"
            elif bird.excluded:
                part = excluded[bird.index].reason == "part"
                bird.label = f"鸟 #{bird.index + 1}（{'并入 #%d' % (excluded[bird.index].other + 1) if part else '已排除'}）"
        if len(self.trace.birds) >= 2:
            self._birds_overview(best_index, excluded)

    def _birds_overview(self, best_index: int, excluded=None) -> None:
        excluded = excluded or {}
        """Side-by-side tiles of every bird's own pixels, coloured by verdict (display only)."""
        from . import analyzer as A
        from .models import FOUND_FULL

        tile_w, tile_h, pad = 720, 540, 16
        n = len(self.trace.birds)
        cols = min(n, 3 if n != 4 else 2)
        rows = (n + cols - 1) // cols
        canvas = np.full((rows * (tile_h + pad) + pad, cols * (tile_w + pad) + pad, 3), 24, np.uint8)
        metrics = []
        found_labels = {**FOUND_LABELS, FOUND_FULL: ""}
        for slot, bird in enumerate(self.trace.birds):
            m = self._measurements.get(bird.index)
            src = bird.steps[0].image
            scale = min((tile_w - 24) / src.shape[1], (tile_h - 64) / src.shape[0])
            tile = cv2.resize(src, (max(1, int(src.shape[1] * scale)), max(1, int(src.shape[0] * scale))),
                              interpolation=cv2.INTER_AREA)
            r, c = divmod(slot, cols)
            x0, y0 = pad + c * (tile_w + pad), pad + r * (tile_h + pad)
            dropped = bird.index in excluded
            color = C_WEAK if dropped or m is None else _verdict_rgb(m.verdict)
            best = bird.index == best_index
            cv2.rectangle(canvas, (x0, y0), (x0 + tile_w - 1, y0 + tile_h - 1), color, 10 if best else 3)
            ty, tx = y0 + 52, x0 + (tile_w - tile.shape[1]) // 2
            canvas[ty:ty + tile.shape[0], tx:tx + tile.shape[1]] = (tile // 3 if dropped else tile)
            text = f"#{bird.index + 1}"
            if m is not None:
                text += f"   sigma {_fmt(m.sigma)}   score {_fmt(m.score, '%d')}"
            if best:
                text += "   BEST"
            elif dropped:
                text += "   PART OF #%d" % (excluded[bird.index].other + 1) if excluded[bird.index].reason == "part" \
                    else "   EXCLUDED"
            cv2.putText(canvas, text, (x0 + 16, y0 + 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
            if dropped:
                cv2.line(canvas, (x0, y0), (x0 + tile_w - 1, y0 + tile_h - 1), C_WEAK, 3, cv2.LINE_AA)
                cv2.line(canvas, (x0 + tile_w - 1, y0), (x0, y0 + tile_h - 1), C_WEAK, 3, cv2.LINE_AA)
            if m is not None and dropped:
                eye = _fmt(m.eye_visibility)
                ex = excluded[bird.index]
                would_be = f"本应 {verdict_label(m.verdict)} · σ {_fmt(m.sigma)} · 分数 {_fmt(m.score, '%d')}"
                if ex.reason == A.EXCLUDED_PART:
                    metrics.append((f"鸟 #{bird.index + 1}（并入 #{ex.other + 1}）",
                                    f"看不到鸟眼（{eye}），鸟框 {ex.overlap:.0%} 落在鸟 #{ex.other + 1} 的框内，"
                                    f"而鸟 #{ex.other + 1} 看得到鸟眼：是同一只鸟的局部（翅膀、尾羽），"
                                    f"不单独计数、不参与取最好（{would_be}）"))
                else:
                    metrics.append((f"鸟 #{bird.index + 1}（已排除）",
                                    f"置信度 {m.confidence:.2f} < {A.EXTRA_BIRD_CONFIDENCE_MAX:.2f}、看不到鸟眼（{eye}），"
                                    f"旁边有置信度 ≥ {A.EXTRA_BIRD_ANCHOR_MIN:.2f} 的鸟：按误识别（树叶、树干等）排除，"
                                    f"不参与取最好（{would_be}）"))
            elif m is not None:
                metrics.append((f"鸟 #{bird.index + 1}{'（最佳）' if best else ''}"
                                f"{found_labels.get(getattr(m, 'found_by', ''), '')}",
                                f"{verdict_label(m.verdict)} · σ {_fmt(m.sigma)} · 分数 {_fmt(m.score, '%d')}"
                                f" · 置信度 {m.confidence:.2f}"))
        self.trace.common.append(TraceStep(
            STEP_BIRDS, "逐只鸟",
            f"识别到 {n} 只鸟。每只鸟只用自己的像素单独计算一遍（鸟体 → 头部 → 边缘 → 分布）；整张照片取分数最高"
            "（其次模糊半径最小、置信度最高）的一只，粗框为最佳。置信度低、看不到鸟眼、又紧挨着一只可信的鸟的“鸟”"
            "按误识别排除；看不到鸟眼、框大半落在另一只看得到眼的鸟里的，是那只鸟的局部（翅膀、尾羽），并入它。"
            "两者都灰色打叉，仍可查看计算过程。下一步起依次是每只鸟的计算过程；"
            "右上角「鸟」可只看其中一只。",
            canvas, "birds", metrics,
            legend=[(hex_color(_verdict_rgb(v)), fields.VERDICT_STYLES[v].label)
                    for v in (fields.VERDICT_SHARP, fields.VERDICT_USABLE, fields.VERDICT_SOFT)]))

    # ── no-bird paths ──
    def focus_window(self, image, window, selection, stats) -> None:
        x1, y1, x2, y2 = window
        img = _dim(self._overview, None, 0.6)
        lw = _line_w(img)
        _rect(img, self._display(window), C_WINDOW, lw + 2)
        if self._focus_px is not None:
            _rect(img, self._display(self._focus_px), C_FOCUS, lw, dashed=True)  # on top: visible if equal
        self.trace.region_steps.append(TraceStep(
            STEP_FOCUS, "焦点区域",
            "没有鸟：用相机焦点框。两边都 ≤ 128 px 时取以焦点为中心的 128×128；任一边 > 128 px 时用焦点框本身"
            "（短边补到 128）。贴边时平移而不缩小。",
            img, "full",
            focus_rect=tuple(int(v) for v in _expand(self._display(window), 3.0, img.shape)),
            metrics=[("焦点框", "—" if self._focus_px is None else
              f"{int(self._focus_px[2] - self._focus_px[0])} × {int(self._focus_px[3] - self._focus_px[1])} px"),
             ("测量窗口", f"{x2 - x1} × {y2 - y1} px")],
            legend=[(hex_color(C_FOCUS), "相机焦点框"), (hex_color(C_WINDOW), "测量窗口")]))
        crop = image.gray[y1:y2, x1:x2]
        base = _dim(_auto_exposure(_to_rgb8(crop)), None, 0.7)
        full = np.ones(crop.shape[:2], bool)
        self.trace.region_steps.append(self._edge_step(base, 1.0, selection, full, full, "focus", "焦点窗口"))
        heat = _dim(_auto_exposure(image.rgb8[y1:y2, x1:x2]), None, 0.4)
        if selection is not None and selection.sigma.size:
            _paint_points(heat, selection.ys, selection.xs, [sigma_color(v) for v in selection.sigma.tolist()], 1)
        self.trace.region_steps.append(TraceStep(
            STEP_DISTRIBUTION, "模糊分布", "焦点窗口内实测边缘的模糊半径；取中位数。", heat, "focus",
            [("测量值（中位数）", _fmt(stats.sigma if stats else None, "%.3f px")),
             ("实测点", str(selection.sigma.size if selection is not None else 0))],
            [_histogram([{"name": "焦点窗口", "color": hex_color(C_WINDOW),
                          "values": selection.sigma if selection is not None else np.empty(0)}],
                        stats.sigma if stats else None, "模糊半径分布（px）")]))

    def full_image(self, tiles, stats) -> None:
        img = _dim(self._overview, None, 0.5)
        lw = _line_w(img)
        all_samples = []
        for x, y, w, h, part in tiles:
            box = self._display((x, y, x + w, y + h))
            if part.size >= 8:
                med = float(np.median(part))
                overlay = img.copy()
                cv2.rectangle(overlay, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), sigma_color(med), -1)
                img = cv2.addWeighted(overlay, 0.35, img, 0.65, 0)
                all_samples.append(part)
            _rect(img, box, (90, 90, 90), max(1, lw // 2))
        samples = np.concatenate(all_samples) if all_samples else np.empty(0, np.float32)
        self.trace.region_steps.append(TraceStep(
            STEP_TILES, "全图分块",
            "没有鸟、也没有可用焦点：全图（仅相机画幅内，不含 RAW 黑边）按 1024 px 分块，每块取最强边缘的模糊半径（色块 = 该块中位数），"
            "汇总取中位数。分块使 25–60 MP 图像内存可控。",
            img, "full",
            [("分块数", str(len(tiles))), ("有效块", str(len(all_samples))),
             ("测量值（中位数）", _fmt(stats.sigma, "%.3f px"))],
            [_histogram([{"name": "全图", "color": "#9aa0a6", "values": samples}], stats.sigma, "模糊半径分布（px）")],
            legend=[(hex_color(C_SHARP), "清晰块"), (hex_color(C_USABLE), "可用块"), (hex_color(C_SOFT), "模糊块")]))

    # ── conclusion ──
    def result(self, result) -> None:
        self.trace.result = result
        img = _dim(self._overview, None, 0.7)
        lw = _line_w(img)
        points = []
        rows = []
        highlights = {}
        for i, bird in enumerate(result.birds or []):
            i = bird.get("index", i)  # detection number, unchanged when false extras are dropped
            color = _verdict_rgb(bird["verdict"])
            box = self._display(bird["box"])
            best = tuple(bird["box"]) == tuple(result.bird_box or ())
            _rect(img, box, color, lw * (3 if best else 1))
            if bird.get("sigma") is not None:
                points.append({"sigma": bird["sigma"], "score": bird["score"], "label": f"#{i + 1}",
                               "color": hex_color(color), "best": best})
            found = FOUND_LABELS.get(bird.get("found_by", ""), "")
            rows.append((f"鸟 #{i + 1}{'（最佳）' if best else ''}{found}",
                         f"{verdict_label(bird['verdict'])} · σ {_fmt(bird.get('sigma'))} · 分数 {_fmt(bird.get('score'), '%d')}"))
            highlights[rows[-1][0]] = box
        if result.region_box is not None and result.region != fields.REGION_BIRD:
            _rect(img, self._display(result.region_box), C_WINDOW, lw + 1)
            if result.sigma is not None:
                points.append({"sigma": result.sigma, "score": result.score,
                               "label": fields.REGION_LABELS.get(result.region, ""), "color": hex_color(C_WINDOW),
                               "best": True})
        if self._focus_px is not None:
            _rect(img, self._display(self._focus_px), C_FOCUS, lw)
        region = fields.REGION_LABELS.get(result.region, "—")
        on_bird = None
        if self._focus_px is not None and self._bird_boxes:
            fx, fy = (self._focus_px[0] + self._focus_px[2]) / 2, (self._focus_px[1] + self._focus_px[3]) / 2
            on_bird = any(b[0] <= fx <= b[2] and b[1] <= fy <= b[3] for b in self._bird_boxes)
        metrics = [("判定", verdict_label(result.verdict) or result.verdict), ("分数（0–1000）", _fmt(result.score, "%d")),
                   ("模糊半径", _fmt(result.sigma, "%.3f px")), ("计算区域", region), ("鸟数", str(result.bird_count)),
                   *rows]
        if on_bird is not None:
            metrics.append(("焦点在鸟上", "是" if on_bird else "否（相机焦点不在任何鸟上）"))
        metrics.append(("边缘统计", {"standard": "标准（最强 30 条边缘的中位数）",
                                 "dense": "密集（≥ 60 条边缘的第 40 百分位，实验性）"}.get(
                                     getattr(result, "edge_estimator", ""), "—")))
        metrics.append(("算法版本", result.version))
        charts = [TraceChart("score_curve", "模糊半径 → 分数（SuperPicky 0–1000 刻度）", {
            "anchors": [list(a) for a in SCORE_ANCHORS], "points": points,
            "gates": [[300, "三星门槛"], [100, "判废门槛"]],
        })]
        self.trace.final.append(TraceStep(
            STEP_RESULT, "结论",
            "多只鸟时取分数最高（其次模糊半径最小）的一只作为整张照片的清晰度；分数写入现有锐度字段，"
            "细节写入 XMP-superpicky:bird_sharpness_*。",
            img, "full", metrics, charts,
            legend=[(hex_color(_verdict_rgb(v)), fields.VERDICT_STYLES[v].label)
                    for v in (fields.VERDICT_SHARP, fields.VERDICT_USABLE, fields.VERDICT_SOFT)],
            highlights=highlights))
