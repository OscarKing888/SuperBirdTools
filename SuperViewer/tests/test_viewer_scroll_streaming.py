"""Real Viewer viewport streaming, selection retention and shutdown."""
import os
import time

import pytest
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QFontDatabase, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser._panel import FileListPanel
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
from SuperViewer.superviewer.ui_theme import build_palette


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def scroll_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    # Synthetic cached images exercise real layout/repaint without file/EXIF work.
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_a, **_k: None)
    monkeypatch.setattr(FileListPanel, "_schedule_selection_visibility_restore", lambda *_a, **_k: None)
    monkeypatch.setattr(FileListPanel, "_start_thumbnail_loader", lambda *_a, **_k: None)
    widget = SuperViewerTaggedFileListPanel(tag_config_path=tmp_path / "tags.cfg")
    paths = [os.path.normpath(str(tmp_path / f"IMG_{i:05d}.jpg")) for i in range(1500)]
    model = widget._thumb_list_model
    model.append_paths(paths, meta_cache={}, tooltip_fn=None, mismatch_fn=None)
    widget._thumb_model_dirty = False
    widget._current_dir = str(tmp_path)
    widget._all_files = list(paths)
    widget._filtered_files = list(paths)
    for i, path in enumerate(paths):
        image = QImage(32, 24, QImage.Format.Format_RGB32)
        image.fill(QColor.fromHsv(i % 360, 120, 200))
        widget._thumb_memory_cache.put(path, 128, image)
    yield widget, paths
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


@pytest.mark.parametrize("theme,font_size,dimensions", [("light", 10, (760, 560)), ("dark", 16, (620, 460))])
def test_real_grid_streams_during_continuous_scroll(scroll_panel, tmp_path, monkeypatch, theme, font_size, dimensions):
    widget, paths = scroll_panel
    if os.path.isfile("C:/Windows/Fonts/msyh.ttc"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    widget.setPalette(build_palette(theme))
    font = widget.font()
    font.setFamily("Microsoft YaHei")
    font.setPointSize(font_size)
    widget.setFont(font)
    widget.resize(*dimensions)
    widget.show()
    widget._list_widget.doItemsLayout()
    QTest.qWait(80)
    widget.set_pending_selection(paths[:2], paths[1])
    selected = set(widget._active_view_selected_paths())
    assert selected == set(paths[:2])
    current = widget._list_widget.currentIndex()
    resets = []
    widget._thumb_list_model.modelReset.connect(lambda: resets.append(True))
    updates = []
    original = widget._update_visible_thumbnail_range

    def update():
        original()
        visible = widget._thumb_visible_range
        if visible is not None:
            updates.append((ticker.isActive(), visible.start_row,
                            sum(widget._thumb_list_model.has_current_pixmap(e.path, 128) for e in visible.entries)))

    # The existing timer captured the bound method before instrumentation.
    widget._thumb_viewport_timer.timeout.disconnect()
    widget._thumb_viewport_timer.timeout.connect(update)
    bar = widget._list_widget.verticalScrollBar()
    ticker = QTimer()
    ticker.setTimerType(Qt.TimerType.PreciseTimer)
    ticks = []

    def scroll():
        ticks.append(time.perf_counter())
        bar.setValue(bar.value() + 30)
        if len(ticks) == 50:
            ticker.stop()

    ticker.timeout.connect(scroll)
    ticker.start(8)
    try:
        for _ in range(400):
            QTest.qWait(5)
            if not ticker.isActive():
                break
        assert len(ticks) == 50
        QTest.qWait(80)
        during = [row for running, row, ready in updates if running and ready > 0]
        assert len(set(during)) >= 3
        visible = widget._build_visible_thumbnail_data_source()
        assert all(widget._thumb_list_model.has_current_pixmap(e.path, 128) for e in visible.entries)
        assert len(widget._thumb_list_model.pixmap_rows()) < 100
        assert set(widget._active_view_selected_paths()) == selected
        assert widget._list_widget.currentIndex() == current
        assert resets == []
        assert widget._list_widget.viewport().rect().width() > 0
        assert widget.grab().save(str(tmp_path / f"scroll-{theme}.png"))
        # Changing view while a refresh is pending must not start image work.
        requested = []
        monkeypatch.setattr(widget, "_start_thumbnail_loader", lambda *a: requested.append(a))
        widget._schedule_visible_thumbnail_update()
        widget._set_view_mode(widget._MODE_LIST)
        QTest.qWait(60)
        assert requested == []
        widget._set_view_mode(widget._MODE_THUMB)
        widget._request_background_shutdown()
        QTest.qWait(60)
        assert not widget._thumb_viewport_timer.isActive()
        assert requested == []
    finally:
        ticker.stop()


def test_cold_scroll_loads_latest_viewport_through_owned_worker_pool(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("SuperViewer_THUMB_WORKERS", "2")
    monkeypatch.setenv("SuperViewer_METADATA_WORKERS", "2")
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_a, **_k: None)
    monkeypatch.setattr(FileListPanel, "_schedule_selection_visibility_restore", lambda *_a, **_k: None)
    (tmp_path / ".superpicky").mkdir()
    paths = []
    for i in range(240):
        path = tmp_path / f"IMG_{i:04d}.jpg"
        Image.new("RGB", (256, 192), (i, 80, 120)).save(path)
        paths.append(str(path))
    widget = SuperViewerTaggedFileListPanel(tag_config_path=tmp_path / "tags.cfg")
    widget._current_dir = str(tmp_path)
    widget._all_files = paths
    widget._filtered_files = list(paths)
    widget._thumb_list_model.append_paths(paths, meta_cache={}, tooltip_fn=None, mismatch_fn=None)
    widget._thumb_model_dirty = False
    widget.resize(760, 560)
    ticker = QTimer()
    ticker.setTimerType(Qt.TimerType.PreciseTimer)
    ticks, loaded_during_scroll = [], []

    def scroll():
        ticks.append(True)
        bar = widget._list_widget.verticalScrollBar()
        bar.setValue(bar.value() + 70)
        if widget._thumb_list_model.pixmap_rows():
            loaded_during_scroll.append(bar.value())
        if len(ticks) == 60:
            ticker.stop()

    ticker.timeout.connect(scroll)
    try:
        widget.show()
        widget._list_widget.doItemsLayout()
        ticker.start(8)
        deadline = time.monotonic() + 8
        while ticker.isActive():
            assert time.monotonic() < deadline
            QTest.qWait(5)
        visible = widget._build_visible_thumbnail_data_source()
        assert visible is not None and visible.start_row > 0
        while not all(widget._thumb_list_model.has_current_pixmap(e.path, 128) for e in visible.entries):
            assert time.monotonic() < deadline
            QTest.qWait(5)
        assert loaded_during_scroll
        assert widget._browser_work_pool is not None
        assert len(widget._thumb_list_model.pixmap_rows()) < 100
        for entry in visible.entries:
            assert widget._thumb_memory_cache.get(entry.path, 128) is not None
            assert widget._thumb_memory_cache.get(entry.path, 256) is None
    finally:
        ticker.stop()
        widget.shutdown()
        widget.close()
        widget.deleteLater()
        _APP.processEvents()
