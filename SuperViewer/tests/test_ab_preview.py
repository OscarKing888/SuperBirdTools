from __future__ import annotations

import os
import time

import pytest
from PIL import Image
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from SuperViewer.superviewer import preview_panel
from SuperViewer.superviewer.ab_preview import ABPreviewPanel


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(.002)
    assert predicate()


def test_independent_sides_active_list_target_filter_and_close(tmp_path):
    photos = [tmp_path / f"照片{i}.png" for i in range(3)]
    for photo in photos:
        Image.new("RGB", (32, 24), "green").save(photo)
    panel = ABPreviewPanel()
    try:
        panel.set_display_paths([str(photo) for photo in photos])
        panel.set_current_list_path(str(photos[1]))
        panel.set_enabled(True)
        assert panel.path_for_side("A") == os.path.normpath(str(photos[1]))
        assert panel.path_for_side("B") == os.path.normpath(str(photos[1]))

        panel.set_active_side("A")
        panel.eventFilter(panel._filenames["B"], QEvent(QEvent.Type.MouseButtonPress))
        assert panel.active_side() == "B"
        panel.set_active_side("A")
        panel.set_current_list_path(str(photos[2]))
        assert panel.path_for_side("A") == os.path.normpath(str(photos[2]))
        assert panel.path_for_side("B") == os.path.normpath(str(photos[1]))

        panel.set_display_paths([str(photos[0])])
        assert panel.path_for_side("A") == os.path.normpath(str(photos[2]))
        assert panel._filenames["A"].toolTip() == str(photos[2])
        panel.eventFilter(panel._filenames["B"], QEvent(QEvent.Type.MouseButtonPress))
        panel.set_current_list_path(str(photos[0]))
        assert panel.active_side() == "B"
        assert panel.path_for_side("B") == os.path.normpath(str(photos[0]))
        assert panel.path_for_side("A") == os.path.normpath(str(photos[2]))
        panel.set_active_side("A")
        panel.set_enabled(False)
        assert panel.active_preview is panel.preview_for_side("A")
        assert panel._widgets["A"].isHidden() is False
        assert panel._widgets["B"].isHidden()
    finally:
        panel.shutdown(wait_timeout_ms=3000)
        panel.deleteLater()
        _APP.processEvents()


def test_ab_committed_selection_skips_quick_preview(tmp_path, monkeypatch):
    photo = tmp_path / "原尺寸.png"
    Image.new("RGB", (64, 48), "blue").save(photo)

    def unexpected_quick(*_args, **_kwargs):
        raise AssertionError("A/B committed selection must not read a thumbnail")

    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", unexpected_quick)
    panel = ABPreviewPanel()
    try:
        panel.set_display_paths([str(photo)])
        panel.set_enabled(True)
        panel.set_current_list_path(str(photo))
        _wait_until(lambda: all(panel.preview_for_side(side)._full_preview_loaded for side in ("A", "B")))
        assert panel.preview_for_side("A").get_preview_image_size() == (64, 48)
        assert panel.preview_for_side("B").get_preview_image_size() == (64, 48)
    finally:
        panel.shutdown(wait_timeout_ms=3000)
        panel.deleteLater()
        _APP.processEvents()


def test_explicit_full_only_mode_rejects_reduced_or_stale_decode(tmp_path):
    photo = tmp_path / "原尺寸.png"
    Image.new("RGB", (64, 48), "blue").save(photo)
    panel = ABPreviewPanel()
    try:
        panel.set_enabled(True)
        preview = panel.preview_for_side("A")
        preview.set_full_only_mode(True)
        preview._current_path = str(photo)
        preview._preview_request_token += 1
        token = preview._preview_request_token
        reduced = QImage(32, 24, preview_panel._qimage_rgb888_format())
        reduced.fill(80)
        preview._on_full_preview_loaded(token, str(photo), reduced, 0.0)
        assert not preview._full_preview_loaded
        assert preview.get_preview_image_size() is None
        assert "无法加载原尺寸预览" in preview.canvas.text()

        full = QImage(64, 48, preview_panel._qimage_rgb888_format())
        full.fill(80)
        preview._on_full_preview_loaded(token - 1, str(photo), full, 0.0)
        assert not preview._full_preview_loaded
    finally:
        panel.shutdown(wait_timeout_ms=3000)
        panel.deleteLater()
        _APP.processEvents()


def test_ab_heif_waits_for_owned_full_decoder_without_sync_thumbnail_decode(tmp_path, monkeypatch):
    photo = tmp_path / "原片.heic"
    photo.write_bytes(b"decoder stub")
    monkeypatch.setattr(preview_panel, "_read_thumb_from_disk_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(preview_panel, "_load_thumbnail_image",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("HEIF cache miss must not synchronously decode")))
    monkeypatch.setattr(preview_panel, "_source_dimensions", lambda _path: (64, 48))

    def decode(_path):
        image = QImage(64, 48, preview_panel._qimage_rgb888_format())
        image.fill(90)
        return image

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", decode)
    panel = ABPreviewPanel()
    try:
        panel.set_display_paths([str(photo)])
        panel.set_enabled(True)
        panel.set_current_list_path(str(photo))
        _wait_until(lambda: all(panel.preview_for_side(side)._full_preview_loaded
                                for side in ("A", "B")))
        assert all(panel.preview_for_side(side).get_preview_image_size() == (64, 48)
                   for side in ("A", "B"))
    finally:
        panel.shutdown(wait_timeout_ms=3000)
        panel.deleteLater()
        _APP.processEvents()


def test_ab_raw_accepts_display_preview_below_source_resolution(tmp_path):
    photo = tmp_path / "原片.arw"
    photo.write_bytes(b"raw stub")
    panel = ABPreviewPanel()
    try:
        panel.set_enabled(True)
        preview = panel.preview_for_side("A")
        preview._current_path = str(photo)
        preview._preview_request_token += 1
        embedded = QImage(32, 24, preview_panel._qimage_rgb888_format())
        embedded.fill(40)
        preview._on_full_preview_loaded(preview._preview_request_token, str(photo), embedded, 0.0)
        assert preview._full_preview_loaded
        assert preview.get_preview_image_size() == (32, 24)
        assert not preview._canvas_source_full_resolution
    finally:
        panel.shutdown(wait_timeout_ms=3000)
        panel.deleteLater()
        _APP.processEvents()
