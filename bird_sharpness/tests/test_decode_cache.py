"""Trace windows reuse their decoded image when recomputing with other parameters."""
from __future__ import annotations

import os
import threading
import time

import numpy as np
import pytest

import bird_sharpness.models as bs_models
from bird_sharpness import analyzer as analyzer_mod
from bird_sharpness.actions import BirdSharpnessTraceAction
from bird_sharpness.analyzer import BirdSharpnessAnalyzer
from bird_sharpness.image_source import AnalysisImage, DecodedImageCache
from bird_sharpness.params import AnalysisParams

from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene


def _image(value: int = 0) -> AnalysisImage:
    rgb = np.full((8, 8, 3), value, np.uint8)
    return AnalysisImage(rgb, rgb[..., 1].astype(np.float32) / 255.0, True)


def test_cache_reuses_and_evicts_least_recent(tmp_path) -> None:
    files = []
    for name in ("a.ARW", "b.ARW", "c.ARW"):
        f = tmp_path / name
        f.write_bytes(b"raw")
        files.append(str(f))
    cache = DecodedImageCache(capacity=2)
    loads = []

    def load(value):
        loads.append(value)
        return _image(value)

    keys = [DecodedImageCache.key(f, "raw") for f in files]
    first, reused = cache.get_or_load(keys[0], lambda: load(0))
    assert not reused and cache.get_or_load(keys[0], lambda: load(9)) == (first, True)
    cache.get_or_load(keys[1], lambda: load(1))
    cache.get_or_load(keys[0], lambda: load(9))  # a used again: b is now the oldest
    cache.get_or_load(keys[2], lambda: load(2))  # evicts b
    assert len(cache) == 2 and loads == [0, 1, 2]
    assert cache.get_or_load(keys[1], lambda: load(1))[1] is False and loads == [0, 1, 2, 1]
    assert DecodedImageCache.key(files[0], "raw") != DecodedImageCache.key(files[0], "jpeg")
    cache.clear()
    assert len(cache) == 0


def test_a_changed_file_is_decoded_again(tmp_path) -> None:
    f = tmp_path / "x_denoised.tif"
    f.write_bytes(b"one")
    before = DecodedImageCache.key(str(tmp_path / "x.ARW"), "denoised", str(f))
    f.write_bytes(b"re-rendered")
    os.utime(f, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    assert DecodedImageCache.key(str(tmp_path / "x.ARW"), "denoised", str(f)) != before


def test_overlapping_runs_decode_once(tmp_path) -> None:
    cache = DecodedImageCache()
    key = DecodedImageCache.key(str(tmp_path / "x.ARW"), "raw")
    started, release, loads, results = threading.Event(), threading.Event(), [], []

    def slow():
        loads.append(1)
        started.set()
        assert release.wait(5)
        return _image(5)

    first = threading.Thread(target=lambda: results.append(cache.get_or_load(key, slow)))
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=lambda: results.append(cache.get_or_load(key, slow)))
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(5)
    second.join(5)
    assert loads == [1] and sorted(r[1] for r in results) == [False, True]
    assert results[0][0] is results[1][0]


def test_trace_reruns_reuse_the_window_decode(monkeypatch) -> None:
    monkeypatch.setattr(bs_models, "check_runtime", lambda: None)
    _install_image(monkeypatch, _scene([(900, 600, 300, 0.3)]))
    decodes = []
    installed = analyzer_mod.load_analysis_image
    monkeypatch.setattr(analyzer_mod, "load_analysis_image", lambda p: decodes.append(p) or installed(p))
    analyzer = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800), focus_provider=_no_focus)
    cache = DecodedImageCache()

    def run(params=None, source="raw"):
        a = analyzer.with_options(params=params) if params else analyzer
        outcome = BirdSharpnessTraceAction(a, "/photos/a.ARW", image_source=source, image_cache=cache).execute()
        assert outcome.trace is not None, outcome.error
        return dict(outcome.trace.common[0].metrics)["解码耗时"]

    assert run().endswith(" s") and len(decodes) == 1
    assert run(AnalysisParams(max_birds=1, edge_estimator="dense")) == "复用本窗口已解码的图像（未重新解码）"
    assert len(decodes) == 1  # the rerun with other parameters did not decode
    # without a cache (batch-like) every run decodes
    BirdSharpnessTraceAction(analyzer, "/photos/a.ARW").execute()
    assert len(decodes) == 2
