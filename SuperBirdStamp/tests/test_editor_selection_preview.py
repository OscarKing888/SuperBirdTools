from __future__ import annotations

import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import pytest

from birdstamp import config
from birdstamp.gui import editor as editor_module
from birdstamp.gui import editor_preview_decode_worker
from birdstamp.gui.editor import BirdStampEditorWindow

editor_module._load_bird_detector = lambda: None
_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_editor_state(tmp_path, monkeypatch):
    # Construction and close may persist state before/after _make_window's
    # per-instance hooks. Keep every runtime path outside the real checkout.
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))


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
    assert _wait_until(app, lambda: window.current_path == resolved and window.current_source_image is not None)


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
            lambda: window.current_path == second_path.resolve(strict=False) and window.current_source_image is not None,
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


def test_click_does_not_resort_or_refresh_list(monkeypatch, tmp_path):
    image_path = tmp_path / "cached.jpg"
    Image.new("RGB", (48, 32), "red").save(image_path)
    window = _make_window()
    try:
        _add_photo(window, image_path)
        window._accept_async_preview_image(image_path, Image.new("RGB", (48, 32))).close()
        monkeypatch.setattr(window, "_update_photo_list_item_display", lambda *a, **k: pytest.fail("click rewrote the row"))
        monkeypatch.setattr(window.photo_list, "setSortingEnabled", lambda *a: pytest.fail("click sorted the list"))
        _select_photo(_APP, window, window.photo_list.topLevelItem(0), image_path)
        assert window.current_source_image is not None
    finally:
        monkeypatch.undo()
        _cleanup_window(_APP, window)


def test_quick_preview_upgrade_preserves_edits_and_metadata(monkeypatch, tmp_path):
    image_path = (tmp_path / "large.tif").resolve()
    Image.new("RGB", (1200, 800), "red").save(image_path)
    entered, release = threading.Event(), threading.Event()
    def decode(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return Image.new("RGB", (600, 400), "blue")
    monkeypatch.setattr(editor_preview_decode_worker, "decode_image_for_preview", decode)
    monkeypatch.setattr(editor_preview_decode_worker, "cached_preview_image", lambda *a: Image.new("RGB", (120, 80), "red"))
    window = _make_window()
    try:
        _add_photo(window, image_path)
        _select_photo(_APP, window, window.photo_list.topLevelItem(0), image_path)
        assert _wait_until(_APP, entered.is_set)
        assert window._preview_is_quick
        assert window.current_source_full_size == (1200, 800)
        assert window._cached_preview_image(image_path) is None
        box = (0.2, 0.1, 0.8, 0.9)
        window._crop_box_override = box
        window._set_photo_crop_box_for_path(image_path, box)
        window._set_center_mode_value("custom", emit_changed=False)
        window._apply_photo_list_metadata_batch({str(image_path): {"SourceFile": str(image_path), "XMP-dc:Title": "新鸟名"}})
        monkeypatch.setattr(window, "_apply_render_settings_to_ui", lambda *a: pytest.fail("upgrade reset settings"))
        window._crop_drag_active = True
        release.set()
        assert _wait_until(_APP, lambda: window._preview_decode_worker is None)
        assert window.current_source_image.size == (120, 80)
        window._crop_drag_active = False
        assert _wait_until(_APP, lambda: not window._preview_is_quick)
        assert window.current_source_image.size == (600, 400)
        assert window._crop_box_override == box
        assert window.current_raw_metadata["XMP-dc:Title"] == "新鸟名"
        assert window._cached_preview_image(image_path).size == (600, 400)
    finally:
        release.set()
        _cleanup_window(_APP, window)


def test_bird_center_detection_is_async_and_updates_crop(monkeypatch, tmp_path):
    from birdstamp.gui import bird_detect_worker, editor_core, editor_crop_calculator
    image_path = (tmp_path / "bird.jpg").resolve()
    Image.new("RGB", (600, 400), "red").save(image_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    box = (0.65, 0.25, 0.85, 0.75)
    def detect(image):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return box
    monkeypatch.setattr(bird_detect_worker, "detect_primary_bird_box", detect)
    monkeypatch.setattr(editor_crop_calculator, "_detect_primary_bird_box", lambda *a: pytest.fail("GUI ran YOLO"))
    window = _make_window()
    del window._schedule_async_bird_detect
    try:
        _add_photo(window, image_path)
        settings = window._build_current_render_settings()
        settings.update(center_mode="bird", ratio=1, crop_padding_left=100, crop_padding_right=100,
                        crop_padding_top=100, crop_padding_bottom=100, draw_text=False, draw_banner=False)
        monkeypatch.setattr(window, "_render_settings_for_path", lambda *a, **k: dict(settings))
        _select_photo(_APP, window, window.photo_list.topLevelItem(0), image_path)
        assert _wait_until(_APP, entered.is_set)
        before = window.preview_overlay_state.crop_effect_box
        window.render_preview()
        window.render_preview()
        assert window._bird_detect_pending is None
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        assert _wait_until(_APP, lambda: ticks)
        release.set()
        assert _wait_until(_APP, lambda: window._bird_detect_worker is None)
        expected, _pad = editor_core.compute_crop_plan_for_image(
            image=window.current_source_image, raw_metadata={}, settings=settings, bird_box=box,
        )
        assert window.preview_overlay_state.crop_effect_box == expected
        assert expected != before
        assert len(calls) == 1 and calls[0] != threading.get_ident()
    finally:
        release.set()
        _wait_until(_APP, lambda: window._bird_detect_worker is None)
        _cleanup_window(_APP, window)


def test_preview_template_never_reads_missing_metadata_synchronously(monkeypatch, tmp_path):
    from birdstamp.gui import template_context as context
    image_path = (tmp_path / "text.jpg").resolve()
    Image.new("RGB", (200, 100), "red").save(image_path)
    window = _make_window()
    try:
        _add_photo(window, image_path)
        settings = window._build_current_render_settings()
        settings.update(ratio="no_crop", draw_text=True, draw_banner=False)
        payload = dict(fields=[dict(name="鸟名", text_source={"type": "auto", "key": "bird"}),
                               dict(name="文件", text_source={"type": "from_file", "key": "filename"})])
        monkeypatch.setattr(window, "_render_settings_for_path", lambda *a, **k: dict(settings))
        monkeypatch.setattr(window, "_resolve_template_payload_for_render", lambda *a: payload)
        monkeypatch.setattr(context, "_read_file_metadata_with_xmp_priority_cached", lambda *a: pytest.fail("preview read ExifTool"))
        monkeypatch.setattr(context, "_read_sidecar_metadata_cached", lambda *a: pytest.fail("preview reread XMP"))
        monkeypatch.setattr(context, "_probe_image_file_properties", lambda *a: pytest.fail("preview opened original"))
        _select_photo(_APP, window, window.photo_list.topLevelItem(0), image_path)
        assert window.last_rendered is not None
        assert not window.current_photo_info.metadata_is_snapshot
    finally:
        _cleanup_window(_APP, window)


def test_preview_cache_uses_lru_and_evicts_dimensions(monkeypatch, tmp_path):
    from birdstamp.gui import editor_renderer
    from birdstamp.gui.editor_renderer import _BirdStampRendererMixin
    class Renderer(_BirdStampRendererMixin):
        _preview_image_cache = {}
        _preview_source_size_cache = {}
    renderer = Renderer()
    monkeypatch.setattr(editor_renderer, "_PREVIEW_IMAGE_CACHE_MAX_BYTES", 2 * 10 * 10 * 4)
    paths = [tmp_path / f"{i}.jpg" for i in range(3)]
    for path in paths[:2]:
        signature = renderer._preview_image_cache_signature(path)
        renderer._store_preview_image_cache(signature, Image.new("RGB", (10, 10)))
        renderer._preview_source_size_cache[signature] = (100, 100)
    renderer._cached_preview_image(paths[0]).close()
    renderer._store_preview_image_cache(renderer._preview_image_cache_signature(paths[2]), Image.new("RGB", (10, 10)))
    assert renderer._cached_preview_image(paths[1]) is None
    assert renderer._preview_image_cache_signature(paths[1]) not in renderer._preview_source_size_cache
    assert renderer._cached_preview_image(paths[0]) is not None
    renderer._clear_decoded_image_caches()


def test_preview_reuses_viewer_per_file_cache_and_rejects_stale(tmp_path, monkeypatch):
    from app_common.file_browser._browser_core import _persistent_thumb_cache_path_for_file
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "local_cache"))
    for directory, color in ((tmp_path / "one", "red"), (tmp_path / "two", "blue")):
        (directory / ".superpicky").mkdir(parents=True)
        path = directory / "same.tif"
        Image.new("RGB", (600, 400), color).save(path)
        cache_path = Path(_persistent_thumb_cache_path_for_file(str(path), str(directory), 512, selected_dir=str(directory)))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (512, 341), color).save(cache_path)
        cached = editor_preview_decode_worker.cached_preview_image(path, 2048)
        assert cached is not None and cached.size == (512, 341)
        assert cached.getpixel((0, 0))[0 if color == "red" else 2] > 240
        cached.close()
        stamp = path.stat().st_mtime
        os.utime(cache_path, (stamp - 60, stamp - 60))
        assert editor_preview_decode_worker.cached_preview_image(path, 2048) is None


def test_worker_reuses_dimensions_from_decode(monkeypatch, tmp_path):
    path = tmp_path / "image.tif"
    Image.new("RGB", (360, 240)).save(path)
    monkeypatch.setattr(editor_preview_decode_worker, "cached_preview_image", lambda *a: None)
    monkeypatch.setattr(editor_preview_decode_worker, "read_decoded_image_size", lambda *a: pytest.fail("redundant source open"))
    worker = editor_preview_decode_worker.EditorPreviewDecodeWorker(1, path, max_long_edge=120)
    results = []
    worker.decoded.connect(lambda *args: results.append(args))
    worker.run()
    assert results[0][2].size == (120, 80)
    assert results[0][3] == (360, 240)
    results[0][2].close()


def test_stale_quick_and_full_results_never_replace_current_image(tmp_path):
    path = (tmp_path / "current.jpg").resolve()
    Image.new("RGB", (24, 16)).save(path)
    window = _make_window()
    try:
        _add_photo(window, path)
        _select_photo(_APP, window, window.photo_list.topLevelItem(0), path)
        original = window.current_source_image
        for callback in (window._on_quick_preview_ready, window._on_preview_decode_ready):
            stale = Image.new("RGB", (12, 8))
            callback(window._preview_decode_token - 1, str(path), stale, (24, 16))
            assert window.current_source_image is original
            with pytest.raises(ValueError):
                stale.getpixel((0, 0))
    finally:
        _cleanup_window(_APP, window)


def test_decode_handoff_waits_for_real_thread_finished(monkeypatch, tmp_path):
    class FinishingWorker:
        interrupted = False
        def isRunning(self):
            return False
        def requestInterruption(self):
            self.interrupted = True
    path = (tmp_path / "next.jpg").resolve()
    Image.new("RGB", (24, 16)).save(path)
    window = _make_window()
    worker = FinishingWorker()
    try:
        _add_photo(window, path)
        window._preview_decode_worker = worker
        starts = []
        monkeypatch.setattr(window, "_start_preview_decode_worker", lambda *args: starts.append(args))
        item = window.photo_list.topLevelItem(0)
        blocked = window.photo_list.blockSignals(True)
        window.photo_list.setCurrentItem(item)
        window.photo_list.blockSignals(blocked)
        window._on_photo_selected(item, None)
        assert window._preview_decode_worker is worker
        assert worker.interrupted and starts == []
        monkeypatch.setattr(window, "sender", lambda: worker)
        window._on_preview_decode_finished()
        assert starts == [(window._preview_decode_token, path)]
    finally:
        window._preview_decode_worker = None
        _cleanup_window(_APP, window)


def test_shutdown_releases_preview_deferred_by_crop_drag(tmp_path):
    path = (tmp_path / "drag.jpg").resolve()
    Image.new("RGB", (24, 16)).save(path)
    window = _make_window()
    try:
        _add_photo(window, path)
        _select_photo(_APP, window, window.photo_list.topLevelItem(0), path)
        window._crop_drag_active = True
        upgrade = Image.new("RGB", (48, 32))
        window._on_preview_decode_ready(window._preview_decode_token, str(path), upgrade, (48, 32))
        window._cancel_preview_decode(shutdown=True)
        def closed():
            try:
                upgrade.getpixel((0, 0))
                return False
            except ValueError:
                return True
        assert _wait_until(_APP, closed)
        assert window.current_source_image.size == (24, 16)
    finally:
        window._crop_drag_active = False
        _cleanup_window(_APP, window)
