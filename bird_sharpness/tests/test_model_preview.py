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


# ── model chain: one window's results fed to the next ──
class _GreyAwareDetector(_Detector):
    """Records each crop; finds a bird only where the crop is not the mask fill."""

    def __init__(self):
        super().__init__()
        self.crops = []

    def detect_objects(self, bgr, *, conf, imgsz=None, birds_only=True):
        self.crops.append(bgr.copy())
        return super().detect_objects(bgr, conf=conf, imgsz=imgsz, birds_only=birds_only)


def test_expand_box_grows_by_margin_with_a_floor_inside_the_image() -> None:
    assert pv.expand_box((100, 100, 200, 300), 0.5, (1000, 1000)) == pytest.approx((50, 0, 250, 400))
    assert pv.expand_box((10, 10, 20, 20), 0.3, (1000, 1000)) == pytest.approx((0, 0, 47, 47))  # 64 px floor, clipped
    assert pv.expand_box((900, 900, 990, 990), 1.0, (1000, 1000))[2:] == (1000.0, 1000.0)


def test_mask_in_resamples_a_mask_onto_another_region() -> None:
    mask = np.zeros((10, 20), bool)
    mask[:, :10] = True  # left half of mask_box (100..300 × 0..100 image px)
    item = pv.PreviewItem("对象 1", 0.9, (100, 0, 200, 100), mask, (100, 0, 300, 100))
    m = pv.mask_in(item, (0, 0, 400, 100), (50, 200))  # 0.5 px per image px
    assert m.shape == (50, 200)
    assert m[:, 50:100].all() and not m[:, :50].any() and not m[:, 100:].any()
    assert pv.mask_in(pv.PreviewItem("x", None, (0, 0, 1, 1)), (0, 0, 10, 10), (5, 5)) is None


def test_detector_on_inputs_zooms_into_each_box_and_tags_its_source() -> None:
    image, det = _image(), _GreyAwareDetector()
    inputs = [pv.PreviewItem("对象 1", 0.8, (1000, 1000, 1200, 1100)), pv.PreviewItem("对象 2", 0.7, (3000, 2000, 3400, 2400))]
    out = pv.run_detector_on(image, pv.DetectorPreview(lift=False, imgsz=800), inputs, margin=0.5, models=det)
    assert [c.shape[:2] for c in det.crops] == [(200, 400), (800, 800)]  # box + 50% per side, full resolution
    assert [i.source for i in out.items] == [1, 2]
    assert out.items[0].box == pytest.approx((1000, 1000, 1100, 1050))  # crop (900, 950)-(1300, 1150), quarter → half
    assert out.items[0].mask_box == (900, 950, 1300, 1150)
    assert "2 个输入各自放大（框外扩 50%）" in out.input_desc and "网络输入 800 px" in out.input_desc
    with pytest.raises(ValueError):
        pv.run_detector_on(image, pv.DetectorPreview(), [], models=det)


def test_detector_on_inputs_can_see_only_the_pixels_inside_the_input_mask() -> None:
    image, det = _image(), _GreyAwareDetector()
    image.rgb8[:] = 200
    mask = np.zeros((100, 100), bool)
    mask[25:75, 25:75] = True  # the middle of the input box
    inputs = [pv.PreviewItem("对象 1", 0.8, (1000, 1000, 1100, 1100), mask, (1000, 1000, 1100, 1100)),
              pv.PreviewItem("检测框", 0.5, (2000, 2000, 2100, 2100))]  # no mask: fed whole
    out = pv.run_detector_on(image, pv.DetectorPreview(lift=False), inputs, margin=0.0, mask_only=True, models=det)
    first, second = det.crops
    assert (first[:25] == pv.MASK_FILL).all() and (first[25:75, 25:75] == 200).all()
    assert (second == 200).all()
    assert "1 个只留轮廓内像素" in out.input_desc


def test_sam_on_inputs_segments_each_box_in_its_own_crop() -> None:
    image, ref = _image(), _Refiner()
    inputs = [pv.PreviewItem("bird", 0.6, (500, 500, 700, 650)), pv.PreviewItem("bird", 0.3, (3000, 2000, 3300, 2300))]
    out = pv.run_sam_on(image, "sam2.1_t.pt", inputs, refiner=ref)
    assert len(ref.calls) == 2 and all(len(c[1]) == 1 and c[2] is None for c in ref.calls)
    assert [(i.label, i.source) for i in out.items] == [("对象 1", 1), ("对象 2", 2)]
    assert out.input_desc.startswith("2 个输入框")
    _img, rows = pv.render(np.zeros((400, 600, 3), np.uint8), 0.1, out.items)
    assert "来自输入 #2" in rows[1].value


def test_items_from_boxes_turns_trace_birds_into_chain_inputs() -> None:
    items = pv.items_from_boxes([(1, 2, 3, 4), np.array([5, 6, 7, 8])])
    assert [i.box for i in items] == [(1.0, 2.0, 3.0, 4.0), (5.0, 6.0, 7.0, 8.0)]
    assert items[0].confidence is None and items[1].label.endswith("2")



# ── 抠出上一步结果的像素: the input results' pixels as a new image ──
def _masked_item(box, mask_box, mask):
    return pv.PreviewItem("对象", 0.9, box, mask, mask_box)


def test_cutout_keeps_only_the_input_pixels_cropped_to_them() -> None:
    image = _image(1000, 1000)
    image.rgb8[:] = 200
    mask = np.zeros((10, 10), bool)
    mask[:5] = True  # top half of 100..200 × 100..200
    cut = pv.cutout(image, [_masked_item((100, 100, 200, 200), (100, 100, 200, 200), mask),
                            pv.PreviewItem("bird", 0.5, (300, 150, 350, 250))])  # no mask: the box
    assert cut.region == (100, 100, 350, 250) and cut.rgb.shape == (150, 250, 3) and cut.count == 2
    assert (cut.rgb[:50, :100] == 200).all() and (cut.rgb[50:100, :100] == pv.MASK_FILL).all()
    assert (cut.rgb[50:150, 200:250] == 200).all() and (cut.rgb[:, 120:190] == pv.MASK_FILL).all()
    assert cut.mask[:50, :100].all() and not cut.mask[60:, :100].any()
    with pytest.raises(ValueError):
        pv.cutout(image, [])


def test_detector_runs_once_on_the_cutout_and_maps_back() -> None:
    image, det = _image(1000, 1000), _GreyAwareDetector()
    inputs = [pv.PreviewItem("bird", 0.5, (100, 200, 500, 400)), pv.PreviewItem("bird", 0.4, (600, 300, 700, 600))]
    out = pv.run_detector_cutout(image, pv.DetectorPreview(lift=False), inputs, models=det)
    assert len(det.crops) == 1 and det.crops[0].shape[:2] == (400, 600)  # one new image: 100..700 × 200..600
    assert out.items[0].box == pytest.approx((250, 300, 400, 400))  # quarter → half of the new image, photo px
    assert out.items[0].mask_box == (100, 200, 700, 600) and out.cutout.region == (100, 200, 700, 600)
    assert out.input_desc.startswith("抠出 2 个输入的像素为新图（600 × 400 px）")
    view = pv.run_detector_cutout(image, pv.DetectorPreview(region=(0, 0, 400, 1000), lift=False), inputs, models=det)
    assert det.crops[1].shape[:2] == (400, 300) and view.items[0].box[0] == pytest.approx(175)  # view ∩ new image
    with pytest.raises(ValueError, match="不在抠出的图内"):
        pv.run_detector_cutout(image, pv.DetectorPreview(region=(800, 0, 1000, 100)), inputs, models=det)


def test_sam_on_the_cutout_uses_drawn_prompts_or_the_whole_new_image() -> None:
    image, ref = _image(1000, 1000), _Refiner()
    inputs = [pv.PreviewItem("bird", 0.5, (100, 200, 500, 400))]
    out = pv.run_sam_cutout(image, pv.SamPreview("sam2.1_t.pt"), inputs, refiner=ref)
    shape, boxes, points, _labels = ref.calls[0]
    assert len(boxes) == 1 and points is None and out.cutout.region == (100, 200, 500, 400)
    assert all(0 <= v <= 400 for v in boxes[0])  # the whole new image, in its crop's px
    assert out.items[0].mask_box[0] >= 100 and out.items[0].box[0] >= 100  # photo px
    pv.run_sam_cutout(image, pv.SamPreview("sam2.1_t.pt", ((150, 250, 300, 350),), ((200, 300, True),)), inputs,
                      refiner=ref)
    _shape, boxes, points, labels = ref.calls[1]
    assert len(boxes) == 1 and len(points) == 1 and labels == [1]


def test_cutout_display_shows_only_the_cut_pixels() -> None:
    display = np.full((100, 100, 3), 10, np.uint8)
    mask = np.zeros((40, 40), bool)
    mask[:20] = True
    cut = pv.Cutout(np.zeros((40, 40, 3), np.uint8), mask, (200, 200, 600, 600), 1)
    img = pv.cutout_display(display, 0.1, cut)  # region 20..60 at display scale
    assert (img[20:40, 20:60] == 10).all() and (img[40:60, 20:60] == pv.MASK_FILL).all()
    assert (img[:20] == pv.MASK_FILL).all()
