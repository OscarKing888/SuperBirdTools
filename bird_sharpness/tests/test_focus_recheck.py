"""No-bird recheck: camouflaged birds below the first-pass confidence (finer input, camera focus point)."""
from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
import pytest

from app_common import bird_sharpness_fields as bsf
from bird_sharpness.analyzer import (DETECT_IMGSZ, FOCUS_ZOOM_DIVISORS, FOCUS_ZOOM_IMGSZ, RECHECK_IMGSZ,
                                     BirdSharpnessAnalyzer, box_iou, box_overlap, zoom_window)
from bird_sharpness.models import FOUND_FOCUS_WEAK, FOUND_FOCUS_ZOOM, FOUND_FULL, FOUND_FULL_FINE, BirdDetection
from bird_sharpness.trace import AnalysisTracer

from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene

SIZE = (1200, 1800)
FOCUS_NORM = (0.47, 0.47, 0.53, 0.53)  # 846..954 x 564..636 px, on the bird at (900, 600)
FOCUS_PX = (FOCUS_NORM[0] * SIZE[1], FOCUS_NORM[1] * SIZE[0], FOCUS_NORM[2] * SIZE[1], FOCUS_NORM[3] * SIZE[0])


def _focus(path, w, h):
    return FOCUS_NORM


class _CamouflageStub(_StubModels):
    """``birds`` [(cx, cy, r, conf[, conf_at_recheck_input])] on the whole frame;
    ``zoom`` [(cx, cy, r, conf)] seen only in zoomed windows around the focus point."""

    def __init__(self, birds, *, zoom=(), **kw):
        super().__init__([b[:3] for b in birds], full_w=SIZE[1], **kw)
        self.confs = [(b[3], b[4] if len(b) > 4 else b[3]) for b in birds]
        self.zoom = list(zoom)
        self.calls = []

    def detect_birds(self, bgr, *, conf=0.25, imgsz=None):
        self.calls.append((bgr.shape[:2], conf, imgsz))
        if bgr.shape[1] == 1024:  # whole frame (the 1024 px detection copy)
            found = super().detect_birds(bgr)
            confs = [c[1] if imgsz == RECHECK_IMGSZ else c[0] for c in self.confs]
            return [replace(d, confidence=c) for d, c in zip(found, confs) if c >= conf]
        h, w = bgr.shape[:2]
        x1, y1, _, _ = zoom_window(FOCUS_PX, (0, 0, SIZE[1], SIZE[0]), w)
        out = []
        for cx, cy, r, c in self.zoom:
            if c < conf:
                continue
            mask = np.zeros((h, w), np.uint8)
            cv2.circle(mask, (cx - x1, cy - y1), r, 1, -1)
            out.append(BirdDetection(c, (cx - r - x1, cy - r - y1, cx + r - x1, cy + r - y1),
                                     mask if self.masks else None))
        return out


def _analyze(monkeypatch, models, *, sigma=0.3, focus=_focus):
    _install_image(monkeypatch, _scene([(900, 600, 150, sigma)], size=SIZE))
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(models, focus_provider=focus).analyze("bird.ARW", tracer=tracer)
    return result, tracer.trace


def _confident_sigma(monkeypatch, sigma=0.3) -> float:
    result, _ = _analyze(monkeypatch, _CamouflageStub([(900, 600, 150, 0.9)]), sigma=sigma)
    assert result.birds[0]["found_by"] == FOUND_FULL
    return result.sigma


def test_weak_candidate_on_the_focus_box_is_the_bird(monkeypatch) -> None:
    models = _CamouflageStub([(900, 600, 150, 0.15)])
    result, trace = _analyze(monkeypatch, models)
    assert result.region == bsf.REGION_BIRD and result.bird_count == 1
    assert result.birds[0]["found_by"] == FOUND_FOCUS_WEAK
    assert result.sigma == pytest.approx(_confident_sigma(monkeypatch), abs=1e-6)
    assert len(models.calls) == 2  # first pass + recheck input; no zoomed inference needed
    assert models.calls[0][1:] == (0.25, DETECT_IMGSZ)
    assert models.calls[1][1] < 0.25 and models.calls[1][2] == RECHECK_IMGSZ
    keys = [s.key for s in trace.steps_for()]
    assert keys[:4] == ["decode", "detect", "recheck", "bird"]
    recheck = next(s for s in trace.common if s.key == "recheck")
    assert "通过" in dict(recheck.metrics)["规则一：弱候选压在焦点上"]
    assert "焦点复检" in " ".join(k for k, _ in trace.final[0].metrics)


def test_weak_candidate_away_from_focus_is_ignored(monkeypatch) -> None:
    models = _CamouflageStub([(900, 600, 150, 0.15)])
    result, trace = _analyze(monkeypatch, models, focus=lambda p, w, h: (0.05, 0.05, 0.1, 0.1))
    # flat corner: nothing measurable at the focus point either, so the whole frame decides
    assert result.region == bsf.REGION_FULL and result.bird_count == 0
    assert len(models.calls) == 2  # no candidate at the focus point: zoom skipped
    recheck = next(s for s in trace.common if s.key == "recheck")
    assert dict(recheck.metrics)["结果"].startswith("仍判为无鸟")


def test_zoomed_bird_confirmed_by_a_weak_candidate(monkeypatch) -> None:
    models = _CamouflageStub([(900, 600, 150, 0.06)], zoom=[(900, 600, 150, 0.8)])
    result, trace = _analyze(monkeypatch, models)
    assert result.region == bsf.REGION_BIRD and result.bird_count == 1
    assert result.birds[0]["found_by"] == FOUND_FOCUS_ZOOM
    assert result.bird_confidence == pytest.approx(0.8)
    # first window (long edge / 6) already confirms it; its mask is mapped back onto the frame
    assert len(models.calls) == 3
    assert models.calls[2][0] == (300, 300) and models.calls[2][2] == FOCUS_ZOOM_IMGSZ
    assert result.sigma == pytest.approx(_confident_sigma(monkeypatch), abs=0.05)
    recheck = next(s for s in trace.common if s.key == "recheck")
    assert "通过" in dict(recheck.metrics)["放大窗口 300 px"]


def test_zoomed_detection_alone_is_not_trusted(monkeypatch) -> None:
    # Dark leaves read as birds once magnified; without a whole-frame candidate nothing is zoomed.
    models = _CamouflageStub([], zoom=[(900, 600, 150, 0.9)])
    result, _trace = _analyze(monkeypatch, models)
    assert result.region == bsf.REGION_FOCUS and result.bird_count == 0
    assert len(models.calls) == 2


def test_zoomed_detection_elsewhere_than_the_candidate_is_rejected(monkeypatch) -> None:
    models = _CamouflageStub([(900, 600, 150, 0.06)], zoom=[(900, 600, 40, 0.9)])
    result, trace = _analyze(monkeypatch, models)
    assert result.region == bsf.REGION_FOCUS and result.bird_count == 0
    assert len(models.calls) == 2 + len(FOCUS_ZOOM_DIVISORS)
    recheck = next(s for s in trace.common if s.key == "recheck")
    assert "不采信" in dict(recheck.metrics)["放大窗口 300 px"]


def test_confident_bird_skips_the_recheck(monkeypatch) -> None:
    models = _CamouflageStub([(900, 600, 150, 0.9)])
    result, trace = _analyze(monkeypatch, models)
    assert result.birds[0]["found_by"] == FOUND_FULL
    assert "recheck" not in [s.key for s in trace.common]
    assert len(models.calls) == 1


def test_finer_input_finds_a_small_dark_bird_without_focus(monkeypatch) -> None:
    models = _CamouflageStub([(900, 600, 150, 0.1, 0.4)])
    result, trace = _analyze(monkeypatch, models, focus=_no_focus)
    assert result.region == bsf.REGION_BIRD and result.birds[0]["found_by"] == FOUND_FULL_FINE
    assert result.sigma == pytest.approx(_confident_sigma(monkeypatch), abs=1e-6)
    recheck = next(s for s in trace.common if s.key == "recheck")
    assert "直接采纳" in dict(recheck.metrics)[f"全图 {RECHECK_IMGSZ} px 输入再识别"]


def test_without_focus_only_the_finer_input_is_tried(monkeypatch) -> None:
    models = _CamouflageStub([(900, 600, 150, 0.15)])
    result, trace = _analyze(monkeypatch, models, focus=_no_focus)
    assert result.region == bsf.REGION_FULL and result.bird_count == 0
    assert len(models.calls) == 2
    recheck = next(s for s in trace.common if s.key == "recheck")
    assert dict(recheck.metrics)["焦点处复检"].startswith("跳过")


def test_one_bird_at_the_focus_point_even_when_split(monkeypatch) -> None:
    # Weak candidates often split a camouflaged bird into head and body (DSC04177).
    models = _CamouflageStub([(860, 600, 110, 0.15), (960, 600, 90, 0.12)])
    result, _trace = _analyze(monkeypatch, models)
    assert result.bird_count == 1 and result.bird_confidence == pytest.approx(0.15)


def test_box_helpers() -> None:
    assert box_overlap((0, 0, 10, 10), (2, 2, 4, 4)) == pytest.approx(1.0)
    assert box_overlap((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert box_iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert box_iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(1 / 3)
    # windows shift (not shrink) at the image edge, and never exceed the bounds
    assert zoom_window((0, 0, 10, 10), (0, 0, 1000, 800), 300) == (0, 0, 300, 300)
    assert zoom_window((990, 790, 1000, 800), (12, 12, 1000, 800), 300) == (700, 500, 1000, 800)
    assert zoom_window((400, 300, 500, 400), (12, 12, 500, 400), 1000) == (12, 12, 500, 400)


class _StrayMaskStub(_CamouflageStub):
    """Weak candidate whose mask also covers a patch far below the bird (DSC05167)."""

    def detect_birds(self, bgr, *, conf=0.25, imgsz=None):
        found = super().detect_birds(bgr, conf=conf, imgsz=imgsz)
        if imgsz == RECHECK_IMGSZ:
            for det in found:
                s = bgr.shape[1] / SIZE[1]
                cv2.circle(det.mask, (int(900 * s), int(1050 * s)), int(80 * s), 1, -1)
                det.box = (det.box[0], det.box[1], det.box[2], 1130 * s)
        return found


def test_stray_mask_patches_away_from_the_focus_point_are_dropped(monkeypatch) -> None:
    result, trace = _analyze(monkeypatch, _StrayMaskStub([(900, 600, 150, 0.15)]))
    assert result.birds[0]["found_by"] == FOUND_FOCUS_WEAK
    x1, y1, x2, y2 = result.bird_box
    assert y2 < 800 and abs((y1 + y2) / 2 - 600) < 20  # box refitted to the bird piece
    assert result.sigma == pytest.approx(_confident_sigma(monkeypatch), abs=0.02)


def test_find_missed_bird_runs_only_the_recheck(monkeypatch) -> None:
    # SuperViewer's preview box has its own first pass and asks only for the recheck.
    _install_image(monkeypatch, _scene([(900, 600, 150, 0.3)], size=SIZE))
    models = _CamouflageStub([(900, 600, 150, 0.15)])
    missed = BirdSharpnessAnalyzer(models, focus_provider=_focus).find_missed_bird("夜鹰.ARW")
    assert [c[2] for c in models.calls] == [RECHECK_IMGSZ]
    assert missed.source == FOUND_FOCUS_WEAK and missed.camera_crop is None
    assert missed.box == pytest.approx((750 / SIZE[1], 450 / SIZE[0], 1050 / SIZE[1], 750 / SIZE[0]), abs=0.01)
    nothing = BirdSharpnessAnalyzer(_CamouflageStub([]), focus_provider=_focus).find_missed_bird("夜鹰.ARW")
    assert nothing is None
