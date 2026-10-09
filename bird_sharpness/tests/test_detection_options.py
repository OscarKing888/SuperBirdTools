"""鸟群复检、检测覆盖与参数版本：不加载模型、不访问网络。"""
from dataclasses import replace
import argparse

import pytest

from bird_sharpness.analyzer import BirdSharpnessAnalyzer, dedupe_detections
from bird_sharpness.models import BirdDetection, FOUND_FULL_FINE, FOUND_FULL_SMALL
from bird_sharpness.params import AnalysisParams, add_detection_arguments, detection_params_from_args
from bird_sharpness.trace import AnalysisTracer
from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene
from test_false_birds import SCENE, _Stub


class RecheckedFlock(_StubModels):
    def __init__(self):
        super().__init__([(500, 600, 45), (1300, 600, 45)], full_w=1800)
        self.calls = []

    def detect_birds(self, bgr, *, conf=.25, imgsz=None):
        self.calls.append((imgsz, conf, bgr.shape[1]))
        if imgsz == 640:
            return []
        birds = super().detect_birds(bgr, conf=conf, imgsz=imgsz)
        return birds if imgsz == 2048 else birds[:1]


@pytest.mark.parametrize('mode, count', [('auto', 2), ('always', 2), ('off', 1)])
def test_small_birds_found_by_full_frame_recheck_also_get_flock_pass(monkeypatch, mode, count):
    _install_image(monkeypatch, _scene([(500, 600, 45, .4), (1300, 600, 45, .4)]))
    models = RecheckedFlock()
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus,
                                  params=AnalysisParams(flock_mode=mode)).analyze('flock.ARW', tracer=tracer)
    assert result.ok and result.bird_count == count, result.error
    if mode == 'auto':
        assert [b['found_by'] for b in result.birds] == [FOUND_FULL_FINE, FOUND_FULL_SMALL]
        assert any('新增 1 只' in value for step in tracer.trace.common for label, value in step.metrics)
    assert (2048 in [i for i, c, w in models.calls]) == (mode != 'off')


def test_larger_copy_and_lower_confidence_reach_model_and_trace(monkeypatch):
    _install_image(monkeypatch, _scene([(500, 600, 45, .4), (1300, 600, 45, .4)]))
    models = RecheckedFlock()
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus, params=AnalysisParams(
        detect_long_edge=2048, detect_imgsz=1024, detect_conf_percent=10, flock_mode='off')).analyze('x.ARW', tracer=tracer)
    assert result.ok
    assert models.calls == [(1024, .1, 1800)]
    desc = next(s.description for s in tracer.trace.common if s.key == 'detect')
    assert '2048' in desc and '1024' in desc and '0.10' in desc


def test_same_copy_size_still_gets_larger_network_flock_pass(monkeypatch):
    _install_image(monkeypatch, _scene([(500, 600, 45, .4), (1300, 600, 45, .4)]))
    models = RecheckedFlock()
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus, params=AnalysisParams(
        detect_long_edge=2048, detect_imgsz=1024)).analyze('x.ARW')
    assert result.bird_count == 2
    assert [i for i, c, w in models.calls] == [1024, 2048]


def test_post_measurement_exclusion_can_be_overridden(monkeypatch):
    _install_image(monkeypatch, _scene(SCENE))
    models = _Stub([.94, .27], [.99, .1])
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus,
                                  params=AnalysisParams(exclude_birds=False)).analyze('x.ARW')
    assert result.bird_count == 2 and 'keep-candidates' in result.version


def test_custom_duplicate_threshold_is_used_by_analyzer():
    import numpy as np
    first = np.ones((10, 10), dtype=np.uint8)
    second = first.copy()
    second[:, :2] = 0
    second = np.roll(second, 1, axis=1)
    birds = [BirdDetection(.9, (0, 0, 10, 10), first), BirdDetection(.8, (2, 0, 12, 10), second)]
    # 框交集 80%，默认去重；提高到 90% 后保留两者。
    assert len(BirdSharpnessAnalyzer()._dedupe(birds)) == 1
    assert len(BirdSharpnessAnalyzer(params=AnalysisParams(duplicate_box_percent=90))._dedupe(birds)) == 2
    a = np.zeros((10, 20), dtype=np.uint8); a[:, :10] = 1
    b = np.zeros_like(a); b[:, 3:13] = 1
    birds = [replace(birds[0], mask=a), replace(birds[0], mask=b)]
    assert len(dedupe_detections(birds)) == 1
    assert len(BirdSharpnessAnalyzer(params=AnalysisParams(duplicate_mask_percent=80))._dedupe(birds)) == 2


def test_detection_options_roundtrip_cli_normalization_and_version():
    parser = argparse.ArgumentParser()
    add_detection_arguments(parser)
    args = parser.parse_args(['--detect-imgsz', '1280', '--detect-long-edge', '2048',
                             '--detect-conf-percent', '10', '--duplicate-box-percent', '90',
                             '--duplicate-mask-percent', '80', '--flock-mode', 'off', '--keep-bird-candidates'])
    p = AnalysisParams.from_params({**detection_params_from_args(args), 'image_source': 'jpeg'})
    assert p == AnalysisParams.from_params(p.as_params())
    assert p.version_tags() == ['detedge2048', 'deti1280', 'detc10', 'dupbox90', 'dupmask80', 'flock-off', 'keep-candidates', 'jpeg']
    bad = AnalysisParams.from_params({'detect_imgsz': 1299, 'detect_conf_percent': -1,
                                    'duplicate_mask_percent': float('nan'), 'flock_mode': 'bad'})
    assert (bad.detect_imgsz, bad.detect_conf_percent, bad.duplicate_mask_percent, bad.flock_mode) == (1280, 5, 70, 'auto')


def test_empty_forced_flock_pass_restores_recheck_coordinates(monkeypatch):
    _install_image(monkeypatch, _scene([(500, 600, 45, .4), (1300, 600, 45, .4)]))
    class EmptyHighRes(RecheckedFlock):
        def detect_birds(self, bgr, *, conf=.25, imgsz=None):
            found = super().detect_birds(bgr, conf=conf, imgsz=imgsz)
            return [] if imgsz == 2048 else found
    models = EmptyHighRes()
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus,
                                  params=AnalysisParams(flock_mode='always')).analyze('x.ARW', tracer=tracer)
    assert result.ok and result.bird_count == 1
    x0, y0, x1, y1 = result.birds[0]['box']
    assert (x0+x1)/2 == pytest.approx(500, abs=1)
    assert (y0+y1)/2 == pytest.approx(600, abs=1)
    assert tracer._bird_boxes[0] == pytest.approx(result.birds[0]['box'], abs=1)
    assert [i for i, c, w in models.calls].count(2048) == 1
