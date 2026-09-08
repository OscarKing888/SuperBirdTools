from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from SuperViewer.superviewer import preview_panel
from SuperViewer.superviewer.qt_compat import QApplication, QImage, QTimer


# Qt must outlive every widget/worker, including tests collected from other
# modules. These tests inject readers and use only temporary image paths.
_APP = QApplication.instance() or QApplication([])


def _image(width=96, height=64):
    image = QImage(width, height, preview_panel._qimage_rgb888_format())
    image.fill(80)
    return image


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.003)
    assert predicate(), "HEIF preview worker did not settle"


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.setattr(preview_panel, "get_keep_view_on_switch", lambda: False)
    value = preview_panel.PreviewPanel()
    try:
        yield value
    finally:
        value.shutdown()
        value.close()
        value.deleteLater()
        _APP.processEvents()


def _forbid_source_thumbnail_decode(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("HEIF quick cache miss must not decode source pixels")

    monkeypatch.setattr(preview_panel, "_load_thumbnail_image", unexpected)
    monkeypatch.setattr(preview_panel.thumb_stream, "load_thumbnail_rgb", unexpected)


@pytest.mark.parametrize("suffix", [".HIF", ".heif", ".HeIc"])
def test_uncached_heif_selection_returns_then_completes_in_worker(panel, tmp_path, monkeypatch, suffix):
    photo = tmp_path / ("大图" + suffix)
    photo.write_bytes(b"header fixture")
    path = str(photo)
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda path: 50_000_000)
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda *args: None)
    _forbid_source_thumbnail_decode(monkeypatch)
    started = threading.Event()
    release = threading.Event()
    decoder_threads = []

    def decode(path):
        decoder_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return _image()

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", decode)
    ready = []
    panel.full_preview_ready.connect(ready.append)
    heartbeat = []
    try:
        panel.set_image(path, quick_size=512)
        assert decoder_threads == []
        assert panel._full_preview_timer.isActive()
        assert panel.get_preview_image_size() is None
        assert "正在加载预览" in panel._canvas.text()
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        _wait_until(lambda: started.is_set() and heartbeat)
        assert len(decoder_threads) == 1
        assert decoder_threads[0] != threading.get_ident()
        assert not ready
        release.set()
        _wait_until(lambda: panel._full_preview_loader is None and ready)
        assert ready == [path]
        assert panel.get_preview_image_size() == (96, 64)
        assert panel._canvas_source_full_resolution
        assert panel._full_preview_loaded
    finally:
        release.set()


@pytest.mark.parametrize("cached_size,expected_reads", [(512, [512]), (256, [512, 256]), (128, [512, 256, 128])])
def test_heif_preserves_cached_tier_fallback(panel, tmp_path, monkeypatch, cached_size, expected_reads):
    photo = tmp_path / "cached.HIF"
    photo.write_bytes(b"header fixture")
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda path: 50_000_000)
    reads = []

    def cached(path, mtime, size):
        reads.append(size)
        return _image(size, size // 2) if size == cached_size else None

    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", cached)
    _forbid_source_thumbnail_decode(monkeypatch)
    panel.set_image(str(photo), quick_size=512)
    assert reads == expected_reads
    assert panel.get_preview_image_size() == (cached_size, cached_size // 2)
    assert panel._full_preview_timer.isActive()


def test_uncached_heif_fast_navigation_never_decodes_or_starts_full_worker(panel, tmp_path, monkeypatch):
    photo = tmp_path / "fast.HIF"
    photo.write_bytes(b"header fixture")
    reads = []
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda path, mtime, size: reads.append(size))
    _forbid_source_thumbnail_decode(monkeypatch)

    def forbidden(*args):
        raise AssertionError("fast navigation must not enter original preview policy")

    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", forbidden)
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", forbidden)
    panel.set_image(str(photo), load_full=False, quick_size=128)
    assert reads == [128]
    assert panel._fast_preview_only
    assert not panel._full_preview_timer.isActive()
    assert panel._full_preview_loader is None


@pytest.mark.parametrize("has_cached_frame", [False, True])
def test_failed_heif_full_decode_keeps_cache_or_replaces_loading_message(panel, tmp_path, monkeypatch, has_cached_frame):
    photo = tmp_path / "damaged.HIF"
    photo.write_bytes(b"damaged fixture")
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda path: 50_000_000)
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda *args: _image() if has_cached_frame else None)
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda path: None)
    _forbid_source_thumbnail_decode(monkeypatch)
    ready = []
    panel.full_preview_ready.connect(ready.append)
    panel.set_image(str(photo))
    _wait_until(lambda: not panel._full_preview_timer.isActive() and panel._full_preview_loader is None)
    assert not ready
    assert not panel._full_preview_loaded
    if has_cached_frame:
        assert panel.get_preview_image_size() == (96, 64)
    else:
        assert panel.get_preview_image_size() is None
        assert "无法预览" in panel._canvas.text()


def test_full_ready_signal_rejects_stale_fast_and_shutdown_results(panel, tmp_path, monkeypatch):
    photo = tmp_path / "current.HIF"
    photo.write_bytes(b"header fixture")
    path = str(photo)
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda path: 50_000_000)
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda *args: _image(128, 64))
    ready = []
    panel.full_preview_ready.connect(ready.append)
    panel.set_image(path)
    original_token = panel._preview_request_token
    panel.set_image(path, load_full=False)
    panel._on_full_preview_loaded(original_token, path, _image(), 1)
    panel._on_full_preview_loaded(panel._preview_request_token, str(tmp_path / "other.HIF"), _image(), 1)
    assert not ready
    panel.request_shutdown()
    panel._on_full_preview_loaded(panel._preview_request_token, path, _image(), 1)
    assert not ready


def test_small_heif_still_uses_existing_direct_original_policy(panel, tmp_path, monkeypatch):
    photo = tmp_path / "small.HIF"
    photo.write_bytes(b"header fixture")
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda path: 21_000_000)
    calls = []
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda path: calls.append(path) or _image())
    ready = []
    panel.full_preview_ready.connect(ready.append)
    panel.set_image(str(photo))
    assert calls == [str(photo)]
    assert panel._full_preview_loaded
    assert panel._canvas_source_full_resolution
    assert not panel._full_preview_timer.isActive()
    # Main already refreshes information during synchronous selection.
    assert not ready


def test_non_heif_cache_miss_retains_bounded_thumbnail_fallback(tmp_path, monkeypatch):
    photo = tmp_path / "ordinary.jpg"
    photo.write_bytes(b"header fixture")
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda *args: None)
    calls = []
    monkeypatch.setattr(preview_panel, "_load_thumbnail_image", lambda path, size: calls.append(size) or _image(128, 64))
    pixmap = preview_panel._load_quick_preview_pixmap(str(photo), 512)
    assert calls == [128]
    assert (pixmap.width(), pixmap.height()) == (128, 64)
