"""Independent head/body scores preserve the established primary decision."""
from dataclasses import replace

import numpy as np
import pytest

from app_common import bird_sharpness_fields as fields
from bird_sharpness import analyzer as analyzer_mod
from bird_sharpness.analyzer import BirdMeasurement, BirdSharpnessAnalyzer, BirdSharpnessResult, HeadKeypoints
from bird_sharpness.metrics import EdgeBlurField, EdgeBlurStats
from bird_sharpness.scoring import ALGORITHM_VERSION
from test_bird_sharpness import _OffCentreEyeStub, _StubModels, _install_image, _no_focus, _scene


class _MeasuredBody:
    def __init__(self, sigma):
        self.sigma = sigma
        self.masks = []

    def body_blur_detail(self, mask):
        self.masks.append(mask.copy())
        return EdgeBlurStats(self.sigma, None, None, 80), None, None


@pytest.mark.parametrize(('head', 'body', 'expected'), [
    (.7, 1.55, (714, 100, 407.0)),  # average converted scores, not the two sigmas
    (1.55, 1.049, (100, 301, 200.5)),
    (2.55, .5, (0, 1000, 500.0)),
    (2.55, 2.55, (0, 0, 0.0)),
    (None, 1.05, (None, 300, None)),
    (1.05, None, (300, None, None)),
    (None, None, (None, None, None)),
])
def test_part_scores_preserve_zero_half_points_and_missing_measurements(head, body, expected):
    field = _MeasuredBody(body)
    original = np.ones((20, 20), bool)
    windows = np.zeros_like(original)
    windows[5:15, 5:15] = True
    assert analyzer_mod._part_scores(field, original, windows, head) == expected
    assert np.array_equal(field.masks[0], original & ~windows)
    assert original.all() and windows.sum() == 100  # inputs are reused by legacy score/trace


def test_unreliable_head_has_no_part_scores_and_does_not_guess_a_body_region():
    field = _MeasuredBody(.7)
    assert analyzer_mod._part_scores(field, np.ones((20, 20), bool), None, .7) == (None, None, None)
    assert not field.masks


def _reliable_head(models, crop, bird_size):
    h, w = crop.shape[:2]
    eye = np.array([w / 2, h / 2], np.float32)
    return HeadKeypoints(np.array([eye, eye, eye + [50, 0]], np.float32),
                         np.array([.99, .1, .9]), 0, eye_gap=0, beak_gap=0)


def test_body_only_excludes_full_small_head_windows_including_highlight_holes_and_failed_variant(monkeypatch):
    scene = _scene([(900, 600, 150, .3)])
    _install_image(monkeypatch, scene)
    monkeypatch.setattr(analyzer_mod, 'locate_head', _reliable_head)

    def glare(gray, radius):
        mask = np.zeros(gray.shape, bool)
        h, w = gray.shape
        mask[h // 2 - 4:h // 2 + 4, w // 2 - 4:w // 2 + 4] = True
        return mask

    monkeypatch.setattr(analyzer_mod, 'specular_highlights', glare)
    body_masks = []
    real_body = EdgeBlurField.body_blur_detail

    def capture_body(self, mask, **kwargs):
        body_masks.append(mask.copy())
        return real_body(self, mask, **kwargs)

    monkeypatch.setattr(EdgeBlurField, 'body_blur_detail', capture_body)
    analyzer = BirdSharpnessAnalyzer(_StubModels([(900, 600, 150)], full_w=1800), focus_provider=_no_focus)
    real_select = analyzer._select
    sampled = []

    def select(field, region):
        sampled.append(region.copy())
        selection = real_select(field, region)
        # The final 1.25-radius window was sampled but has no usable result.
        return replace(selection, sigma=np.empty(0, np.float32)) if len(sampled) == 7 else selection

    monkeypatch.setattr(analyzer, '_select', select)
    result = analyzer.analyze('small.jpg')
    assert result.ok and len(sampled) == 7 and len(body_masks) == 2
    assert len(result.birds[0]['head_samples']) == 6
    legacy_body, body_only = body_masks
    h, w = legacy_body.shape
    yy, xx = np.ogrid[:h, :w]
    complete_union = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 <= 75 ** 2  # 60 px head × 1.25
    assert np.array_equal(body_only, legacy_body & ~complete_union)
    assert legacy_body[h // 2, w // 2] and not body_only[h // 2, w // 2]  # highlight hole stays excluded
    assert not sampled[-1][h // 2, w // 2]
    assert body_only[h // 2, w // 2 + 80]  # nearby real body is not unnecessarily erased
    assert result.head_score is not None and result.body_score is not None


@pytest.mark.parametrize('head_blur, body_blur', [(.3, 2.0), (2.0, .3), (.3, .3)],
                         ids=['sharp-head-soft-body', 'soft-head-sharp-body', 'both-sharp'])
def test_real_edges_produce_separate_head_and_body_scores_without_changing_primary_decision(
        monkeypatch, head_blur, body_blur):
    head_pixels = _scene([(900, 600, 300, head_blur)])
    body_pixels = _scene([(900, 600, 300, body_blur)])
    yy, xx = np.ogrid[:head_pixels.shape[0], :head_pixels.shape[1]]
    distance = np.hypot(xx - 900, yy - 600)
    blend = np.clip((105 - distance) / 20, 0, 1).astype(np.float32)
    scene = head_pixels * blend + body_pixels * (1 - blend)
    _install_image(monkeypatch, scene)
    monkeypatch.setattr(analyzer_mod, 'locate_head', _reliable_head)
    analyzer = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800), focus_provider=_no_focus)
    result = analyzer.analyze('head-and-body.jpg')
    assert result.head_score >= 500 if head_blur < 1 else result.head_score < 300
    assert result.body_score >= 500 if body_blur < 1 else result.body_score < 300
    if head_blur != body_blur:
        assert min(result.head_score, result.body_score) < result.mean_score < max(result.head_score, result.body_score)
    assert result.mean_score == round((result.head_score + result.body_score) / 2, 1)
    with monkeypatch.context() as patch:
        patch.setattr(analyzer_mod, '_part_scores', lambda *args: (None, None, None))
        legacy = analyzer.analyze('head-and-body.jpg')
    for name in ('verdict', 'score', 'sigma', 'head_sigma', 'body_sigma', 'motion_ratio',
                 'bird_box', 'head_edges', 'eye_xy', 'head_radius'):
        assert getattr(result, name) == getattr(legacy, name), name
    assert result.score == result.head_score  # head-centric primary score stays independent of the mean


@pytest.mark.parametrize('mode', ['missing_model', 'invisible', 'unreliable'])
def test_missing_or_unreliable_head_keeps_all_three_part_scores_empty(monkeypatch, mode):
    _install_image(monkeypatch, _scene([(900, 600, 300, .3)]))
    if mode == 'unreliable':
        models = _OffCentreEyeStub([(900, 600, 300)], full_w=1800, eye_x=.25)
    else:
        models = _StubModels([(900, 600, 300)], full_w=1800,
                             eye_vis=.1 if mode == 'invisible' else .99, keypoints=mode != 'missing_model')
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus).analyze('bird.jpg')
    assert result.ok and result.score is not None
    assert (result.head_score, result.body_score, result.mean_score) == (None, None, None)
    assert all(result.birds[0][name] is None for name in ('head_score', 'body_score', 'mean_score'))


def test_blank_head_does_not_turn_the_legacy_penalty_into_a_measured_head_score(monkeypatch):
    scene = _scene([(900, 600, 300, .3)])
    scene[480:720, 780:1020] = .6  # the reliably located head is featureless; body retains real detail
    _install_image(monkeypatch, scene)
    monkeypatch.setattr(analyzer_mod, 'locate_head', _reliable_head)
    result = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800),
                                   focus_provider=_no_focus).analyze('blank-head.jpg')
    assert result.head_sigma is None and result.sigma >= 1.55 and result.score <= 100
    assert result.head_score is None and result.body_score is not None and result.mean_score is None


def test_head_score_uses_the_direction_corrected_measurement(monkeypatch):
    from bird_sharpness.head_direction import HeadDirection

    _install_image(monkeypatch, _scene([(900, 600, 300, .3)]))
    monkeypatch.setattr(analyzer_mod, 'locate_head', _reliable_head)
    monkeypatch.setattr(analyzer_mod, 'head_direction',
                        lambda field, region: HeadDirection((.5, 2.0), (100, 100), 2.0, .5, 4.0))
    result = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800),
                                   focus_provider=_no_focus).analyze('moving-head.jpg')
    assert result.verdict == 'motion' and result.head_sigma == 2.0
    assert result.head_score == result.score == 55
    assert result.mean_score == round((55 + result.body_score) / 2, 1)


def test_empty_remaining_body_does_not_fall_back_to_the_head(monkeypatch):
    _install_image(monkeypatch, _scene([(900, 600, 100, .3)]))

    def oversized_head(models, crop, bird_size):
        result = _reliable_head(models, crop, bird_size)
        result.pts[2] = result.eye + [500, 0]  # all body pixels fall inside the reliably located head window
        return result

    monkeypatch.setattr(analyzer_mod, 'locate_head', oversized_head)
    result = BirdSharpnessAnalyzer(_StubModels([(900, 600, 100)], full_w=1800),
                                   focus_provider=_no_focus).analyze('head-only.jpg')
    assert result.head_score is not None
    assert result.body_score is None and result.mean_score is None


def _measurement(score, sigma, head, body, mean, x):
    return BirdMeasurement('sharp', score, sigma, sigma, 1.0, 1.0, .99, .9,
                           (x, 0, x + 100, 100), head_score=head, body_score=body, mean_score=mean)


def test_photo_part_scores_come_from_the_same_legacy_best_bird_not_the_best_mean():
    winner = _measurement(800, .64, 800, 0, 400.0, 100)
    other = _measurement(600, .78, 600, 1000, 800.0, 300)
    analyzer = BirdSharpnessAnalyzer(_StubModels([], full_w=1000), focus_provider=_no_focus)
    result = analyzer._bird_result('flock.jpg', [winner, other], 1000)
    assert result.score == 800 and result.bird_box == winner.box
    assert (result.head_score, result.body_score, result.mean_score) == (800, 0, 400.0)
    assert [bird['mean_score'] for bird in result.birds] == [400.0, 800.0]
    assert result.to_dict()['mean_score'] == 400.0


def test_no_bird_fallback_and_old_results_do_not_fabricate_head_body_or_mean_scores(monkeypatch):
    _install_image(monkeypatch, _scene(texture=[(900, 600, 220, .3)]))
    result = BirdSharpnessAnalyzer(_StubModels([], full_w=1800), focus_provider=_no_focus,
                                   manual_focus_provider=lambda path: False).analyze('no-bird.jpg')
    assert result.ok and result.region == 'full' and result.score is not None
    assert (result.head_score, result.body_score, result.mean_score) == (None, None, None)
    old = BirdSharpnessResult('old.jpg', 'sharp', score=800, head_sigma=.64, body_sigma=1.0)
    assert (old.head_score, old.body_score, old.mean_score) == (None, None, None)


def test_xmp_part_scores_write_half_and_zero_and_clear_missing_values():
    result = BirdSharpnessResult('bird.jpg', 'soft', score=100, head_score=0, body_score=301, mean_score=150.5)
    values = result.to_xmp_fields()
    assert values[fields.xmp_key(fields.FIELD_HEAD_SCORE)] == '0'
    assert values[fields.xmp_key(fields.FIELD_BODY_SCORE)] == '301'
    assert values[fields.xmp_key(fields.FIELD_MEAN_SCORE)] == '150.5'
    assert values[fields.SHARPNESS_XMP_KEY] == '100.00'
    cleared = BirdSharpnessResult('bird.jpg', 'no_bird').to_xmp_fields()
    for field in (fields.FIELD_HEAD_SCORE, fields.FIELD_BODY_SCORE, fields.FIELD_MEAN_SCORE):
        assert cleared[fields.xmp_key(field)] == ''
    assert BirdSharpnessResult('bad.jpg', 'error').to_xmp_fields() == {}
    assert ALGORITHM_VERSION == 'sbt-blur-v19'
