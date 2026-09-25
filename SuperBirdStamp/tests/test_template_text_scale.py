"""文字在画幅、逐图重载、预览/导出和持久化之间保持一致。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageChops, ImageStat
from PyQt6.QtWidgets import QApplication
import pytest

from birdstamp import config
from birdstamp.export_stage import (
    VideoFrameJob, build_default_image_proc_pipeline, render_video_frame,
    source_frame_signature_for_job,
)
from birdstamp.export_stage import core as export_core
from birdstamp.export_frame_cache import build_source_frame_bucket_key
from birdstamp.gui import editor_options, editor_template as template
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_renderer import _BirdStampRendererMixin
from birdstamp.gui.editor_utils import path_key
from birdstamp.render.text_scale import normalize_text_scale
from birdstamp.workspace import read_workspace_json, write_workspace_json

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def payload(monkeypatch):
    monkeypatch.setattr(template, "_resolve_template_field_text", lambda *args: "Bird")
    return dict(name="scale-test", ratio="no_crop", draw_banner_background=False, fields=[
        dict(text_source={"type": "from_file", "key": "stem"}, font_size=100,
             x_offset_pct=10, y_offset_pct=10, color="#FFFFFF"),
    ])


@pytest.mark.parametrize("portrait", [False, True])
def test_text_occupies_same_fraction_of_small_and_large_images(payload, portrait):
    fractions = []
    for width, height in [(400, 225), (1600, 900), (6400, 3600)]:
        if portrait:
            width, height = height, width
        with Image.new("RGB", (width, height)) as source:
            result = template.render_template_overlay(
                source, raw_metadata={}, metadata_context={}, template_payload=payload,
            )
        box = result.getbbox()
        assert box is not None
        fractions.append(((box[2] - box[0]) / width, (box[3] - box[1]) / height))
        result.close()
    for fraction in fractions:
        assert fraction == pytest.approx(fractions[1], abs=0.009)


def test_small_output_can_use_fonts_below_eight_pixels(payload, monkeypatch):
    payload["fields"][0]["font_size"] = 24
    draw = Mock(wraps=template._draw_styled_text)
    monkeypatch.setattr(template, "_draw_styled_text", draw)
    result = template.render_template_overlay(
        Image.new("RGB", (400, 225)), raw_metadata={}, metadata_context={}, template_payload=payload,
    )
    assert draw.call_args.kwargs["font"].size == 6
    result.close()


def test_large_multiplier_still_fits_long_text(payload, monkeypatch):
    text = "A long camera and lens description " * 9
    monkeypatch.setattr(template, "_resolve_template_field_text", lambda *args: text)
    draw = Mock(wraps=template._draw_styled_text)
    monkeypatch.setattr(template, "_draw_styled_text", draw)
    result = template.render_template_overlay(
        Image.new("RGB", (1600, 900)), raw_metadata={}, metadata_context={},
        template_payload=payload, text_scale=3,
    )
    font = draw.call_args.kwargs["font"]
    box = font.getbbox(text)
    assert box[2] - box[0] <= 1600
    result.close()


@pytest.mark.parametrize("scale", [0.25, 1.0, 2.0, 3.0])
@pytest.mark.parametrize("crop", [False, True])
def test_preview_matches_pipeline_with_crop_resize_and_text_scale(payload, scale, crop):
    settings = dict(template_payload=payload, ratio="no_crop", text_scale=scale,
                    draw_banner=False, max_long_edge=1200)
    box = (0.25, 0, 0.75, 1) if crop else None
    if crop:
        settings.update(ratio="free", center_mode="custom", crop_box=box)
    source = Image.new("RGB", (3200, 1800))
    renderer = _BirdStampRendererMixin()
    renderer.template_paths = {}
    renderer.current_photo_info = None
    renderer.current_metadata_context = {}
    renderer.current_source_image = source.resize((800, 450))
    for order in [("template_crop", "resize_limit", "template_overlay"),
                  ("template_crop", "template_overlay", "resize_limit")]:
        settings["pipeline_stage_order"] = list(order)
        exported = render_video_frame(VideoFrameJob(
            path=Path("Bird.jpg"), settings=settings, source_image=source,
            raw_metadata={}, metadata_context={},
        ))
        preview = renderer._render_preview_pipeline_image(
            renderer.current_source_image.copy(), {}, source_image=renderer.current_source_image,
            settings=settings, crop_box=box, outer_pad=(0, 0, 0, 0),
            crop_output_size=(1600, 1800) if crop else source.size,
        )
        if crop:
            preview = preview.crop((200, 0, 600, 450))
        difference = ImageChops.difference(exported.resize(preview.size, Image.Resampling.LANCZOS), preview)
        assert max(ImageStat.Stat(difference).mean) < 1.5
        assert preview.getbbox() is not None
        exported.close()
        preview.close()
    renderer.current_source_image.close()
    source.close()


@pytest.mark.parametrize("value, expected", [
    (None, 1.0), ("bad", 1.0), (float("nan"), 1.0), (float("inf"), 1.0),
    (-1, 0.25), (100, 3.0), ("1.75", 1.75),
])
def test_normalization_legacy_and_invalid_values(value, expected):
    assert normalize_text_scale(value) == expected
    settings = {"text_scale": value}
    assert export_core._clone_render_settings(settings)["text_scale"] == expected
    assert _BirdStampRendererMixin()._clone_render_settings(settings)["text_scale"] == expected


def test_scale_is_a_stage_parameter_and_invalidates_only_its_frame():
    descriptor = next(d for d in build_default_image_proc_pipeline().ui_descriptors()
                      if d.stage_id == "template_overlay")
    option = next(o for o in descriptor.parameter_options if o.key == "text_scale")
    assert (option.default, option.minimum, option.maximum) == (1.0, 0.25, 3.0)
    job = VideoFrameJob(path=Path("Bird.jpg"), settings={}, raw_metadata={}, metadata_context={})
    original = source_frame_signature_for_job(job)
    job.settings["text_scale"] = 1.0
    assert source_frame_signature_for_job(job) == original
    bucket = build_source_frame_bucket_key(global_export_settings=job.settings)
    job.settings["text_scale"] = 1.75
    assert source_frame_signature_for_job(job) != original
    assert build_source_frame_bucket_key(global_export_settings=job.settings) == bucket


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for name in ("_start_bird_detector_preload", "_run_deferred_startup_tasks",
                 "_restart_photo_list_metadata_loader", "_schedule_async_bird_detect"):
        monkeypatch.setattr(BirdStampEditorWindow, name, lambda *args, **kwargs: None)
    instance = BirdStampEditorWindow()
    try:
        yield instance
    finally:
        instance.close()
        instance.deleteLater()
        _APP.processEvents()


def test_slider_per_photo_switch_batch_and_workspace_roundtrip(window, payload, tmp_path, monkeypatch):
    settings = window._build_current_render_settings()
    settings.update(template_name="scale-test", template_payload=payload, ratio="no_crop")
    paths = [tmp_path / "first.png", tmp_path / "second.png"]
    for path in paths:
        image = Image.new("RGB", (800, 450))
        image.save(path)
        window._append_photo_path_to_list(path, existing_keys=set(), default_settings=settings)
        window._store_preview_image_cache(window._preview_image_cache_signature(path), image)

    def select(path):
        item = window._find_photo_item_by_path(path)
        window.photo_list.blockSignals(True)
        window.photo_list.setCurrentItem(item)
        window.photo_list.blockSignals(False)
        window._on_photo_selected(item, None)

    assert window._pipeline_stage_option_groups["template_overlay"].isAncestorOf(window.text_scale_slider)
    assert (window.text_scale_slider.minimum(), window.text_scale_slider.maximum()) == (25, 300)
    select(paths[0])
    window._photo_export_dirty_keys.clear()
    old_key = window._original_mode_cache_key()
    window.text_scale_slider.setValue(175)
    assert window.text_scale_value_label.text() == "175%"
    assert window.photo_render_overrides[path_key(paths[0])]["text_scale"] == 1.75
    assert window._photo_export_dirty_keys == {path_key(paths[0])}
    assert window._original_mode_cache_key() != old_key
    select(paths[1])
    assert window.text_scale_slider.value() == 100
    window.text_scale_slider.setValue(60)
    select(paths[0])
    assert window.text_scale_slider.value() == 175

    workspace = tmp_path / "scale.birdstamp-workspace.json"
    saved = window._collect_workspace_payload(workspace)
    write_workspace_json(workspace, saved)
    assert sorted(p["render_settings"]["text_scale"] for p in saved["photos"]) == [0.6, 1.75]
    monkeypatch.setattr(window, "_schedule_workspace_photo_selection", lambda *args, **kwargs: None)
    window._restore_workspace_payload(read_workspace_json(workspace), workspace, autosave_after_restore=False)
    while window._workspace_restore_in_progress():
        window._process_workspace_restore_photo_batch()
    select(paths[1])
    assert window.text_scale_slider.value() == 60
    select(paths[0])
    assert window.text_scale_slider.value() == 175
    window._apply_current_settings_to_all_photos()
    select(paths[1])
    assert window.text_scale_slider.value() == 175
    window.text_scale_reset_btn.click()
    assert window._build_current_render_settings()["text_scale"] == 1.0
    assert window.photo_render_overrides[path_key(paths[0])]["text_scale"] == 1.75


def test_legacy_photo_does_not_inherit_previous_photos_scale():
    renderer = _BirdStampRendererMixin()
    fallback = {"text_scale": 2.5}
    for old in (None, {}, {"ratio": "no_crop"}):
        assert renderer._normalize_render_settings(old, fallback)["text_scale"] == 1.0


def test_slider_options_are_loaded_from_editor_options(monkeypatch):
    monkeypatch.setattr(editor_options, "_load_builtin_editor_options_raw", lambda: {
        "text_scale_slider": dict(minimum=50, maximum=200, default=125, step=5),
    })
    assert editor_options.load_editor_options()["text_scale_slider"] == dict(
        minimum=50, maximum=200, default=125, step=5,
    )


def test_cli_text_scale_changes_rendered_text_size(payload, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from birdstamp import cli

    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    source = tmp_path / "Bird.png"
    Image.new("RGB", (800, 450)).save(source)
    template_path = tmp_path / "template.json"
    template.save_template_payload(template_path, payload)
    monkeypatch.setattr(cli, "extract_many_with_xmp_priority", lambda *a, **k: {source: {"SourceFile": str(source)}})
    widths = []
    for scale in (0.5, 2.0):
        output = tmp_path / str(scale)
        result = CliRunner().invoke(cli.app, [
            "render", str(source), "--out", str(output), "--template", str(template_path),
            "--format", "png", "--max-long-edge", "0", "--text-scale", str(scale),
        ])
        assert result.exit_code == 0, result.output + str(result.exception)
        with Image.open(next(output.glob("*.png"))) as rendered:
            box = rendered.getbbox()
            widths.append(box[2] - box[0])
    assert widths[1] / widths[0] == pytest.approx(4.0, rel=0.1)
