"""Measuring given birds (the model chain's 测清晰度): a temporary image, no detection, no focus box."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from app_common import bird_sharpness_fields as bsf
from bird_sharpness import analyzer as analyzer_mod, models as bs_models, preview as pv
from bird_sharpness.actions import BirdSharpnessTraceAction
from bird_sharpness.analyzer import BirdSharpnessAnalyzer
from bird_sharpness.image_source import AnalysisImage
from bird_sharpness.models import FOUND_GIVEN
from bird_sharpness.trace import AnalysisTracer

from test_bird_sharpness import _StubModels, _expected, _scene


class _BlindModels(_StubModels):
    """Finds no bird (like YOLO on an occluded one) and counts detection calls."""

    def __init__(self):
        super().__init__([], full_w=1800)
        self.detect_calls = 0

    def detect_birds(self, bgr_small, *, conf=0.25, imgsz=None):
        self.detect_calls += 1
        return []


def _photo(sigma: float) -> AnalysisImage:
    gray = _scene([(900, 600, 300, sigma)])
    rgb8 = np.repeat((np.clip(gray, 0, 1) * 255).astype(np.uint8)[..., None], 3, axis=2)
    return AnalysisImage(rgb8, gray, True, (0.0, 0.0, 1.0, 1.0))


def _sam_like_item() -> pv.PreviewItem:
    mask = np.zeros((1200, 1800), bool)
    cv2.circle(mask.view(np.uint8), (900, 600), 300, 1, -1)
    return pv.PreviewItem("对象 1", 0.84, (600, 300, 1200, 900), mask, (0, 0, 1800, 1200))


def _no_focus_allowed(*_args):
    raise AssertionError("a temporary image has no focus box")


def test_analysis_input_crops_the_photo_pixels_around_the_results() -> None:
    photo = _photo(0.3)
    image, given, region = pv.analysis_input(photo, [_sam_like_item(), pv.PreviewItem("bird", None, (100, 100, 200, 200))],
                                             "模型链 ② sam2.1_t.pt 的 2 个结果")
    assert region == (0.0, 0.0, 1530.0, 1140.0)  # union (100..1200 × 100..900) + 30 % per side, in the frame
    assert image.rgb8.shape[:2] == image.gray.shape == (1140, 1530) and image.camera_crop is None
    assert np.array_equal(image.gray, photo.gray[:1140, :1530])  # real pixels, no grey fill
    sam, box = given.birds
    assert sam.box == (600.0, 300.0, 1200.0, 900.0) and sam.mask.shape == (1140, 1530) and sam.mask[600, 900]
    assert not sam.mask[600, 1250] and sam.confidence == pytest.approx(0.84)
    assert box.mask is None and box.confidence == 1.0 and given.label.startswith("模型链 ②")
    with pytest.raises(ValueError):
        pv.analysis_input(photo, [])


@pytest.mark.parametrize(("sigma", "verdict"), [(0.3, bsf.VERDICT_SHARP), (1.6, bsf.VERDICT_SOFT)])
def test_given_birds_are_measured_without_detection(sigma, verdict) -> None:
    image, given, _region = pv.analysis_input(_photo(sigma), [_sam_like_item()], "模型链 ② 的 1 个结果")
    models = _BlindModels()
    analyzer = BirdSharpnessAnalyzer(models, focus_provider=_no_focus_allowed,
                                     manual_focus_provider=_no_focus_allowed)
    tracer = AnalysisTracer()
    tracer.decode_note = "临时图：模型链 ② 的 1 个结果"
    result = analyzer.analyze("/photos/a.ARW", image_loader=lambda _p: image, given=given, tracer=tracer)
    assert models.detect_calls == 0
    assert result.verdict == verdict and result.region == bsf.REGION_BIRD and result.bird_count == 1
    assert result.birds[0]["found_by"] == FOUND_GIVEN
    assert result.head_sigma == pytest.approx(_expected(sigma), abs=0.2)
    steps = {s.key: s for s in tracer.trace.common}
    detect = next(s for s in tracer.trace.common if dict(s.metrics).get("识别模型"))
    assert "模型链 ② 的 1 个结果" in dict(detect.metrics)["识别模型"]
    decode = tracer.trace.common[0]
    assert "临时图" in decode.description and "未重新解码" in dict(decode.metrics)["解码耗时"]
    assert steps  # every step recorded


def test_trace_action_measures_given_input_without_decoding(monkeypatch) -> None:
    monkeypatch.setattr(bs_models, "check_runtime", lambda: None)
    monkeypatch.setattr(analyzer_mod, "load_analysis_image", lambda p: (_ for _ in ()).throw(AssertionError("decode")))
    image, given, _region = pv.analysis_input(_photo(0.3), [_sam_like_item()], "模型链 ② 的 1 个结果")
    analyzer = BirdSharpnessAnalyzer(_BlindModels(), focus_provider=_no_focus_allowed,
                                     manual_focus_provider=_no_focus_allowed)
    outcome = BirdSharpnessTraceAction(analyzer, "/photos/a.ARW", given_input=(image, given)).execute()
    assert outcome.trace is not None and outcome.result.verdict == bsf.VERDICT_SHARP
