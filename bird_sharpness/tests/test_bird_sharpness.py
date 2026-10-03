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
    assert sigma_to_score(1.05) == 300  # SuperPicky 3-star eligibility gate
    assert sigma_to_score(1.55) == 100  # SuperPicky reject gate
    assert sigma_to_score(None) is None
    assert sigma_to_score(float("nan")) is None


@pytest.mark.parametrize(
    ("head", "body", "ratio", "eye", "verdict"),
    [
        (0.70, 0.9, 1.1, True, bsf.VERDICT_SHARP),
        (1.00, 0.9, 1.1, True, bsf.VERDICT_USABLE),
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


# ── pipeline with stub models (no Torch needed) ──────────────────────────────

from bird_sharpness.focus import focus_window
from bird_sharpness.models import BirdDetection


class _StubModels:
    """Birds are bright discs; each detection's eye sits at its crop centre, beak to the right.

    ``birds``: [(cx, cy, r)] in full-resolution pixels. ``masks=False`` mimics the
    yolo11n box-only detector, ``keypoints=False`` a missing CUB keypoint model.
    """

    def __init__(self, birds, *, full_w, eye_vis=0.99, masks=True, keypoints=True):
        self.birds, self.full_w, self.eye_vis = list(birds), full_w, eye_vis
        self.masks, self.has_kp = masks, keypoints
        self.crop_shapes = []

    def load(self):
        pass

    def release(self):
        pass

    def detect_birds(self, bgr_small, *, conf=0.25, imgsz=None):
        h, w = bgr_small.shape[:2]
        s = w / self.full_w
        out = []
        for cx, cy, r in self.birds:
            mask = None
            if self.masks:
                mask = np.zeros((h, w), np.uint8)
                cv2.circle(mask, (int(cx * s), int(cy * s)), int(r * s), 1, -1)
            out.append(BirdDetection(0.9, ((cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s), mask))
        return out

    def keypoints(self, rgb_crop):
        if not self.has_kp:
            return None
        self.crop_shapes.append(rgb_crop.shape)
        h, w = rgb_crop.shape[:2]
        eye = (0.5, 0.5)
        beak = (0.5 + 60.0 / w, 0.5)
        return np.array([eye, eye, beak], np.float32), np.array([self.eye_vis, 0.1, 0.9], np.float32)


def _install_image(monkeypatch, gray: np.ndarray, camera_crop=None) -> None:
    rgb8 = np.repeat((np.clip(gray, 0, 1) * 255).astype(np.uint8)[..., None], 3, axis=2)
    monkeypatch.setattr(analyzer_mod, "load_analysis_image",
                        lambda path: AnalysisImage(rgb8, gray, False, camera_crop))


def _scene(birds=(), *, size=(1200, 1800), texture=(), noise=0.004, seed=1) -> np.ndarray:
    """Flat background; ``birds`` [(cx, cy, r, sigma)]; ``texture`` [(x, y, half, sigma)] patches."""
    h, w = size
    img = np.full((h, w), 0.15, np.float32)
    for cx, cy, r, sigma in birds:
        layer = img.copy()
        cv2.circle(layer, (cx, cy), r, 0.75, -1)
        for dx in range(-r + 40, r - 39, 50):
            for dy in range(-r + 40, r - 39, 50):
                if dx * dx + dy * dy < (r - 40) ** 2:
                    cv2.circle(layer, (cx + dx, cy + dy), 8, 0.3, -1)
        layer = cv2.GaussianBlur(layer, (0, 0), sigma)
        pad = r + 30
        img[cy - pad:cy + pad, cx - pad:cx + pad] = layer[cy - pad:cy + pad, cx - pad:cx + pad]
    for x, y, half, sigma in texture:
        layer = img.copy()
        for i in range(-half, half, 16):
            cv2.rectangle(layer, (x + i, y - half), (x + i + 7, y + half), 0.8, -1)
        layer = cv2.GaussianBlur(layer, (0, 0), sigma)
        img[y - half - 20:y + half + 20, x - half - 20:x + half + 20] = \
            layer[y - half - 20:y + half + 20, x - half - 20:x + half + 20]
    img += np.random.default_rng(seed).normal(0, noise, img.shape).astype(np.float32)
    return img


def _no_focus(path, w, h):
    return None


@pytest.mark.parametrize(("sigma", "verdict"), [(0.3, bsf.VERDICT_SHARP), (1.6, bsf.VERDICT_SOFT)])
def test_analyzer_measures_head_region_at_full_resolution(monkeypatch, sigma, verdict) -> None:
    _install_image(monkeypatch, _scene([(900, 600, 300, sigma)]))
    models = _StubModels([(900, 600, 300)], full_w=1800)
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus).analyze("bird.jpg")
    assert result.verdict == verdict, result
    assert result.region == bsf.REGION_BIRD and result.bird_count == 1
    assert result.sigma == result.head_sigma
    assert result.head_sigma == pytest.approx(_expected(sigma), abs=0.2)
    assert result.eye_xy == pytest.approx((900, 600), abs=3)
    # keypoints see the padded full-resolution bird crop, not the 1024 px detection image
    assert models.crop_shapes[0][0] > 600


def test_each_bird_is_measured_separately_and_the_sharpest_wins(monkeypatch) -> None:
    _install_image(monkeypatch, _scene([(450, 600, 260, 1.8), (1350, 600, 260, 0.3)]))
    for order in ([(450, 600, 260), (1350, 600, 260)], [(1350, 600, 260), (450, 600, 260)]):
        result = BirdSharpnessAnalyzer(_StubModels(order, full_w=1800), focus_provider=_no_focus).analyze("b.jpg")
        assert result.bird_count == 2 and len(result.birds) == 2
        assert result.verdict == bsf.VERDICT_SHARP
        assert result.bird_box[0] > 900  # the right-hand (sharp) bird decided the photo
        per_bird = sorted(b["sigma"] for b in result.birds)
        assert per_bird[0] == result.sigma and per_bird[1] > 1.4  # soft bird kept its own value


def test_box_only_detector_without_keypoint_model_measures_whole_bird(monkeypatch) -> None:
    _install_image(monkeypatch, _scene([(900, 600, 300, 0.3)]))
    models = _StubModels([(900, 600, 300)], full_w=1800, masks=False, keypoints=False)
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus).analyze("bird.jpg")
    assert result.region == bsf.REGION_BIRD
    assert result.head_sigma is None and result.eye_visibility is None
    assert result.sigma == pytest.approx(_expected(0.3), abs=0.2)
    assert result.verdict == bsf.VERDICT_SHARP  # not capped as "no eye": there is no eye model


def test_analyzer_without_visible_eye_caps_score(monkeypatch) -> None:
    _install_image(monkeypatch, _scene([(900, 600, 300, 0.3)]))
    models = _StubModels([(900, 600, 300)], full_w=1800, eye_vis=0.1)
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus).analyze("bird.jpg")
    assert result.head_sigma is None
    assert result.verdict == bsf.VERDICT_NO_EYE
    assert result.score is not None and result.score <= NO_EYE_SCORE_CAP


@pytest.mark.parametrize(("focus", "expected_box"), [
    # small focus box -> 128 x 128 window centred on it
    ((0.495, 0.495, 0.505, 0.505), (836, 536, 964, 664)),
    # wider than 128 -> the focus box itself; its 60 px tall side widened to 128
    ((0.4, 0.475, 0.6, 0.525), (720, 536, 1080, 664)),
])
def test_no_bird_measures_focus_window(monkeypatch, focus, expected_box) -> None:
    _install_image(monkeypatch, _scene(texture=[(900, 600, 200, 0.8)]))
    seen = []

    def provider(path, w, h):
        seen.append((w, h))
        return focus

    result = BirdSharpnessAnalyzer(_StubModels([], full_w=1800), focus_provider=provider).analyze("x.jpg")
    assert seen == [(1800, 1200)]
    assert result.verdict == bsf.VERDICT_NO_BIRD and result.region == bsf.REGION_FOCUS
    assert result.region_box == expected_box
    assert result.sigma == pytest.approx(_expected(0.8), abs=0.25)
    assert result.score == sigma_to_score(result.sigma) and result.score is not None


def test_focus_box_maps_through_raw_camera_crop(monkeypatch) -> None:
    # RAW output keeps sensor margins: the camera frame is the inner 10%..90% here.
    _install_image(monkeypatch, _scene(texture=[(900, 600, 200, 0.8)]), camera_crop=(0.1, 0.1, 0.9, 0.9))
    seen = []

    def provider(path, w, h):
        seen.append((w, h))
        return (0.495, 0.495, 0.505, 0.505)

    result = BirdSharpnessAnalyzer(_StubModels([], full_w=1800), focus_provider=provider).analyze("x.ARW")
    assert seen == [(1440, 960)]  # provider works in the camera frame
    x1, y1, x2, y2 = result.region_box
    assert ((x1 + x2) / 2, (y1 + y2) / 2) == pytest.approx((900, 600), abs=2)


def test_no_bird_without_focus_measures_whole_image(monkeypatch) -> None:
    _install_image(monkeypatch, _scene(texture=[(500, 400, 150, 1.0), (1300, 800, 150, 1.0)]))
    result = BirdSharpnessAnalyzer(_StubModels([], full_w=1800), focus_provider=_no_focus).analyze("x.jpg")
    assert result.region == bsf.REGION_FULL and result.region_box == (0, 0, 1800, 1200)
    assert result.sigma == pytest.approx(_expected(1.0), abs=0.25)


def test_featureless_focus_window_falls_back_to_whole_image(monkeypatch) -> None:
    # Focus on flat, noisy sky: noise must not read as a perfectly sharp edge.
    _install_image(monkeypatch, _scene(texture=[(1500, 900, 150, 1.0)], noise=0.01))
    result = BirdSharpnessAnalyzer(_StubModels([], full_w=1800),
                                   focus_provider=lambda p, w, h: (0.2, 0.2, 0.21, 0.21)).analyze("x.jpg")
    assert result.region == bsf.REGION_FULL
    assert result.sigma is not None and result.sigma > 0.6


def test_noise_and_thin_lines_are_not_measured_as_sharp_edges() -> None:
    noise = 0.4 + np.random.default_rng(3).normal(0, 0.01, (400, 400)).astype(np.float32)
    assert EdgeBlurField(noise).strongest_edge_blur(None).sigma is None
    lines = np.full((400, 400), 0.2, np.float32)
    for x in range(20, 380, 25):
        lines[:, x] = 0.9  # 1 px bright lines (twigs, eye-ring highlights)
    samples = EdgeBlurField(lines).strongest_edge_samples(None)
    assert samples.size == 0 or float(np.median(samples)) > 0.4


def test_focus_window_rules() -> None:
    assert focus_window((100, 100, 110, 110), 1000, 800) == (41, 41, 169, 169)
    assert focus_window((0, 0, 10, 10), 1000, 800) == (0, 0, 128, 128)  # shifted inside, not shrunk
    assert focus_window((995, 795, 1000, 800), 1000, 800) == (872, 672, 1000, 800)
    assert focus_window((100, 100, 400, 150), 1000, 800) == (100, 61, 400, 189)
    assert focus_window((10, 10, 20, 20), 100, 60) == (0, 0, 100, 60)  # image smaller than the window


def test_analyzer_reports_errors(monkeypatch) -> None:
    def boom(path):
        raise OSError("damaged file")

    monkeypatch.setattr(analyzer_mod, "load_analysis_image", boom)
    failed = BirdSharpnessAnalyzer(_StubModels([], full_w=1800)).analyze("broken.ARW")
    assert failed.verdict == bsf.VERDICT_ERROR and "damaged" in failed.error
    assert failed.to_xmp_fields() == {}


def test_result_xmp_fields_use_superpicky_formats() -> None:
    result = BirdSharpnessResult(path="x", verdict="soft", score=193, sigma=1.2689, region="bird", bird_count=2,
                                 head_sigma=1.2689, body_sigma=1.1, motion_ratio=1.234, eye_visibility=0.998)
    out = result.to_xmp_fields()
    assert out[bsf.SHARPNESS_XMP_KEY] == "193.00"
    assert out["XMP-superpicky:bird_sharpness_verdict"] == "soft"
    assert out["XMP-superpicky:bird_sharpness_sigma"] == "1.269"
    assert out["XMP-superpicky:bird_sharpness_region"] == "bird"
    assert out["XMP-superpicky:bird_sharpness_bird_count"] == "2"
    assert out["XMP-superpicky:bird_sharpness_head_sigma"] == "1.269"
    assert out["XMP-superpicky:bird_sharpness_motion_ratio"] == "1.23"
    assert out["XMP-superpicky:bird_sharpness_version"] == "sbt-blur-v6"
    # No bird: the focus/whole-image value still fills the sharpness slot.
    focus = BirdSharpnessResult(path="x", verdict="no_bird", score=420, sigma=0.9, region="focus").to_xmp_fields()
    assert focus[bsf.SHARPNESS_XMP_KEY] == "420.00"
    assert focus["XMP-superpicky:bird_sharpness_head_sigma"] == ""


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

    def analyze(self, path, on_stage=None, cancelled=None):
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


def test_heavily_blurred_noisy_head_is_soft_not_sharp(monkeypatch) -> None:
    """Regression (DSC04757, ISO 6400): an eye is found but the head has no edge above
    the noise; the few weak noise-biased edges used to read as ~0.7 px "sharp"."""
    scene = _scene([(900, 600, 300, 7.0)], noise=0.02, seed=5)
    _install_image(monkeypatch, scene)
    models = _StubModels([(900, 600, 300)], full_w=1800)
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus).analyze("dark.ARW")
    assert result.verdict in (bsf.VERDICT_SOFT, bsf.VERDICT_MOTION)
    assert result.head_sigma is None and result.sigma >= 1.55
    assert result.score is not None and result.score <= 100


def test_weak_edges_near_noise_are_not_measured() -> None:
    rng = np.random.default_rng(7)
    img = _blurred_disc(0.5)
    weak = 0.4 + (img - img.mean()) * 0.04 + rng.normal(0, 0.01, img.shape).astype(np.float32)
    assert EdgeBlurField(weak).strongest_edge_blur(None).sigma is None
    strong = img + rng.normal(0, 0.01, img.shape).astype(np.float32)
    assert EdgeBlurField(strong).strongest_edge_blur(None).sigma == pytest.approx(_expected(0.5), rel=0.15)


def test_classify_blank_head() -> None:
    from bird_sharpness.scoring import blank_head_sigma

    verdict, score = classify(None, 1.2, 1.1, eye_visible=True, head_blank=True)
    assert verdict == bsf.VERDICT_SOFT and score == sigma_to_score(1.55) == 100
    verdict, score = classify(None, 2.0, 1.8, eye_visible=True, head_blank=True)
    assert verdict == bsf.VERDICT_MOTION and score == sigma_to_score(2.0)
    assert blank_head_sigma(None) == 1.55
