"""Manual-focus photos without a bird: sharpest small tiles of the frame centre; tiling options."""
from __future__ import annotations

import numpy as np
import pytest

from app_common import bird_sharpness_fields as bsf
from bird_sharpness.analyzer import BirdSharpnessAnalyzer
from bird_sharpness.focus import is_manual_focus
from bird_sharpness.metrics import (MF_MIN_TILES, MF_TILE_MIN_EDGES, TileOptions, full_image_blur,
                                    sharpest_tiles_blur, tile_on_detail)
from bird_sharpness.scoring import ALGORITHM_VERSION
from bird_sharpness.trace import AnalysisTracer

from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene

# One sharp patch in the middle among blurry ones (out-of-focus foreground/background).
SHARP = (900, 600, 70, 0.4)
BLURRY = [(600, 430, 100, 1.9), (1200, 770, 100, 1.9), (600, 780, 90, 1.9), (1200, 420, 90, 1.9),
          (250, 250, 120, 1.9), (1550, 950, 120, 1.9)]


@pytest.mark.parametrize(("meta", "manual"), [
    ({"Make": "SONY", "MakerNote FocusMode": [0]}, True),       # Sony 0x201B: 0 = Manual
    ({"Make": "SONY", "MakerNote FocusMode": [3]}, False),      # AF-C
    ({"Image Make": "SONY", "MakerNote FocusMode": [6]}, False),  # DMF: autofocus + manual touch-up
    ({"Make": "NIKON CORPORATION", "MakerNote FocusMode": "MANUAL"}, True),
    ({"Make": "Canon", "FocusMode": "Manual Focus (3)"}, True),
    ({"Make": "SONY", "FocusMode": "DMF"}, False),
    ({"Make": "FUJIFILM", "MakerNote FocusMode": [0]}, False),  # numbers are only read for Sony
    ({"Make": "SONY", "MakerNote FocusMode2": "Manual"}, None),  # not the FocusMode tag itself
    ({"Make": "SONY"}, None),
])
def test_manual_focus_from_metadata(meta, manual) -> None:
    assert is_manual_focus(meta) is manual


def test_tile_options_normalize_round_trip_and_tag_the_version() -> None:
    assert TileOptions().version_tag() == ""
    assert TileOptions(full_tile=512).version_tag() == "t512"
    assert TileOptions(mf_center=False).version_tag() == "mf-off"
    assert TileOptions(mf_tile=128).version_tag() == "mf50-128-10"
    assert TileOptions(full_tile=1, mf_tile=10 ** 6, mf_sharpest_percent=0).normalized() == \
        TileOptions(128, True, 50, 2048, 1)
    params = {"full_tile": 512, "mf_center": False, "mf_center_percent": 30, "mf_tile": 64,
              "mf_sharpest_percent": 20, "max_birds": 3}
    assert TileOptions.from_params(params).as_params() == {k: v for k, v in params.items() if k != "max_birds"}
    assert TileOptions.from_params({}) == TileOptions()


def _on_sharp_patch(box) -> bool:
    x, y, w, h = box
    sx, sy, half, _sigma = SHARP
    return x < sx + half and x + w > sx - half and y < sy + half and y + h > sy - half


def test_sharpest_tiles_ignore_the_out_of_focus_parts() -> None:
    gray = _scene(texture=[SHARP, *BLURRY])
    everything = full_image_blur(gray, tile=128)
    picked = sharpest_tiles_blur(gray, tile=128, sharpest_percent=5)
    stats, chosen = picked.stats, picked.chosen
    assert len(chosen) == MF_MIN_TILES  # 5% of the 41 measurable tiles is fewer than the minimum
    assert all(_on_sharp_patch(box) for box in chosen)
    assert stats.sigma < 0.85 and everything.sigma > 1.7
    ten = sharpest_tiles_blur(gray, tile=128, sharpest_percent=10)
    assert len(ten.chosen) == max(MF_MIN_TILES, -(-len(ten.candidates) // 10))
    assert sum(map(_on_sharp_patch, ten.chosen)) >= 3 and ten.stats.sigma < 0.85


def test_noise_only_tiles_never_count_as_sharp() -> None:
    # Night frame: almost black, with grain. Its few surviving "edges" read very sharp, but they are noise.
    rng = np.random.default_rng(3)
    gray = np.clip(0.01 + rng.normal(0, 0.006, (600, 900)), 0, 1).astype(np.float32)
    assert sharpest_tiles_blur(gray, tile=128, sharpest_percent=10).candidates == []
    assert not tile_on_detail(np.full(10, 0.4, np.float32), 30)  # 10 steps among the 30 strongest edges
    assert not tile_on_detail(np.full(MF_TILE_MIN_EDGES - 1, 0.4, np.float32), MF_TILE_MIN_EDGES - 1)
    assert tile_on_detail(np.full(20, 0.9, np.float32), 30)


def test_edges_cut_by_a_tile_border_are_not_read_as_sharp() -> None:
    # Blurry stripes only: tiles that clip a stripe at their border used to measure it as a thin, sharp line.
    gray = _scene(texture=BLURRY)
    stats = sharpest_tiles_blur(gray, tile=128, sharpest_percent=1, min_tiles=1).stats
    assert stats.sigma > 1.6  # even the "sharpest" tile is the blurred stripes (was ~0.77 without the margin)


def _analyze(monkeypatch, *, manual, options=None, trace=True):
    _install_image(monkeypatch, _scene(texture=[SHARP, *BLURRY]))
    analyzer = BirdSharpnessAnalyzer(_StubModels([], full_w=1800), focus_provider=_no_focus,
                                     manual_focus_provider=lambda path: manual,
                                     tile_options=options or TileOptions(mf_tile=128))
    tracer = AnalysisTracer() if trace else None
    return analyzer, analyzer.analyze("night.ARW", tracer=tracer), tracer


def test_manual_focus_without_bird_measures_the_sharpest_centre_tiles(monkeypatch) -> None:
    analyzer, result, tracer = _analyze(monkeypatch, manual=True)
    assert result.region == bsf.REGION_MANUAL and result.verdict == bsf.VERDICT_NO_BIRD
    assert result.region_box == (450, 300, 1350, 900)  # centre 50% of each side
    assert result.sigma < 0.8 and result.version == analyzer.version == f"{ALGORITHM_VERSION}-mf50-128-10"
    keys = [s.key for s in tracer.trace.steps_for()]
    assert keys == ["decode", "detect", "recheck", "manual", "result"]
    step = tracer.trace.region_steps[0]
    metrics = dict(step.metrics)
    assert metrics["对焦方式"].startswith("手动对焦") and metrics["分块"] == "128 px"
    assert metrics["取最清晰"].endswith(f"至少 {MF_MIN_TILES} 块）")
    assert "不挑块" in metrics["对比：全部有效块"] and step.focus_rect is not None
    assert dict(tracer.trace.final[0].metrics)["计算区域"] == "手动对焦焦平面"


def test_autofocus_or_disabled_option_keeps_the_whole_image(monkeypatch) -> None:
    _a, af, tracer = _analyze(monkeypatch, manual=False, options=TileOptions(full_tile=256, mf_tile=128))
    assert af.region == bsf.REGION_FULL and af.sigma > 1.5  # the median over all tiles: blur dominates
    assert [s.key for s in tracer.trace.region_steps] == ["tiles"]
    analyzer, off, _t = _analyze(monkeypatch, manual=True, options=TileOptions(mf_center=False, full_tile=512))
    assert off.region == bsf.REGION_FULL and analyzer.version == f"{ALGORITHM_VERSION}-t512-mf-off"
    assert dict(_t.trace.region_steps[0].metrics)["分块"] == "512 px"
    # the manual-focus lookup is not even needed when the option is off
    calls = []
    a2 = BirdSharpnessAnalyzer(_StubModels([], full_w=1800), focus_provider=_no_focus,
                               manual_focus_provider=lambda p: calls.append(p) or True,
                               tile_options=TileOptions(mf_center=False))
    a2.analyze("x.ARW")
    assert calls == []


def test_centre_without_measurable_tiles_falls_back_to_the_whole_image(monkeypatch) -> None:
    _install_image(monkeypatch, _scene(texture=[(200, 200, 100, 0.4)]))  # detail only in a corner
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(_StubModels([], full_w=1800), focus_provider=_no_focus,
                                   manual_focus_provider=lambda p: True).analyze("x.ARW", tracer=tracer)
    assert result.region == bsf.REGION_FULL and result.sigma is not None
    assert [s.key for s in tracer.trace.region_steps] == ["manual", "tiles"]
    assert "改用全图" in tracer.trace.region_steps[0].description


def test_trace_options_reach_a_sibling_analyzer(monkeypatch) -> None:
    analyzer = BirdSharpnessAnalyzer(_StubModels([], full_w=1800), manual_focus_provider=lambda p: True)
    sibling = analyzer.with_options(tile_options=TileOptions(mf_tile=64))
    assert sibling.tile_options.mf_tile == 64 and analyzer.tile_options == TileOptions()
    assert sibling.manual_focus_provider is analyzer.manual_focus_provider
    assert analyzer.with_options(edge_estimator="dense").version == f"{ALGORITHM_VERSION}-dense"
    assert analyzer.with_options(edge_estimator="dense", tile_options=TileOptions(full_tile=2048)).version == \
        f"{ALGORITHM_VERSION}-dense-t2048"
