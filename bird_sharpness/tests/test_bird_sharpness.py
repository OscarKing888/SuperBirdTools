"""bird_sharpness: estimator accuracy on synthetic edges, scoring, pipeline geometry, XMP output."""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app_common import bird_sharpness_fields as bsf
from bird_sharpness import analyzer as analyzer_mod
from bird_sharpness.analyzer import BirdSharpnessAnalyzer, BirdSharpnessResult
from bird_sharpness.image_source import AnalysisImage
from bird_sharpness.metrics import EdgeBlurField
from bird_sharpness.scoring import (
    NO_EYE_SCORE_CAP,
    SCORE_ANCHORS,
    classify,
    sigma_to_score,
)


def _blurred_disc(sigma: float, *, size: int = 400, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    """High-contrast discs and stripes blurred by a known Gaussian, optional sensor noise."""
    img = np.full((size, size), 0.2, np.float32)
    cv2.circle(img, (size // 2, size // 2), size // 4, 0.8, -1)
    for x in range(30, size - 30, 40):
        cv2.rectangle(img, (x, 20), (x + 15, 60), 0.9, -1)
    if sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), sigma)
    if noise:
        img = img + np.random.default_rng(seed).normal(0, noise, img.shape).astype(np.float32)
    return img


# Pixel sampling + the Sobel difference add a fixed ~0.73 px in quadrature.
_MEASUREMENT_FLOOR = 0.73


def _expected(sigma: float) -> float:
    return float(np.hypot(sigma, _MEASUREMENT_FLOOR))


@pytest.mark.parametrize("sigma", [0.0, 0.8, 1.2, 2.0, 3.0])
def test_strongest_edge_blur_recovers_known_gaussian_sigma(sigma) -> None:
    img = _blurred_disc(sigma, noise=0.01)
    stats = EdgeBlurField(img).strongest_edge_blur(np.ones_like(img, bool))
    assert stats.sigma == pytest.approx(_expected(sigma), rel=0.1)


def test_blur_estimate_is_contrast_and_exposure_invariant() -> None:
    img = _blurred_disc(1.5)
    full = EdgeBlurField(img).strongest_edge_blur(np.ones_like(img, bool)).sigma
    dim = EdgeBlurField(img * 0.2 + 0.05).strongest_edge_blur(np.ones_like(img, bool)).sigma
    assert dim == pytest.approx(full, abs=0.05)


def test_motion_blur_raises_directional_ratio() -> None:
    sharp = _blurred_disc(0.8, noise=0.005)
    kernel = np.zeros((1, 15), np.float32)
    kernel[0, :] = 1.0 / 15.0
    moving = cv2.filter2D(sharp, -1, kernel)
    region = np.ones_like(sharp, bool)
    _, iso_ratio = EdgeBlurField(sharp).body_blur(region)
    moving_stats, motion_ratio = EdgeBlurField(moving).body_blur(region)
    assert motion_ratio is not None and iso_ratio is not None
    assert motion_ratio > 1.5 > iso_ratio
    assert moving_stats.sigma > 1.0


def test_score_mapping_is_monotonic_and_matches_superpicky_gates() -> None:
    sigmas = np.linspace(0.2, 3.0, 57)
    scores = [sigma_to_score(s) for s in sigmas]
    assert all(a >= b for a, b in zip(scores, scores[1:]))
    assert sigma_to_score(SCORE_ANCHORS[0][0]) == 1000
    assert sigma_to_score(1.0) == 300  # SuperPicky 3-star eligibility gate
    assert sigma_to_score(1.5) == 100  # SuperPicky reject gate
    assert sigma_to_score(None) is None
    assert sigma_to_score(float("nan")) is None


@pytest.mark.parametrize(
    ("head", "body", "ratio", "eye", "verdict"),
    [
        (0.70, 0.9, 1.1, True, bsf.VERDICT_SHARP),
        (0.95, 0.9, 1.1, True, bsf.VERDICT_USABLE),
        (1.27, 1.0, 1.2, True, bsf.VERDICT_SOFT),
        (1.42, 1.6, 1.8, True, bsf.VERDICT_MOTION),
        (None, 1.64, 1.8, False, bsf.VERDICT_MOTION),
        (None, 2.4, 1.2, False, bsf.VERDICT_SOFT),
        (None, 0.9, 1.1, False, bsf.VERDICT_NO_EYE),
        (None, None, None, False, bsf.VERDICT_NO_EYE),
    ],
)
def test_classify(head, body, ratio, eye, verdict) -> None:
    got, score = classify(head, body, ratio, eye_visible=eye)
    assert got == verdict
    if not eye and score is not None:
        assert score <= NO_EYE_SCORE_CAP


# ── pipeline geometry with stub models (no Torch needed) ──────────────────────

class _T:
    """Minimal tensor stand-in exposing ``.cpu().numpy()``."""

    def __init__(self, array):
        self._a = np.asarray(array)

    def cpu(self):
        return self

    def numpy(self):
        return self._a

    def __getitem__(self, item):
        return _T(self._a[item])

    def __len__(self):
        return len(self._a)


class _Boxes:
    def __init__(self, xyxy, conf):
        self.xyxy = _T(np.asarray(xyxy, np.float32))
        self.conf = _T(np.asarray(conf, np.float32))

    def __len__(self):
        return len(self.conf)


class _Masks:
    def __init__(self, data):
        self.data = _T(np.asarray(data, np.float32))


class _Det:
    def __init__(self, boxes, masks):
        self.boxes = boxes
        self.masks = masks


class _StubModels:
    """Bird = bright disc centred at (cx, cy); eye at its centre, beak to the right."""

    def __init__(self, cx, cy, r, *, eye_vis=0.99, found=True):
        self.cx, self.cy, self.r, self.eye_vis, self.found = cx, cy, r, eye_vis, found
        self.crop_shapes = []

    def load(self):
        pass

    def release(self):
        pass

    def segment(self, bgr_small):
        h, w = bgr_small.shape[:2]
        if not self.found:
            return _Det(_Boxes(np.zeros((0, 4)), []), None)
        s = w / self.full_w
        mask = np.zeros((h, w), np.float32)
        cv2.circle(mask, (int(self.cx * s), int(self.cy * s)), int(self.r * s), 1.0, -1)
        box = [(self.cx - self.r) * s, (self.cy - self.r) * s, (self.cx + self.r) * s, (self.cy + self.r) * s]
        return _Det(_Boxes([box], [0.9]), _Masks([mask]))

    def keypoints(self, rgb_crop):
        self.crop_shapes.append(rgb_crop.shape)
        h, w = rgb_crop.shape[:2]
        # eye at crop centre (bird centre), beak 0.3 r to the right
        eye = (0.5, 0.5)
        beak = (0.5 + 0.3 * self.r / w, 0.5)
        return np.array([eye, eye, beak], np.float32), np.array([self.eye_vis, 0.1, 0.9], np.float32)


def _install_image(monkeypatch, gray: np.ndarray) -> None:
    rgb8 = np.repeat((np.clip(gray, 0, 1) * 255).astype(np.uint8)[..., None], 3, axis=2)
    monkeypatch.setattr(analyzer_mod, "load_analysis_image", lambda path: AnalysisImage(rgb8, gray, False))


def _bird_image(sigma: float) -> np.ndarray:
    img = np.full((1200, 1800), 0.15, np.float32)
    cv2.circle(img, (900, 600), 300, 0.75, -1)
    # plumage-like dark spots inside the bird
    for dx in range(-200, 201, 60):
        for dy in range(-200, 201, 60):
            if dx * dx + dy * dy < 220 ** 2:
                cv2.circle(img, (900 + dx, 600 + dy), 9, 0.3, -1)
    return cv2.GaussianBlur(img, (0, 0), sigma)


@pytest.mark.parametrize(("sigma", "verdict"), [(0.3, bsf.VERDICT_SHARP), (1.6, bsf.VERDICT_SOFT)])
def test_analyzer_measures_head_region_at_full_resolution(monkeypatch, sigma, verdict) -> None:
    _install_image(monkeypatch, _bird_image(sigma))
    models = _StubModels(900, 600, 300)
    models.full_w = 1800
    result = BirdSharpnessAnalyzer(models).analyze("bird.jpg")
    assert result.verdict == verdict, result
    # curved edges sit slightly below the axis-aligned 0.73 px floor
    assert result.head_sigma == pytest.approx(_expected(sigma), abs=0.2)
    assert result.eye_xy == pytest.approx((900, 600), abs=3)
    # keypoints see the padded full-resolution bird crop, not the 1024 px detection image
    assert models.crop_shapes[0][0] > 600


def test_analyzer_without_visible_eye_caps_score(monkeypatch) -> None:
    _install_image(monkeypatch, _bird_image(0.6))
    models = _StubModels(900, 600, 300, eye_vis=0.1)
    models.full_w = 1800
    result = BirdSharpnessAnalyzer(models).analyze("bird.jpg")
    assert result.head_sigma is None
    assert result.verdict == bsf.VERDICT_NO_EYE
    assert result.score is not None and result.score <= NO_EYE_SCORE_CAP


def test_analyzer_reports_no_bird_and_errors(monkeypatch) -> None:
    _install_image(monkeypatch, _bird_image(0.6))
    models = _StubModels(900, 600, 300, found=False)
    models.full_w = 1800
    assert BirdSharpnessAnalyzer(models).analyze("bird.jpg").verdict == bsf.VERDICT_NO_BIRD

    def boom(path):
        raise OSError("damaged file")

    monkeypatch.setattr(analyzer_mod, "load_analysis_image", boom)
    failed = BirdSharpnessAnalyzer(models).analyze("broken.ARW")
    assert failed.verdict == bsf.VERDICT_ERROR and "damaged" in failed.error
    assert failed.to_xmp_fields() == {}


def test_result_xmp_fields_use_superpicky_formats() -> None:
    result = BirdSharpnessResult(path="x", verdict="soft", score=193, head_sigma=1.2689, body_sigma=1.1,
                                 motion_ratio=1.234, eye_visibility=0.998)
    out = result.to_xmp_fields()
    assert out[bsf.SHARPNESS_XMP_KEY] == "193.00"
    assert out["XMP-superpicky:bird_sharpness_verdict"] == "soft"
    assert out["XMP-superpicky:bird_sharpness_head_sigma"] == "1.269"
    assert out["XMP-superpicky:bird_sharpness_motion_ratio"] == "1.23"
    no_bird = BirdSharpnessResult(path="x", verdict="no_bird").to_xmp_fields()
    assert bsf.SHARPNESS_XMP_KEY not in no_bird
    assert no_bird["XMP-superpicky:bird_sharpness_head_sigma"] == ""


def test_write_result_roundtrip_on_chinese_path(tmp_path, monkeypatch) -> None:
    from app_common.exif_io.exiftool_path import get_exiftool_executable_path
    from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
    from bird_sharpness.xmp_store import browser_meta_updates, write_result

    if not get_exiftool_executable_path():
        pytest.skip("ExifTool is unavailable")
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    photo = tmp_path / "世纪公园鹰鹃.jpg"
    Image.new("RGB", (16, 12), "gray").save(photo)
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), {"XMP-dc:Title": "鹰鹃"})
    result = BirdSharpnessResult(path=str(photo), verdict="usable", score=418, head_sigma=0.85, body_sigma=1.0)
    assert write_result(str(photo), result)
    rec = writer.read(str(photo))
    assert rec["Title"] == "鹰鹃"
    assert rec["bird_sharpness_verdict"] == "usable"
    assert float(rec["XMP-photoshop:City"]) == pytest.approx(418)
    display = bsf.bird_sharpness_from_meta(browser_meta_updates(result))
    assert display.text() == "可用 0.85"


def test_collect_image_paths_filters_and_recurses(tmp_path) -> None:
    from bird_sharpness.__main__ import collect_image_paths

    (tmp_path / "sub").mkdir()
    for name in ("a.ARW", "b.jpg", "notes.txt", ".hidden.jpg", "sub/c.HIF"):
        (tmp_path / name).write_bytes(b"x")
    flat = [Path(p).name for p in collect_image_paths([str(tmp_path)], recursive=False)]
    deep = [Path(p).name for p in collect_image_paths([str(tmp_path)], recursive=True)]
    assert flat == ["a.ARW", "b.jpg"]
    assert sorted(deep) == ["a.ARW", "b.jpg", "c.HIF"]


# ── WorkerAction / parallel execution ────────────────────────────────────────

class _CountingAnalyzer:
    """Thread-safe fake that records peak concurrency."""

    def __init__(self, delay=0.05):
        import threading

        self.delay = delay
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.calls = []

    def load(self):
        pass

    def analyze(self, path, on_stage=None):
        import time

        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(path)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        sigma = 0.7 if "sharp" in os.path.basename(path) else 1.3
        verdict, score = classify(sigma, 1.0, 1.1, eye_visible=True)
        return BirdSharpnessResult(path=path, verdict=verdict, score=score, head_sigma=sigma)


def test_analyze_paths_parallel_keeps_input_order_and_reports_every_file() -> None:
    from bird_sharpness.analyzer import analyze_paths

    paths = [f"/p/{'sharp' if i % 2 else 'soft'}_{i}.ARW" for i in range(12)]
    fake = _CountingAnalyzer()
    seen = []
    results = analyze_paths(paths, analyzer=fake, workers=4, on_result=lambda i, n, r: seen.append((i, n)))
    assert [r.path for r in results] == [os.path.normpath(p) for p in paths]
    assert fake.peak > 1
    assert [i for i, _ in seen] == list(range(1, 13)) and all(n == 12 for _, n in seen)


def test_analyze_paths_parallel_stops_submitting_after_cancel() -> None:
    import threading

    from bird_sharpness.analyzer import analyze_paths

    cancel = threading.Event()
    fake = _CountingAnalyzer(delay=0.02)
    results = analyze_paths([f"/p/{i}.ARW" for i in range(40)], analyzer=fake, workers=2,
                            cancel_event=cancel, on_result=lambda i, n, r: cancel.set() if i == 3 else None)
    assert 3 <= len(results) < 40


def test_action_writes_sidecar_skips_existing_and_respects_cancel(tmp_path, monkeypatch) -> None:
    from app_common.exif_io.exiftool_path import get_exiftool_executable_path
    from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
    from bird_sharpness.actions import BirdSharpnessAction

    if not get_exiftool_executable_path():
        pytest.skip("ExifTool is unavailable")
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    photo = tmp_path / "鹰鹃_sharp.jpg"
    Image.new("RGB", (16, 12), "gray").save(photo)
    fake = _CountingAnalyzer(delay=0)

    outcome = BirdSharpnessAction(fake, str(photo), str(photo)).execute()
    assert outcome.written and outcome.result.verdict == bsf.VERDICT_SHARP
    assert PhotoMetaDataXMP().read(str(photo))["bird_sharpness_verdict"] == "sharp"

    again = BirdSharpnessAction(fake, str(photo), str(photo), skip_existing=True).execute()
    assert again.skipped and again.result is None and len(fake.calls) == 1

    before = BirdSharpnessAction(fake, str(photo), str(photo), cancelled=lambda: True).execute()
    assert before.cancelled and len(fake.calls) == 1

    # Cancelled while analysing: the result is dropped instead of being written.
    other = tmp_path / "其它_soft.jpg"
    Image.new("RGB", (16, 12), "gray").save(other)
    flags = iter([False, True])
    late = BirdSharpnessAction(fake, str(other), str(other), cancelled=lambda: next(flags)).execute()
    assert late.cancelled and not late.written
    assert not other.with_suffix(".xmp").exists()
