"""切换 RAW/降噪像素后，裁切仍保存相机画幅坐标，预览再映射到实际像素。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY
from birdstamp.gui import editor_core
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_crop_calculator import _BirdStampCropMixin
from birdstamp.gui.preview_source_geometry import camera_to_preview_box, preview_to_camera_box


FULL_SIZE = (1000, 800)
CAMERA_CROP = (.1, .125, .9, .875)
CAMERA_SIZE = (800, 600)
PATH = Path("鸟.ARW")


class _CropHarness(_BirdStampCropMixin):
    def __init__(self, full_size=FULL_SIZE, bird_box=None):
        self.current_path = PATH
        self.current_source_full_size = full_size
        self._bird_box_cache = {"source": bird_box}

    def _source_signature(self, path):
        return "source"

    def _crop_edit_mode_active(self):
        return False

    def _schedule_async_bird_detect(self, *_args):
        pytest.fail("cached preview crop must not start detection")


def _settings(mode="custom", box=None):
    return {
        "ratio": 4 / 3, "center_mode": mode, "crop_box": box,
        "crop_padding_top": 80, "crop_padding_bottom": 120,
        "crop_padding_left": 160, "crop_padding_right": 64,
    }


@pytest.mark.parametrize("camera_box", [(.2, .1, .8, .9), (-.3, -.25, 1.4, 1.2)])
def test_extended_manual_camera_box_maps_and_inverses_without_clipping(camera_box):
    actual_box = camera_to_preview_box(camera_box, CAMERA_CROP)
    assert preview_to_camera_box(actual_box, CAMERA_CROP, clip=False) == pytest.approx(camera_box)


@pytest.mark.parametrize("pixel_mode", ["raw", "denoised"])
@pytest.mark.parametrize("camera_box", [(.2, .1, .8, .9), (-.3, -.25, 1.4, 1.2)])
def test_manual_crop_roundtrip_saves_camera_box_including_outer_padding(pixel_mode, camera_box):
    image = Image.new("RGB", (500, 400))
    image.info[RAW_FOCUS_CROP_KEY] = CAMERA_CROP
    image.info["birdstamp_preview_source_mode"] = pixel_mode
    harness = _CropHarness()
    settings = _settings(box=camera_box)
    plan, pads = harness._compute_crop_plan_for_image(
        path=PATH, image=image, raw_metadata={}, settings=settings, preview_only=True,
    )
    actual_box = editor_core.crop_box_to_source(plan, FULL_SIZE, pads)
    assert actual_box == pytest.approx(camera_to_preview_box(camera_box, CAMERA_CROP))
    display_plan, display_pad = editor_core.rescale_crop_plan(plan, pads, FULL_SIZE, image.size)
    window = SimpleNamespace(
        current_path=PATH, current_source_image=image,
        _current_preview_outer_pad=lambda: display_pad,
        _crop_display_source_size=lambda: FULL_SIZE,
        _set_custom_center_from_box=Mock(), _update_crop_padding_from_box=Mock(),
        _set_photo_crop_box_for_path=Mock(), _crop_drag_active=True,
        _on_crop_settings_changed=Mock(), preview_label=SimpleNamespace(canvas=SimpleNamespace()),
    )
    try:
        BirdStampEditorWindow._on_canvas_crop_box_changed(window, display_plan)
        assert window._crop_box_override == pytest.approx(camera_box)
        saved_path, saved_box = window._set_photo_crop_box_for_path.call_args.args
        assert saved_path == PATH and saved_box == pytest.approx(camera_box)
        padding_box, padding_size = window._update_crop_padding_from_box.call_args.args
        assert padding_box == pytest.approx(camera_box) and padding_size == CAMERA_SIZE
        window._on_crop_settings_changed.assert_not_called()
        window._crop_drag_active = False
        BirdStampEditorWindow._on_canvas_crop_box_changed(window, display_plan)
        window._on_crop_settings_changed.assert_called_once()
        assert settings["crop_box"] == camera_box
    finally:
        image.close()


@pytest.mark.parametrize("mode", ["focus", "bird", "image", "custom"])
@pytest.mark.parametrize("image_size", [FULL_SIZE, (500, 400)])
def test_automatic_crop_uses_camera_anchor_and_padding_then_maps_to_actual_pixels(mode, image_size):
    metadata = {"Composite:FocusX": .85, "Composite:FocusY": .12,
                "ImageWidth": CAMERA_SIZE[0], "ImageHeight": CAMERA_SIZE[1]}
    bird_box = (.1, .3, .3, .5)
    settings = {**_settings(mode), "custom_center_x": .7, "custom_center_y": .3}
    with Image.new("RGB", CAMERA_SIZE) as camera, Image.new("RGB", image_size) as actual:
        actual.info[RAW_FOCUS_CROP_KEY] = CAMERA_CROP
        expected_plan, expected_pad = editor_core.compute_crop_plan_for_image(
            image=camera, raw_metadata=metadata, settings=settings,
            bird_box=bird_box, source_size=CAMERA_SIZE,
        )
        expected_camera_box = editor_core.crop_box_to_source(expected_plan, CAMERA_SIZE, expected_pad)
        plan, pads = _CropHarness(bird_box=bird_box)._compute_crop_plan_for_image(
            path=PATH, image=actual, raw_metadata=metadata, settings=settings, preview_only=True,
        )
        actual_box = editor_core.crop_box_to_source(plan, FULL_SIZE, pads)
        assert actual_box == pytest.approx(camera_to_preview_box(expected_camera_box, CAMERA_CROP))


@pytest.mark.parametrize("mode", ["focus", "bird", "custom", "image"])
def test_default_camera_preview_retains_original_crop_behavior(mode):
    metadata = {"Composite:FocusX": .7, "Composite:FocusY": .3,
                "ImageWidth": CAMERA_SIZE[0], "ImageHeight": CAMERA_SIZE[1]}
    bird_box = (.1, .3, .3, .5)
    settings = _settings(mode, box=(-.1, .2, .7, 1.1) if mode == "custom" else None)
    with Image.new("RGB", (400, 300)) as image:
        expected = editor_core.compute_crop_plan_for_image(
            image=image, raw_metadata=metadata, settings=settings,
            bird_box=bird_box, source_size=CAMERA_SIZE,
        )
        actual = _CropHarness(CAMERA_SIZE, bird_box)._compute_crop_plan_for_image(
            path=PATH, image=image, raw_metadata=metadata, settings=settings, preview_only=True,
        )
        assert actual == expected


def test_no_crop_preserves_entire_actual_raw_frame():
    with Image.new("RGB", (500, 400)) as image:
        image.info[RAW_FOCUS_CROP_KEY] = CAMERA_CROP
        plan = _CropHarness()._compute_crop_plan_for_image(
            path=PATH, image=image, raw_metadata={}, settings={"ratio": "no_crop"}, preview_only=True,
        )
        assert plan == (None, (0, 0, 0, 0))
