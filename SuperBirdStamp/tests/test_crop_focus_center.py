from __future__ import annotations

from birdstamp.gui import editor_core


def _settings(
    *,
    ratio: float = 1.0,
    top: int = 0,
    bottom: int = 0,
    left: int = 0,
    right: int = 0,
) -> dict:
    return {
        "ratio": ratio,
        "center_mode": editor_core.CENTER_MODE_FOCUS,
        "crop_padding_top": top,
        "crop_padding_bottom": bottom,
        "crop_padding_left": left,
        "crop_padding_right": right,
        "crop_box": None,
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


def _focus_metadata_from_box(box: tuple[float, float, float, float]) -> dict:
    left, top, right, bottom = box
    center_x = (left + right) * 0.5
    center_y = (top + bottom) * 0.5
    return {
        "Composite:FocusX": center_x,
        "Composite:FocusY": center_y,
        "FocusFrameSize": f"{int((right - left) * 1000)} {int((bottom - top) * 1000)}",
        "ImageWidth": 1000,
        "ImageHeight": 1000,
    }


def test_focus_center_uses_box_center_not_box_extent_for_padding() -> None:
    from PIL import Image

    image = Image.new("RGB", (1000, 1000), "#ffffff")
    huge_focus_box = (0.05, 0.05, 0.95, 0.95)
    offset_focus_box = (0.0, 0.0, 0.8, 0.4)
    settings = _settings(top=128, bottom=128, left=128, right=128)

    huge_crop, huge_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata=_focus_metadata_from_box(huge_focus_box),
        settings=settings,
    )
    offset_crop, offset_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata=_focus_metadata_from_box(offset_focus_box),
        settings=settings,
    )

    assert huge_crop is not None
    assert offset_crop is not None

    huge_px = _crop_pixels(huge_crop, width=1000, height=1000, outer_pad=huge_pad)
    offset_px = _crop_pixels(offset_crop, width=1000, height=1000, outer_pad=offset_pad)

    assert abs((huge_px[2] - huge_px[0]) - 256.0) < 2.0
    assert abs((huge_px[3] - huge_px[1]) - 256.0) < 2.0
    assert abs((huge_px[0] + huge_px[2]) * 0.5 - 500.0) < 2.0
    assert abs((huge_px[1] + huge_px[3]) * 0.5 - 500.0) < 2.0

    assert abs((offset_px[0] + offset_px[2]) * 0.5 - 400.0) < 2.0
    assert abs((offset_px[1] + offset_px[3]) * 0.5 - 200.0) < 2.0
    assert abs((offset_px[2] - offset_px[0]) - 256.0) < 2.0
    assert abs((offset_px[3] - offset_px[1]) - 256.0) < 2.0


def test_focus_center_asymmetric_padding_matches_image_center_semantics() -> None:
    from PIL import Image

    image = Image.new("RGB", (1000, 1000), "#ffffff")
    focus_box = (0.19, 0.19, 0.41, 0.41)
    anchor_x = 300.0
    anchor_y = 300.0
    settings = _settings(ratio=1.0, top=32, bottom=256, left=64, right=128)

    focus_crop, focus_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata=_focus_metadata_from_box(focus_box),
        settings=settings,
    )
    image_crop, image_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata={},
        settings={
            **settings,
            "center_mode": editor_core.CENTER_MODE_IMAGE,
        },
    )

    assert focus_crop is not None
    assert image_crop is not None

    keep_left = anchor_x - 64
    keep_right = anchor_x + 128
    keep_top = anchor_y - 32
    keep_bottom = anchor_y + 256

    left, top, right, bottom = _crop_pixels(
        focus_crop,
        width=1000,
        height=1000,
        outer_pad=focus_pad,
    )
    assert left <= keep_left + 0.5
    assert right >= keep_right - 0.5
    assert top <= keep_top + 0.5
    assert bottom >= keep_bottom - 0.5
    assert abs((left + right) * 0.5 - anchor_x) < 1.0
    assert abs((top + bottom) * 0.5 - anchor_y) < 1.0
    assert abs((right - left) - 512.0) < 2.0
    assert abs((bottom - top) - 512.0) < 2.0
    assert focus_crop != image_crop


def test_focus_center_crop_plan_matches_export_path() -> None:
    from pathlib import Path

    from PIL import Image

    from birdstamp.video_export import core as video_export_core

    image = Image.new("RGB", (3000, 2000), "#ffffff")
    focus_box = (0.35, 0.25, 0.55, 0.45)
    settings = {
        "ratio": 16 / 9,
        "center_mode": editor_core.CENTER_MODE_FOCUS,
        "crop_padding_top": 128,
        "crop_padding_bottom": 128,
        "crop_padding_left": 64,
        "crop_padding_right": 128,
        "crop_box": None,
    }
    gui_crop, gui_pad = editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata=_focus_metadata_from_box(focus_box),
        settings=settings,
    )
    export_crop, export_pad = video_export_core._compute_crop_plan_for_image(
        path=Path("sample.jpg"),
        image=image,
        raw_metadata=_focus_metadata_from_box(focus_box),
        settings=settings,
        bird_box_cache={},
    )
    assert gui_crop == export_crop
    assert gui_pad == export_pad
