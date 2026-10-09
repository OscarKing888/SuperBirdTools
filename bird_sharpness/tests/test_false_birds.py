"""False extra birds (leaves, trunks), parts of a bird (wings) and duplicate detections of one bird."""
from __future__ import annotations

from dataclasses import replace

import pytest
import numpy as np
import cv2

from app_common import bird_sharpness_fields as bsf
from bird_sharpness.analyzer import BirdSharpnessAnalyzer, dedupe_detections, detection_mask_overlap
from bird_sharpness.models import BirdDetection
from bird_sharpness.trace import C_WEAK, AnalysisTracer, hex_color

from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene

# A confident but blurred bird on the left, a sharp "bird" on the right.
SCENE = [(450, 600, 260, 1.8), (1350, 600, 260, 0.3)]


class _Stub(_StubModels):
    def __init__(self, confs, eyes, scene=SCENE, **kw):
        super().__init__([b[:3] for b in scene], full_w=1800, **kw)
        self.confs, self.eyes, self._k = confs, list(eyes), 0

    def detect_birds(self, bgr, *, conf=0.25, imgsz=None):
        found = super().detect_birds(bgr, conf=conf, imgsz=imgsz)
        return [replace(d, confidence=c) for d, c in zip(found, self.confs) if c >= conf]

    def keypoints(self, rgb_crop):
        result = super().keypoints(rgb_crop)
        if result is None:
            return None
        coords, vis = result
        vis = vis.copy()
        # Birds are measured in detection order, each located on its crop and its mirror image.
        vis[0] = self.eyes[self._k // 2]
        self._k += 1
        return coords, vis


def _analyze(monkeypatch, confs, eyes, scene=SCENE, **kw):
    _install_image(monkeypatch, _scene(scene))
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(_Stub(confs, eyes, scene, **kw),
                                   focus_provider=_no_focus).analyze("x.ARW", tracer=tracer)
    return result, tracer.trace


def test_weak_eyeless_extra_next_to_a_confident_bird_is_dropped(monkeypatch) -> None:
    # DSC04392: a 0.27 leaf next to a 0.94 bird; the sharp leaf must not decide the photo.
    result, trace = _analyze(monkeypatch, [0.94, 0.27], [0.99, 0.1])
    assert result.bird_count == 1 and result.birds[0]["index"] == 0
    assert result.verdict != bsf.VERDICT_SHARP and result.bird_box[0] < 900
    # still visible in the trace, marked as excluded
    dropped = trace.birds[1]
    assert dropped.excluded and "已排除" in dropped.label and not dropped.best
    overview = next(s for s in trace.common if s.key == "birds")
    last = overview.bird_rows[-1]  # not a candidate for best: listed after the measured birds
    assert "已排除" in last.label and "误识别" in last.value and last.color == hex_color(C_WEAK)
    assert [s.bird for s in trace.steps_all() if s.bird is not None].count(1) == len(dropped.steps)
    assert [row.label for row in trace.final[0].bird_rows] == ["鸟 #1（最佳）"]


@pytest.mark.parametrize(("confs", "eyes", "stub"), [
    ([0.94, 0.27], [0.99, 0.9], {}),    # the extra shows an eye: a real bird
    ([0.45, 0.27], [0.99, 0.1], {}),    # no confident bird to compare with
    ([0.94, 0.45], [0.99, 0.1], {}),    # the extra itself is confident enough
    ([0.94, 0.27], [0.99, 0.1], {"keypoints": False}),  # no eye model: cannot judge
])
def test_extra_birds_are_kept_otherwise(monkeypatch, confs, eyes, stub) -> None:
    result, trace = _analyze(monkeypatch, confs, eyes, **stub)
    assert result.bird_count == 2 and not any(b.excluded for b in trace.birds)


def test_whole_bird_and_its_part_are_one_bird_in_either_order() -> None:
    whole, part = (100, 100, 400, 400), (120, 110, 300, 250)
    for first, second in ((part, whole), (whole, part)):
        kept = dedupe_detections([BirdDetection(0.7, first), BirdDetection(0.37, second)])
        assert [d.box for d in kept] == [first]
    apart = dedupe_detections([BirdDetection(0.7, whole), BirdDetection(0.4, (390, 100, 700, 400))])
    assert len(apart) == 2  # two birds touching are still two birds


def _nested_flock():
    # DSC00462：小鸟整个框位于大鸟的框内，但鸟体轮廓完全分离。
    large = np.zeros((120, 200), np.uint8)
    large[10:45, 30:185] = 1
    large[45:110, 165:175] = 1
    small = np.zeros_like(large)
    small[65:105, 70:100] = 1
    return (BirdDetection(.95, (30, 10, 185, 110), large),
            BirdDetection(.90, (70, 65, 100, 105), small))


@pytest.mark.parametrize('reverse', [False, True])
def test_contained_boxes_with_separate_silhouettes_are_two_birds(reverse):
    birds = list(_nested_flock())
    if reverse:
        birds.reverse()
    assert detection_mask_overlap(*birds) == 0
    kept = dedupe_detections(birds)
    assert len(kept) == 2 and all(a is b for a, b in zip(kept, birds))


@pytest.mark.parametrize('different_resolution', [False, True])
def test_flock_masks_align_across_passes_and_true_duplicate_is_still_removed(different_resolution):
    large, small = _nested_flock()
    duplicate = replace(small, confidence=.7, mask=small.mask.copy())
    if different_resolution:
        duplicate.mask = cv2.resize(duplicate.mask, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    before = [b.mask.copy() for b in (large, small, duplicate)]
    assert detection_mask_overlap(small, duplicate) == 1
    kept = dedupe_detections([large, small, duplicate])
    assert len(kept) == 2 and kept[0] is large and kept[1] is small
    assert all(np.array_equal(old, b.mask) for old, b in zip(before, (large, small, duplicate)))


@pytest.mark.parametrize('overlap, expected_count', [(6, 2), (7, 1)])
@pytest.mark.parametrize('reverse', [False, True])
def test_silhouette_containment_threshold_uses_smaller_mask(overlap, expected_count, reverse):
    # 小掩膜有 10 个像素：即使大掩膜远大于它，交集达 70% 仍是重复。
    large = np.zeros((20, 20), np.uint8)
    large[:10, :10] = 1
    small = np.zeros_like(large)
    small[9, 10-overlap:20-overlap] = 1
    birds = [BirdDetection(.9, (0, 0, 20, 20), large),
             BirdDetection(.8, (0, 0, 20, 20), small)]
    if reverse:
        birds.reverse()
    assert detection_mask_overlap(*birds) == pytest.approx(overlap / 10)
    kept = dedupe_detections(birds)
    assert len(kept) == expected_count and kept[0] is birds[0]


@pytest.mark.parametrize('mask', [None, np.zeros((120, 200), np.uint8), np.zeros((0, 0)), np.zeros((2, 3, 4))])
def test_unavailable_masks_keep_box_duplicate_fallback(mask):
    large, small = _nested_flock()
    small.mask = mask
    assert detection_mask_overlap(large, small) is None
    assert len(dedupe_detections([large, small])) == 1


def test_separate_nested_birds_reach_measurement_and_final_result(monkeypatch):
    from bird_sharpness.image_source import AnalysisImage
    from bird_sharpness.params import AnalysisParams
    large, small = _nested_flock()
    gray = np.full((120, 200), .2, np.float32)
    gray[(large.mask | small.mask).astype(bool)] = .8
    image = AnalysisImage(np.repeat((gray * 255).astype(np.uint8)[..., None], 3, axis=2), gray, False)
    models = _StubModels([], full_w=200, keypoints=False)
    monkeypatch.setattr(models, 'detect_birds', lambda *args, **kw: [large, small])
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(models, focus_provider=_no_focus,
                                  params=AnalysisParams()).analyze('flock.png', image_loader=lambda _: image, tracer=tracer)
    assert result.ok and result.bird_count == 2
    assert [b['box'] for b in result.birds] == [large.box, small.box]
    assert len(tracer.trace.birds) == 2


# A whole bird and a raised "wing" whose box is about half inside it (DSC05008: 65 %).
WING_SCENE = [(900, 600, 260, 1.8), (1150, 450, 120, 0.3)]


def test_eyeless_box_mostly_inside_a_bird_with_an_eye_is_its_wing(monkeypatch) -> None:
    # The wing is confident (0.55 > 0.43) and sharp; it must neither count nor decide.
    result, trace = _analyze(monkeypatch, [0.43, 0.55], [0.87, 0.07], WING_SCENE)
    assert result.bird_count == 1 and result.birds[0]["index"] == 0
    assert result.verdict != bsf.VERDICT_SHARP
    wing = trace.birds[1]
    assert wing.excluded and "并入 #1" in wing.label
    overview = next(s for s in trace.common if s.key == "birds")
    assert any("并入 #1" in r.label and "局部" in r.value for r in overview.bird_rows)


@pytest.mark.parametrize("eyes", [[0.87, 0.9], [0.2, 0.07]])
def test_overlapping_birds_stay_separate_unless_only_one_shows_an_eye(monkeypatch, eyes) -> None:
    result, trace = _analyze(monkeypatch, [0.43, 0.55], eyes, WING_SCENE)
    assert result.bird_count == 2 and not any(b.excluded for b in trace.birds)
