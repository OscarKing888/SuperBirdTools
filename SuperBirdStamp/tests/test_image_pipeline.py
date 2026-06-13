import math
from pathlib import Path

from PIL import Image

from birdstamp.image_pipeline import ImageProcContext, ImageProcExportStage, ImageProcPipeline, ImageProcStage
from birdstamp.gui import editor_core
from birdstamp.gui.editor_core import transform_source_box_after_crop_padding
from birdstamp.video_export import (
    PIPELINE_STAGE_ORDER_KEY,
    VideoFrameJob,
    build_default_image_proc_pipeline,
    build_image_proc_export_stages,
    normalize_pipeline_stage_order,
    render_video_frame,
)


def _raw_embedded_focus_metadata(source_path: Path) -> dict[str, object]:
    return {
        "SourceFile": str(source_path),
        "Make": "SONY",
        "Model": "ILCE-1M2",
        "ExifImageWidth": 100,
        "ExifImageHeight": 100,
        "MakerNote Tag 0x2027": "100 100 80 80 20 20",
    }


class _AppendStage(ImageProcStage):
    stage_id = "append"
    label = "Append"
    enabled_option_key = "append_enabled"

    def __init__(self, value: str) -> None:
        self._value = value

    def process(self, context: ImageProcContext) -> ImageProcContext:
        values = context.precomputed.setdefault("values", [])
        values.append(self._value)
        return context


def test_image_proc_pipeline_runs_enabled_stages_in_order() -> None:
    context = ImageProcContext(
        image=Image.new("RGB", (8, 8), "#ffffff"),
        settings={"append_enabled": True},
    )

    pipeline = ImageProcPipeline((_AppendStage("a"), _AppendStage("b")))
    processed = pipeline.process(context)

    assert processed.precomputed["values"] == ["a", "b"]


def test_image_proc_pipeline_skips_disabled_stage() -> None:
    context = ImageProcContext(
        image=Image.new("RGB", (8, 8), "#ffffff"),
        settings={"append_enabled": False},
    )

    pipeline = ImageProcPipeline((_AppendStage("a"),))
    processed = pipeline.process(context)

    assert "values" not in processed.precomputed


def test_default_pipeline_exposes_ui_stage_options() -> None:
    descriptors = build_default_image_proc_pipeline().ui_descriptors()
    by_id = {descriptor.stage_id: descriptor for descriptor in descriptors}

    assert {"template_crop", "resize_limit", "template_overlay", "focus_overlay"} <= set(by_id)
    assert by_id["template_crop"].enabled_option is None
    assert by_id["resize_limit"].enabled_option is not None
    assert any(option.key == "template_name" for option in by_id["template_crop"].parameter_options)
    assert any(option.key == "ratio" for option in by_id["template_crop"].parameter_options)
    assert any(option.key == "draw_banner" for option in by_id["template_overlay"].parameter_options)
    assert not any(option.key == "template_name" for option in by_id["template_overlay"].parameter_options)


def test_default_pipeline_can_be_reordered() -> None:
    order = normalize_pipeline_stage_order(["template_crop", "template_overlay", "focus_overlay", "resize_limit"])
    pipeline = build_default_image_proc_pipeline(order)

    assert [stage.stage_id for stage in pipeline.stages] == list(order)


def test_template_crop_stage_is_always_first() -> None:
    order = normalize_pipeline_stage_order(["resize_limit", "template_overlay", "template_crop", "focus_overlay"])

    assert order[0] == "template_crop"
    assert order[1:] == ("resize_limit", "template_overlay", "focus_overlay")


def test_export_stages_are_terminal_stage_descriptors() -> None:
    stages = build_image_proc_export_stages()

    assert [stage.stage_id for stage in stages] == ["export_png", "export_gif", "export_video"]
    assert all(isinstance(stage, ImageProcExportStage) for stage in stages)
    assert all(stage.ui_descriptor().enabled_option is None for stage in stages)


def test_render_video_frame_cannot_disable_template_crop_stage(tmp_path: Path) -> None:
    source_path = tmp_path / "source.jpg"
    source_image = Image.new("RGB", (100, 50), "#ff0000")
    for x in range(50, 100):
        for y in range(50):
            source_image.putpixel((x, y), (0, 0, 255))
    source_image.save(source_path)

    job = VideoFrameJob(
        path=source_path,
        settings={
            "draw_banner": False,
            "draw_text": False,
            "draw_focus": False,
            "stage_template_crop_enabled": False,
            "crop_box": [0.0, 0.0, 0.5, 1.0],
        },
        raw_metadata={"SourceFile": str(source_path)},
        metadata_context={},
        source_image=source_image,
    )

    rendered = render_video_frame(job)
    try:
        assert rendered.size == (50, 50)
        assert rendered.getpixel((10, 25)) == (255, 0, 0)
    finally:
        rendered.close()
        source_image.close()


def test_focus_box_transform_uses_padded_crop_coordinates() -> None:
    focus_box = transform_source_box_after_crop_padding(
        (0.4, 0.4, 0.6, 0.6),
        crop_box=(0.0, 0.0, 100 / 150, 1.0),
        source_width=100,
        source_height=100,
        pt=0,
        pb=0,
        pl=50,
        pr=0,
    )

    assert focus_box == (0.9, 0.4, 1.0, 0.6)


def test_editor_core_focus_box_prefers_raw_embedded_metadata(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source.ARW"
    source_path.write_bytes(b"raw")
    stale_metadata = {
        "SourceFile": str(source_path),
        "Make": "SONY",
        "Model": "ILCE-1M2",
        "ExifImageWidth": 100,
        "ExifImageHeight": 100,
        "FocusX": 10,
        "FocusY": 10,
        "FocusW": 20,
        "FocusH": 20,
    }
    monkeypatch.setattr(
        editor_core,
        "read_raw_embedded_focus_metadata",
        lambda _path: _raw_embedded_focus_metadata(source_path),
    )
    editor_core._read_raw_embedded_focus_metadata_cached.cache_clear()

    try:
        focus_box = editor_core.extract_focus_box_for_display(
            stale_metadata,
            100,
            100,
            source_path=source_path,
        )

        assert focus_box is not None
        expected = (0.7, 0.7, 0.9, 0.9)
        assert all(math.isclose(actual, target, abs_tol=1e-9) for actual, target in zip(focus_box, expected))
    finally:
        editor_core._read_raw_embedded_focus_metadata_cached.cache_clear()


def test_focus_overlay_after_resize_keeps_padded_crop_position(tmp_path: Path) -> None:
    source_path = tmp_path / "source.jpg"
    source_image = Image.new("RGB", (100, 100), "#ffffff")
    source_image.save(source_path)

    job = VideoFrameJob(
        path=source_path,
        settings={
            "draw_banner": False,
            "draw_text": False,
            "draw_focus": True,
            "max_long_edge": 50,
            "crop_box": [-0.5, 0.0, 0.5, 1.0],
            PIPELINE_STAGE_ORDER_KEY: ["template_crop", "resize_limit", "focus_overlay"],
        },
        raw_metadata={"FocusX": 50, "FocusY": 50, "FocusW": 20, "FocusH": 20},
        metadata_context={},
        source_image=source_image,
    )

    rendered = render_video_frame(job)
    try:
        assert rendered.size == (50, 50)
        assert rendered.getpixel((46, 21)) == (46, 255, 85)
        assert rendered.getpixel((40, 21)) == (255, 255, 255)
    finally:
        rendered.close()
        source_image.close()


def test_render_video_frame_focus_overlay_prefers_raw_embedded_metadata(monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "source.ARW"
    source_path.write_bytes(b"raw")
    source_image = Image.new("RGB", (100, 100), "#ffffff")
    monkeypatch.setattr(
        editor_core,
        "read_raw_embedded_focus_metadata",
        lambda _path: _raw_embedded_focus_metadata(source_path),
    )
    editor_core._read_raw_embedded_focus_metadata_cached.cache_clear()

    job = VideoFrameJob(
        path=source_path,
        settings={
            "draw_banner": False,
            "draw_text": False,
            "draw_focus": True,
            "ratio": "no_crop",
            "stage_template_overlay_enabled": False,
            "stage_resize_limit_enabled": False,
            PIPELINE_STAGE_ORDER_KEY: ["template_crop", "focus_overlay"],
        },
        raw_metadata={
            "SourceFile": str(source_path),
            "Make": "SONY",
            "Model": "ILCE-1M2",
            "ExifImageWidth": 100,
            "ExifImageHeight": 100,
            "FocusX": 10,
            "FocusY": 10,
            "FocusW": 20,
            "FocusH": 20,
        },
        metadata_context={},
        source_image=source_image,
    )

    rendered = None
    try:
        rendered = render_video_frame(job)
        assert rendered.getpixel((75, 72)) == (46, 255, 85)
        assert rendered.getpixel((10, 10)) == (255, 255, 255)
    finally:
        if rendered is not None:
            rendered.close()
        source_image.close()
        editor_core._read_raw_embedded_focus_metadata_cached.cache_clear()
