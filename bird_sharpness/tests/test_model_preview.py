"""Model preview backend: one model's raw output, with its own parameters."""
from __future__ import annotations

import numpy as np
import pytest

from bird_sharpness import preview as pv
from bird_sharpness.image_source import AnalysisImage, DecodedImageCache
from bird_sharpness.models import BirdSharpnessModels
from bird_sharpness.refine import SamRefiner


def _image(h=3000, w=4500) -> AnalysisImage:
    rgb = np.full((h, w, 3), 120, np.uint8)
    return AnalysisImage(rgb, rgb[..., 1].astype(np.float32) / 255.0, True)


class _Detector:
    detector_name, device = "yolo11x-seg.pt", "mps"

    def __init__(self):
        self.calls = []

    def detect_objects(self, bgr, *, conf, imgsz=None, birds_only=True):
        self.calls.append((bgr.shape, conf, imgsz, birds_only))
        h, w = bgr.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        mask[h // 4:h // 2, w // 4:w // 2] = 1
        out = [("bird", 0.66, (w / 4, h / 4, w / 2, h / 2), mask)]
        if not birds_only:
            out.append(("potted plant", 0.07, (0.0, 0.0, 10.0, 10.0), None))
        return out


def test_detector_preview_maps_region_crops_back_to_image_pixels() -> None:
    image, det = _image(), _Detector()
    full = pv.run_detector(image, pv.DetectorPreview("yolo11x-seg.pt", lift=False), models=det)
    assert det.calls[0][0][:2] == (1365, 2048)  # long edge capped at PREVIEW_INPUT_MAX
    assert full.items[0].box == pytest.approx((1125, 750, 2250, 1500), abs=3)
    assert full.model == "yolo11x-seg.pt" and full.input_desc.startswith("全图")
    region = pv.run_detector(image, pv.DetectorPreview(region=(1000, 500, 2000, 1500), imgsz=1024, min_conf=0.2,
                                                       birds_only=False, lift=False), models=det)
    assert det.calls[1][1:] == (0.2, 1024, False)
    assert region.items[0].box == pytest.approx((1250, 750, 1500, 1000))  # within the region
    assert [i.label for i in region.items] == ["bird", "potted plant"] and region.items[0].mask_box == (1000, 500, 2000, 1500)
    with pytest.raises(ValueError):
        pv.run_detector(image, pv.DetectorPreview(region=(10, 10, 12, 12)), models=det)


class _Refiner:
    device = "mps"

    def __init__(self):
        self.calls = []

    def segment(self, rgb, *, boxes=None, points=None, labels=None):
        self.calls.append((rgb.shape, boxes, points, labels))
        mask = np.zeros(rgb.shape[:2], bool)
        mask[10:60, 20:90] = True
        return [(mask, 0.84)] * (len(boxes) if boxes and not points else 1)


def test_sam_preview_crops_around_prompts_and_reports_each_object() -> None:
    image, ref = _image(), _Refiner()
    one = pv.run_sam(image, pv.SamPreview(boxes=((1000, 1000, 1400, 1300),)), refiner=ref)
    shape, boxes, points, labels = ref.calls[0]
    region = pv.sam_region(image, pv.SamPreview(boxes=((1000, 1000, 1400, 1300),)))
    assert region == (800, 850, 1600, 1450) and shape[:2] == (600, 800)  # prompts + 50 % margin
    assert boxes == [(200, 150, 600, 450)] and points is None and len(one.items) == 1
    assert one.items[0].box == pytest.approx((820, 860, 890, 910)) and one.items[0].confidence == 0.84
    pv.run_sam(image, pv.SamPreview(boxes=((1000, 1000, 1400, 1300),), points=((1100, 1100, True), (1300, 1200, False))),
               refiner=ref)
    assert ref.calls[1][3] == [1, 0] and len(ref.calls[1][2]) == 2
    two = pv.run_sam(image, pv.SamPreview(boxes=((100, 100, 300, 300), (3000, 2000, 3200, 2300))), refiner=ref)
    assert len(two.items) == 2
    with pytest.raises(ValueError, match="画框"):
        pv.run_sam(image, pv.SamPreview(), refiner=ref)
    with pytest.raises(ValueError, match="一个框"):
        pv.run_sam(image, pv.SamPreview(boxes=((0, 0, 9, 9), (20, 20, 40, 40)), points=((5, 5, True),)), refiner=ref)


def test_render_tints_masks_lists_rows_and_draws_ascii_labels() -> None:
    image = _image()
    display, scale = pv.display_image(image)
    result = pv.run_sam(image, pv.SamPreview(boxes=((1000, 1000, 1400, 1300),)), refiner=_Refiner())
    img, rows = pv.render(display, scale, result.items)
    assert img.shape == display.shape and not np.array_equal(img, display)
    assert rows[0].label == "#1 对象 1" and "置信度 0.84" in rows[0].value and "轮廓" in rows[0].value
    assert rows[0].box == pytest.approx(tuple(v * scale for v in result.items[0].box))
    assert all(isinstance(v, float) for v in rows[0].box)


def test_sam_segment_builds_the_prompt_shapes_sam2_expects(monkeypatch) -> None:
    calls = []

    class _Model:
        def predict(self, bgr, **kw):
            calls.append(kw)
            raise RuntimeError("stop")  # only the prompt format matters here

    ref = SamRefiner("sam2.1_t.pt")
    ref._model, ref.device = _Model(), "cpu"
    rgb = np.zeros((50, 60, 3), np.uint8)
    for kw in (dict(boxes=[(1, 2, 3, 4), (5, 6, 7, 8)]), dict(boxes=[(1, 2, 3, 4)], points=[(5, 6), (7, 8)], labels=[1, 0]),
               dict(points=[(5, 6)], labels=[1])):
        with pytest.raises(RuntimeError):
            ref.segment(rgb, **kw)
    assert calls[0] == {"device": "cpu", "verbose": False, "bboxes": [[1, 2, 3, 4], [5, 6, 7, 8]]}
    assert calls[1]["bboxes"] == [[1, 2, 3, 4]] and calls[1]["points"] == [[[5, 6], [7, 8]]]
    assert calls[1]["labels"] == [[1, 0]] and "bboxes" not in calls[2]
    assert ref.segment(rgb) == []


class _T:
    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


def test_detect_objects_returns_every_class_unless_birds_only() -> None:
    seen = []

    class _Seg:
        names = {14: "bird", 58: "potted plant"}

        def predict(self, bgr, **kw):
            seen.append(kw.get("classes"))
            boxes = type("B", (), {"conf": _T([0.07, 0.6]), "xyxy": _T([[0, 0, 5, 5], [1, 1, 9, 9]]),
                                   "cls": _T([58, 14]), "__len__": lambda self: 2})()
            return [type("R", (), {"boxes": boxes, "masks": None})()]

    models = BirdSharpnessModels()
    models._seg, models._masks = _Seg(), False
    out = models.detect_objects(np.zeros((10, 10, 3), np.uint8), conf=0.05, birds_only=False)
    assert [(o[0], o[1]) for o in out] == [("bird", 0.6), ("potted plant", 0.07)] and seen == [None]
    models.detect_objects(np.zeros((10, 10, 3), np.uint8), conf=0.05)
    assert seen[-1] == [14]
    assert [d.confidence for d in models.detect_birds(np.zeros((10, 10, 3), np.uint8))] == [0.6, 0.07]


def test_cache_latest_follows_use_and_source(tmp_path) -> None:
    cache = DecodedImageCache()
    raw, jpeg = _image(10, 10), _image(12, 12)
    cache.get_or_load(DecodedImageCache.key(str(tmp_path / "a.ARW"), "raw"), lambda: raw)
    cache.get_or_load(DecodedImageCache.key(str(tmp_path / "a.ARW"), "jpeg"), lambda: jpeg)
    assert cache.latest() is jpeg and cache.latest("raw") is raw and cache.latest("denoised") is None
