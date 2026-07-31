from __future__ import annotations

import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from birdstamp.gui import editor as editor_module
from birdstamp.gui import editor_preview_decode_worker
from birdstamp.gui.editor import BirdStampEditorWindow

editor_module._load_bird_detector = lambda: None


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_window() -> BirdStampEditorWindow:
    window = BirdStampEditorWindow()
    window._schedule_workspace_autosave = lambda *args, **kwargs: None
    window._autosave_workspace_now = lambda *args, **kwargs: None
    window._schedule_async_bird_detect = lambda *args, **kwargs: None
    if hasattr(window, "show_bird_box_check"):
        window.show_bird_box_check.setChecked(False)
    return window


def _cleanup_window(app: QApplication, window: BirdStampEditorWindow) -> None:
    try:
        window._cancel_preview_decode(shutdown=True)
        worker = getattr(window, "_preview_decode_worker", None)
        if worker is not None and worker.isRunning():
            deadline = time.monotonic() + 3.0
            while worker.isRunning() and time.monotonic() < deadline:
                app.processEvents()
                worker.wait(10)
    except Exception:
        pass
    try:
        window._stop_photo_list_metadata_loader(wait=True, reset_progress=True)
    except Exception:
        pass
    try:
        window._stop_received_photo_import(reset_progress=True)
    except Exception:
        pass
    window.close()
    window.deleteLater()
    app.processEvents()


def _wait_until(
    app: QApplication,
    predicate,
    *,
    timeout: float = 3.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def _add_photo(window: BirdStampEditorWindow, path: Path) -> None:
    window._append_photo_path_to_list(
        path.resolve(strict=False),
        existing_keys=set(),
        default_settings=window._build_current_render_settings(),
    )


def _select_photo(
    app: QApplication,
    window: BirdStampEditorWindow,
    item,
    path: Path,
) -> None:
    previous = window.photo_list.blockSignals(True)
    window.photo_list.setCurrentItem(item)
    window.photo_list.blockSignals(previous)
    window._on_photo_selected(item, None)
    resolved = path.resolve(strict=False)
    assert _wait_until(app, lambda: window.current_path == resolved)


def test_photo_selection_does_not_block_on_metadata_read(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    image_path = tmp_path / "first.jpg"
    Image.new("RGB", (24, 24), (180, 30, 20)).save(image_path, format="JPEG")
    window = _make_window()
    try:
        _add_photo(window, image_path)
        item = window.photo_list.topLevelItem(0)

        def fail_metadata_read(_path: Path) -> dict:
            raise AssertionError("selection should not synchronously read metadata")

        monkeypatch.setattr(window, "_load_raw_metadata", fail_metadata_read)
        monkeypatch.setattr(
            window,
            "_provider_text_candidates",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("selection should not synchronously resolve display providers")
            ),
        )
        monkeypatch.setattr(
            editor_module,
            "_build_metadata_context",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("selection should not synchronously build full metadata context")
            ),
        )
        _select_photo(app, window, item, image_path)

        assert window.current_path == image_path.resolve(strict=False)
        assert window.current_source_image is not None
        assert window.preview_pixmap is not None
        assert window.current_raw_metadata == {"SourceFile": str(image_path.resolve(strict=False))}
        assert window.current_metadata_context["filename"] == image_path.name
    finally:
        _cleanup_window(app, window)


def test_current_preview_refreshes_when_background_metadata_arrives(tmp_path: Path) -> None:
    app = _app()
    image_path = tmp_path / "second.jpg"
    Image.new("RGB", (24, 24), (20, 80, 180)).save(image_path, format="JPEG")
    window = _make_window()
    render_snapshots: list[dict] = []
    try:
        _add_photo(window, image_path)
        item = window.photo_list.topLevelItem(0)
        window.render_preview = lambda *args, **kwargs: render_snapshots.append(dict(window.current_raw_metadata))

        _select_photo(app, window, item, image_path)
        metadata = {
            "SourceFile": str(image_path.resolve(strict=False)),
            "XMP-dc:Title": "metadata title",
        }
        window._apply_photo_list_metadata_batch({str(image_path.resolve(strict=False)): metadata})

        assert render_snapshots[0] == {"SourceFile": str(image_path.resolve(strict=False))}
        assert render_snapshots[-1].get("XMP-dc:Title") == "metadata title"
        assert window.current_raw_metadata.get("XMP-dc:Title") == "metadata title"
    finally:
        _cleanup_window(app, window)


def test_preview_decode_is_single_flight_and_keeps_gui_responsive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    first_path = tmp_path / "slow-first.jpg"
    second_path = tmp_path / "second.jpg"
    Image.new("RGB", (48, 32), (180, 30, 20)).save(first_path, format="JPEG")
    Image.new("RGB", (48, 32), (20, 80, 180)).save(second_path, format="JPEG")

    entered = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    active = 0
    max_active = 0
    decode_calls: list[Path] = []

    def slow_decode(path: Path, *, max_long_edge: int, decoder: str) -> Image.Image:
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
            decode_calls.append(Path(path))
            call_index = len(decode_calls)
        try:
            if call_index == 1:
                entered.set()
                assert release.wait(timeout=3.0)
            with Image.open(path) as source:
                return source.convert("RGB")
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(editor_preview_decode_worker, "decode_image_for_preview", slow_decode)
    window = _make_window()
    timer = QTimer()
    heartbeats: list[int] = []
    timer.timeout.connect(lambda: heartbeats.append(1))
    timer.start(10)
    try:
        _add_photo(window, first_path)
        _add_photo(window, second_path)
        first_item = window.photo_list.topLevelItem(0)
        second_item = window.photo_list.topLevelItem(1)

        previous = window.photo_list.blockSignals(True)
        window.photo_list.setCurrentItem(first_item)
        window.photo_list.blockSignals(previous)
        window._on_photo_selected(first_item, None)
        assert _wait_until(app, entered.is_set)

        previous = window.photo_list.blockSignals(True)
        window.photo_list.setCurrentItem(second_item)
        window.photo_list.blockSignals(previous)
        window._on_photo_selected(second_item, first_item)

        heartbeat_deadline = time.monotonic() + 0.15
        while time.monotonic() < heartbeat_deadline:
            app.processEvents()
            time.sleep(0.005)
        assert len(heartbeats) >= 3
        assert window.current_path != first_path.resolve(strict=False)

        release.set()
        assert _wait_until(
            app,
            lambda: window.current_path == second_path.resolve(strict=False),
        )
        assert decode_calls == [
            first_path.resolve(strict=False),
            second_path.resolve(strict=False),
        ]
        assert max_active == 1
    finally:
        release.set()
        timer.stop()
        _cleanup_window(app, window)
