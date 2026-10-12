"""Capture fields stay in the original status label and follow each source."""
import os

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from app_common.file_browser._browser_core import _set_cached_actual_path
from SuperViewer.tests.test_directory_selection_responsiveness import window
from SuperViewer.superviewer.preview_photo_info import photo_capture_info_fields

_APP = QApplication.instance() or QApplication([])


def _photo(tmp_path, name):
    path = str(tmp_path / name)
    Image.new("RGB", (96, 72), "gray").save(path)
    return path


def _status(preview):
    return preview._preview_status_label.text()


def test_formatting_reuses_xmp_exif_and_report_aliases():
    assert dict(photo_capture_info_fields({
        "ExposureTime": "1/2000", "FNumber": 5.6, "ISO": 640,
        "FocalLength": 600, "LensModel": "FE 200-600mm F5.6-6.3 G OSS",
        "Model": "ILCE-1", "XMP-exif:ExposureTime": "1/1000",
    })) == {
        "快门": "1/1000s", "光圈": "f/5.6", "ISO": "640", "焦距": "600mm",
        "镜头型号": "FE 200-600mm F5.6-6.3 G OSS", "机器型号": "ILCE-1",
    }
    assert dict(photo_capture_info_fields({"XMP-superpicky:camera_model": "中文机型"}))["机器型号"] == "中文机型"
    assert all(value == "—" for _, value in photo_capture_info_fields({}))


def test_single_and_ab_statuses_keep_independent_photos(window, tmp_path, monkeypatch):
    first = _photo(tmp_path, "甲.jpg")
    second = _photo(tmp_path, "乙.jpg")
    files = window._file_list
    files._meta_cache[first] = {"ISO": 100, "Model": "相机甲", "LensModel": "镜头甲"}
    files._meta_cache[second] = {"ISO": 3200, "Model": "相机乙", "LensModel": "镜头乙"}
    monkeypatch.setattr(window, "_update_preview_focus_box", lambda *args, **kwargs: None)
    original_label = window.preview_panel._preview_status_label
    window._on_file_selected_from_list(first)
    assert "当前预览分辨率: 96x72" in _status(window.preview_panel)
    assert "ISO：100" in _status(window.preview_panel)
    window.ab_preview.enabled.setChecked(True)
    window.ab_preview.activate("a")
    window._on_file_selected_from_list(second)
    assert "相机乙" in _status(window.preview_a)
    assert "相机甲" in _status(window.preview_panel)
    window.ab_preview.activate("b")
    assert "相机乙" in _status(window.preview_a)
    assert "相机甲" in _status(window.preview_panel)
    window.preview_panel.set_display_scale_percent(200)
    assert "当前缩放: 200%" in _status(window.preview_panel)
    assert "镜头甲" in _status(window.preview_panel)
    assert window.preview_panel._preview_status_label is original_label
    before = _status(window.preview_panel)

    def forbidden(*args, **kwargs):
        raise AssertionError("theme updates must not reload metadata or pixels")

    monkeypatch.setattr(files, "cached_preview_metadata_for_path", forbidden)
    monkeypatch.setattr(window.preview_panel, "set_image", forbidden)
    for scheme in ("dark", "light"):
        window._apply_ui_theme(scheme)
        _APP.processEvents()
        label = window.preview_panel._preview_status_label
        foreground = label.palette().color(label.foregroundRole())
        assert (foreground.lightness() > 128) == (scheme == "dark")
        assert _status(window.preview_panel) == before


def test_late_metadata_updates_inactive_side_and_never_borrows_selection(window, tmp_path, monkeypatch):
    first = _photo(tmp_path, "尚无元数据.jpg")
    second = _photo(tmp_path, "有元数据.jpg")
    files = window._file_list
    files._meta_cache[second] = {"ISO": 800, "Model": "另一台相机"}
    files._selected_display_path = second
    monkeypatch.setattr(window, "_update_preview_focus_box", lambda *args, **kwargs: None)
    window._on_file_selected_from_list(first)
    assert "ISO：—" in _status(window.preview_panel)
    assert "另一台相机" not in _status(window.preview_panel)
    window.ab_preview.enabled.setChecked(True)
    window.ab_preview.activate("a")
    window._on_file_selected_from_list(second)
    before = _status(window.preview_a)
    files._on_metadata_batch_ready({first: {"ISO": 200, "Model": "后台相机"}})
    assert "ISO：200" in _status(window.preview_panel)
    assert "后台相机" in _status(window.preview_panel)
    assert _status(window.preview_a) == before
    files._on_metadata_batch_ready({str(tmp_path / "过期.jpg"): {"ISO": 9999}})
    assert "9999" not in _status(window.preview_panel)
    window.preview_a.clear_image()
    assert "ISO" not in _status(window.preview_a)
    window.ab_preview.enabled.setChecked(False)
    assert "后台相机" in _status(window.preview_panel)


@pytest.mark.parametrize("memory_frame", [False, True])
def test_fast_frames_read_only_displayed_source_cache(window, tmp_path, monkeypatch, memory_frame):
    source = str(tmp_path / "源照片.ARW")
    files = window._file_list
    files._selected_display_path = source
    files._meta_cache[source] = {"ISO": 400, "Model": "RAW 相机"}
    files._key_navigation_playback_active = True
    quick = QPixmap(128, 96)
    quick.fill(Qt.GlobalColor.gray)
    window.preview_panel.set_quick_preview_provider(lambda *args: quick)

    def forbidden(*args, **kwargs):
        raise AssertionError("status refresh must not read EXIF or probe the original")

    monkeypatch.setattr(files._meta_proxy, "read", forbidden)
    monkeypatch.setattr(os.path, "isfile", forbidden)
    if memory_frame:
        window._on_file_fast_preview_pixmap_requested(source, quick, 128)
    else:
        window._on_file_fast_preview_requested("cache/hashed-128.jpg")
    assert "RAW 相机" in _status(window.preview_panel)
    assert "ISO：400" in _status(window.preview_panel)
    assert not window.preview_panel._full_preview_timer.isActive()
    files._selected_display_path = str(tmp_path / "尚未显示的下一张.ARW")
    files._on_metadata_batch_ready({source: {"ISO": 1600, "Model": "RAW 相机"}})
    assert "ISO：1600" in _status(window.preview_panel)


def test_report_alias_cache_updates_the_actual_source(window, tmp_path, monkeypatch):
    actual = _photo(tmp_path, "实际.jpg")
    listed = str(tmp_path / "旧路径" / "实际.jpg")
    files = window._file_list
    _set_cached_actual_path(listed, actual)
    row = {"current_path": listed, "original_path": listed, "camera_model": "报告相机"}
    files._report_row_by_path[actual] = row
    files._meta_cache[listed] = {"ISO": 100, "camera_model": "缓存相机"}
    monkeypatch.setattr(window, "_update_preview_focus_box", lambda *args, **kwargs: None)
    window._on_file_selected_from_list(actual)
    assert "缓存相机" in _status(window.preview_panel)
    files._selected_display_path = "其他照片.jpg"
    files._on_metadata_batch_ready({listed: {"ISO": 500, "camera_model": "已补全相机"}})
    assert "ISO：500" in _status(window.preview_panel)
    assert "已补全相机" in _status(window.preview_panel)


def test_video_and_decode_errors_keep_status_consistent(window, tmp_path, monkeypatch):
    source = _photo(tmp_path, "解码.jpg")
    monkeypatch.setattr(window, "_update_preview_focus_box", lambda *args, **kwargs: None)
    window._on_file_selected_from_list(source)
    preview = window.preview_panel
    preview._preview_source_mode = "denoised"
    preview._on_full_preview_loaded(preview._preview_request_token, source, None, 1)
    window._file_list._on_metadata_batch_ready({source: {"ISO": 640}})
    assert "降噪成片与原图均无法预览" in _status(preview)
    assert "ISO：640" in _status(preview)
    # Fast video selection neither probes nor retains photo capture values.
    preview.set_image(str(tmp_path / "视频.mp4"), load_full=False)
    assert preview._preview_status_label.isHidden()
    assert "ISO" not in _status(preview)
