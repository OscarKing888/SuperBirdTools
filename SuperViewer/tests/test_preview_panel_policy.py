from __future__ import annotations

import io
import os
import struct
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

from SuperViewer.superviewer import preview_panel
from SuperViewer.superviewer.qt_compat import QApplication, QColor, QImage, QPixmap


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _process_events_until(app: QApplication, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def _write_preview_psd(path: Path, width: int = 6, height: int = 4) -> None:
    header = struct.pack(
        ">4sH6sHIIHH",
        b"8BPS",
        1,
        b"\x00" * 6,
        3,
        height,
        width,
        16,
        3,
    )
    sections = struct.pack(">III", 0, 0, 0)
    pixels = width * height
    planes = b"".join(
        struct.pack(f">{pixels}H", *([value] * pixels))
        for value in (65535, 32768, 0)
    )
    path.write_bytes(header + sections + b"\x00\x00" + planes)


def test_large_image_keeps_thumbnail_then_background_policy(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    photo = tmp_path / "large.jpg"
    photo.write_bytes(b"placeholder")
    quick = QPixmap(64, 48)
    quick.fill()
    calls: list[str] = []

    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *_args: quick)
    monkeypatch.setattr(
        preview_panel,
        "_preview_source_pixel_count",
        lambda _path: preview_panel._DIRECT_ORIGINAL_PREVIEW_MAX_PIXELS + 1,
    )

    def fail_sync_decode(path: str):
        calls.append(path)
        raise AssertionError("超过直接显示阈值的大图不能在 GUI 线程同步解码")

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", fail_sync_decode)
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_image(str(photo), load_full=True)
        assert calls == []
        assert panel._full_preview_timer.isActive()
        assert panel.source_pixmap_for_path(str(photo)) is not None
    finally:
        panel._full_preview_timer.stop()
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_small_image_displays_original_immediately(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    photo = tmp_path / "small.jpg"
    Image.new("RGB", (320, 200), (10, 20, 30)).save(photo, "JPEG")
    decoded_paths: list[str] = []

    monkeypatch.setattr(
        preview_panel,
        "_load_quick_preview_pixmap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("小图直接显示原图时不应再读取 quick thumbnail")
        ),
    )

    def decode_original(path: str):
        decoded_paths.append(path)
        image = QImage(320, 200, preview_panel._qimage_rgb888_format())
        image.fill(30)
        return image

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", decode_original)
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_image(str(photo), load_full=True)
        assert decoded_paths == [str(photo)]
        assert not panel._full_preview_timer.isActive()
        assert panel._full_preview_loader is None
        assert panel._full_preview_loaded
        assert panel._canvas_source_full_resolution
        assert not panel._fast_preview_only
        assert panel.get_preview_image_size() == (320, 200)
    finally:
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_raw_never_uses_direct_original_policy(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    raw_path = tmp_path / "preview.arw"
    raw_path.write_bytes(b"raw-placeholder")
    quick = QPixmap(64, 48)
    quick.fill()

    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *_args: quick)
    monkeypatch.setattr(
        preview_panel,
        "_load_full_preview_qimage",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("RAW 不能在 set_image 热路径同步完整解码")
        ),
    )
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_image(str(raw_path), load_full=True)
        assert not panel._full_preview_loaded
        assert not panel._canvas_source_full_resolution
        assert panel._full_preview_timer.isActive()
        assert panel.get_preview_image_size() == (64, 48)
    finally:
        panel._full_preview_timer.stop()
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_load_full_false_never_starts_decoder(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    photo = tmp_path / "fast.jpg"
    photo.write_bytes(b"placeholder")
    quick = QPixmap(64, 48)
    quick.fill()
    panel = preview_panel.PreviewPanel()
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *_args: quick)
    monkeypatch.setattr(
        preview_panel,
        "_preview_source_pixel_count",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("load_full=False 不应检查同步原图策略")
        ),
    )
    monkeypatch.setattr(
        preview_panel,
        "_load_full_preview_qimage",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("load_full=False 不应解码原图")
        ),
    )
    try:
        panel.set_image(str(photo), load_full=False, quick_size=128)
        assert not panel._full_preview_timer.isActive()
        assert panel._full_preview_loader is None
        assert panel._fast_preview_only
    finally:
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_same_path_load_full_false_cancels_pending_full_preview(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    photo = tmp_path / "same-fast.jpg"
    photo.write_bytes(b"placeholder")
    quick = QPixmap(64, 48)
    quick.fill()
    panel = preview_panel.PreviewPanel()
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *_args: quick)
    monkeypatch.setattr(
        preview_panel,
        "_preview_source_pixel_count",
        lambda _path: preview_panel._DIRECT_ORIGINAL_PREVIEW_MAX_PIXELS + 1,
    )
    try:
        panel.set_image(str(photo), load_full=True)
        original_token = panel._preview_request_token
        assert panel._full_preview_timer.isActive()

        panel.set_image(str(photo), load_full=False, quick_size=128)
        assert panel._preview_request_token == original_token + 1
        assert not panel._full_preview_timer.isActive()
        assert panel._full_preview_loader is None
        assert panel._fast_preview_only
        assert panel.get_preview_image_size() == (64, 48)
    finally:
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_same_path_fast_mode_discards_active_full_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    photo = tmp_path / "same-active.jpg"
    photo.write_bytes(b"placeholder")
    quick = QPixmap(64, 48)
    quick.fill()
    started = threading.Event()
    release = threading.Event()

    def slow_decode(_path: str):
        started.set()
        assert release.wait(2.0)
        image = QImage(640, 480, preview_panel._qimage_rgb888_format())
        image.fill(20)
        return image

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", slow_decode)
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_quick_pixmap(str(photo), quick, quick_size=128)
        panel._start_full_preview_loader()
        assert started.wait(1.0)

        panel.set_image(str(photo), load_full=False, quick_size=128)
        release.set()
        assert _process_events_until(app, lambda: panel._full_preview_loader is None)
        assert not panel._full_preview_loaded
        assert not panel._canvas_source_full_resolution
        assert panel._fast_preview_only
        assert panel.get_preview_image_size() == (64, 48)
    finally:
        release.set()
        panel.shutdown()
        panel.close()


def test_final_commit_reuses_same_path_memory_quick_pixmap(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    photo = tmp_path / "same.jpg"
    photo.write_bytes(b"placeholder")
    quick = QPixmap(64, 48)
    quick.fill()
    decoded_paths: list[str] = []
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_quick_pixmap(str(photo), quick, quick_size=128)
        monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda _path: 640 * 480)
        monkeypatch.setattr(
            preview_panel,
            "_load_quick_preview_pixmap",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("最终提交不应重新读取同路径 quick thumbnail")
            ),
        )

        def decode_original(path: str):
            decoded_paths.append(path)
            image = QImage(640, 480, preview_panel._qimage_rgb888_format())
            image.fill(20)
            return image

        monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", decode_original)
        panel.set_image(str(photo), load_full=True)
        assert decoded_paths == [str(photo)]
        assert not panel._full_preview_timer.isActive()
        assert panel._full_preview_loaded
        assert panel._canvas_source_full_resolution
        assert not panel._fast_preview_only
        assert panel.get_preview_image_size() == (640, 480)
        assert panel.source_pixmap_for_path(str(photo)) is not None
    finally:
        panel._full_preview_timer.stop()
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_full_preview_loader_is_single_flight_and_coalesces_latest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
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


def test_direct_policy_does_not_overlap_active_background_decoder(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    first = tmp_path / "active.jpg"
    latest = tmp_path / "latest.jpg"
    first.write_bytes(b"placeholder")
    latest.write_bytes(b"placeholder")
    quick = QPixmap(64, 48)
    quick.fill()
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
            if name == first.name:
                first_started.set()
                assert release_first.wait(2.0)
            image = QImage(320, 200, preview_panel._qimage_rgb888_format())
            image.fill(20)
            return image
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", fake_decode)
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda _path: 320 * 200)
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *_args: quick)
    panel = preview_panel.PreviewPanel()
    try:
        panel._current_path = os.path.normpath(str(first))
        panel._preview_request_token = 1
        panel._start_full_preview_loader()
        assert first_started.wait(1.0)

        panel.set_image(str(latest), load_full=True)
        assert decoded == [first.name]
        assert panel.get_preview_image_size() == (64, 48)
        assert panel._full_preview_timer.isActive()

        assert _process_events_until(
            app,
            lambda: panel._pending_full_preview_request is not None,
        )
        assert decoded == [first.name]
        release_first.set()
        assert _process_events_until(
            app,
            lambda: panel._full_preview_loader is None and latest.name in decoded,
        )
        assert decoded == [first.name, latest.name]
        assert max_active == 1
        assert panel._full_preview_loaded
        assert panel._canvas_source_full_resolution
    finally:
        release_first.set()
        panel.shutdown()
        panel.close()


def test_stale_finished_cleanup_cannot_consume_current_pending_request(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    paths = [tmp_path / name for name in ("a.jpg", "b.jpg", "c.jpg")]
    for path in paths:
        path.write_bytes(b"placeholder")
    decoded: list[str] = []

    def fake_decode(path: str):
        decoded.append(Path(path).name)
        image = QImage(16, 12, preview_panel._qimage_rgb888_format())
        image.fill(20)
        return image

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", fake_decode)
    panel = preview_panel.PreviewPanel()
    try:
        panel._current_path = str(paths[0])
        panel._preview_request_token = 1
        panel._start_full_preview_loader()
        stale_loader = panel._full_preview_loader
        assert stale_loader is not None
        assert stale_loader.wait(1_000)

        # 模拟 worker 已结束、queued finished 尚未进入 GUI 事件循环的窗口。
        panel._current_path = str(paths[1])
        panel._preview_request_token = 2
        panel._start_full_preview_loader()
        assert panel._full_preview_loader is stale_loader
        assert panel._pending_full_preview_request == (2, os.path.normpath(str(paths[1])))

        panel._cleanup_full_preview_loader(stale_loader)
        current_loader = panel._full_preview_loader
        assert current_loader is not None and current_loader is not stale_loader

        panel._current_path = str(paths[2])
        panel._preview_request_token = 3
        panel._start_full_preview_loader()
        assert panel._pending_full_preview_request == (3, os.path.normpath(str(paths[2])))

        # 迟到的旧 cleanup 只能 deleteLater，不能取走 C 或启动第三个 worker。
        panel._cleanup_full_preview_loader(stale_loader)
        assert panel._full_preview_loader is current_loader
        assert panel._pending_full_preview_request == (3, os.path.normpath(str(paths[2])))
        assert _process_events_until(
            app,
            lambda: panel._full_preview_loader is None and "c.jpg" in decoded,
        )
        assert decoded in (["a.jpg", "c.jpg"], ["a.jpg", "b.jpg", "c.jpg"])
    finally:
        panel.shutdown()
        panel.close()


def test_raw_embedded_preview_wins_without_demosaic(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "embedded.arw"
    raw_path.write_bytes(b"raw-placeholder")
    encoded = io.BytesIO()
    Image.new("RGB", (80, 60), (30, 40, 50)).save(encoded, "JPEG")

    class _RawpyProbe:
        @staticmethod
        def imread(_path):
            raise AssertionError("存在内嵌预览时不应调用 rawpy demosaic")

    monkeypatch.setattr(preview_panel.thumb_stream, "get_raw_preview_jpeg", lambda _path: encoded.getvalue())
    monkeypatch.setitem(sys.modules, "rawpy", _RawpyProbe)

    result = preview_panel._load_full_preview_qimage(str(raw_path))
    assert result is not None and not result.isNull()
    assert (result.width(), result.height()) == (80, 60)


def test_raw_missing_embedded_uses_half_size(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "fallback.arw"
    raw_path.write_bytes(b"raw-placeholder")
    calls: list[dict] = []

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def postprocess(self, **kwargs):
            calls.append(dict(kwargs))
            return np.zeros((24, 32, 3), dtype=np.uint8)

    class _RawpyProbe:
        @staticmethod
        def imread(_path):
            return _Raw()

    monkeypatch.setattr(preview_panel.thumb_stream, "get_raw_preview_jpeg", lambda _path: None)
    monkeypatch.setitem(sys.modules, "rawpy", _RawpyProbe)
    monkeypatch.setattr(preview_panel, "_get_orientation_from_file", lambda _path: 1)

    result = preview_panel._load_full_preview_qimage(str(raw_path))
    assert result is not None and not result.isNull()
    assert calls and calls[0]["half_size"] is True


def test_16_bit_psd_full_preview_and_overlay_export(tmp_path: Path) -> None:
    app = _app()
    psd_path = tmp_path / "16-bit.psd"
    _write_preview_psd(psd_path)

    decoded = preview_panel._load_full_preview_qimage(str(psd_path))
    assert decoded is not None and not decoded.isNull()
    assert (decoded.width(), decoded.height()) == (6, 4)

    quick = QPixmap(3, 2)
    quick.fill()
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_image(str(psd_path), load_full=True)
        assert panel._full_preview_loaded
        assert panel._canvas_source_full_resolution
        assert not panel._full_preview_timer.isActive()
        assert panel.get_preview_image_size() == (6, 4)

        panel.set_composition_grid_mode("thirds")
        panel.set_quick_pixmap(str(psd_path), quick, quick_size=128)
        rendered = panel.render_source_pixmap_with_overlays()
        assert rendered is not None and not rendered.isNull()
        assert (rendered.width(), rendered.height()) == (6, 4)
        assert panel._canvas_source_full_resolution
    finally:
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_raw_overlay_export_forces_full_resolution(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    raw_path = tmp_path / "export.arw"
    raw_path.write_bytes(b"raw-placeholder")
    quick = QPixmap(80, 60)
    quick.fill()
    background = QColor(18, 37, 55)
    full = QPixmap(400, 300)
    full.fill(background)
    monkeypatch.setattr(preview_panel, "_load_raw_full_as_pixmap", lambda _path: full)

    panel = preview_panel.PreviewPanel()
    try:
        panel.set_composition_grid_mode("thirds")
        panel.set_composition_grid_line_width(2)
        panel.set_quick_pixmap(str(raw_path), quick, quick_size=128)
        rendered = panel.render_source_pixmap_with_overlays()
        assert rendered is not None and not rendered.isNull()
        assert (rendered.width(), rendered.height()) == (400, 300)
        assert panel._canvas_source_full_resolution
        rendered_image = rendered.toImage()
        assert any(
            rendered_image.pixelColor(x, 20) != background
            for x in range(130, 138)
        )

        output_path = tmp_path / "raw-overlay.png"
        assert panel.save_source_pixmap_with_overlays(str(output_path), fmt="PNG")
        saved = QImage(str(output_path))
        assert not saved.isNull()
        assert (saved.width(), saved.height()) == (400, 300)
        assert any(saved.pixelColor(x, 20) != background for x in range(130, 138))
    finally:
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_non_raw_overlay_export_replaces_quick_pixmap_with_original(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    photo = tmp_path / "export.jpg"
    photo.write_bytes(b"placeholder")
    quick = QPixmap(64, 48)
    quick.fill()
    full = QPixmap(640, 480)
    full.fill()
    monkeypatch.setattr(preview_panel, "_load_preview_pixmap_for_canvas", lambda _path: full)

    panel = preview_panel.PreviewPanel()
    try:
        panel.set_quick_pixmap(str(photo), quick, quick_size=128)
        rendered = panel.render_source_pixmap_with_overlays()
        assert rendered is not None
        assert (rendered.width(), rendered.height()) == (640, 480)
        assert panel._canvas_source_full_resolution
    finally:
        panel.shutdown()
        panel.close()
    assert app is QApplication.instance()


def test_overlay_export_drains_active_worker_without_late_low_res_overwrite(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    raw_path = tmp_path / "active.arw"
    raw_path.write_bytes(b"raw-placeholder")
    quick = QPixmap(64, 48)
    quick.fill()
    full = QPixmap(400, 300)
    full.fill()
    started = threading.Event()
    release = threading.Event()

    def slow_display_decode(_path: str):
        started.set()
        assert release.wait(2.0)
        image = QImage(80, 60, preview_panel._qimage_rgb888_format())
        image.fill(20)
        return image

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", slow_display_decode)
    monkeypatch.setattr(preview_panel, "_load_raw_full_as_pixmap", lambda _path: full)
    panel = preview_panel.PreviewPanel()
    try:
        panel.set_quick_pixmap(str(raw_path), quick, quick_size=128)
        panel._start_full_preview_loader()
        assert started.wait(1.0)
        release_timer = threading.Timer(0.05, release.set)
        release_timer.start()
        try:
            rendered = panel.render_source_pixmap_with_overlays()
        finally:
            release_timer.cancel()
            release.set()
        assert rendered is not None
        assert (rendered.width(), rendered.height()) == (400, 300)
        app.processEvents()
        source = panel.source_pixmap_for_path(str(raw_path))
        assert source is not None
        assert (source.width(), source.height()) == (400, 300)
        assert panel._canvas_source_full_resolution
    finally:
        release.set()
        panel.shutdown()
        panel.close()


def test_close_event_retries_without_blocking_on_active_worker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    photo = tmp_path / "closing.jpg"
    photo.write_bytes(b"placeholder")
    started = threading.Event()
    release = threading.Event()

    def slow_decode(_path: str):
        started.set()
        assert release.wait(2.0)
        image = QImage(32, 24, preview_panel._qimage_rgb888_format())
        image.fill(20)
        return image

    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", slow_decode)
    panel = preview_panel.PreviewPanel()
    panel._current_path = str(photo)
    panel._preview_request_token = 1
    panel.show()
    panel._start_full_preview_loader()
    assert started.wait(1.0)
    try:
        close_started = time.monotonic()
        panel.close()
        assert time.monotonic() - close_started < 0.25
        assert panel.isVisible()
        threading.Timer(0.05, release.set).start()
        assert _process_events_until(
            app,
            lambda: not panel.isVisible() and panel._full_preview_loader is None,
        )
    finally:
        release.set()
        panel.shutdown(wait_timeout_ms=1_000)
        panel.close()


def test_preview_grid_selector_contract_keeps_all_supported_modes() -> None:
    import importlib

    from app_common.preview_canvas import PREVIEW_COMPOSITION_GRID_MODES
    viewer_main = importlib.import_module("SuperViewer.main")

    visible_items = [
        (mode, label)
        for mode, label in viewer_main.PREVIEW_GRID_MODE_ITEMS
        if mode in set(PREVIEW_COMPOSITION_GRID_MODES)
    ]
    assert [mode for mode, _label in visible_items] == list(PREVIEW_COMPOSITION_GRID_MODES)
    assert all(label.startswith("构图线：") for _mode, label in visible_items)


def test_main_window_builds_visible_preview_grid_selector() -> None:
    import importlib

    app = _app()
    from app_common.preview_canvas import PREVIEW_COMPOSITION_GRID_MODES
    viewer_main = importlib.import_module("SuperViewer.main")

    # 传入占位列表以跳过用户上次目录恢复，避免测试扫描工作区外的照片目录。
    window = viewer_main.MainWindow(initial_received_files=["__grid_selector_smoke__"])
    try:
        window.show()
        app.processEvents()
        combo = window.combo_preview_grid
        assert combo.isVisibleTo(window)
        assert [combo.itemData(index) for index in range(combo.count())] == list(
            PREVIEW_COMPOSITION_GRID_MODES
        )
    finally:
        # 避免 GUI smoke 把临时 splitter 布局写入用户设置。
        window._save_main_splitter_state = lambda: None
        window.close()
        assert _process_events_until(app, lambda: window._shutdown_finalized)


def test_shutdown_drops_pending_request(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
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
    panel._current_path = str(first)
    panel._preview_request_token = 1
    panel._start_full_preview_loader()
    assert started.wait(1.0)
    panel._current_path = str(second)
    panel._preview_request_token = 2
    panel._start_full_preview_loader()
    release_timer = threading.Timer(0.05, release.set)
    release_timer.start()
    try:
        assert panel.shutdown(wait_timeout_ms=1_000)
        assert panel._full_preview_loader is None
        assert panel._pending_full_preview_request is None
        assert decoded == ["first.jpg"]
    finally:
        release.set()
        release_timer.cancel()
        panel.close()
    assert app is QApplication.instance()
