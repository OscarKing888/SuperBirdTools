import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest

from SuperViewer.main import MainWindow
from SuperViewer.superviewer import paths_settings, preview_panel
from SuperViewer.superviewer.qt_compat import QApplication, QPixmap
from app_common.file_browser._panel import FileListPanel


_APP = QApplication.instance() or QApplication([])


def _pixmap(width=1200, height=800):
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.black)
    return pixmap


@pytest.fixture
def panel():
    result = preview_panel.PreviewPanel()
    result.resize(640, 480)
    result.show()
    _APP.processEvents()
    result.set_auto_focus_center(True)
    # 自动居中即使在原有“切图保持视图”关闭时也保留放大程度。
    result.set_keep_view_on_switch(False)
    yield result
    result.shutdown()
    result.close()


def _assert_center(panel, expected):
    assert panel.canvas._view_center_ratio() == pytest.approx(expected)


def test_focus_center_survives_zoom_resize_and_hidden_overlay(panel):
    panel.set_quick_pixmap("a.jpg", _pixmap(), quick_size=2048)
    panel.set_show_focus_enabled(False)
    panel.set_focus_box((0.88, 0.08, 0.92, 0.12))
    panel.set_display_scale_percent(200)
    _assert_center(panel, (0.9, 0.1))
    assert panel.canvas._show_focus_box is False
    canvas = panel.canvas
    # 鼠标在左上角缩放，焦点也应留在中央；边缘留白不能被拖动边界截断。
    event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    canvas.wheelEvent(event)
    _assert_center(panel, (0.9, 0.1))
    panel.resize(800, 600)
    _APP.processEvents()
    _assert_center(panel, (0.9, 0.1))
    panel.set_auto_focus_center(False)
    assert canvas._can_pan()
    # 关闭功能后重新启用正常边界，能自由拖动。
    assert canvas._view_center_ratio() != pytest.approx((0.9, 0.1))


@pytest.mark.parametrize("box", [None, (float("nan"), 0, 1, 1), (0.8, 0.2, 0.1, 0.4)])
def test_missing_or_invalid_focus_uses_image_center(panel, box):
    panel.set_quick_pixmap("first.jpg", _pixmap(), quick_size=2048)
    panel.set_display_scale_percent(200)
    panel.set_focus_box((0, 0, 0.1, 0.1))
    zoom = panel.canvas._zoom
    panel.set_quick_pixmap("second.jpg", _pixmap(512, 342), quick_size=512)
    panel.set_focus_box(box)
    _assert_center(panel, (0.5, 0.5))
    assert panel.canvas._zoom == pytest.approx(zoom)
    assert not panel._full_preview_timer.isActive()


@pytest.mark.parametrize("kind", ["small", "large", "raw"])
def test_committed_selection_and_full_upgrade_keep_zoom_and_focus(panel, monkeypatch, tmp_path, kind):
    source = tmp_path / ("photo.ARW" if kind == "raw" else "photo.jpg")
    if kind == "raw":
        source.write_bytes(b"raw-fixture")
    else:
        Image.new("RGB", (1200, 800)).save(source)
    panel.set_quick_pixmap("previous.jpg", _pixmap(), quick_size=2048)
    panel.set_display_scale_percent(200)
    zoom = panel.canvas._zoom
    monkeypatch.setattr(preview_panel, "_SYNC_FULL_PREVIEW_MAX_PIXELS", 1 if kind == "large" else 40_000_000)
    panel.set_quick_preview_provider(lambda *_args: None if kind == "raw" else _pixmap(512, 342))
    panel.set_image(str(source), quick_size=512)
    if kind != "small":
        assert not panel._full_preview_loaded
        if kind == "raw":
            assert panel.get_preview_image_size() is None
        panel._full_preview_timer.stop()
        panel._on_full_preview_loaded(panel._preview_request_token, str(source), _pixmap().toImage(), 0.0)
    assert panel._full_preview_loaded
    panel.set_focus_box((0.15, 0.65, 0.25, 0.75))
    assert panel.canvas._zoom == pytest.approx(zoom)
    _assert_center(panel, (0.2, 0.7))
    print(f"focus-center logged check: {kind}, zoom={zoom:.3f}, center=(0.2, 0.7)")


@pytest.mark.parametrize("memory_frame", [True, False])
def test_held_frames_use_source_memory_cache_only(panel, monkeypatch, memory_frame):
    source = os.path.normpath("photos/鸟.ARW")
    cache = os.path.normpath("cache/hashed-512.jpg")
    browser = SimpleNamespace(
        _meta_cache={source: {"focus_box": (0.7, 0.2, 0.9, 0.4)}},
        get_selected_display_path=lambda: source,
        preview_quick_size=lambda: 512,
    )
    browser.get_cached_focus_box_state_for_path = lambda path: FileListPanel.get_cached_focus_box_state_for_path(browser, path)
    calls = []
    window = SimpleNamespace(
        preview_panel=panel, _file_list=browser,
        check_auto_focus_center=SimpleNamespace(isChecked=lambda: True),
        _stop_focus_loader=lambda: calls.append("stop"),
    )
    window._get_cached_focus_box_for_preview = lambda *args: MainWindow._get_cached_focus_box_for_preview(window, *args)
    window._update_preview_focus_box = lambda *args, **kwargs: MainWindow._update_preview_focus_box(window, *args, **kwargs)
    panel.set_quick_preview_provider(lambda *_args: _pixmap(512, 342))
    panel.set_quick_pixmap("previous.jpg", _pixmap(), quick_size=2048)
    panel.set_display_scale_percent(200)
    zoom = panel.canvas._zoom

    def forbidden(*_args, **_kwargs):
        raise AssertionError("held playback must not read metadata, scan sources or decode originals")

    window._resolve_focus_metadata_source_path = forbidden
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", forbidden)
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", forbidden)
    monkeypatch.setattr(os.path, "isfile", forbidden)
    if memory_frame:
        MainWindow._on_file_fast_preview_pixmap_requested(window, source, _pixmap(512, 342), 512)
    else:
        MainWindow._on_file_fast_preview_requested(window, cache)
    _assert_center(panel, (0.8, 0.3))
    assert panel.canvas._zoom == pytest.approx(zoom)
    assert calls == ["stop"]
    assert not panel._full_preview_timer.isActive()
    # 下一帧缓存未就绪/明确无焦点时不沿用上一张照片的焦点。
    browser._meta_cache.clear()
    window._update_preview_focus_box(source, allow_async_load=False)
    _assert_center(panel, (0.5, 0.5))


def test_toolbar_persists_option_and_keeps_grid_export(tmp_path, monkeypatch):
    from app_common import superviewer_user_options
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(tmp_path / "state"))
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    assert not paths_settings.load_auto_focus_center_from_settings()
    window = MainWindow(initial_received_files=["skip-restore"])
    window.show()
    _APP.processEvents()
    try:
        window.check_auto_focus_center.setChecked(True)
        assert paths_settings.load_auto_focus_center_from_settings()
        window.check_show_focus.setChecked(False)
        panel = window.preview_panel
        source = str(tmp_path / "sample.jpg")
        Image.new("RGB", (120, 90)).save(source)
        window._current_exif_path = source
        window._file_list._meta_cache[source] = {"focus_box": (0.1, 0.6, 0.3, 0.8)}
        panel.set_quick_pixmap(source, _pixmap(120, 90), quick_size=128)
        window._update_preview_focus_box(source)
        _assert_center(panel, (0.2, 0.7))
        index = window.combo_preview_grid.findData("thirds")
        assert index >= 0 and window.combo_preview_grid.isVisible()
        window.combo_preview_grid.setCurrentIndex(index)
        image = panel.canvas.render_source_pixmap_with_overlays().toImage()
        assert image.pixelColor(40, 45).red() > 0
        assert panel.composition_grid_mode() == "thirds"
    finally:
        window.close()
        for _ in range(200):
            if window._shutdown_finalized:
                break
            QTest.qWait(5)
        assert window._shutdown_finalized
    restored = MainWindow(initial_received_files=["skip-restore"])
    restored.show()
    _APP.processEvents()
    try:
        assert restored.check_auto_focus_center.isChecked()
        assert restored.preview_panel.canvas._auto_focus_center
    finally:
        restored.close()
        for _ in range(200):
            if restored._shutdown_finalized:
                break
            QTest.qWait(5)
        assert restored._shutdown_finalized
