"""Visual computation trace: steps mirror the real computation; no-bird paths; export."""
from __future__ import annotations

import json
import os

import cv2
import numpy as np
import pytest

from app_common import bird_sharpness_fields as bsf
from bird_sharpness import analyzer as analyzer_mod
from bird_sharpness.analyzer import BirdSharpnessAnalyzer, dedupe_detections, valid_bounds
from bird_sharpness.image_source import AnalysisImage
from bird_sharpness.models import BirdDetection
from bird_sharpness.trace import AnalysisTracer, sigma_color, C_SHARP, C_SOFT

from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene


def _run(monkeypatch, scene, birds, *, focus=_no_focus, crop=None, **stub):
    _install_image(monkeypatch, scene, camera_crop=crop)
    analyzer = BirdSharpnessAnalyzer(_StubModels(birds, full_w=scene.shape[1], **stub), focus_provider=focus)
    tracer = AnalysisTracer()
    result = analyzer.analyze("bird.ARW", tracer=tracer)
    plain = BirdSharpnessAnalyzer(_StubModels(birds, full_w=scene.shape[1], **stub),
                                  focus_provider=focus).analyze("bird.ARW")
    return result, plain, tracer.trace


def test_bird_trace_steps_match_the_computation(monkeypatch) -> None:
    result, plain, trace = _run(monkeypatch, _scene([(900, 600, 300, 0.3)]), [(900, 600, 300)])
    assert result.to_xmp_fields() == plain.to_xmp_fields()  # tracing never changes results
    keys = [s.key for s in trace.steps_for()]
    assert keys == ["decode", "detect", "bird", "head", "edges", "distribution", "result"]
    dist = next(s for s in trace.steps_for() if s.key == "distribution")
    head_values = np.asarray(dist.charts[0].data["series"][0]["values"])
    assert float(np.median(head_values)) == pytest.approx(result.sigma, abs=1e-3)
    assert dist.focus_rect is not None
    edges = next(s for s in trace.steps_for() if s.key == "edges")
    assert dict(edges.metrics)["实测点"] == str(head_values.size)
    for step in trace.steps_for():
        assert step.image.dtype == np.uint8 and step.image.ndim == 3 and step.image.shape[2] == 3
    assert trace.result is result


def test_multi_bird_trace_marks_best_and_switches_steps(monkeypatch) -> None:
    scene = _scene([(450, 600, 260, 1.8), (1350, 600, 260, 0.3)])
    result, plain, trace = _run(monkeypatch, scene, [(450, 600, 260), (1350, 600, 260)])
    assert result.to_xmp_fields() == plain.to_xmp_fields()  # the overview is display only
    assert len(trace.birds) == 2
    best = trace.birds[trace.best_bird_index()]
    assert best.best and "最佳" in best.label
    assert result.bird_box[0] > 900 and best.index == 1
    other = 1 - trace.best_bird_index()
    assert trace.steps_for(other)[3].frame == f"bird{other}"
    # overview of all birds, then every bird's steps in turn
    overview = next(s for s in trace.common if s.key == "birds")
    assert overview.frame == "birds" and len(overview.metrics) == 2
    every = trace.steps_all()
    assert [s.key for s in every[:3]] == ["decode", "detect", "birds"] and every[-1].key == "result"
    assert [s.bird for s in every[3:-1]] == [0] * len(trace.birds[0].steps) + [1] * len(trace.birds[1].steps)
    final = trace.final[0]
    points = final.charts[0].data["points"]
    assert len(points) == 2 and sum(p["best"] for p in points) == 1


def test_focus_and_full_image_traces(monkeypatch) -> None:
    scene = _scene(texture=[(900, 600, 200, 0.8)])
    result, _p, trace = _run(monkeypatch, scene, [], focus=lambda p, w, h: (0.495, 0.495, 0.505, 0.505))
    assert result.region == bsf.REGION_FOCUS
    assert [s.key for s in trace.steps_for()] == ["decode", "detect", "recheck", "focus", "edges", "distribution",
                                                    "result"]
    result, _p, trace = _run(monkeypatch, scene, [])
    assert result.region == bsf.REGION_FULL
    assert [s.key for s in trace.steps_for()] == ["decode", "detect", "recheck", "tiles", "result"]


def test_raw_padding_outside_camera_frame_is_never_measured(monkeypatch) -> None:
    # Camera frame = left/top 75%; the rest is black RAW padding with a hard border.
    scene = _scene(texture=[(500, 400, 150, 1.4)], size=(1200, 1800))
    scene[900:, :] = 0.0
    scene[:, 1350:] = 0.0
    crop = (0.0, 0.0, 0.75, 0.75)
    result, _p, trace = _run(monkeypatch, scene, [], crop=crop)
    assert valid_bounds(AnalysisImage(np.zeros((1200, 1800, 3), np.uint8), scene, True, crop)) == (0, 0, 1350, 900)
    assert result.region_box == (0, 0, 1350, 900)
    assert result.sigma > 1.2  # the black border would read as a ~0.7 px "sharp" edge
    # focus near the padding: window shifted inside the frame
    result, _p, _t = _run(monkeypatch, scene, [], crop=crop, focus=lambda p, w, h: (0.98, 0.98, 0.99, 0.99))
    if result.region == bsf.REGION_FOCUS:
        x1, y1, x2, y2 = result.region_box
        assert x2 <= 1350 and y2 <= 900


def test_split_bird_detections_are_merged() -> None:
    whole = BirdDetection(0.9, (100, 100, 500, 700))
    upper_half = BirdDetection(0.8, (120, 100, 480, 380))
    other_bird = BirdDetection(0.7, (600, 100, 900, 500))
    assert dedupe_detections([whole, upper_half, other_bird]) == [whole, other_bird]


def test_export_writes_step_pngs_and_manifest(monkeypatch, tmp_path) -> None:
    _result, _p, trace = _run(monkeypatch, _scene([(900, 600, 300, 0.3)]), [(900, 600, 300)])
    out = tmp_path / "计算过程"
    written = trace.export(str(out))
    pngs = sorted(p for p in written if p.endswith(".png"))
    assert len(pngs) == 7 and os.path.basename(pngs[0]).startswith("01_decode")
    manifest = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    assert [s["key"] for s in manifest["steps"]][-1] == "result"
    assert manifest["result"]["verdict"] == "sharp"
    assert cv2.imdecode(np.fromfile(pngs[0], np.uint8), cv2.IMREAD_COLOR) is not None


def test_sigma_colours_follow_verdict_thresholds() -> None:
    assert sigma_color(0.6) == C_SHARP
    assert sigma_color(1.55) == C_SOFT
