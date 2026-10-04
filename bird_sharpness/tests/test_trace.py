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
from bird_sharpness.metrics import edge_sigma_map
from bird_sharpness.trace import AnalysisTracer, C_PEAKING, C_SHARP, C_SOFT, peaking_overlay, sigma_color

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
    assert overview.frame == "birds" and not overview.metrics and len(overview.bird_rows) == 2
    oh, ow = overview.image.shape[:2]
    for row in overview.bird_rows:  # box = that bird's tile in the overview canvas
        x1, y1, x2, y2 = row.box
        assert 0 <= x1 < x2 <= ow and 0 <= y1 < y2 <= oh
    assert overview.bird_rows[0].box[0] < overview.bird_rows[1].box[0]  # list order = tile order
    assert "最佳" in overview.bird_rows[0].label  # sharpest first
    assert next(r for r in overview.bird_rows if "最佳" in r.label).color == \
        bsf.VERDICT_STYLES[result.verdict].color.lower()
    every = trace.steps_all()
    assert [s.key for s in every[:3]] == ["decode", "detect", "birds"] and every[-1].key == "result"
    assert [s.bird for s in every[3:-1]] == [0] * len(trace.birds[0].steps) + [1] * len(trace.birds[1].steps)
    final = trace.final[0]
    points = final.charts[0].data["points"]
    assert len(points) == 2 and sum(p["best"] for p in points) == 1
    # 识别 / 结论 list their birds (swatch colour + box to highlight) instead of metric rows / legend entries
    detect = next(s for s in trace.common if s.key == "detect")
    assert [r.label for r in final.bird_rows] == ["鸟 #2（最佳）", "鸟 #1"]  # 结论: sharpest first
    for step in (detect, final):
        assert sorted(r.label[:4] for r in step.bird_rows) == ["鸟 #1", "鸟 #2"]
        assert not any(label.startswith("鸟 #") for label, _ in step.metrics)
        assert not any(label.startswith("鸟 #") for _c, label in step.legend)
        h, w = step.image.shape[:2]
        for row in step.bird_rows:
            x1, y1, x2, y2 = row.box
            assert 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h and row.color.startswith("#")
    assert detect.bird_rows[0].color != detect.bird_rows[1].color  # detection colours, one per bird
    assert [r.bird for r in detect.bird_rows] == [0, 1]  # the same bird keeps its detection number
    for step in (overview, final):  # sharpest first
        assert [r.bird for r in step.bird_rows] == [1, 0]
    best = next(r for r in final.bird_rows if "最佳" in r.label)
    assert best.box[0] > detect.image.shape[1] / 2  # best bird is the right-hand one
    assert best.color == bsf.VERDICT_STYLES[result.verdict].color.lower()
    assert json.loads(json.dumps(final.to_json(), default=str))["bird_rows"][0]["label"] == best.label


def test_result_lists_birds_sharpest_first(monkeypatch) -> None:
    birds = [(300, 600, 220, 1.6), (900, 600, 220, 0.3), (1500, 600, 220, 1.0)]
    result, _plain, trace = _run(monkeypatch, _scene(birds), [b[:3] for b in birds])
    rows = trace.final[0].bird_rows
    assert [r.bird for r in rows] == [1, 2, 0] and "最佳" in rows[0].label
    scores = [next(b["score"] for b in result.birds if b["index"] == r.bird) for r in rows]
    assert scores == sorted(scores, reverse=True)
    # 逐只鸟: same order, tiles laid out in it too; 识别 keeps detection order
    overview = next(s for s in trace.common if s.key == "birds").bird_rows
    assert [r.bird for r in overview] == [1, 2, 0]
    assert [r.box[0] for r in overview] == sorted(r.box[0] for r in overview)
    assert [r.bird for r in next(s for s in trace.common if s.key == "detect").bird_rows] == [0, 1, 2]


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


def test_edge_sigma_map_marks_focused_edges_only() -> None:
    # Left: sharp squares; right: the same squares blurred; bottom/right: black RAW padding.
    gray = np.full((600, 1200), 0.2, np.float32)
    for x in range(40, 1100, 120):
        cv2.rectangle(gray, (x, 60), (x + 60, 420), 0.8, -1)
    gray[:, 600:] = cv2.GaussianBlur(gray, (0, 0), 2.5)[:, 600:]
    gray += np.random.default_rng(3).normal(0, 0.004, gray.shape).astype(np.float32)
    gray[500:, :] = 0.0
    sigma = edge_sigma_map(gray, (0, 0, 1200, 600), (600, 300), bounds=(0, 0, 1200, 500), tile=256)
    assert sigma.shape == (300, 600) and sigma.dtype == np.float16
    left, right = sigma[:, :280], sigma[:, 320:]
    assert np.nanmedian(left) < 0.85 < np.nanmedian(right)
    assert int((left <= 0.85).sum()) > 50 and int((right <= 0.85).sum()) < 5
    assert np.isnan(sigma[245:]).all()  # the padding border is never read as an edge
    # a box maps onto its own output; cancelling stops
    crop = edge_sigma_map(gray, (0, 0, 300, 300), (300, 300))
    assert crop.shape == (300, 300) and int((crop <= 0.85).sum()) > 100
    assert edge_sigma_map(gray, (0, 0, 1200, 600), (600, 300), cancelled=lambda: True) is None


def test_peaking_overlay_tints_focused_edges_red() -> None:
    image = np.full((40, 40, 3), 100, np.uint8)
    sigma = np.full((40, 40), np.nan, np.float16)
    sigma[10, 5:30] = 0.6    # a sharp edge line
    sigma[20, 5:30] = 1.6    # a blurred one
    sigma[30, 20] = 0.5      # an isolated grain pixel
    out = peaking_overlay(image, sigma, 0.85)
    red = (out[..., 0] > 200) & (out[..., 1] < 100)
    assert red[10, 6:29].all() and not red[20].any() and not red[29:32, 19:22].any()
    assert peaking_overlay(image, sigma, 2.0)[20, 10, 0] > 200  # threshold is the viewer's spin box
    assert peaking_overlay(image, None, 0.85) is image
    assert peaking_overlay(image, sigma[:20], 0.85) is image  # wrong frame: untouched


def test_trace_keeps_a_sigma_map_per_frame(monkeypatch) -> None:
    scene = _scene([(450, 600, 260, 1.8), (1350, 600, 260, 0.3)])
    _result, _p, trace = _run(monkeypatch, scene, [(450, 600, 260), (1350, 600, 260)])
    assert set(trace.sigma_maps) == {"full", "bird0", "bird1"}
    for step in trace.steps_all():
        sigma = trace.sigma_map(step)
        assert (sigma is None) == (step.frame == "birds")
        assert sigma is None or sigma.shape == step.image.shape[:2]

    def focused(index):
        return int((trace.sigma_maps[f"bird{index}"] <= 0.85).sum())

    assert focused(1) > 200 and focused(0) < focused(1) / 10  # the sharp bird is on the focal plane
    full = trace.sigma_maps["full"]
    assert int((full[:, 700:] <= 0.85).sum()) > 10 * int((full[:, :500] <= 0.85).sum())
    tracer = AnalysisTracer(focus_peaking=False)
    BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800), focus_provider=_no_focus).analyze(
        "bird.ARW", tracer=tracer)
    assert tracer.trace.sigma_maps == {}
