"""Head motion must not disappear when the strongest gradients remain sharp."""
import cv2
import numpy as np
import pytest

from bird_sharpness.head_direction import head_direction
from bird_sharpness.metrics import EdgeBlurField
from bird_sharpness.scoring import classify


def disc(sigma=0.0):
    image = np.full((400, 400), .2, np.float32)
    cv2.circle(image, (200, 200), 60, .8, -1)
    return cv2.GaussianBlur(image, (0, 0), sigma) if sigma else image


@pytest.mark.parametrize('angle', [0, 30, 60, 90, 135])
@pytest.mark.parametrize('length', [9, 15, 25])
def test_directional_head_motion_survives_strongest_edge_bias(angle, length):
    kernel = np.zeros((length, length), np.float32)
    a = np.deg2rad(angle)
    offset = np.array([np.cos(a), np.sin(a)]) * (length - 1) / 2
    center = np.array([length // 2, length // 2])
    cv2.line(kernel, tuple(np.rint(center-offset).astype(int)),
             tuple(np.rint(center+offset).astype(int)), 1.0, 1)
    kernel /= kernel.sum()
    moving = cv2.filter2D(disc(), -1, kernel)
    field = EdgeBlurField(moving)
    direction = head_direction(field, np.ones(moving.shape, bool))
    assert direction.motion, direction
    strongest = field.strongest_edge_blur(None).sigma
    verdict, score = classify(max(strongest, direction.major), None, None,
                              eye_visible=True, head_motion=direction.motion)
    assert verdict == 'motion' and score < 300


@pytest.mark.parametrize('sigma', [0, .5, 1, 2, 3])
def test_isotropic_blur_does_not_create_motion(sigma):
    image = disc(sigma)
    direction = head_direction(EdgeBlurField(image), np.ones(image.shape, bool))
    assert not direction.motion, direction


def test_one_long_edge_is_insufficient_direction_evidence():
    image = np.zeros((200, 200), np.float32)
    image[:, 100:] = 1
    direction = head_direction(EdgeBlurField(image), np.ones(image.shape, bool))
    assert direction.major is None and not direction.motion


def test_blurred_wing_outside_head_cannot_override_sharp_head():
    image = disc()
    image[:, :100] = cv2.GaussianBlur(image[:, :100], (0, 0), 5)
    head = np.zeros(image.shape, bool)
    head[110:290, 110:290] = True
    direction = head_direction(EdgeBlurField(image), head)
    assert not direction.motion
    verdict, _ = classify(.7, 3.0, 5.0, eye_visible=True, head_motion=direction.motion)
    assert verdict == 'sharp'


def test_analyzer_and_trace_use_head_motion_without_body_gate(monkeypatch):
    from bird_sharpness.analyzer import BirdSharpnessAnalyzer
    from bird_sharpness.trace import AnalysisTracer
    from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene

    scene = _scene([(900, 600, 300, .3)], noise=0)
    # Texture in the head is dragged too; this exercises the actual analyzer,
    # including the small-bird / eye policy, not just the standalone estimator.
    moving = cv2.filter2D(scene, -1, np.ones((1, 15), np.float32) / 15)
    _install_image(monkeypatch, moving)
    analyzer = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800), focus_provider=_no_focus)
    tracer = AnalysisTracer(focus_peaking=False)
    result = analyzer.analyze('motion.jpg', tracer=tracer)
    plain = analyzer.analyze('motion.jpg')
    assert result.to_xmp_fields() == plain.to_xmp_fields()
    assert result.verdict == 'motion' and result.score < 300
    bird = result.birds[0]
    assert bird['head_motion'] and bird['head_direction_ratio'] >= 1.5
    assert result.head_sigma >= bird['head_direction_sigma']
    distribution = next(s for s in tracer.trace.steps_for() if s.key == 'distribution')
    chart = next(c for c in distribution.charts if '头部边缘 8 方向' in c.title)
    assert chart.data['bins'] == bird['head_direction_bins']
    assert chart.data['condition'] == '头部长轴 σ>1.05'
    assert distribution.charts[0].data['marker_label'] == '最终值'
    assert dict(distribution.metrics)['头部方向修正'] == '已采用'


def test_sparse_nonphysical_direction_fit_is_rejected():
    from types import SimpleNamespace
    # Three bins can fit *any* three numbers exactly; negative variance on the
    # missing axis must not be clamped into evidence of head motion.
    angles = np.repeat(np.array([0, 3, 6]) * np.pi / 8, 20)
    sigmas = np.repeat([.5, .5, 1.1], 20).astype(np.float32)
    selection = SimpleNamespace(ys=np.zeros(60, int), xs=np.arange(60), sigma=sigmas)
    field = SimpleNamespace(gx0=np.cos(angles)[None, :], gy0=np.sin(angles)[None, :],
                            select_strongest_edges=lambda *args, **kwargs: selection)
    direction = head_direction(field, np.ones((1, 60), bool))
    assert direction.major is None and not direction.motion


def test_direction_evidence_survives_empty_strongest_selection(monkeypatch):
    from dataclasses import replace
    from bird_sharpness.analyzer import BirdSharpnessAnalyzer
    from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene

    scene = _scene([(900, 600, 300, .3)], noise=0)
    moving = cv2.filter2D(scene, -1, np.ones((1, 15), np.float32) / 15)
    _install_image(monkeypatch, moving)
    select = BirdSharpnessAnalyzer._select
    def strongest_rejected(self, field, region):
        selected = select(self, field, region)
        return replace(selected, xs=np.empty(0, int), ys=np.empty(0, int), sigma=np.empty(0, np.float32))
    monkeypatch.setattr(BirdSharpnessAnalyzer, '_select', strongest_rejected)
    analyzer = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800), focus_provider=_no_focus)
    result = analyzer.analyze('motion.jpg')
    assert result.head_edges == 0
    assert result.birds[0]['head_motion']
    assert result.head_sigma == result.birds[0]['head_direction_sigma']
    assert result.verdict == 'motion' and result.score < 100
