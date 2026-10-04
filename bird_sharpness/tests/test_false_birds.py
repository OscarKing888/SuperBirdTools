"""False extra birds (leaves, trunks) and duplicate detections of one bird."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app_common import bird_sharpness_fields as bsf
from bird_sharpness.analyzer import BirdSharpnessAnalyzer, dedupe_detections
from bird_sharpness.models import BirdDetection
from bird_sharpness.trace import AnalysisTracer

from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene

# A confident but blurred bird on the left, a sharp "bird" on the right.
SCENE = [(450, 600, 260, 1.8), (1350, 600, 260, 0.3)]


class _Stub(_StubModels):
    def __init__(self, confs, eyes, **kw):
        super().__init__([b[:3] for b in SCENE], full_w=1800, **kw)
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
        vis[0] = self.eyes[self._k]  # birds are measured in detection order
        self._k += 1
        return coords, vis


def _analyze(monkeypatch, confs, eyes, **kw):
    _install_image(monkeypatch, _scene(SCENE))
    tracer = AnalysisTracer()
    result = BirdSharpnessAnalyzer(_Stub(confs, eyes, **kw), focus_provider=_no_focus).analyze("x.ARW", tracer=tracer)
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
    assert any("已排除" in label and "误识别" in value for label, value in overview.metrics)
    assert [s.bird for s in trace.steps_all() if s.bird is not None].count(1) == len(dropped.steps)
    assert all(label.startswith("鸟 #1") for label, _ in trace.final[0].metrics if label.startswith("鸟 #"))


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
