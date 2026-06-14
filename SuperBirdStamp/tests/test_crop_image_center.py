from __future__ import annotations

from pathlib import Path

from PIL import Image

from birdstamp.gui import editor_core
from birdstamp.video_export import VideoFrameJob, core as video_export_core, render_video_frame


def _settings(
    *,
    ratio: float = 16 / 9,
    center_mode: str = editor_core.CENTER_MODE_IMAGE,
    top: int = 0,
    bottom: int = 0,
    left: int = 0,
    right: int = 0,
    crop_box: tuple[float, float, float, float] | None = None,
) -> dict:
    return {
        "ratio": ratio,
        "center_mode": center_mode,
        "crop_padding_top": top,
        "crop_padding_bottom": bottom,
        "crop_padding_left": left,
        "crop_padding_right": right,
        "crop_box": list(crop_box) if crop_box is not None else None,
        "draw_banner": False,
        "draw_text": False,
        "stage_template_overlay_enabled": False,
    }


def _crop_pixels(
    crop_box: tuple[float, float, float, float],
    *,
    width: int,
    height: int,
    outer_pad: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> tuple[float, float, float, float]:
    pad_top, pad_bottom, pad_left, pad_right = outer_pad
    canvas_w = width + pad_left + pad_right
    canvas_h = height + pad_top + pad_bottom
    left = crop_box[0] * canvas_w - pad_left
    top = crop_box[1] * canvas_h - pad_top
    right = crop_box[2] * canvas_w - pad_left
    bottom = crop_box[3] * canvas_h - pad_top
    return (left, top, right, bottom)


def test_image_center_crop_is_horizontally_centered_on_3x2_without_padding() -> None:
    image = Image.new("RGB", (3000, 2000), "#ffffff")
    settings = _settings()
    crop_box, outer_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=settings,
    )
    assert crop_box is not None
    assert editor_core.crop_box_has_effect(crop_box)
    assert outer_pad == (0, 0, 0, 0)

    left, top, right, bottom = _crop_pixels(crop_box, width=3000, height=2000)
    assert abs(left - 0.0) < 1.0
    assert abs(right - 3000.0) < 1.0
    assert abs((top + bottom) * 0.5 - 1000.0) < 1.0


def test_image_center_pad128_all_produces_256_square_for_1x1() -> None:
    image = Image.new("RGB", (11232, 7488), "#ffffff")
    settings = _settings(ratio=1.0, top=128, bottom=128, left=128, right=128)
    crop_box, outer_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=settings,
    )
    assert crop_box is not None
    assert outer_pad == (0, 0, 0, 0)
    left, top, right, bottom = _crop_pixels(crop_box, width=11232, height=7488, outer_pad=outer_pad)
    assert abs((right - left) - 256.0) < 2.0
    assert abs((bottom - top) - 256.0) < 2.0
    assert abs((left + right) * 0.5 - 5616.0) < 2.0
    assert abs((top + bottom) * 0.5 - 3744.0) < 2.0

    job = VideoFrameJob(
        path=Path("sample.tif"),
        settings=settings,
        raw_metadata={},
        metadata_context={},
        source_image=image.copy(),
    )
    rendered = render_video_frame(job)
    try:
        assert rendered.size == (256, 256)
    finally:
        rendered.close()


def test_image_center_asymmetric_padding_expands_from_center() -> None:
    image = Image.new("RGB", (1000, 1000), "#ffffff")
    settings = _settings(ratio=1.0, top=32, bottom=256, left=64, right=128)
    crop_box, outer_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=settings,
    )
    assert crop_box is not None

    center_x = 500.0
    center_y = 500.0
    keep_left = center_x - 64
    keep_right = center_x + 128
    keep_top = center_y - 32
    keep_bottom = center_y + 256

    left, top, right, bottom = _crop_pixels(crop_box, width=1000, height=1000, outer_pad=outer_pad)
    assert left <= keep_left + 0.5
    assert right >= keep_right - 0.5
    assert top <= keep_top + 0.5
    assert bottom >= keep_bottom - 0.5
    assert abs((left + right) * 0.5 - center_x) < 1.0
    assert abs((top + bottom) * 0.5 - center_y) < 1.0
    assert abs((right - left) - 512.0) < 2.0
    assert abs((bottom - top) - 512.0) < 2.0


def test_image_center_differs_from_bird_center_when_bird_offset() -> None:
    image = Image.new("RGB", (1000, 800), "#ffffff")
    bird_box = (0.05, 0.1, 0.35, 0.9)
    image_settings = _settings(ratio=1.5)
    bird_settings = dict(image_settings)
    bird_settings["center_mode"] = editor_core.CENTER_MODE_BIRD

    image_crop, _ = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=image_settings,
    )
    bird_crop, _ = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=bird_settings,
        bird_box=bird_box,
    )
    assert image_crop is not None
    assert bird_crop is not None
    assert image_crop != bird_crop


def test_crop_box_override_ignored_for_image_center_mode() -> None:
    image = Image.new("RGB", (3000, 2000), "#ffffff")
    override = (0.1, 0.0, 0.7, 1.0)
    settings = _settings(crop_box=override)
    crop_box, _ = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=settings,
        crop_edit_active=False,
    )
    baseline, _ = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=_settings(),
    )
    assert crop_box == baseline


def test_compute_crop_plan_matches_gui_path() -> None:
    image = Image.new("RGB", (3000, 2000), "#ffffff")
    ratio = 16 / 9
    padding = 128
    settings = _settings(top=padding, bottom=padding, left=1, right=0)
    gui_crop, gui_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=settings,
    )
    template_crop, template_pad = editor_core.compute_crop_plan(
        image=image,
        raw_metadata={},
        ratio=ratio,
        center_mode=editor_core.CENTER_MODE_IMAGE,
        inner_top=padding,
        inner_bottom=padding,
        inner_left=1,
    )
    export_crop, export_pad = video_export_core._compute_crop_plan_for_image(
        path=Path("sample.jpg"),
        image=image,
        raw_metadata={},
        settings=settings,
        bird_box_cache={},
    )
    assert gui_crop == template_crop == export_crop
    assert gui_pad == template_pad == export_pad


def test_preview_output_size_matches_export_for_image_center_padding() -> None:
    image = Image.new("RGB", (11232, 7488), "#ffffff")
    settings = _settings(ratio=1.0, top=128, bottom=128, left=128, right=128)
    crop_box, outer_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings=settings,
    )
    preview_size = editor_core.compute_crop_output_size(
        11232,
        7488,
        crop_box,
        outer_pad,
    )
    job = VideoFrameJob(
        path=Path("sample.tif"),
        settings=settings,
        raw_metadata={},
        metadata_context={},
        source_image=image.copy(),
    )
    rendered = render_video_frame(job)
    try:
        assert preview_size == rendered.size
    finally:
        rendered.close()


def test_crop_plan_invariant_when_preview_image_is_downscaled() -> None:
    full_w, full_h = 11232, 7488
    full_image = Image.new("RGB", (full_w, full_h), "#ffffff")
    preview_image = full_image.resize((2048, 1366), Image.Resampling.LANCZOS)
    settings = _settings(ratio=1.0, top=128, bottom=128, left=128, right=128)
    source_size = (full_w, full_h)

    full_crop, full_pad = editor_core.compute_crop_plan_for_image(
        image=full_image,
        raw_metadata={},
        settings=settings,
        source_size=source_size,
    )
    preview_scaled_crop, preview_scaled_pad = editor_core.compute_crop_plan_for_image(
        image=preview_image,
        raw_metadata={},
        settings=settings,
        source_size=source_size,
    )
    preview_only_crop, _ = editor_core.compute_crop_plan_for_image(
        image=preview_image,
        raw_metadata={},
        settings=settings,
    )

    assert full_crop == preview_scaled_crop
    assert full_pad == preview_scaled_pad
    assert preview_only_crop != full_crop


def test_expand_keep_region_from_image_center_matches_asymmetric_padding() -> None:
    keep = editor_core.expand_keep_region_from_image_center(
        1000,
        1000,
        top=32,
        bottom=256,
        left=64,
        right=128,
    )
    assert keep is not None
    assert abs(keep[0] - (500 - 64)) < 1e-6
    assert abs(keep[1] - (500 - 32)) < 1e-6
    assert abs(keep[2] - (500 + 128)) < 1e-6
    assert abs(keep[3] - (500 + 256)) < 1e-6
