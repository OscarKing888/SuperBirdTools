"""三态来源的后台查找、快切约束、迟到结果和 A/B 状态回归。"""
import threading

from PIL import Image
import pytest

from SuperViewer.superviewer import preview_panel as preview
from SuperViewer.superviewer.qt_compat import QPixmap
from SuperViewer.tests.test_directory_selection_responsiveness import window, _APP, _wait_until
from SuperViewer.tests.test_raw_preview_toggle import _image
from image_denoise.export import _prepare_sidecar


def test_denoised_actual_pixels_keep_source_identity_and_export_default(tmp_path):
    source = tmp_path / "中文.jpg"
    Image.new("RGB", (80, 60), "red").save(source)
    target = tmp_path / "denoised" / "中文_denoised.jpg"
    target.parent.mkdir()
    Image.new("RGB", (80, 60), "blue").save(target)
    _prepare_sidecar(source, target.with_suffix(".xmp"), 80, 60, camera_crop=(.1, .2, .9, .8))
    panel = preview.PreviewPanel()
    ready = []
    panel.full_preview_ready.connect(ready.append)
    try:
        panel.set_preview_source_mode("denoised")
        panel.set_image(str(source), quick_size=256)
        assert not panel._full_preview_loaded
        panel.set_focus_box((.25, .25, .75, .75))
        panel.set_bird_box((.25, .25, .75, .75))
        _wait_until(lambda: panel._full_preview_loaded)
        assert panel.current_path() == str(source) and ready == [str(source)]
        assert panel.source_pixmap_for_path(str(source)).toImage().pixelColor(0, 0).blue() > 240
        assert panel._denoised_display_path == str(target)
        assert panel.canvas._bird_box == pytest.approx((.3, .35, .7, .65))
        assert panel.canvas._focus_box == pytest.approx((.3, .35, .7, .65))
        rendered = panel.render_source_pixmap_with_overlays().toImage()
        assert rendered.pixelColor(0, 0).red() > 240
        assert panel.source_pixmap_for_path(str(source)).toImage().pixelColor(0, 0).blue() > 240
    finally:
        panel.shutdown()
        panel.close()


def test_missing_output_falls_back_and_completed_output_refreshes(tmp_path, monkeypatch):
    source = tmp_path / "plain.jpg"
    Image.new("RGB", (80, 60), "red").save(source)
    panel = preview.PreviewPanel()
    try:
        panel.set_preview_source_mode("denoised")
        panel.set_image(str(source))
        _wait_until(lambda: panel._full_preview_loaded)
        assert "未找到降噪成片" in panel._preview_status_label.text()
        assert panel._denoised_display_path == ""
        target = tmp_path / "denoised" / "plain_denoised.jpg"
        target.parent.mkdir()
        Image.new("RGB", (80, 60), "blue").save(target)
        _prepare_sidecar(source, target.with_suffix(".xmp"), 80, 60)
        panel.refresh_denoised_preview(str(source))
        _wait_until(lambda: panel._full_preview_loaded)
        assert panel._denoised_display_path == str(target)
        assert "显示降噪成片" in panel._preview_status_label.text()
    finally:
        panel.shutdown()
        panel.close()


def test_held_mode_changes_never_lookup_decode_or_load_cache(tmp_path, monkeypatch):
    source = tmp_path / "photo.ARW"
    source.touch()
    panel = preview.PreviewPanel()
    quick = QPixmap.fromImage(_image(256, 160))
    calls = []
    gui = threading.get_ident()
    monkeypatch.setattr(preview, "_load_denoised_preview_qimage",
                        lambda path, **kw: calls.append(threading.get_ident()) or _image(80, 60, "blue"))
    try:
        panel.set_quick_pixmap(str(source), quick, quick_size=256)
        panel.set_quick_preview_provider(lambda *_: pytest.fail("held mode must retain decoded frame"))
        for mode in ("raw", "denoised", "default", "denoised"):
            panel.set_preview_source_mode(mode)
        panel.refresh_denoised_preview(str(source))
        assert not panel._full_preview_timer.isActive() and calls == []
        assert panel.canvas._source_pixmap.cacheKey() == quick.cacheKey()
        panel.set_quick_preview_provider(lambda *_: quick)
        panel.set_image(str(source), quick_size=256)
        _wait_until(lambda: panel._full_preview_loaded)
        assert len(calls) == 1 and calls[0] != gui
    finally:
        panel.shutdown()
        panel.close()


def test_late_denoised_worker_cannot_replace_new_raw_request(tmp_path, monkeypatch):
    source = tmp_path / "photo.ARW"
    source.touch()
    started, release = threading.Event(), threading.Event()

    def decode(path, **kwargs):
        started.set()
        assert release.wait(3)
        return _image(80, 60, "blue")

    monkeypatch.setattr(preview, "_load_denoised_preview_qimage", decode)
    monkeypatch.setattr(preview, "_load_sensor_raw_qimage", lambda path: _image(96, 72, "green"))
    panel = preview.PreviewPanel()
    try:
        panel.set_preview_source_mode("denoised")
        panel.set_image(str(source))
        _wait_until(started.is_set)
        old_token = panel._preview_request_token
        panel.set_preview_source_mode("raw")
        panel._on_full_preview_loaded(old_token, str(source), _image(80, 60), 0)
        assert not panel._full_preview_loaded
        release.set()
        _wait_until(lambda: panel._full_preview_loaded)
        assert panel.preview_source_mode() == "raw"
        assert panel.get_preview_image_size() == (96, 72)
    finally:
        release.set()
        panel.shutdown()
        panel.close()


def test_toolbar_cycles_three_modes_independently_and_skips_raw_for_jpeg(window, tmp_path):
    raw = tmp_path / "photo.ARW"
    raw.touch()
    jpg = tmp_path / "photo.jpg"
    Image.new("RGB", (80, 60)).save(jpg)
    panel = window.preview_panel
    panel.set_quick_pixmap(str(raw), QPixmap.fromImage(_image()))
    button = window.ab_preview.b_panel.source_button
    for mode, label in (("raw", "显示 RAW"), ("denoised", "显示降噪"), ("default", "默认预览")):
        button.click()
        assert panel.preview_source_mode() == mode and button.text() == label
        assert window.preview_a.preview_source_mode() == "default"
    panel.set_quick_pixmap(str(jpg), QPixmap.fromImage(_image()))
    button.click()
    assert panel.preview_source_mode() == "denoised"
    button.click()
    assert panel.preview_source_mode() == "default"
    assert not panel._full_preview_timer.isActive()


def test_playback_start_without_cached_next_frame_blocks_reload_until_commit(tmp_path, monkeypatch):
    source = tmp_path / "retained.jpg"
    Image.new("RGB", (80, 60), "red").save(source)
    calls = []
    monkeypatch.setattr(preview, "_load_denoised_preview_qimage",
                        lambda path, **kw: calls.append(path) or _image(96, 72, "blue"))
    panel = preview.PreviewPanel()
    try:
        panel.set_image(str(source))
        assert panel._full_preview_loaded and not panel._fast_preview_only
        retained = panel.canvas._source_pixmap.cacheKey()
        token = panel._preview_request_token
        # 第一个重复目标未命中缓存，没有 set_quick_pixmap / fast handler。
        panel.set_navigation_playback_active(True)
        panel.set_preview_source_mode("denoised")
        panel.refresh_denoised_preview(str(source))
        panel._start_full_preview_loader()
        panel._on_full_preview_loaded(token, str(source), _image(32, 24), 0)
        assert not panel._full_preview_timer.isActive() and calls == []
        assert panel.canvas._source_pixmap.cacheKey() == retained
        panel.set_navigation_playback_active(False)
        panel.set_image(str(source))
        _wait_until(lambda: panel._full_preview_loaded and not panel._fast_preview_only)
        assert calls == [str(source)] and panel.get_preview_image_size() == (96, 72)
    finally:
        panel.shutdown()
        panel.close()


def test_disk_cache_frame_toolbar_uses_original_raw_identity(window, tmp_path):
    raw = tmp_path / "source.ARW"
    raw.touch()
    cached = tmp_path / "hash.jpg"
    Image.new("RGB", (80, 60)).save(cached)
    panel = window.preview_panel
    panel.set_navigation_playback_active(True)
    panel.set_image(str(cached), load_full=False)
    panel.set_source_identity(str(raw))
    button = window.ab_preview.b_panel.source_button
    button.click()
    assert panel.preview_source_mode() == "raw" and button.text() == "显示 RAW"
    button.click()
    assert panel.preview_source_mode() == "denoised" and button.text() == "显示降噪"
    assert panel.current_path() == str(cached) and not panel._full_preview_timer.isActive()
