"""The split trace displays the actual head-excluded body computation, without remeasuring."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from bird_sharpness import analyzer as analyzer_mod
from bird_sharpness.analyzer import BirdSharpnessAnalyzer, BirdMeasurement
from bird_sharpness.metrics import EdgeBlurField, EdgeBlurStats
from bird_sharpness.trace import AnalysisTracer, AnalysisTrace
from test_bird_sharpness import _StubModels, _OffCentreEyeStub, _install_image, _no_focus, _scene
from test_part_scores import _reliable_head


def _steps(trace):
    return {step.key: step for step in trace.birds[0].steps}


def test_body_steps_use_the_single_real_measurement_and_exclude_all_small_head_windows(monkeypatch):
    scene = _scene([(900, 600, 150, .3)])
    _install_image(monkeypatch, scene)
    monkeypatch.setattr(analyzer_mod, 'locate_head', _reliable_head)
    captured = []
    original = EdgeBlurField.body_blur_detail

    def capture(self, region, **kwargs):
        answer = original(self, region, **kwargs)
        captured.append((region.copy(), answer))
        return answer

    monkeypatch.setattr(EdgeBlurField, 'body_blur_detail', capture)
    analyzer = BirdSharpnessAnalyzer(_StubModels([(900, 600, 150)], full_w=1800), focus_provider=_no_focus)
    tracer = AnalysisTracer()
    result = analyzer.analyze('small.ARW', tracer=tracer)
    assert result.ok and len(captured) == 2  # inclusive legacy + exclusive part; trace does not remeasure
    inclusive, _ = captured[0]
    exclusive, (stats, ratio, (ys, xs, sigma, bins)) = captured[1]
    h, w = exclusive.shape
    yy, xx = np.ogrid[:h, :w]
    windows = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 <= 75 ** 2  # actual 1.25× head sample
    assert np.array_equal(exclusive, inclusive & ~windows)
    assert exclusive[ys, xs].all() and not windows[ys, xs].any()
    steps = _steps(tracer.trace)
    region = dict(steps['body_region'].metrics)
    assert region['身体测量像素'] == f'{exclusive.sum():,}'
    assert region['排除的头部窗口像素'] == f'{(inclusive & windows).sum():,}'
    body = steps['body_score']
    np.testing.assert_array_equal(body.charts[0].data['series'][0]['values'], sigma)
    assert body.charts[0].data['median'] == stats.sigma
    assert body.charts[1].data['bins'] == bins
    assert body.charts[1].data['ratio'] == ratio
    assert body.charts[2].data['points'][0]['score'] == result.body_score
    assert dict(steps['body_edges'].metrics)['有效实测点'] == str(sigma.size)
    map_ = tracer.trace.sigma_map(body)
    assert map_.shape == exclusive.shape and not np.isfinite(map_[windows]).any()
    assert np.isfinite(map_).sum() == sigma.size
    assert all(steps[k].frame == 'bird0' for k in ('head_score', 'body_region', 'body_edges', 'body_score', 'mean_score'))
    assert tracer.trace.sigma_map(steps['mean_score']) is None
    plain = analyzer.analyze('small.ARW')
    traced_dict, plain_dict = result.to_dict(), plain.to_dict()
    for key in ('elapsed_s', 'stage_s'):
        traced_dict.pop(key, None); plain_dict.pop(key, None)
    assert traced_dict == plain_dict


@pytest.mark.parametrize('mode', ['no_model', 'invisible', 'unreliable'])
def test_missing_head_steps_explain_empty_parts_and_do_not_show_whole_bird_as_head(monkeypatch, mode):
    _install_image(monkeypatch, _scene([(900, 600, 300, .3)]))
    if mode == 'unreliable':
        models = _OffCentreEyeStub([(900, 600, 300)], full_w=1800, eye_x=.25)
    else:
        models = _StubModels([(900, 600, 300)], full_w=1800, keypoints=mode != 'no_model',
                             eye_vis=.1 if mode == 'invisible' else .99)
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus).analyze('missing.ARW', tracer=tracer)
    assert result.ok
    steps = _steps(tracer.trace)
    assert steps['head'].title == '头部定位'
    assert steps['edges'].title == '整鸟回退边缘'
    for key, label in [('head_score', '头部清晰度'), ('body_score', '身体清晰度'), ('mean_score', '平均清晰度')]:
        assert dict(steps[key].metrics)[label] == '—'
        assert '定位' in dict(steps[key].metrics)['状态']
        assert tracer.trace.sigma_map(steps[key]) is None
    assert not steps['head_score'].charts[0].data['series'][0]['values'].size
    assert dict(steps['body_region'].metrics)['身体测量像素'] == '—'


def test_blank_head_does_not_display_primary_fallback_as_a_measured_part(monkeypatch):
    scene = _scene([(900, 600, 300, .3)])
    scene[480:720, 780:1020] = .6
    _install_image(monkeypatch, scene)
    monkeypatch.setattr(analyzer_mod, 'locate_head', _reliable_head)
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800),
                                   focus_provider=_no_focus).analyze('blank.ARW', tracer=tracer)
    steps = _steps(tracer.trace)
    assert result.score is not None and result.sigma >= 1.55
    assert result.head_score is None and result.body_score is not None
    assert steps['head_score'].charts[-1].data['points'] == []
    assert dict(steps['head_score'].metrics)['实测头部 σ'] == '—'
    assert '推断' in dict(steps['head_score'].metrics)['状态']
    assert dict(steps['body_score'].metrics)['身体清晰度'] == str(result.body_score)
    assert dict(steps['mean_score'].metrics)['平均清晰度'] == '—'


@pytest.mark.parametrize('body_score,mean', [(841, 420.5), (0, 0.0)])
def test_mean_step_preserves_zero_and_fraction_without_averaging_sigmas(body_score, mean):
    tracer = AnalysisTracer(focus_peaking=False)
    tracer.trace = AnalysisTrace('parts.jpg')
    body = np.ones((40, 40), bool)
    windows = np.zeros_like(body)
    windows[:20] = True
    measurement = BirdMeasurement('soft', 0, 2.55, 2.55, 1.0, None, .99, .9, (0, 0, 40, 40),
                                  head_score=0, body_score=body_score, mean_score=mean)
    parts = tracer._part_steps(np.full((40, 40, 3), 100, np.uint8), 1.0, 'bird0', body, windows,
                               None, measurement, {'head_windows': windows, 'head_sigma': 2.55,
                                  'body': body & ~windows, 'body_stats': EdgeBlurStats(.6, None, None, 0)}, None)
    steps = {s.key: s for s in parts}
    values = dict(steps['mean_score'].metrics)
    assert values['计算公式'] == f'(0 + {body_score}) / 2 = {mean:.1f}'
    assert values['平均清晰度'] == f'{mean:.1f}'
    assert not tracer.trace.sigma_maps


def test_export_includes_separate_parts_for_each_bird_without_diagnostic_arrays(monkeypatch, tmp_path):
    birds = [(450, 600, 260, 1.8), (1350, 600, 260, .3)]
    _install_image(monkeypatch, _scene(birds))
    tracer = AnalysisTracer(focus_peaking=False)
    result = BirdSharpnessAnalyzer(_StubModels([b[:3] for b in birds], full_w=1800),
                                   focus_provider=_no_focus).analyze('多鸟.ARW', tracer=tracer)
    target = tmp_path / '计算过程'
    tracer.trace.export(str(target))
    manifest = json.loads((target / 'trace.json').read_text())
    for bird in result.birds:
        steps = {s['key']: s for s in manifest['steps'] if s['bird'] == bird['index']}
        assert all(k in steps for k in ('head_score', 'body_region', 'body_edges', 'body_score', 'mean_score'))
        assert dict(steps['body_score']['metrics'])['身体清晰度'] == str(bird['body_score'])
        assert (target / steps['body_edges']['file']).is_file()
    assert not any('sigma_map_key' in s or 'head_windows' in s or 'body_only' in s for s in manifest['steps'])


def test_focus_peaking_false_omits_maps_even_when_both_parts_have_real_edges(monkeypatch):
    _install_image(monkeypatch, _scene([(900, 600, 300, .3)]))
    monkeypatch.setattr(analyzer_mod, 'locate_head', _reliable_head)
    tracer = AnalysisTracer(focus_peaking=False)
    result = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800),
                                   focus_provider=_no_focus).analyze('sharp.ARW', tracer=tracer)
    assert result.head_score is not None and result.body_score is not None
    steps = _steps(tracer.trace)
    assert steps['head_score'].charts[0].data['series'][0]['values'].size > 0
    assert steps['body_score'].charts[0].data['series'][0]['values'].size > 0
    assert tracer.trace.sigma_maps == {}
    assert all(tracer.trace.sigma_map(step) is None for step in tracer.trace.steps_all())


def test_downscaled_part_maps_average_collisions_instead_of_using_the_last_point():
    tracer = AnalysisTracer()
    tracer.trace = AnalysisTrace('parts.jpg')
    body = np.ones((40, 40), bool)
    windows = np.zeros_like(body)
    windows[:20] = True
    measurement = BirdMeasurement('sharp', 500, .85, .85, .85, None, .99, .9, (0, 0, 40, 40),
                                  head_score=500, body_score=500, mean_score=500.)
    selection = SimpleNamespace(ys=np.array([4, 4]), xs=np.array([4, 5]), sigma=np.array([.6, 1.]))
    parts = tracer._part_steps(np.full((20, 20, 3), 100, np.uint8), .5, 'bird0', body, windows,
                               selection, measurement, {'head_windows': windows, 'head_sigma': .85,
                                'body': body & ~windows, 'body_stats': EdgeBlurStats(.85, None, None, 2),
                                'body_detail': (np.array([24, 24]), np.array([4, 5]), np.array([1., .6]), [None] * 8)}, None)
    steps = {s.key: s for s in parts}
    assert tracer.trace.sigma_map(steps['head_score'])[2, 2] == pytest.approx(.8, abs=.001)
    assert tracer.trace.sigma_map(steps['body_score'])[12, 2] == pytest.approx(.8, abs=.001)
    assert np.isfinite(tracer.trace.sigma_map(steps['body_score'])).sum() == 1
