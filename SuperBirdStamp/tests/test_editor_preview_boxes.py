from __future__ import annotations

import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMessageBox

from birdstamp import config
from birdstamp.gui import bird_detect_worker, editor_renderer
from birdstamp.gui.editor import BirdStampEditorWindow

_APP = QApplication.instance() or QApplication([])
FOCUS_METADATA = {
    "Make": "SONY", "Model": "ILCE-1", "ImageWidth": 600, "ImageHeight": 400,
    "SubjectArea": "180 160 60 40",
}
BIRD_BOX = (0.55, 0.3, 0.8, 0.75)


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


@pytest.fixture
def preview_window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "state")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(BirdStampEditorWindow, "_start_bird_detector_preload", lambda self: None)
    source = tmp_path / "example.jpg"
    Image.new("RGB", (600, 400), "black").save(source)
    monkeypatch.setattr(editor_renderer, "_default_placeholder_path", lambda: source)
    monkeypatch.setattr(bird_detect_worker, "detect_primary_bird_box", lambda image: BIRD_BOX)
    window = BirdStampEditorWindow()
    window._schedule_workspace_autosave = lambda *a: None
    window._autosave_workspace_now = lambda *a: None
    window._load_raw_metadata = lambda path: {**FOCUS_METADATA, "SourceFile": str(path)}
    settings = window._build_current_render_settings()
    settings.update(ratio="no_crop", center_mode="image", draw_text=False, draw_banner=False, draw_focus=False)
    monkeypatch.setattr(window, "_render_settings_for_path", lambda *a, **k: dict(settings))
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda *args: errors.append(args))
    try:
        yield window, source, errors
    finally:
        window._cancel_async_bird_detect(shutdown=True)
        window._cancel_preview_decode(shutdown=True)
        wait_until(lambda: window._bird_detect_worker is None and window._preview_decode_worker is None)
        window._stop_photo_list_metadata_loader(wait=True, reset_progress=True)
        window.close()
        window.deleteLater()
        _APP.processEvents()


def test_empty_startup_activates_example_with_focus_and_bird_boxes(preview_window, monkeypatch):
    window, source, errors = preview_window
    monkeypatch.setattr(window, "_restore_startup_workspace", lambda: False)
    window._run_deferred_startup_tasks()
    wait_until(lambda: window.current_path == source and window.preview_overlay_state.bird_box is not None)
    assert errors == []
    assert window._is_placeholder_active()
    assert window.photo_list.topLevelItemCount() == 0
    assert window.preview_overlay_state.focus_box == pytest.approx((0.25, 0.35, 0.35, 0.45))
    assert window.preview_overlay_state.bird_box == BIRD_BOX
    canvas = window.preview_label.canvas
    visible = canvas.render_source_pixmap_with_overlays().toImage()
    fx, fy = round(visible.width() * 0.25) + 2, round(visible.height() * 0.4)
    bx, by = round(visible.width() * 0.6), round(visible.height() * 0.4)
    assert visible.pixelColor(fx, fy).green() > 0
    assert visible.pixelColor(bx, by).blue() > 0
    window.show_focus_box_check.setChecked(False)
    window.show_bird_box_check.setChecked(False)
    hidden = canvas.render_source_pixmap_with_overlays().toImage()
    assert hidden.pixelColor(fx, fy).green() == 0
    assert hidden.pixelColor(bx, by).blue() == 0


def test_turning_bird_box_on_starts_detection_without_rerender(preview_window, monkeypatch):
    window, source, errors = preview_window
    window.show_bird_box_check.setChecked(False)
    window._show_placeholder_preview()
    # 真实照片沿用相同渲染路径；此处取消示例标识以覆盖普通照片的开关。
    window.placeholder_path = None
    assert window.preview_overlay_state.bird_box is None
    calls = []
    monkeypatch.setattr(window, "render_preview", lambda *a: calls.append("render"))
    window.show_bird_box_check.setChecked(True)
    wait_until(lambda: window.preview_overlay_state.bird_box is not None)
    assert calls == []
    assert errors == []
    assert window.preview_label.canvas._bird_box == BIRD_BOX
    window.show_bird_box_check.setChecked(False)
    window.show_bird_box_check.setChecked(True)
    assert window.preview_label.canvas._bird_box == BIRD_BOX


def test_startup_example_does_not_replace_selected_or_restored_photos(preview_window, monkeypatch):
    window, source, errors = preview_window
    shown = []
    monkeypatch.setattr(window, "_show_placeholder_preview", lambda: shown.append(True))
    monkeypatch.setattr(window, "_restore_startup_workspace", lambda: True)
    window._run_deferred_startup_tasks()
    _APP.processEvents()
    assert shown == []
    window.current_path = source
    window.placeholder_path = None
    window._run_deferred_startup_tasks()
    _APP.processEvents()
    assert shown == []


def test_restoring_workspace_without_available_photos_loads_example(preview_window, tmp_path, monkeypatch):
    window, source, errors = preview_window
    workspace = tmp_path / "empty.birdstamp-workspace.json"
    payload = window._collect_workspace_payload(workspace)
    payload["photos"] = [{"path": str(tmp_path / "missing.jpg")}]
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Ok)
    window._restore_workspace_payload(payload, workspace, autosave_after_restore=False)
    wait_until(lambda: window.preview_overlay_state.bird_box is not None)
    assert errors == []
    assert window.current_path == source
    assert window.preview_overlay_state.focus_box is not None
    assert window.photo_list.topLevelItemCount() == 0


def test_late_focus_metadata_appears_with_current_checkbox_state(preview_window):
    window, source, errors = preview_window
    window.show_bird_box_check.setChecked(False)
    window._load_raw_metadata = lambda path: {"SourceFile": str(path)}
    window._show_placeholder_preview()
    window.placeholder_path = None
    assert window.preview_overlay_state.focus_box is None
    window.show_focus_box_check.setChecked(False)
    window._apply_current_photo_metadata(source, FOCUS_METADATA, refresh_preview=True)
    assert window.preview_overlay_state.focus_box is not None
    assert window.preview_label.canvas._show_focus_box is False
    window.show_focus_box_check.setChecked(True)
    assert window.preview_label.canvas._show_focus_box is True
    assert window.preview_label.canvas._focus_box == window.preview_overlay_state.focus_box
    assert errors == []


def test_bird_toggle_uses_padded_preview_coordinates(preview_window, monkeypatch):
    window, source, errors = preview_window
    window.show_bird_box_check.setChecked(False)
    window._show_placeholder_preview()
    window.current_source_image.close()
    window.current_source_image = Image.new("RGB", (300, 200), "black")
    settings = window._build_current_render_settings()
    settings.update(ratio=None, center_mode="custom", crop_box=(-0.25, -0.125, 1, 1.125),
                    draw_text=False, draw_banner=False, draw_focus=False)
    monkeypatch.setattr(window, "_render_settings_for_path", lambda *a, **k: dict(settings))
    window.render_preview()
    crop_before = window.preview_overlay_state.crop_effect_box
    window._bird_box_cache[window._source_signature(source)] = BIRD_BOX
    window.show_bird_box_check.setChecked(True)
    pt, pb, pl, pr = window._preview_outer_pad
    assert (pt, pb, pl, pr) == (25, 25, 75, 0)
    assert window.preview_overlay_state.bird_box == pytest.approx((0.64, 0.34, 0.84, 0.7))
    assert window.preview_overlay_state.focus_box == pytest.approx((0.4, 0.38, 0.48, 0.46))
    assert window.preview_overlay_state.crop_effect_box == crop_before
    assert errors == []
