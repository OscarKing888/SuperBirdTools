from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from SuperViewer.superviewer import exif_helpers
from SuperViewer.superviewer.image_info_tab_exif import ImageInfoTabPanel_EXIF, _ExifRowsLoader


_APP = QApplication.instance() or QApplication([])


def _rows(name):
    return [(None, None, "File", "FileName", name, name, "File:FileName")]


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.003)
    assert predicate(), "EXIF worker did not finish"


@pytest.fixture
def photos(tmp_path, monkeypatch):
    # These worker tests inject their reader and never use real EXIF/settings.
    monkeypatch.setattr(exif_helpers, "load_tag_label_chinese_from_settings", lambda: False)
    paths = []
    for name in ("a.png", "b.png", "c.png"):
        path = tmp_path / name
        path.write_bytes(b"reader fixture")
        paths.append(str(path))
    return paths


def test_native_finished_exif_worker_retains_ownership_until_queued_handoff(photos):
    decoded = []

    def read(path, chinese):
        decoded.append(Path(path).name)
        return _rows(Path(path).name)

    panel = ImageInfoTabPanel_EXIF(read, lambda *args: None)
    try:
        panel.on_photo_selected(photos[0])
        first = panel._loader
        assert first.wait(1000)
        # Deliberately do not process the queued loaded/finished Qt signals.
        panel.on_photo_selected(photos[1])
        assert panel._loader is first
        panel.on_photo_selected(photos[2])
        assert panel._loader is first
        assert decoded == ["a.png"]
        _wait_until(lambda: panel._loader is None and "c.png" in decoded)
        assert decoded == ["a.png", "c.png"]
        assert panel.last_rows() == _rows("c.png")
    finally:
        panel.shutdown()
        panel.close()
        _APP.processEvents()


def test_stale_finished_signal_cannot_steal_active_workers_pending_request(photos):
    started = threading.Event()
    release = threading.Event()
    decoded = []

    def read(path, chinese):
        decoded.append(Path(path).name)
        if path == photos[1]:
            started.set()
            assert release.wait(3)
        return []

    panel = ImageInfoTabPanel_EXIF(read, lambda *args: None)
    stale = _ExifRowsLoader(0, photos[0], False, lambda *args: [], panel)
    try:
        panel.on_photo_selected(photos[1])
        assert started.wait(1)
        active = panel._loader
        panel.on_photo_selected(photos[2])
        pending = panel._pending_request
        panel._on_loader_finished(stale)
        assert panel._loader is active
        assert panel._pending_request == pending
        assert decoded == ["b.png"]
        # A deadline is not completed shutdown; ownership must survive it.
        assert not panel.shutdown(wait_timeout_ms=0)
        assert panel._loader is active
        assert panel._pending_request is None
        panel.on_photo_selected(photos[0])
        assert panel._pending_request is None
        release.set()
        _wait_until(lambda: panel._loader is None)
        assert decoded == ["b.png"]
        assert panel.last_rows() == []
        assert panel.shutdown(wait_timeout_ms=0)
    finally:
        release.set()
        panel.shutdown()
        panel.close()
        _APP.processEvents()


def test_clearing_selection_cancels_pending_exif_and_rejects_late_rows(photos):
    panel = ImageInfoTabPanel_EXIF(lambda path, chinese: _rows(path), lambda *args: None)
    try:
        panel.on_photo_selected(photos[0])
        first = panel._loader
        assert first.wait(1000)
        panel.on_photo_selected(photos[1])
        panel.on_photo_selected("")
        assert panel._loader is first
        assert panel._pending_request is None
        _wait_until(lambda: panel._loader is None)
        assert panel.last_rows() == []
        assert panel.exif_table.rowCount() == 0
        panel.request_shutdown()
        # Even a matching token supplied after close must not update the UI.
        panel._current_photo_path = photos[2]
        panel._on_rows_loaded(panel._display_request_token, photos[2], _rows("late"))
        assert panel.last_rows() == []
        assert panel.exif_table.rowCount() == 0
    finally:
        panel.shutdown()
        panel.close()
        _APP.processEvents()
