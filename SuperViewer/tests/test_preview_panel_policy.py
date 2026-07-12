import os
import sys
import threading
import time
from pathlib import Path

from SuperViewer.superviewer import preview_panel
from SuperViewer.superviewer.qt_compat import QApplication, QImage, QPixmap


def test_sync_full_preview_policy_uses_pixel_threshold(monkeypatch, tmp_path: Path) -> None:
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"placeholder")
    monkeypatch.setattr(preview_panel, "_SYNC_FULL_PREVIEW_MAX_PIXELS", 40_000_000)

    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 12_000_000)
    assert preview_panel._should_load_full_preview_sync(str(photo))

    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 50_000_000)
    assert not preview_panel._should_load_full_preview_sync(str(photo))


def test_sync_full_preview_policy_never_syncs_raw(monkeypatch, tmp_path: Path) -> None:
    raw = tmp_path / "a.arw"
    raw.write_bytes(b"placeholder")
    monkeypatch.setattr(preview_panel, "_SYNC_FULL_PREVIEW_MAX_PIXELS", 40_000_000)
    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 1)

    assert not preview_panel._should_load_full_preview_sync(str(raw))


def test_quick_preview_target_uses_requested_thumbnail_size() -> None:
    assert preview_panel._quick_preview_target_size(None, 1024) == 1024
    assert preview_panel._quick_preview_target_size(None, 0) == preview_panel._QUICK_PREVIEW_SIZE


def test_fast_only_same_path_reenters_normal_loading_policy() -> None:
    assert not preview_panel._can_reuse_current_preview(
        same_path=True,
        has_pixmap=True,
        load_full=True,
        fast_preview_only=True,
    )
    assert preview_panel._can_reuse_current_preview(
        same_path=True,
        has_pixmap=True,
        load_full=True,
        fast_preview_only=False,
    )


def _process_events_until(app: QApplication, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def test_full_preview_loader_is_single_flight_and_coalesces_latest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    paths = [tmp_path / name for name in ("a.jpg", "b.jpg", "c.jpg")]
    for path in paths:
        path.write_bytes(b"placeholder")

    first_started = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    decoded: list[str] = []
    active = 0
    max_active = 0

    def fake_decode(path: str):
        nonlocal active, max_active
        name = Path(path).name
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            decoded.append(name)
        try:
            if name == "a.jpg":
                first_started.set()
                assert release_first.wait(2.0)
            image = QImage(32, 24, preview_panel._qimage_rgb888_format())
            image.fill(40)
            return image
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", fake_decode)
    panel = preview_panel.PreviewPanel()
    try:
        panel._current_path = os.path.normpath(str(paths[0]))
        panel._preview_request_token = 1
        panel._start_full_preview_loader()
        assert first_started.wait(1.0)

        panel._current_path = os.path.normpath(str(paths[1]))
        panel._preview_request_token = 2
        panel._start_full_preview_loader()
        panel._current_path = os.path.normpath(str(paths[2]))
        panel._preview_request_token = 3
        panel._start_full_preview_loader()

        assert decoded == ["a.jpg"]
        assert max_active == 1
        release_first.set()
        assert _process_events_until(
            app,
            lambda: panel._full_preview_loader is None and "c.jpg" in decoded,
        )
        assert decoded == ["a.jpg", "c.jpg"]
        assert max_active == 1
        assert panel._full_preview_loaded
    finally:
        release_first.set()
        panel.shutdown()
        panel.close()


def test_ordinary_raw_full_preview_never_uses_rawpy_demosaic(
    monkeypatch,
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "missing-embedded.arw"
    raw_path.write_bytes(b"raw-placeholder")
    calls = {"imread": 0}

    class _RawpyProbe:
        @staticmethod
        def imread(path):
            calls["imread"] += 1
            raise AssertionError("ordinary preview must not demosaic RAW")

    monkeypatch.setitem(sys.modules, "rawpy", _RawpyProbe)
    monkeypatch.setattr(preview_panel, "_load_raw_embedded_preview_qimage", lambda path: None)

    assert preview_panel._load_full_preview_qimage(str(raw_path)) is None
    assert calls["imread"] == 0


def test_set_image_does_not_overlap_sync_full_with_running_loader(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    first_started = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    decoded: list[str] = []
    active = 0
    max_active = 0

    def fake_decode(path: str):
        nonlocal active, max_active
        name = Path(path).name
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            decoded.append(name)
        try:
            if name == "first.jpg":
                first_started.set()
                assert release_first.wait(2.0)
            image = QImage(32, 24, preview_panel._qimage_rgb888_format())
            image.fill(30)
            return image
        finally:
            with state_lock:
                active -= 1

    quick_pixmap = QPixmap(64, 48)
    quick_pixmap.fill()
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", fake_decode)
    monkeypatch.setattr(preview_panel, "_should_load_full_preview_sync", lambda path: True)
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda path, size: quick_pixmap)
    panel = preview_panel.PreviewPanel()
    try:
        panel._current_path = os.path.normpath(str(first_path))
        panel._preview_request_token = 1
        panel._start_full_preview_loader()
        assert first_started.wait(1.0)

        panel.set_image(str(second_path), load_full=True, quick_size=128)
        assert decoded == ["first.jpg"]
        assert max_active == 1
        panel._full_preview_timer.stop()
        panel._start_full_preview_loader()
        release_first.set()
        assert _process_events_until(
            app,
            lambda: panel._full_preview_loader is None and "second.jpg" in decoded,
        )
        assert decoded == ["first.jpg", "second.jpg"]
        assert max_active == 1
    finally:
        release_first.set()
        panel.shutdown()
        panel.close()


def test_full_preview_shutdown_waits_for_owned_worker_and_drops_pending(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    started = threading.Event()
    release = threading.Event()
    decoded: list[str] = []

    def fake_decode(path: str):
        decoded.append(Path(path).name)
        started.set()
        assert release.wait(2.0)
        image = QImage(16, 12, preview_panel._qimage_rgb888_format())
        image.fill(20)
        return image

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", fake_decode)
    panel = preview_panel.PreviewPanel()
    panel._current_path = os.path.normpath(str(first_path))
    panel._preview_request_token = 1
    panel._start_full_preview_loader()
    assert started.wait(1.0)
    panel._current_path = os.path.normpath(str(second_path))
    panel._preview_request_token = 2
    panel._start_full_preview_loader()
    release_timer = threading.Timer(0.05, release.set)
    release_timer.start()
    try:
        panel.shutdown()
        app.processEvents()
        assert panel._full_preview_loader is None
        assert panel._pending_full_preview_request is None
        assert decoded == ["first.jpg"]
    finally:
        release.set()
        release_timer.cancel()
        panel.close()


def test_source_pixmap_provider_rejects_quick_tier() -> None:
    app = QApplication.instance() or QApplication([])
    panel = preview_panel.PreviewPanel()
    pixmap = QPixmap(128, 96)
    pixmap.fill()
    try:
        panel.set_quick_pixmap("quick.jpg", pixmap, quick_size=128)
        assert panel.source_pixmap_for_path("quick.jpg") is None
        panel._full_preview_loaded = True
        panel._fast_preview_only = False
        assert panel.source_pixmap_for_path("quick.jpg") is not None
    finally:
        panel.shutdown()
        panel.close()
