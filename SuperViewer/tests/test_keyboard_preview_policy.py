"""Keyboard steps use the selected source limit before presenting thumbnails."""
import threading

import pytest
from PIL import Image
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QImage, QKeyEvent, QPixmap
from PyQt6.QtTest import QTest

from app_common import superviewer_user_options as options
from SuperViewer.superviewer import preview_panel
from SuperViewer.tests.test_preview_info_sync import window, _APP
from SuperViewer.tests.test_ab_preview import _wait_until


@pytest.fixture(autouse=True)
def isolate_options(monkeypatch):
    monkeypatch.setattr(options, "_RUNTIME_OPTIONS", options.normalize_user_options(None))


def quick_pixmap():
    pixmap = QPixmap(40, 30)
    pixmap.fill()
    return pixmap


@pytest.mark.parametrize("view_mode", ["list", "thumb"])
@pytest.mark.parametrize("condition", ["dimensions", "file_size"])
@pytest.mark.parametrize("cached", [False, True])
def test_physical_repeat_and_release_skip_small_frames(window, tmp_path, monkeypatch, view_mode, condition, cached):
    paths = [str(tmp_path / f"图片{i}.jpg") for i in range(4)]
    for path in paths:
        Image.new("RGB", (120, 90)).save(path)
    files = window._file_list
    files._all_files = paths
    files._set_view_mode(files._MODE_LIST if view_mode == "list" else files._MODE_THUMB)
    files._rebuild_views()
    window.resize(1700, 800)
    window.show()
    window.activateWindow()
    _APP.processEvents()
    window._on_file_selected_from_list(paths[0])
    ab = window.preview_compare
    ab.set_enabled(True)
    ab.set_active_side("A")
    files.select_display_path_silently(paths[0])
    panel = ab.active_preview
    QTest.mouseClick(panel.canvas, Qt.MouseButton.LeftButton)
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_WIDTH: 120 if condition == "dimensions" else 10,
                                       options.KEY_DIRECT_PREVIEW_MAX_HEIGHT: 90 if condition == "dimensions" else 10,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 0 if condition == "dimensions" else 1})
    pixmap = quick_pixmap() if cached else None
    monkeypatch.setattr(files, "_current_thumbnail_fast_preview_pixmap", lambda path: pixmap)
    priorities = []
    monkeypatch.setattr(files, "_prioritize_fast_preview_thumbnail", priorities.append)
    monkeypatch.setattr(files, "resolve_preview_path", lambda *a, **k: pytest.fail("navigation used derived path"))
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda *a: pytest.fail("eligible image read small disk frame"))
    decoded, painted, committed = [], [], []
    decode = preview_panel._load_full_preview_qimage
    def record_decode(path):
        decoded.append(path)
        return decode(path)
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", record_decode)
    paint = panel._set_canvas_pixmap
    def record_paint(pixmap, **kwargs):
        painted.append((pixmap.width(), pixmap.height()))
        return paint(pixmap, **kwargs)
    monkeypatch.setattr(panel, "_set_canvas_pixmap", record_paint)
    files.file_selected.connect(committed.append)
    key = Qt.Key.Key_Down if view_mode == "list" else Qt.Key.Key_Right
    def send(kind, repeat=False):
        _APP.sendEvent(panel.canvas, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, "", repeat))
    send(QEvent.Type.KeyPress)
    assert panel.current_path() == paths[1] and panel._full_preview_loaded
    send(QEvent.Type.KeyPress, True)
    assert panel.current_path() == paths[2] and panel._full_preview_loaded
    files._on_key_navigation_playback_tick()
    assert panel.current_path() == paths[3] and panel._full_preview_loaded
    assert committed == []
    assert window._current_exif_path == paths[0]
    # The background thumbnail can arrive while the key is still held.
    files._emit_fast_preview_for_path(paths[3])
    assert panel.get_preview_image_size() == (120, 90)
    send(QEvent.Type.KeyRelease)
    assert not files._key_navigation_playback_active
    assert committed == [paths[3]]
    assert window._current_exif_path == paths[3]
    assert decoded == paths[1:]
    assert painted == [(120, 90)] * 3
    assert ab.path_for_side("B") == paths[0]
    if view_mode == "list":
        assert priorities == []


@pytest.mark.parametrize("width,height,file_mb,large_file", [(39, 30, 0, False), (40, 29, 0, False), (10, 10, 1, True), (0, 0, 0, False)])
def test_over_limit_keyboard_frame_stays_quick_until_commit(window, tmp_path, monkeypatch, width, height, file_mb, large_file):
    photo = tmp_path / "source.jpg"
    Image.new("RGB", (40, 30)).save(photo)
    if large_file:
        with photo.open("ab") as output:
            output.write(b"\0" * (1024 * 1024 + 1 - photo.stat().st_size))
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_WIDTH: width,
                                       options.KEY_DIRECT_PREVIEW_MAX_HEIGHT: height,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: file_mb})
    quick = QPixmap(16, 12)
    quick.fill()
    files = window._file_list
    monkeypatch.setattr(files, "_current_thumbnail_fast_preview_pixmap", lambda path: quick)
    with monkeypatch.context() as patch:
        patch.setattr(preview_panel, "_load_full_preview_qimage", lambda path: pytest.fail("over-limit keyboard decode"))
        files._emit_fast_preview_for_path(str(photo))
        panel = window.preview_compare.active_preview
        assert panel.current_path() == str(photo)
        assert panel.get_preview_image_size() == (16, 12)
        assert not panel._full_preview_loaded
        assert not panel._full_preview_timer.isActive() and panel._full_preview_loader is None
    window._on_file_selected_from_list(str(photo))
    _wait_until(lambda: panel._full_preview_loaded and panel._full_preview_loader is None)
    assert panel.get_preview_image_size() == (40, 30)


@pytest.mark.parametrize("extension", ["jpg", "HIF", "ARW"])
def test_disabled_or_raw_cache_miss_never_decodes_while_held(window, tmp_path, monkeypatch, extension):
    photo = tmp_path / ("source." + extension)
    photo.write_bytes(b"source")
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_WIDTH: 0,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 32 if extension == "ARW" else 0})
    files = window._file_list
    files._set_view_mode(files._MODE_LIST)
    monkeypatch.setattr(files, "_current_thumbnail_fast_preview_pixmap", lambda path: None)
    monkeypatch.setattr(files, "_prioritize_fast_preview_thumbnail", lambda path: pytest.fail("list started thumbnails"))
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda *a: None)
    for name in ("_load_full_preview_qimage", "_load_thumbnail_image", "_load_quick_preview_pixmap"):
        monkeypatch.setattr(preview_panel, name, lambda *a: pytest.fail("held cache miss decoded source"))
    files._emit_fast_preview_for_path(str(photo))
    panel = window.preview_compare.active_preview
    assert panel.current_path() == str(photo)
    assert panel.get_preview_image_size() is None
    assert not panel._full_preview_timer.isActive() and panel._full_preview_loader is None


def test_disk_fallback_keeps_source_identity_and_rechecks_live_setting(window, tmp_path, monkeypatch):
    photo = tmp_path / "source.jpg"
    Image.new("RGB", (40, 30)).save(photo)
    files = window._file_list
    files._set_view_mode(files._MODE_LIST)
    monkeypatch.setattr(files, "_current_thumbnail_fast_preview_pixmap", lambda path: None)
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_WIDTH: 0,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 0})
    cached = QImage(16, 12, preview_panel._qimage_rgb888_format())
    cached.fill(80)
    probed = []
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda path, *a: probed.append(path) or cached)
    files._emit_fast_preview_for_path(str(photo))
    panel = window.preview_compare.active_preview
    assert panel.current_path() == str(photo) and probed == [str(photo)]
    assert panel.get_preview_image_size() == (16, 12)
    options.apply_runtime_user_options({})
    files._emit_fast_preview_for_path(str(photo))
    assert panel._full_preview_loaded and panel.get_preview_image_size() == (40, 30)


def test_keyboard_selection_retains_decoder_owner_and_rejects_old_result(window, tmp_path, monkeypatch):
    paths = [str(tmp_path / f"{i}.jpg") for i in range(3)]
    for path in paths:
        Image.new("RGB", (40, 30)).save(path)
    entered, release = threading.Event(), threading.Event()
    decoded = []
    def decode(path):
        decoded.append(path)
        if path == paths[0]:
            entered.set()
            assert release.wait(5)
        image = QImage(40, 30, preview_panel._qimage_rgb888_format())
        image.fill(80)
        return image
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", decode)
    panel = window.preview_compare.active_preview
    panel._current_path = paths[0]
    panel._start_full_preview_loader()
    try:
        assert entered.wait(2)
        owner = panel._full_preview_loader
        old_token = panel._preview_request_token
        for path in paths[1:]:
            panel.set_navigation_image(path, quick_pixmap())
            assert panel._full_preview_loader is owner
            assert not panel._full_preview_timer.isActive()
        assert decoded == paths[:1]
        release.set()
        _wait_until(lambda: panel._full_preview_loader is None)
        panel._on_full_preview_loaded(old_token, paths[0], QImage(80, 60, preview_panel._qimage_rgb888_format()), 0)
        assert panel.current_path() == paths[2] and not panel._full_preview_loaded
        window._on_file_selected_from_list(paths[2])
        assert panel._full_preview_loaded and decoded == [paths[0], paths[2]]
    finally:
        release.set()
        _wait_until(lambda: panel._full_preview_loader is None)
