"""Flocks of small birds: a high-resolution pass adds the birds the first pass misses."""
from __future__ import annotations

import pytest

from bird_sharpness.analyzer import (DETECT_IMGSZ, FLOCK_BIRD_SIDE, SMALL_DETECT_IMGSZ, BirdSharpnessAnalyzer,
                                     has_small_birds)
from bird_sharpness.models import FOUND_FULL, FOUND_FULL_SMALL, BirdDetection
from bird_sharpness.trace import AnalysisTracer

from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene


class _FlockStub(_StubModels):
    """``hires_only`` birds are seen only at the high-resolution input size."""

    def __init__(self, birds, hires_only=(), **kw):
        super().__init__([*birds, *hires_only], full_w=1800, **kw)
        self.n_first = len(birds)
        self.sizes = []

    def detect_birds(self, bgr, *, conf=0.25, imgsz=None):
        self.sizes.append((imgsz, bgr.shape[1]))
        found = super().detect_birds(bgr, conf=conf, imgsz=imgsz)
        return found if imgsz == SMALL_DETECT_IMGSZ else found[:self.n_first]


def _run(monkeypatch, birds, hires_only=()):
    _install_image(monkeypatch, _scene([(x, y, r, 0.4) for x, y, r in [*birds, *hires_only]]))
    models = _FlockStub(birds, hires_only)
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus).analyze("flock.ARW", tracer=tracer)
    return result, models, tracer.trace


def test_small_birds_trigger_a_high_resolution_pass_that_only_adds_birds(monkeypatch) -> None:
    alone, _m, _t = _run(monkeypatch, [(500, 600, 45)])
    result, models, trace = _run(monkeypatch, [(500, 600, 45)], hires_only=[(1300, 600, 45)])
    assert [size for size, _w in models.sizes] == [DETECT_IMGSZ, SMALL_DETECT_IMGSZ]
    assert result.bird_count == 2
    first, added = sorted(result.birds, key=lambda b: b["box"][0])
    assert first["found_by"] == FOUND_FULL and added["found_by"] == FOUND_FULL_SMALL
    # the first-pass bird is measured exactly as without the extra pass
    assert first["sigma"] == alone.birds[0]["sigma"] and tuple(first["box"]) == tuple(alone.birds[0]["box"])
    detect = dict(next(s for s in trace.common if s.key == "detect").metrics)
    assert "新增 1 只" in detect["小鸟高分辨率补检"]


def test_ordinary_birds_skip_the_high_resolution_pass(monkeypatch) -> None:
    result, models, trace = _run(monkeypatch, [(900, 600, 200)], hires_only=[(1500, 300, 45)])
    assert [size for size, _w in models.sizes] == [DETECT_IMGSZ] and result.bird_count == 1
    assert "小鸟高分辨率补检" not in dict(next(s for s in trace.common if s.key == "detect").metrics)


def test_small_bird_threshold_is_in_detection_copy_pixels() -> None:
    assert has_small_birds([BirdDetection(0.9, (0, 0, FLOCK_BIRD_SIDE - 1, 20))])
    assert not has_small_birds([BirdDetection(0.9, (0, 0, FLOCK_BIRD_SIDE, 20))])
    assert not has_small_birds([])
