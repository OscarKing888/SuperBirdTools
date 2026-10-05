"""Selectable models, analysis parameters, enhanced bird search and SAM mask refinement."""
from __future__ import annotations

import io
from dataclasses import replace

import cv2
import numpy as np
import pytest

from app_common import bird_sharpness_fields as bsf
from bird_sharpness import model_catalog, models as models_mod
from bird_sharpness.analyzer import BirdSharpnessAnalyzer, enhanced_windows
from bird_sharpness.metrics import TileOptions
from bird_sharpness.models import FOUND_ENHANCED, BirdDetection, BirdSharpnessModelError
from bird_sharpness.params import AnalysisParams, EnhancedSearch
from bird_sharpness.scoring import ALGORITHM_VERSION
from bird_sharpness.trace import AnalysisTracer

from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene


# ── catalog / download ─────────────────────────────────────────────────────

def test_catalog_lists_every_supported_model() -> None:
    names = {m.name for m in model_catalog.DETECTORS}
    assert len(model_catalog.DETECTORS) == 35 and len(model_catalog.SAM_MODELS) == 8
    assert {"yolo11l-seg.pt", "yolo11x.pt", "yolo26x-seg.pt", "yolo12m.pt", "yolov8n-seg.pt"} <= names
    assert "yolo12m-seg.pt" not in names  # YOLO12 ships detection weights only
    big = model_catalog.catalog_model("sam2.1_l.pt")
    assert big.kind == "sam" and big.megabytes == pytest.approx(449.2) and "SAM2.1" in big.label
    assert model_catalog.catalog_model("yolo11x-seg.pt").label == "YOLO11 xlarge 分割（125.1 MB）"
    assert model_catalog.catalog_model("evil.pt") is None
    assert model_catalog.user_model_dir().name == "models"


class _Response(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _fake_model(monkeypatch, name: str, payload: bytes):
    """Make ``name`` a catalog model whose size and SHA-256 are those of ``payload``."""
    import hashlib

    real = model_catalog.catalog_model(name)
    fake = replace(real, size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setitem(model_catalog._BY_NAME, name, fake)
    return fake


def test_catalog_pins_size_and_checksum_of_every_model() -> None:
    for m in (*model_catalog.DETECTORS, *model_catalog.SAM_MODELS):
        assert m.size_bytes > 1_000_000 and len(m.sha256) == 64
    assert model_catalog.catalog_model("yolo11n.pt").size_bytes == 5613764


def test_download_verifies_streams_atomically_and_cleans_up(monkeypatch, tmp_path) -> None:
    import urllib.request

    payload = bytes(range(256)) * 12_000
    _fake_model(monkeypatch, "yolo11n.pt", payload)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout, context: _Response(payload))
    seen = []
    path = model_catalog.download("yolo11n.pt", directory=tmp_path, progress=lambda d, t: seen.append((d, t)))
    assert path == tmp_path / "yolo11n.pt" and path.read_bytes() == payload
    assert seen[-1] == (len(payload), len(payload)) and not list(tmp_path.glob("*.part"))
    assert model_catalog.verify(path) and not model_catalog.verify(tmp_path / "missing.pt")
    with pytest.raises(model_catalog.DownloadCancelled):
        model_catalog.download("yolo11s.pt", directory=tmp_path, cancelled=lambda: True)
    _fake_model(monkeypatch, "yolo11m.pt", payload)  # right size, wrong bytes: checksum must catch it
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout, context: _Response(b"y" * len(payload)))
    with pytest.raises(IOError, match="SHA-256"):
        model_catalog.download("yolo11m.pt", directory=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["yolo11n.pt"]  # nothing bad left behind
    with pytest.raises(ValueError):
        model_catalog.download("../../etc/passwd", directory=tmp_path)


def test_download_resumes_a_cut_connection(monkeypatch, tmp_path) -> None:
    import urllib.request

    payload = bytes(range(256)) * 8_000
    _fake_model(monkeypatch, "yolo11s.pt", payload)
    ranges = []

    def cut_after_a_third(req, timeout, context):
        start = int((req.get_header("Range") or "bytes=0-")[6:-1])
        ranges.append(start)
        end = min(len(payload), start + len(payload) // 3)
        return _Response(payload[start:end], 206 if start else 200)

    monkeypatch.setattr(urllib.request, "urlopen", cut_after_a_third)
    path = model_catalog.download("yolo11s.pt", directory=tmp_path)
    assert path.read_bytes() == payload and ranges[0] == 0 and len(ranges) >= 3 and ranges == sorted(ranges)
    # a server that ignores Range starts over (status 200) and still ends verified
    calls = []

    def ignore_range(req, timeout, context):
        calls.append(req.get_header("Range"))
        return _Response(payload if len(calls) > 1 else payload[:1000])

    monkeypatch.setattr(urllib.request, "urlopen", ignore_range)
    assert model_catalog.download("yolo11s.pt", directory=tmp_path).read_bytes() == payload
    assert calls == [None, "bytes=1000-"]
    # endless cuts: give up after MAX_RESUMES, no partial file kept
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout, context: _Response(b"", 206))
    with pytest.raises(IOError, match="下载不完整"):
        model_catalog.download("yolo11s.pt", directory=tmp_path / "other")
    assert not list((tmp_path / "other").glob("*"))


# ── parameters ─────────────────────────────────────────────────────────────

def test_params_round_trip_normalize_and_tag_the_version() -> None:
    d = AnalysisParams()
    assert d.version_tags() == [] and AnalysisParams.from_params(d.as_params()) == d
    custom = AnalysisParams.from_params({
        "max_birds": 3, "edge_estimator": "dense", "detector": "yolo26x-seg.pt", "sam_model": "sam2.1_b.pt",
        "sam_scope": "all", "enh_mode": "manual", "enh_region_percent": 70, "enh_grid": 4, "enh_imgsz": 1000,
        "enh_min_conf_percent": 60, "enh_lift": False, "full_tile": 512, "unknown": 1})
    assert custom.enhanced == EnhancedSearch("manual", 70, 4, 992, 60, False)  # imgsz: multiple of 32
    assert custom.version_tags() == ["dense", "t512", "yolo26x-seg", "enh-manual70g4i992c60-nolift", "sam2.1_b-all"]
    assert AnalysisParams.from_params(custom.as_params()) == custom
    bad = AnalysisParams.from_params({"detector": "../x.pt", "sam_model": "a b.pt", "enh_mode": "always",
                                      "sam_scope": "?", "enh_grid": 99, "max_birds": -2})
    assert (bad.detector, bad.sam_model, bad.enhanced.mode, bad.sam_scope) == ("auto", "", "off", "rechecked")
    assert bad.enhanced.grid == 6 and bad.max_birds == 0


def test_analyzer_options_share_models_per_detector(monkeypatch) -> None:
    a = BirdSharpnessAnalyzer()
    assert a.models is models_mod.shared_models("auto") and a.version == ALGORITHM_VERSION
    b = a.with_options(params=replace(a.params, detector="yolo11x.pt"))
    assert b.models is models_mod.shared_models("yolo11x.pt") and b.models is not a.models
    assert b.version == f"{ALGORITHM_VERSION}-yolo11x" and a.params.detector == "auto"
    a.params = replace(a.params, detector="yolo11x.pt")  # switching the detector swaps to its shared models
    assert a.models is b.models
    a.max_birds, a.edge_estimator, a.tile_options = 4, "dense", TileOptions(full_tile=512)
    assert a.params.max_birds == 4 and a.version == f"{ALGORITHM_VERSION}-dense-t512-yolo11x"
    stub = _StubModels([], full_w=100)
    explicit = BirdSharpnessAnalyzer(stub)
    assert explicit.with_options(params=replace(explicit.params, detector="yolo11x.pt")).models is stub


def test_missing_detector_is_reported_with_where_to_get_it(monkeypatch) -> None:
    monkeypatch.setattr(models_mod, "find_model", lambda names: None)
    with pytest.raises(BirdSharpnessModelError, match="设置 → 用户选项 → 鸟清晰度"):
        models_mod.BirdSharpnessModels(detector="yolo26x-seg.pt").load()


# ── enhanced bird search ────────────────────────────────────────────────────

class _HiddenBirdModels(_StubModels):
    """The bird is invisible on the whole frame (like DSC05639) and found only in zoomed windows,
    by its own pixels; ``leaf`` adds a weaker false candidate in every window."""

    def __init__(self, *, conf=0.67, leaf=None, slice_conf=None, **kw):
        super().__init__([], full_w=1800, **kw)
        self.conf, self.leaf, self.slice_conf, self.calls = conf, leaf, slice_conf, []

    def detect_birds(self, bgr, *, conf=0.25, imgsz=None):
        h, w = bgr.shape[:2]
        self.calls.append(((h, w), imgsz))
        if max(h, w) >= 1000:  # whole-frame passes (1024 px copies)
            return []
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        mask = (gray > 120).astype(np.uint8)
        out = []
        if mask.sum() > 400:
            ys, xs = np.nonzero(mask)
            box = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
            sliced = box[0] <= 1 or box[1] <= 1 or box[2] >= w - 2 or box[3] >= h - 2
            out.append(BirdDetection(self.slice_conf if sliced and self.slice_conf else self.conf, box, mask))
        if self.leaf is not None:
            out.append(BirdDetection(self.leaf, (5.0, 5.0, 60.0, 60.0), None))
        return [d for d in out if d.confidence >= conf]


def _enhanced_analyzer(monkeypatch, *, mode="nobird", min_conf=50, manual=True, sam=None, **model_kw):
    _install_image(monkeypatch, _scene([(900, 600, 120, 0.3)]))
    refiner = model_kw.pop("refiner", None)
    models = _HiddenBirdModels(**model_kw)
    params = AnalysisParams(enhanced=EnhancedSearch(mode, 50, 3, 640, min_conf), **(sam or {}))
    return models, BirdSharpnessAnalyzer(models, focus_provider=_no_focus, manual_focus_provider=lambda p: manual,
                                         params=params, refiner_provider=refiner)


def test_enhanced_windows_cover_the_region_with_overlap() -> None:
    wins = enhanced_windows((100, 200, 1100, 800), 3)
    assert len(wins) == 9 and wins[0][:2] == (100, 200) and wins[-1][2:] == (1100, 800)
    assert wins[1][0] < wins[0][2]  # neighbours overlap
    assert enhanced_windows((0, 0, 500, 300), 1) == [(0, 0, 500, 300)]


def test_enhanced_search_finds_the_hidden_bird(monkeypatch) -> None:
    models, analyzer = _enhanced_analyzer(monkeypatch, leaf=0.3)
    tracer = AnalysisTracer()
    result = analyzer.analyze("night.ARW", tracer=tracer)
    assert result.region == bsf.REGION_BIRD and result.bird_count == 1
    assert result.birds[0]["found_by"] == FOUND_ENHANCED and result.verdict == bsf.VERDICT_SHARP
    assert result.version == f"{ALGORITHM_VERSION}-enh-nobird50g3i640c50"
    assert sum(1 for shape, imgsz in models.calls if imgsz == 640 and max(shape) < 1000) == 9
    step = next(s for s in tracer.trace.common if s.key == "enhanced")
    assert dict(step.metrics)["采纳"] == "1 只" and "画面中心" in dict(step.metrics)["区域"]
    labels = [r.label for r in step.bird_rows]
    assert labels[0] == "候选 1（采纳）" and any("候选" in l and "采纳" not in l for l in labels)  # the leaf
    assert "增强找鸟" in tracer.trace.final[0].bird_rows[0].label


def test_overlapping_windows_keep_the_whole_bird_not_its_slices(monkeypatch) -> None:
    # Windows cutting through the bird see slices, here more confident than the whole bird.
    _m, analyzer = _enhanced_analyzer(monkeypatch, slice_conf=0.9)
    result = analyzer.analyze("x.ARW")
    assert result.bird_count == 1
    x1, y1, x2, y2 = result.birds[0]["box"]
    assert (x2 - x1, y2 - y1) == (pytest.approx(240, abs=4), pytest.approx(240, abs=4))


def test_enhanced_search_respects_mode_and_threshold(monkeypatch) -> None:
    _m, off = _enhanced_analyzer(monkeypatch, mode="off")
    assert off.analyze("x.ARW").region != bsf.REGION_BIRD
    models, af = _enhanced_analyzer(monkeypatch, mode="manual", manual=False)
    assert af.analyze("x.ARW").region != bsf.REGION_BIRD and all(max(s) >= 1000 for s, _i in models.calls)
    _m, mf = _enhanced_analyzer(monkeypatch, mode="manual", manual=True)
    assert mf.analyze("x.ARW").region == bsf.REGION_BIRD
    _m, strict = _enhanced_analyzer(monkeypatch, min_conf=80)  # 0.67 < 0.80: shown, not taken
    tracer = AnalysisTracer()
    result = strict.analyze("x.ARW", tracer=tracer)
    assert result.region != bsf.REGION_BIRD
    step = next(s for s in tracer.trace.common if s.key == "enhanced")
    assert dict(step.metrics)["采纳"] == "0 只" and "低于门槛" in step.bird_rows[0].value


# ── SAM refinement ─────────────────────────────────────────────────────────

class _FakeRefiner:
    def __init__(self, keep=0.5):
        self.keep, self.calls = keep, []

    def mask(self, rgb, box):
        self.calls.append((rgb.shape, tuple(box)))
        x1, y1, x2, y2 = box
        out = np.zeros(rgb.shape[:2], bool)
        out[y1:y1 + int((y2 - y1) * self.keep), x1:x2] = True  # the top part of the box
        return out


def test_sam_refines_rechecked_birds_and_keeps_detector_masks_otherwise(monkeypatch) -> None:
    refiner = _FakeRefiner()
    _m, analyzer = _enhanced_analyzer(monkeypatch, sam={"sam_model": "sam2.1_t.pt"}, refiner=lambda name: refiner)
    tracer = AnalysisTracer()
    result = analyzer.analyze("x.ARW", tracer=tracer)
    assert result.birds[0]["refined_by"] == "sam2.1_t.pt" and result.sam_model == "sam2.1_t.pt"
    assert len(refiner.calls) == 1 and result.version.endswith("-sam2.1_t-rechecked")
    bird_step = next(s for s in tracer.trace.steps_all() if s.key == "bird")
    assert "sam2.1_t.pt 精修" in dict(bird_step.metrics)["像素来源"]
    assert dict(tracer.trace.final[0].metrics)["SAM 精修"] == "sam2.1_t.pt"
    # birds of the normal pass are left alone unless the scope is "all"
    _install_image(monkeypatch, _scene([(900, 600, 300, 0.3)]))
    normal = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800), focus_provider=_no_focus,
                                   params=AnalysisParams(sam_model="sam2.1_t.pt"), refiner_provider=lambda n: refiner)
    assert normal.analyze("x.ARW").birds[0]["refined_by"] == "" and len(refiner.calls) == 1
    everyone = normal.with_options(params=replace(normal.params, sam_scope="all"))
    assert everyone.analyze("x.ARW").birds[0]["refined_by"] == "sam2.1_t.pt"


def test_sam_mask_that_misses_the_bird_is_not_used(monkeypatch) -> None:
    _install_image(monkeypatch, _scene([(900, 600, 300, 0.3)]))
    tiny = _FakeRefiner(keep=0.05)  # 5 % of the box: the wrong object
    analyzer = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800), focus_provider=_no_focus,
                                     params=AnalysisParams(sam_model="sam2.1_t.pt", sam_scope="all"),
                                     refiner_provider=lambda n: tiny)
    assert analyzer.analyze("x.ARW").birds[0]["refined_by"] == ""
