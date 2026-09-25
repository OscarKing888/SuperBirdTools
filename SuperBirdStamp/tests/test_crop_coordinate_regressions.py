"""原图/补边/预览坐标回归；也可在没有 pytest 时直接用 unittest 运行。"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageChops, ImageStat
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from birdstamp.export_stage import VideoFrameJob, render_video_frame
from birdstamp.export_stage import core as export_core
from birdstamp import config
from birdstamp.gui import editor_core as core, editor_template as template
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas
from birdstamp.gui.editor_renderer import _BirdStampRendererMixin
from birdstamp.gui.editor_template_dialog import TemplateManagerDialog


_APP = None


class CropCoordinateTests(unittest.TestCase):
    def assertBoxAlmostEqual(self, actual, expected):
        self.assertIsNotNone(actual)
        for got, want in zip(actual, expected):
            self.assertAlmostEqual(got, want, places=9)

    def test_expanded_and_full_overrides_survive_export(self):
        image = Image.new("RGB", (120, 80), "red")
        for box, expected_size in [
            ((-0.25, -0.25, 1.25, 1.25), (180, 120)),
            ((0, 0, 1, 1), (120, 80)),
            ((1.1, 0.8, -0.1, 0.2), (144, 48)),
        ]:
            with self.subTest(box=box):
                settings = dict(ratio=1, center_mode="custom", crop_box=box,
                                crop_padding_fill="#FFFFFF", draw_banner=False, draw_text=False)
                normalized = core.normalize_extended_unit_box(box)
                cloned = export_core._clone_render_settings(settings)
                self.assertEqual(tuple(cloned["crop_box"]), normalized)
                plan = core.compute_crop_plan_for_image(image=image, raw_metadata={}, settings=settings)
                self.assertBoxAlmostEqual(core.crop_box_to_source(plan[0], image.size, plan[1]), normalized)
                rendered = render_video_frame(VideoFrameJob(
                    path=Path("source.jpg"), source_image=image, settings=settings,
                    raw_metadata={}, metadata_context={},
                ))
                self.assertEqual(rendered.size, expected_size)
                self.assertEqual(rendered.getpixel((rendered.width // 2, rendered.height // 2)), (255, 0, 0))
                if box[0] < 0:
                    self.assertEqual(rendered.getpixel((0, 0)), (255, 255, 255))
                rendered.close()
        self.assertTrue(core.crop_box_has_effect((-0.1, -0.1, 1.1, 1.1)))
        self.assertFalse(core.crop_box_has_effect((0, 0, 1, 1)))

    def test_invalid_coordinates_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            self.assertIsNone(core.normalize_extended_unit_box((0, 0, bad, 1)))
            self.assertIsNone(core.parse_ratio_value(bad))
            self.assertFalse(core.should_use_crop_box_override(dict(center_mode="custom", crop_box=(0, 0, bad, 1))))

    def test_custom_and_original_ratio_respect_pixel_padding(self):
        image = Image.new("RGB", (1000, 800))
        settings = dict(ratio=1, center_mode="custom", custom_center_x=0.1,
                        custom_center_y=0.2, crop_padding_top=200, crop_padding_bottom=200,
                        crop_padding_left=200, crop_padding_right=200)
        box, pad = core.compute_crop_plan_for_image(image=image, raw_metadata={}, settings=settings)
        self.assertEqual(core.compute_crop_output_size(*image.size, box, pad), (400, 400))
        self.assertBoxAlmostEqual(core.crop_box_to_source(box, image.size, pad), (-0.1, -0.05, 0.3, 0.45))
        settings.update(ratio=None, center_mode="image")
        box, pad = core.compute_crop_plan_for_image(image=image, raw_metadata={}, settings=settings)
        self.assertEqual(core.compute_crop_output_size(*image.size, box, pad), (500, 400))
        for ratio in ("free", "no_crop"):
            settings["ratio"] = ratio
            self.assertEqual(core.compute_crop_plan_for_image(image=image, raw_metadata={}, settings=settings),
                             (None, (0, 0, 0, 0)))

    def test_preview_plan_preserves_source_coordinates_with_asymmetric_padding(self):
        for full_size, preview_size in [((6000, 4000), (1200, 800)), ((4000, 6000), (800, 1200)),
                                        ((11232, 7488), (2048, 1366))]:
            for box in [(-0.2, 0.1, 0.7, 1.1), (0.8, -0.1, 1.2, 0.9), (-0.2, -0.3, 1.2, 1.1)]:
                with self.subTest(full_size=full_size, box=box):
                    plan = core._crop_plan_from_override(*full_size, box)
                    preview = core.rescale_crop_plan(*plan, full_size, preview_size)
                    self.assertBoxAlmostEqual(core.crop_box_to_source(preview[0], preview_size, preview[1]), box)
                    self.assertBoxAlmostEqual(core.crop_box_to_source(plan[0], full_size, plan[1]), box)
                    expected = ((box[2] - box[0]) * preview_size[0], (box[3] - box[1]) * preview_size[1])
                    actual = core.compute_crop_output_size(*preview_size, *preview)
                    self.assertLessEqual(abs(actual[0] - expected[0]), 1)
                    self.assertLessEqual(abs(actual[1] - expected[1]), 1)

    def test_template_crop_survives_utf8_save_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "自定义模板.json"
            payload = dict(name="自定义模板", ratio="free", center_mode="custom",
                           crop_box=[-0.2, 0.1, 0.7, 1.1], custom_center_x=0.25, custom_center_y=0.6, fields=[])
            template.save_template_payload(path, payload)
            loaded = template.load_template_payload(path)
            self.assertEqual(loaded["name"], "自定义模板")
            self.assertBoxAlmostEqual(loaded["crop_box"], payload["crop_box"])
            self.assertEqual(loaded["custom_center_x"], 0.25)
            self.assertEqual(loaded["custom_center_y"], 0.6)
            self.assertEqual(template.normalize_template_payload(loaded, "fallback"), loaded)

    def test_uniform_crop_preserves_explicit_full_frame(self):
        settings = dict(ratio=1, center_mode="custom", crop_box=[0, 0, 1, 1], uniform_auto_crop=True)
        self.assertIsNone(export_core._uniform_crop_group_key(settings))
        self.assertFalse(export_core._stabilization_eligible(settings))
        jobs = [VideoFrameJob(path=Path(str(i)), source_image=Image.new("RGB", size),
                              settings=settings, raw_metadata={}, metadata_context={})
                for i, size in enumerate(((120, 80), (80, 120)))]
        export_core.prepare_uniform_auto_crop_plans(jobs)
        self.assertEqual([render_video_frame(job).size for job in jobs], [(120, 80), (80, 120)])

    def test_bird_center_single_axis_padding_is_not_discarded(self):
        image = Image.new("RGB", (1200, 800))
        bird_box = (0.85, 0.0, 0.95, 0.2)
        for side in ("top", "bottom", "left", "right"):
            with self.subTest(side=side):
                settings = dict(ratio=1, center_mode="bird")
                settings[f"crop_padding_{side}"] = 128
                box, pad = core.compute_crop_plan_for_image(image=image, raw_metadata={},
                                                           settings=settings, bird_box=bird_box)
                self.assertEqual(core.compute_crop_output_size(*image.size, box, pad), (256, 256))
                center = core.box_center(core.crop_box_to_source(box, image.size, pad))
                self.assertAlmostEqual(center[0], 0.9)
                self.assertAlmostEqual(center[1], 0.1)
                preview_box, preview_pad = core.rescale_crop_plan(box, pad, image.size, (300, 200))
                self.assertBoxAlmostEqual(core.crop_box_to_source(preview_box, (300, 200), preview_pad),
                                          core.crop_box_to_source(box, image.size, pad))

    def test_no_crop_text_only_preset_preserves_image_and_ignores_old_crop(self):
        payload = template.load_template_payload(Path(__file__).parents[1] / "config/templates/不裁切_仅文字.json")
        self.assertEqual(payload["ratio"], "no_crop")
        self.assertFalse(payload["draw_banner_background"])
        for size in ((640, 400), (400, 640)):
            with self.subTest(size=size):
                source = Image.new("RGB", size, "#557799")
                settings = dict(payload, template_payload=payload, center_mode="bird", draw_text=True,
                                draw_banner=True, crop_box=[-0.5, 0.1, 0.5, 0.9], crop_padding_top=500)
                job = VideoFrameJob(path=Path("sample.jpg"), source_image=source, settings=settings,
                                    raw_metadata={"XMP-dc:Title": "红胁蓝尾鸲"}, metadata_context={})
                with patch.object(export_core, "_resolve_bird_box_for_image", side_effect=AssertionError("no detection")):
                    self.assertEqual(export_core._compute_crop_plan_for_image(
                        path=job.path, image=source, raw_metadata={}, settings=settings, bird_box_cache={}),
                        (None, (0, 0, 0, 0)))
                    # 不裁切还必须覆盖调用方遗留的预计算计划。
                    job.crop_plan = ((0, 0, 0.5, 0.5), (10, 10, 10, 10))
                    rendered = render_video_frame(job)
                self.assertEqual(rendered.size, size)
                self.assertEqual(rendered.getpixel((0, 0)), source.getpixel((0, 0)))
                self.assertIsNotNone(ImageChops.difference(rendered, source).getbbox())

    def test_cli_template_crop_and_explicit_unlimited_size(self):
        from typer.testing import CliRunner
        from birdstamp import cli

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "get_user_data_dir", return_value=Path(directory) / "user"):
            root = Path(directory)
            source = root / "原图.png"
            Image.new("RGB", (120, 80), "red").save(source)
            raw = {"SourceFile": str(source), "XMP-dc:Title": "红胁蓝尾鸲"}
            for ratio, expected in (("free", (60, 80)), ("no_crop", (120, 80))):
                with self.subTest(ratio=ratio):
                    path = root / f"{ratio}.json"
                    template.save_template_payload(path, dict(ratio=ratio, center_mode="custom",
                        crop_box=[0, 0, 0.5, 1], max_long_edge=32, fields=[]))
                    output = root / ratio
                    with patch.object(cli, "extract_many_with_xmp_priority", return_value={source.resolve(): raw}):
                        result = CliRunner().invoke(cli.app, ["render", str(source), "--out", str(output),
                            "--template", str(path), "--format", "png", "--max-long-edge", "0"])
                    self.assertEqual(result.exit_code, 0, result.output + str(result.exception))
                    files = list(output.glob("*.png"))
                    self.assertEqual(len(files), 1, result.output)
                    with Image.open(files[0]) as rendered:
                        self.assertEqual(rendered.size, expected)


class CropCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _APP
        _APP = QApplication.instance() or QApplication([])

    def test_each_corner_keeps_opposite_corner_and_pixel_ratio(self):
        canvas = EditorPreviewCanvas()
        start = (0.2, 0.2, 0.8, 0.8)
        for aspect in (1.5, 2 / 3):
            for ratio in (16 / 9, 1, 9 / 16):
                canvas.set_crop_ratio_constraint(ratio, False)
                for handle, point, fixed_indices in [
                    ("nw", (0.1, 0.15), (2, 3)), ("ne", (0.9, 0.15), (0, 3)),
                    ("se", (0.9, 0.85), (0, 1)), ("sw", (0.1, 0.85), (2, 1)),
                ]:
                    with self.subTest(aspect=aspect, ratio=ratio, handle=handle):
                        box = canvas._box_after_drag(start, handle, *point, aspect)
                        for index in fixed_indices:
                            self.assertAlmostEqual(box[index], start[index])
                        self.assertAlmostEqual((box[2] - box[0]) / (box[3] - box[1]) * aspect, ratio)
        canvas.close()

    def test_original_and_free_modes_start_with_editable_full_box(self):
        canvas = EditorPreviewCanvas()
        canvas.set_crop_effect_box(None)
        canvas.set_crop_edit_mode(True)
        self.assertEqual(canvas._crop_effect_box, (0, 0, 1, 1))
        canvas.set_crop_effect_box(None)
        self.assertEqual(canvas._crop_effect_box, (0, 0, 1, 1))
        canvas.close()

    def test_manual_resize_converts_padded_coordinates_and_commits_custom_mode(self):
        source_box = (-0.25, 0.1, 0.75, 0.9)
        plan_box, pads = core._crop_plan_from_override(1000, 800, source_box)
        window = SimpleNamespace(
            current_source_image=Image.new("RGB", (1000, 800)), current_path=Path("photo.jpg"),
            _current_preview_outer_pad=lambda: pads, _crop_display_source_size=lambda: (5000, 4000),
            _set_custom_center_from_box=Mock(), _update_crop_padding_from_box=Mock(),
            _set_photo_crop_box_for_path=Mock(), _crop_drag_active=True,
            _preview_debounce_timer=Mock(), _on_crop_settings_changed=Mock(),
            preview_label=SimpleNamespace(canvas=SimpleNamespace()),
        )
        BirdStampEditorWindow._on_canvas_crop_box_changed(window, plan_box)
        for got, expected in zip(window._crop_box_override, source_box):
            self.assertAlmostEqual(got, expected)
        window._set_custom_center_from_box.assert_called_once_with(window._crop_box_override)
        window._update_crop_padding_from_box.assert_called_once_with(window._crop_box_override, (5000, 4000))
        window._preview_debounce_timer.start.assert_not_called()
        window._on_crop_settings_changed.assert_not_called()
        window._crop_drag_active = False
        BirdStampEditorWindow._on_canvas_crop_box_changed(window, plan_box)
        window._on_crop_settings_changed.assert_called_once()

    def test_template_drag_uses_source_coordinates_and_saves_on_release(self):
        box = (-0.25, 0.1, 0.75, 0.9)
        plan_box, pads = core._crop_plan_from_override(1000, 800, box)
        dialog = SimpleNamespace(
            current_payload={}, _preview_source_image=Image.new("RGB", (1000, 800)),
            _preview_outer_pad=pads, _preview_photo_info=None, _crop_drag_active=True,
            crop_padding_editor=Mock(),
            _set_tmpl_center_mode_value=Mock(), _save_current_template=Mock(), _refresh_preview=Mock(),
        )
        TemplateManagerDialog._on_tmpl_canvas_crop_box_changed(dialog, plan_box)
        for got, expected in zip(dialog.current_payload["crop_box"], box):
            self.assertAlmostEqual(got, expected)
        self.assertEqual(dialog.current_payload["center_mode"], "custom")
        dialog._refresh_preview.assert_not_called()
        dialog._crop_drag_active = False
        TemplateManagerDialog._on_tmpl_canvas_crop_box_changed(dialog, plan_box)
        dialog._save_current_template.assert_called_once()
        dialog._refresh_preview.assert_called_once()

    def test_window_downscaled_preview_and_drag_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "get_user_data_dir", return_value=Path(directory) / "user"), \
                patch.object(BirdStampEditorWindow, "_start_bird_detector_preload"), \
                patch.object(BirdStampEditorWindow, "_run_deferred_startup_tasks"):
            window = BirdStampEditorWindow()
            try:
                window.current_path = Path(directory) / "photo.jpg"
                window.current_source_image = Image.new("RGB", (300, 200), "red")
                window.current_source_full_size = (1200, 800)
                window.current_raw_metadata = {}
                window._show_error = Mock(side_effect=AssertionError)
                settings = dict(ratio=1.5, center_mode="custom", crop_box=[-0.2, 0.1, 0.8, 0.9],
                                draw_banner=False, draw_text=False, max_long_edge=0)
                window._render_settings_for_path = Mock(return_value=settings)
                window.render_preview()
                self.assertEqual(window._preview_outer_pad, (0, 0, 60, 0))
                self.assertEqual(window.last_rendered.size, (360, 200))
                self.assertEqual(window._preview_crop_size, (1200, 640))
                self.assertEqual(window.last_rendered.getpixel((10, 100)), (255, 255, 255))
                self.assertEqual(window.last_rendered.getpixel((100, 100)), (255, 0, 0))
                grid_index = window.preview_grid_combo.findData("thirds")
                self.assertGreaterEqual(grid_index, 0)
                window.preview_grid_combo.setCurrentIndex(grid_index)
                overlay = window.preview_label.canvas.render_source_pixmap_with_overlays().toImage()
                self.assertGreater(overlay.pixelColor(100, 100).green(), 0)
                self.assertEqual(overlay.pixelColor(350, 100).green(), 0)
                window._crop_drag_active = True
                window._on_canvas_crop_box_changed(window.preview_overlay_state.crop_effect_box)
                snapshot = window._build_current_render_settings()
                self.assertEqual(snapshot["center_mode"], "custom")
                for got, expected in zip(snapshot["crop_box"], settings["crop_box"]):
                    self.assertAlmostEqual(got, expected)
                self.assertEqual(snapshot["crop_padding_left"], 600)
                self.assertEqual(snapshot["crop_padding_top"], 320)
                window._set_edit_mode_button_checked("crop_adjust")
                window.center_mode_buttons["bird"].click()
                self.assertIsNone(window._crop_box_override)
                self.assertIsNone(window._custom_center)
                window._crop_drag_active = False
                no_crop_index = window.ratio_combo.findData("no_crop")
                self.assertGreaterEqual(no_crop_index, 0)
                window.ratio_combo.setCurrentIndex(no_crop_index)
                window._set_edit_mode_button_checked("crop_adjust")
                settings["ratio"] = "no_crop"
                window.render_preview()
                self.assertEqual(window.last_rendered.size, (300, 200))
                self.assertEqual(window._preview_crop_size, (1200, 800))
                self.assertFalse(window.preview_label.canvas.crop_edit_mode())
            finally:
                window.close()
                window.deleteLater()
                _APP.processEvents()

    def test_template_dialog_saves_drag_and_shows_resized_output_size(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(config, "get_user_data_dir", return_value=Path(directory) / "user"), \
                patch.object(TemplateManagerDialog, "_load_preview_source"), \
                patch.object(TemplateManagerDialog, "_preview_source_bird_box", return_value=None):
            template_dir = Path(directory) / "templates"
            template_dir.mkdir()
            payload = dict(name="default", ratio="free", center_mode="custom", fields=[],
                           crop_box=[-0.2, 0.1, 0.8, 0.9], max_long_edge=200)
            template.save_template_payload(template_dir / "default.json", payload)
            dialog = TemplateManagerDialog(template_dir, Image.new("RGB", (1000, 800), "red"))
            try:
                dialog._on_template_selected(dialog.template_list.findItems("default", Qt.MatchFlag.MatchExactly)[0], None)
                self.assertEqual(dialog._preview_crop_size, (200, 128))
                grid_index = dialog.preview_grid_combo.findData("thirds")
                self.assertGreaterEqual(grid_index, 0)
                dialog.preview_grid_combo.setCurrentIndex(grid_index)
                overlay = dialog.preview_label.canvas.render_source_pixmap_with_overlays().toImage()
                self.assertGreater(overlay.pixelColor(333, 400).green(), 0)
                self.assertEqual(overlay.pixelColor(1150, 400).green(), 0)
                dialog._on_tmpl_canvas_crop_box_changed(dialog.preview_overlay_state.crop_effect_box)
                loaded = template.load_template_payload(template_dir / "default.json")
                for got, expected in zip(loaded["crop_box"], payload["crop_box"]):
                    self.assertAlmostEqual(got, expected)
                self.assertEqual(loaded["center_mode"], "custom")
            finally:
                dialog.close()
                dialog.deleteLater()
                _APP.processEvents()


class TemplateLayoutTests(unittest.TestCase):
    def test_all_bundled_templates_preview_matches_export_layout(self):
        template_dir = Path(__file__).parents[1] / "config" / "templates"
        paths = sorted(template_dir.glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            for size in ((2400, 1600), (1600, 2400)):
                with self.subTest(template=path.name, size=size):
                    payload = template.load_template_payload(path)
                    kwargs = dict(raw_metadata={"SourceFile": "sample.jpg", "XMP-dc:Title": "红胁蓝尾鸲"},
                                  metadata_context={}, template_payload=payload)
                    large = template.render_template_overlay(Image.new("RGB", size, "#999999"), **kwargs)
                    preview_size = tuple(value // 4 for value in size)
                    small = template.render_template_overlay(Image.new("RGB", preview_size, "#999999"),
                                                             layout_size=size, **kwargs)
                    difference = ImageChops.difference(large.resize(preview_size, Image.Resampling.LANCZOS), small)
                    self.assertLess(max(ImageStat.Stat(difference).mean), 1.5)

    def test_long_text_shrinks_to_fit_even_without_other_fields(self):
        payload = dict(fields=[dict(text_source={"type": "from_file", "key": "A long camera and lens description"},
                                  font_size=100, align_horizontal="left", align_vertical="top")])
        with patch.object(template, "_resolve_template_field_text", return_value="A long camera and lens description"), \
                patch.object(template, "_draw_styled_text", wraps=template._draw_styled_text) as draw:
            template.render_template_overlay(Image.new("RGB", (320, 180)), raw_metadata={},
                                             metadata_context={}, template_payload=payload, auto_scale_font=False)
            self.assertTrue(draw.called)
            self.assertLess(draw.call_args.kwargs["font"].size, 100)

    def test_preview_uses_export_dimensions_at_overlay_stage(self):
        renderer = _BirdStampRendererMixin()
        renderer.current_metadata_context = {}
        renderer.current_photo_info = None
        renderer._render_overlay_for_preview_frame = Mock(side_effect=lambda **kw: kw["preview_base"])
        source = Image.new("RGB", (600, 400))
        for order, expected in [(["template_crop", "resize_limit", "template_overlay"], (800, 800)),
                                (["template_crop", "template_overlay", "resize_limit"], (2000, 2000))]:
            renderer._render_preview_pipeline_image(source, {}, source_image=source, crop_box=(0.25, 0, 0.75, 1),
                outer_pad=(0, 0, 0, 0), crop_output_size=(2000, 2000),
                settings={"max_long_edge": 800, "pipeline_stage_order": order})
            self.assertEqual(renderer._render_overlay_for_preview_frame.call_args.kwargs["layout_size"], expected)


if __name__ == "__main__":
    unittest.main()
