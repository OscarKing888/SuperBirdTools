from __future__ import annotations

import threading
import time
from pathlib import Path

from PIL import Image

from SuperViewer.superviewer.image_info_tab_base import ImageInfoTabPanel
from SuperViewer.superviewer.image_info_tab_exif import ImageInfoTabPanel_EXIF
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
from SuperViewer.superviewer.image_info_tab_widget import ImageInfoTabWidget
from SuperViewer.superviewer.qt_compat import QApplication, QPixmap


def _process_events_until(app: QApplication, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


class _CountingPanel(ImageInfoTabPanel):
    tab_title = "probe"

    def __init__(self) -> None:
        self.refresh_count = 0
        self.shutdown_count = 0
        super().__init__()

    def create_ui(self) -> None:
        return

    def refresh_ui(self):
        self.refresh_count += 1
        return self.current_photo_path()

    def shutdown(self) -> None:
        self.shutdown_count += 1


def test_image_info_tabs_refresh_only_active_panel() -> None:
    app = QApplication.instance() or QApplication([])
    tabs = ImageInfoTabWidget()
    first = _CountingPanel()
    second = _CountingPanel()
    try:
        tabs.add_info_panel(first)
        tabs.add_info_panel(second)
        tabs.setCurrentIndex(0)

        result = tabs.on_photo_selected("first.jpg")
        assert list(result) == ["_CountingPanel"]
        assert first.refresh_count == 1
        assert second.refresh_count == 0
        assert second.current_photo_path().endswith("first.jpg")

        tabs.setCurrentIndex(1)
        app.processEvents()
        assert first.refresh_count == 1
        assert second.refresh_count == 1

        tabs.on_photo_selected("second.jpg")
        assert first.refresh_count == 1
        assert second.refresh_count == 2
        tabs.setCurrentIndex(0)
        app.processEvents()
        assert first.refresh_count == 2

        tabs.shutdown()
        assert first.shutdown_count == 1
        assert second.shutdown_count == 1
    finally:
        tabs.close()
        first.close()
        second.close()


def test_exif_loader_is_async_single_flight_and_applies_latest(
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
    calls: list[str] = []
    applied_values: list[str] = []
    active = 0
    max_active = 0

    def rows_loader(path: str, use_chinese: bool):
        nonlocal active, max_active
        name = Path(path).name
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            calls.append(name)
        try:
            if name == "first.jpg":
                first_started.set()
                assert release_first.wait(2.0)
            return [(None, None, "Group", "Name", name, None, None)]
        finally:
            with state_lock:
                active -= 1

    panel = ImageInfoTabPanel_EXIF(rows_loader, lambda *args: None)
    original_set_exif = panel.exif_table.set_exif

    def record_set_exif(rows):
        if rows:
            applied_values.extend(str(row[4]) for row in rows)
        original_set_exif(rows)

    panel.exif_table.set_exif = record_set_exif
    try:
        started_at = time.perf_counter()
        assert panel.on_photo_selected(str(first_path)) == []
        assert (time.perf_counter() - started_at) < 0.1
        assert first_started.wait(1.0)

        assert panel.on_photo_selected(str(second_path)) == []
        assert max_active == 1
        release_first.set()
        assert _process_events_until(
            app,
            lambda: panel._loader is None and bool(panel.last_rows()),
        )
        assert calls == ["first.jpg", "second.jpg"]
        assert max_active == 1
        assert applied_values == ["second.jpg"]
        assert panel.last_rows()[0][4] == "second.jpg"
    finally:
        release_first.set()
        panel.shutdown()
        panel.close()


def test_hidden_image_info_preview_does_no_pixmap_work_and_uses_true_size(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    provider_calls: list[str] = []
    full_pixmap = QPixmap(640, 480)
    full_pixmap.fill()

    def pixmap_provider(path: str):
        provider_calls.append(path)
        return full_pixmap

    panel = ImageInfoTabPanel_ImageInfo(
        lambda: [],
        lambda path: set(),
        lambda paths, tag, enabled: None,
        lambda path, name: path,
        metadata_provider=lambda path: {},
        preview_pixmap_provider=pixmap_provider,
    )
    try:
        panel._load_preview("hidden.jpg")
        panel._update_preview_pixmap()
        assert provider_calls == []

        assert panel._image_size(
            "missing.jpg",
            metadata={"EXIF:ExifImageWidth": "5616", "EXIF:ExifImageLength": 3744},
        ) == (5616, 3744)
        assert provider_calls == []

        image_path = tmp_path / "header.jpg"
        Image.new("RGB", (321, 123), (20, 30, 40)).save(image_path, "JPEG")
        assert panel._image_size(str(image_path), metadata={}) == (321, 123)
        assert provider_calls == []

        assert panel._image_size(str(tmp_path / "missing.jpg"), metadata={}) == (640, 480)
        assert len(provider_calls) == 1
    finally:
        panel.close()


def test_exif_shutdown_waits_for_worker_and_drops_pending(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def rows_loader(path: str, use_chinese: bool):
        calls.append(Path(path).name)
        started.set()
        assert release.wait(2.0)
        return [(None, None, "Group", "Name", Path(path).name, None, None)]

    panel = ImageInfoTabPanel_EXIF(rows_loader, lambda *args: None)
    assert panel.on_photo_selected(str(first_path)) == []
    assert started.wait(1.0)
    assert panel.on_photo_selected(str(second_path)) == []
    release_timer = threading.Timer(0.05, release.set)
    release_timer.start()
    try:
        panel.shutdown()
        app.processEvents()
        assert panel._loader is None
        assert panel._pending_request is None
        assert calls == ["first.jpg"]
        assert panel.last_rows() == []
    finally:
        release.set()
        release_timer.cancel()
        panel.close()
