from __future__ import annotations

import importlib
import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from SuperViewer.superviewer import paths_settings, preview_panel
from SuperViewer.superviewer.qt_compat import QImage, QPixmap
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


# Keep Qt alive across this whole test process, including other test modules.
_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.003)
    assert predicate(), "background work did not finish"


@pytest.fixture
def window(tmp_path, monkeypatch):
    main = importlib.import_module("SuperViewer.main")
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / paths_settings.CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(settings / "state"))
    monkeypatch.setattr(main, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(settings))
    monkeypatch.setenv("LOCALAPPDATA", str(settings / "cache"))
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("飞行\n", encoding="utf-8")
    monkeypatch.setattr(main, "SuperViewerTaggedFileListPanel", lambda: SuperViewerTaggedFileListPanel(tag_config_path=cfg))
    result = main.MainWindow(initial_received_files=["skip-restore"])
    # Exercise the actual selection callback without starting directory I/O.
    monkeypatch.setattr(result, "_sync_directory_browser_to_file_selection", lambda path: None)
    try:
        yield result
    finally:
        result.close()
        _wait_until(lambda: result._shutdown_finalized)
        result.deleteLater()
        _APP.processEvents()


def _image(width=64, height=48):
    result = QImage(width, height, preview_panel._qimage_rgb888_format())
    result.fill(80)
    return result


def test_hif_selection_does_not_wait_for_metadata_or_decode_on_gui(window, tmp_path, monkeypatch):
    photo = tmp_path / "大图.HIF"
    photo.write_bytes(b"heif header placeholder")
    path = os.path.normpath(str(photo))
    file_list = window._file_list
    file_list._all_files = [path]
    file_list._selected_display_path = path
    file_list._meta_cache[path] = {"rating": 3}
    slow_reads = []
    quick_decodes = []
    started = threading.Event()
    release = threading.Event()
    decoder_threads = []

    def forbidden_metadata_read(source):
        slow_reads.append(source)
        # Emulate waiting behind an in-flight ExifTool batch. The selection
        # must never enter this path, even with incomplete browser metadata.
        release.wait(0.5)
        return {}

    def decode(source):
        decoder_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return _image()

    monkeypatch.setattr(file_list._meta_proxy, "read", forbidden_metadata_read)
    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 50_000_000)
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *args: quick_decodes.append(args))
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", decode)
    monkeypatch.setattr(file_list, "get_report_row_for_path", lambda path: {"camera_model": "Alpha 1"})
    ready = []
    window.preview_panel.full_preview_ready.connect(ready.append)
    heartbeat = []
    try:
        start = time.monotonic()
        window._on_file_selected_from_list(path)
        assert time.monotonic() - start < 0.4
        assert slow_reads == []
        assert quick_decodes == []
        assert window.preview_panel.get_preview_image_size() is None
        assert window.image_info_panel.basic_rows["评分"].text() == "★★★☆☆"
        assert file_list.cached_photo_metadata_for_path(path)["camera_model"] == "Alpha 1"
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        _wait_until(lambda: started.is_set() and heartbeat)
        assert len(decoder_threads) == 1
        assert decoder_threads[0] != threading.get_ident()
        assert not ready
        release.set()
        _wait_until(lambda: window.preview_panel._full_preview_loader is None and ready)
        assert ready == [path]
        assert window.preview_panel.get_preview_image_size() == (64, 48)
        assert window.image_info_panel.basic_rows["尺寸"].text() == "64 × 48"
    finally:
        release.set()


def test_background_metadata_updates_fields_without_overwriting_drafts(window, tmp_path, monkeypatch):
    photo = tmp_path / "选中.png"
    Image.new("RGB", (24, 18)).save(photo)
    path = os.path.normpath(str(photo))
    panel = window._file_list
    panel._all_files = [path]
    panel._meta_cache[path] = {"comment": "已保存", "rating": 1}
    window._on_file_selected_from_list(path)
    info = window.image_info_panel
    info.comment_edit.setPlainText("尚未保存的新备注")
    info.filename_edit.setText("待改文件名")
    forbidden = []
    monkeypatch.setattr(info, "_load_preview", lambda path: forbidden.append("preview"))
    monkeypatch.setattr(window, "_update_preview_focus_box", lambda path: forbidden.append("focus"))
    monkeypatch.setattr(panel, "_enqueue_meta_apply", lambda batch: None)
    panel._on_metadata_batch_ready({path: {"comment": "后台读取备注", "rating": 4, "ISO": 640}})
    assert info.comment_edit.toPlainText() == "尚未保存的新备注"
    assert info.filename_edit.text() == "待改文件名"
    assert info._current_comment == "后台读取备注"
    assert info.basic_rows["评分"].text() == "★★★★☆"
    assert info.basic_rows["ISO"].text() == "640"
    assert forbidden == []
    # With no draft, later metadata can fill the initially blank comment.
    info.comment_edit.setPlainText(info._current_comment)
    panel._on_metadata_batch_ready({path: {"comment": "完整备注", "rating": 5}})
    assert info.comment_edit.toPlainText() == "完整备注"
    cursor = info.comment_edit.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(3, cursor.MoveMode.KeepAnchor)
    info.comment_edit.setTextCursor(cursor)
    panel._on_metadata_batch_ready({path: {"comment": "完整备注", "rating": 5}})
    window._on_full_preview_ready(path)
    retained = info.comment_edit.textCursor()
    assert (retained.position(), retained.anchor()) == (3, 1)
    assert retained.selectedText() == "整备"


def test_async_selection_refresh_ignores_playback_other_files_and_shutdown(window, tmp_path, monkeypatch):
    path = os.path.normpath(str(tmp_path / "current.HIF"))
    other = os.path.normpath(str(tmp_path / "other.HIF"))
    window._current_exif_path = path
    monkeypatch.setattr(window.image_info_panel, "current_photo_path", lambda: path)
    refreshes = []
    monkeypatch.setattr(window.image_info_panel, "refresh_metadata_fields", lambda: refreshes.append("info"))
    monkeypatch.setattr(window, "_update_preview_focus_box", lambda path: refreshes.append("focus"))
    window._on_photo_metadata_cache_updated([other])
    window._on_full_preview_ready(other)
    window._file_list._key_navigation_playback_active = True
    window._on_photo_metadata_cache_updated([path])
    window._on_full_preview_ready(path)
    assert refreshes == []
    window._file_list._key_navigation_playback_active = False
    window._on_photo_metadata_cache_updated([path])
    window._on_full_preview_ready(path)
    assert refreshes == ["info", "focus", "info"]
    window._shutdown_requested = True
    window._on_photo_metadata_cache_updated([path])
    window._on_full_preview_ready(path)
    assert refreshes == ["info", "focus", "info"]
    window._shutdown_requested = False


def test_exact_tier_quick_cache_prefers_memory_and_never_falls_back_to_original(window, tmp_path, monkeypatch):
    panel = window._file_list
    panel._thumb_size = 512
    memory = QPixmap(512, 341)
    memory.fill()
    resolver_calls = []
    cache = tmp_path / "cached.png"
    Image.new("RGB", (500, 330)).save(cache)
    monkeypatch.setattr(panel, "_current_thumbnail_fast_preview_pixmap", lambda path: memory)
    monkeypatch.setattr(panel, "_resolve_existing_sized_preview_image_path", lambda path, **kwargs: resolver_calls.append(kwargs) or str(cache))
    assert panel.cached_quick_preview_for_path("source.HIF", 512).cacheKey() == memory.cacheKey()
    assert resolver_calls == []
    assert panel.cached_quick_preview_for_path("source.HIF", 1024) is None
    monkeypatch.setattr(panel, "_current_thumbnail_fast_preview_pixmap", lambda path: None)
    cached = panel.cached_quick_preview_for_path("source.HIF", 512)
    assert (cached.width(), cached.height()) == (500, 330)
    assert resolver_calls == [{"exact_size_only": True}]
    monkeypatch.setattr(panel, "_resolve_existing_sized_preview_image_path", lambda *args, **kwargs: None)
    assert panel.cached_quick_preview_for_path("source.HIF", 512) is None


@pytest.mark.parametrize("load_full", [True, False])
def test_large_hif_reuses_cached_quick_preview_without_source_decode(tmp_path, monkeypatch, load_full):
    photo = tmp_path / "cached.HIF"
    photo.write_bytes(b"placeholder")
    quick = QPixmap(512, 341)
    quick.fill()
    calls = []
    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 50_000_000)
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda path: calls.append("full"))
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *args: calls.append("quick"))
    panel = preview_panel.PreviewPanel()
    panel.set_quick_preview_provider(lambda path, size: quick)
    try:
        panel.set_image(str(photo), load_full=load_full, quick_size=512)
        assert panel.get_preview_image_size() == (512, 341)
        assert calls == []
        assert panel._full_preview_timer.isActive() is load_full
    finally:
        panel.shutdown()
        panel.close()


def test_small_hif_keeps_synchronous_full_preview(tmp_path, monkeypatch):
    photo = tmp_path / "small.HIF"
    photo.write_bytes(b"placeholder")
    calls = []
    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 21_000_000)
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda path: calls.append(path) or _image())
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_image(str(photo))
        assert calls == [str(photo)]
        assert panel._full_preview_loaded
        assert not panel._full_preview_timer.isActive()
    finally:
        panel.shutdown()
        panel.close()


def test_failed_async_hif_decode_replaces_loading_message(tmp_path, monkeypatch):
    photo = tmp_path / "damaged.HIF"
    photo.write_bytes(b"damaged")
    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 50_000_000)
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda path: None)
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_image(str(photo))
        assert "正在加载" in panel._canvas.text()
        _wait_until(lambda: not panel._full_preview_timer.isActive() and panel._full_preview_loader is None)
        assert "无法预览" in panel._canvas.text()
        assert not panel._full_preview_loaded
    finally:
        panel.shutdown()
        panel.close()


def test_preview_owns_finished_worker_until_queued_finished_is_handled(tmp_path, monkeypatch):
    photos = [tmp_path / name for name in ("a.HIF", "b.HIF", "c.HIF")]
    for photo in photos:
        photo.write_bytes(b"placeholder")
    decoded = []
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda path: decoded.append(Path(path).name) or _image())
    panel = preview_panel.PreviewPanel()
    try:
        panel._current_path = str(photos[0])
        panel._preview_request_token = 1
        panel._start_full_preview_loader()
        first = panel._full_preview_loader
        assert first.wait(1000)
        # Native completion alone cannot release ownership: its queued Qt slot
        # has not run, and must not consume the next worker's pending request.
        for token, photo in enumerate(photos[1:], 2):
            panel._current_path = str(photo)
            panel._preview_request_token = token
            panel._start_full_preview_loader()
            assert panel._full_preview_loader is first
        assert decoded == ["a.HIF"]
        _wait_until(lambda: panel._full_preview_loader is None and "c.HIF" in decoded)
        assert decoded == ["a.HIF", "c.HIF"]
        assert panel.current_path() == str(photos[2])
        assert panel._full_preview_loaded
    finally:
        panel.shutdown()
        panel.close()


def test_window_close_waits_for_canceled_directory_scan_finished_slot(window, tmp_path, monkeypatch):
    from app_common.file_browser._workers import DirectoryScanWorker

    started = threading.Event()
    release = threading.Event()

    def blocking_scan(worker):
        started.set()
        release.wait(3)

    monkeypatch.setattr(DirectoryScanWorker, "run", blocking_scan)
    window._file_list.load_directory(str(tmp_path))
    worker = window._file_list._directory_scan_worker
    assert started.wait(1)
    try:
        start = time.monotonic()
        window.close()
        assert time.monotonic() - start < 0.5
        assert window._shutdown_requested
        assert not window._shutdown_finalized
        assert worker.isInterruptionRequested()
        assert window._file_list.has_pending_directory_scans()

        release.set()
        assert worker.wait(1000)
        # The queued finished callback must run before the window may destroy
        # the scan owner, even though the native thread has already exited.
        assert window._file_list.has_pending_directory_scans()
        assert not window._shutdown_finalized
        _wait_until(lambda: window._shutdown_finalized)
        assert not window._file_list.has_pending_directory_scans()
    finally:
        release.set()
