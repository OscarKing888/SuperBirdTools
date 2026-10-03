"""RAW source selection, independent viewport modes and late worker rejection."""
import io
import os
import sys
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PIL import Image

from SuperViewer.superviewer import preview_panel as preview
from SuperViewer.superviewer.qt_compat import QPixmap
from SuperViewer.tests.test_directory_selection_responsiveness import window, _APP, _wait_until


def _image(width=120, height=80, color="red"):
    return preview._qimage_from_pil_image(Image.new("RGB", (width, height), color))


def _jpeg(size=(1600, 900), orientation=1):
    output = io.BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    Image.new("RGB", size, "red").save(output, "JPEG", exif=exif)
    return output.getvalue()


@pytest.mark.parametrize("data,expected", [
    (_jpeg(orientation=6), (900, 1600)),
    (_jpeg((1599, 900)), (120, 80)),
    (b"broken jpeg", (120, 80)),
    (None, (120, 80)),
])
def test_default_raw_source_threshold_orientation_and_fallback(tmp_path, monkeypatch, data, expected):
    path = tmp_path / "照片.ARW"
    path.touch()
    monkeypatch.setattr(preview.thumb_stream, "get_raw_preview_jpeg", lambda path: data)
    calls = []
    monkeypatch.setattr(preview, "_load_sensor_raw_qimage", lambda path: calls.append(path) or _image())
    image = preview._load_full_preview_qimage(str(path))
    assert (image.width(), image.height()) == expected
    assert bool(calls) == (expected == (120, 80))
    assert image.pixelColor(0, 0).red() > 240


def test_rawpy_decode_uses_camera_wb_and_closes_windows_unicode_stream(tmp_path, monkeypatch):
    path = tmp_path / "中文.ARW"
    path.touch()
    sources = []
    closed = []

    class Raw:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def postprocess(self, **kwargs):
            assert kwargs == dict(use_camera_wb=True, no_auto_bright=False, output_bps=8)
            return np.full((60, 100, 3), (10, 20, 30), dtype=np.uint8)

    monkeypatch.setattr(preview.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "rawpy", SimpleNamespace(imread=lambda source: sources.append(source) or Raw()))
    image = preview._load_sensor_raw_qimage(str(path))
    assert (image.width(), image.height()) == (100, 60)
    assert image.pixelColor(0, 0).getRgb() == (10, 20, 30, 255)
    assert sources[0].closed and closed == [True]


def test_toggle_cancels_late_decode_and_held_navigation_upgrades_final_frame(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("first.ARW", "last.ARW")]
    for path in paths:
        path.touch()
    started, release = threading.Event(), threading.Event()
    calls = []
    gui_thread = threading.get_ident()

    def decode_embedded(path):
        calls.append(("embedded", path, threading.get_ident()))
        started.set()
        assert release.wait(4)
        return _image(1600, 900)

    def decode_raw(path):
        calls.append(("raw", path, threading.get_ident()))
        return _image(3000, 2000, "blue")

    monkeypatch.setattr(preview, "_load_full_preview_qimage", decode_embedded)
    monkeypatch.setattr(preview, "_load_sensor_raw_qimage", decode_raw)
    panel = preview.PreviewPanel()
    quick = QPixmap.fromImage(_image(256, 160, "green"))
    panel.set_quick_preview_provider(lambda path, size: quick)
    ready = []
    panel.full_preview_ready.connect(ready.append)
    try:
        panel.set_image(str(paths[0]), quick_size=256)
        _wait_until(started.is_set)
        old_token = panel._preview_request_token
        panel.set_show_raw(True)
        assert panel._preview_request_token > old_token
        panel._on_full_preview_loaded(old_token, str(paths[0]), _image(), 0)
        assert panel.get_preview_image_size() == (256, 160)
        assert not panel._full_preview_loaded
        panel.set_quick_pixmap(str(paths[1]), quick, quick_size=256)
        release.set()
        _wait_until(lambda: panel._full_preview_loader is None)
        panel.set_show_raw(False)
        panel.set_show_raw(True)
        assert not panel._full_preview_timer.isActive()
        assert [c[0] for c in calls] == ["embedded"]
        assert ready == []
        panel.set_image(str(paths[1]), quick_size=256)
        _wait_until(lambda: panel._full_preview_loaded)
        assert panel.get_preview_image_size() == (3000, 2000)
        assert ready == [str(paths[1])]
        assert all(c[2] != gui_thread for c in calls)
        panel.set_show_raw(False)
        _wait_until(lambda: panel._full_preview_loaded)
        assert panel.get_preview_image_size() == (1600, 900)
        assert [c[0] for c in calls] == ["embedded", "raw", "embedded"]
    finally:
        release.set()
        panel.shutdown()
        panel.close()


def test_ab_toolbar_raw_visibility_and_session_state(window, tmp_path, monkeypatch):
    # window fixture redirects configuration/cache before constructing MainWindow.
    from SuperViewer.superviewer import preview_panel as live_preview
    monkeypatch.setattr(live_preview, "_load_full_preview_qimage_raw", lambda path: _image(1600, 900))
    monkeypatch.setattr(live_preview, "_load_sensor_raw_qimage", lambda path: _image(3000, 2000, "blue"))
    raw = tmp_path / "source.ARW"
    raw.touch()
    jpg = tmp_path / "image.jpg"
    Image.new("RGB", (96, 72)).save(jpg)
    ab = window.ab_preview
    window.show()
    assert ab.a_panel.raw_toggle.isHidden() and ab.b_panel.raw_toggle.isHidden()
    window.preview_panel.set_image(str(raw), quick_size=256)
    assert ab.b_panel.raw_toggle.isVisible()
    assert not ab.b_panel.raw_toggle.isChecked()
    ab.b_panel.raw_toggle.click()
    _wait_until(lambda: window.preview_panel._full_preview_loaded)
    assert window.preview_panel.get_preview_image_size() == (3000, 2000)
    ab.enabled.setChecked(True)
    window.preview_a.set_image(str(raw), quick_size=256)
    _wait_until(lambda: window.preview_a._full_preview_loaded)
    assert ab.a_panel.raw_toggle.isVisible() and not ab.a_panel.raw_toggle.isChecked()
    assert window.preview_a.get_preview_image_size() == (1600, 900)
    assert window.preview_panel.show_raw()
    assert ab.a_panel.toolbar.height() == ab.b_panel.toolbar.height()
    window.preview_panel.set_image(str(jpg))
    # 非 RAW 也能查看降噪成片；记住 RAW 偏好，但此时按钮显示默认预览。
    assert ab.b_panel.source_button.isVisible() and not ab.b_panel.source_button.isChecked()
    assert ab.b_panel.source_button.text() == "默认预览" and window.preview_panel.show_raw()
    window.preview_panel.set_image(str(raw), load_full=False, quick_size=256)
    assert ab.b_panel.raw_toggle.isVisible() and ab.b_panel.raw_toggle.isChecked()
    assert not window.preview_panel._full_preview_timer.isActive()
    video = tmp_path / "video.mp4"
    video.touch()
    window.preview_panel.set_image(str(video), load_full=False)
    assert ab.b_panel.raw_toggle.isHidden() and window.preview_panel.show_raw()
    window.preview_panel.clear_image()
    assert ab.b_panel.raw_toggle.isHidden()
    assert window.preview_panel.show_raw()


def test_raw_toggle_does_not_change_overlay_export_or_viewport(tmp_path, monkeypatch):
    raw = tmp_path / "source.ARW"
    raw.touch()
    monkeypatch.setattr(preview, "_load_full_preview_qimage_raw", lambda path: _image(1600, 900))
    panel = preview.PreviewPanel()
    try:
        panel.set_show_raw(True)
        panel.set_quick_pixmap(str(raw), QPixmap.fromImage(_image(256, 160, "blue")))
        panel.set_composition_grid_mode("thirds")
        original = panel.canvas._source_pixmap
        token = panel._preview_request_token
        rendered = panel.render_source_pixmap_with_overlays().toImage()
        assert (rendered.width(), rendered.height()) == (1600, 900)
        assert rendered.pixelColor(0, 0).red() == 255
        assert rendered.pixelColor(533, 450) != rendered.pixelColor(500, 450)
        assert panel.canvas._source_pixmap is original
        assert panel._preview_request_token == token
        assert panel.show_raw() and not panel._full_preview_loaded
        output = tmp_path / "export.png"
        assert panel.save_source_pixmap_with_overlays(str(output))
        with Image.open(output) as image:
            assert image.size == (1600, 900)
    finally:
        panel.shutdown()
        panel.close()


def test_failed_raw_worker_keeps_quick_preview_and_allows_toggle_back(tmp_path, monkeypatch):
    raw = tmp_path / "broken.ARW"
    raw.touch()
    monkeypatch.setattr(preview, "_load_sensor_raw_qimage", lambda path: None)
    monkeypatch.setattr(preview, "_load_full_preview_qimage", lambda path: _image(1600, 900))
    panel = preview.PreviewPanel()
    quick = QPixmap.fromImage(_image(256, 160))
    panel.set_quick_preview_provider(lambda path, size: quick)
    try:
        assert not panel.show_raw()  # 新窗口不继承另一侧/前次会话的状态。
        panel.set_show_raw(True)
        panel.set_image(str(raw), quick_size=256)
        _wait_until(lambda: "RAW 解码失败" in panel._preview_status_label.text())
        assert not panel._full_preview_loaded
        assert panel.source_pixmap_for_path(str(raw)) is None
        assert panel.get_preview_image_size() == (256, 160)
        panel.set_show_raw(False)
        _wait_until(lambda: panel._full_preview_loaded)
        assert panel.get_preview_image_size() == (1600, 900)
    finally:
        panel.shutdown()
        panel.close()
