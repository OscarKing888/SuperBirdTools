"""列表和网格菜单的真实刷新、选择保留及异步生命周期。"""
import time
from pathlib import Path

from PIL import Image
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import FileListPanel
from app_common.file_browser import _browser_core as core
from SuperViewer.superviewer.file_context_menu import build_file_context_menu
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
from SuperViewer.superviewer.ui_theme import build_palette

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline = time.monotonic() + 8
    while not predicate():
        assert time.monotonic() < deadline
        _APP.processEvents()
        QTest.qWait(5)


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "local"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_a, **_k: None)
    monkeypatch.setattr(FileListPanel, "_schedule_selection_visibility_restore", lambda *_a, **_k: None)
    monkeypatch.setattr(FileListPanel, "_start_metadata_loader", lambda *_a, **_k: None)
    tags = tmp_path / ".superpicky/tags.cfg"
    tags.parent.mkdir()
    tags.write_text("飞行\n", encoding="utf-8")
    widget = SuperViewerTaggedFileListPanel(tag_config_path=tags)
    widget.resize(760, 560)
    paths, cached = [], []
    for i in range(3):
        photo = tmp_path / f"中文照片{i}.jpg"
        Image.new("RGB", (320, 240), "blue").save(photo)
        paths.append(str(photo))
        cache = Path(core._persistent_thumb_cache_path_for_file(str(photo), str(tmp_path), 128, selected_dir=str(tmp_path)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (128, 96), "red").save(cache)
        cached.append(cache)
    widget._current_dir = str(tmp_path)
    widget._all_files = paths
    widget._meta_cache = {path: {} for path in paths}
    yield widget, paths, cached
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


@pytest.mark.parametrize("mode", [FileListPanel._MODE_LIST, FileListPanel._MODE_THUMB], ids=["list", "grid"])
@pytest.mark.parametrize("theme,font_size", [("light", 12), ("dark", 18)])
def test_refresh_menu_rebuilds_selection_without_resetting_view(panel, tmp_path, mode, theme, font_size):
    widget, paths, cached = panel
    widget.setPalette(build_palette(theme))
    font = widget.font()
    font.setPointSize(font_size)
    widget.setFont(font)
    widget._set_view_mode(mode)
    widget._rebuild_views()
    widget.show()
    wait_for(lambda: widget._thumb_list_model.rowCount() == 3 if mode == widget._MODE_THUMB else widget._file_table_model.rowCount() == 3)
    widget.set_pending_selection(paths[:2], paths[1])
    wait_for(lambda: set(widget._active_view_selected_paths()) == set(paths[:2]))
    if mode == widget._MODE_THUMB:
        wait_for(lambda: widget._thumb_list_model.has_current_pixmap(paths[0], 128))
    image = QImage(128, 96, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    for path in paths:
        widget._thumb_memory_cache.put(path, 128, image)
    resets = []
    widget._thumb_list_model.modelReset.connect(lambda: resets.append(True))
    untouched = cached[2].read_bytes()
    menu = build_file_context_menu(widget, paths[:2], paths[1], log_prefix="refresh-test")
    try:
        action = next(a for a in menu.actions() if a.objectName() == "refreshThumbnailsAction")
        assert action.isEnabled()
        menu.setFont(font)
        menu.setPalette(build_palette(theme))
        menu.popup(QPoint(20, 20))
        _APP.processEvents()
        rect = menu.actionGeometry(action)
        assert rect.height() >= menu.fontMetrics().height()
        assert rect.width() >= menu.fontMetrics().horizontalAdvance(action.text())
        assert menu.rect().contains(rect)
        menu.setActiveAction(action)
        assert menu.grab().save(str(tmp_path / f"menu-{theme}-{mode}.png"))
        QTest.keyClick(menu, Qt.Key.Key_Return)
        wait_for(lambda: widget._persistent_thumb_cache_done >= 2)
        wait_for(lambda: not widget._refreshed_thumbnail_paths and not widget._thumbnail_refresh_apply_timer.isActive())
        assert widget._persistent_thumb_cache_generated == 2
        assert widget._persistent_thumb_cache_failed == 0
        for cache in cached[:2]:
            with Image.open(cache) as result:
                assert result.getpixel((0, 0))[2] > 200
        assert cached[2].read_bytes() == untouched
        assert set(widget._active_view_selected_paths()) == set(paths[:2])
        assert not resets
        if mode == widget._MODE_THUMB:
            wait_for(lambda: widget._thumb_list_model.has_current_pixmap(paths[0], 128))
            pixmap = widget._current_thumbnail_fast_preview_pixmap(paths[0])
            assert isinstance(pixmap, QPixmap) and pixmap.toImage().pixelColor(0, 0).blue() > 200
        else:
            assert widget._thumb_memory_cache.get(paths[0], 128) is None
        # 再次刷新不会被已完成任务去重吞掉。
        widget.refresh_thumbnails(paths[:1])
        wait_for(lambda: widget._persistent_thumb_cache_worker._refresh_serial == 2 and
                 widget._persistent_thumb_cache_done == widget._persistent_thumb_cache_total)
    finally:
        menu.close()
        menu.deleteLater()


def test_shutdown_refuses_new_refresh(panel):
    widget, paths, cached = panel
    before = [p.read_bytes() for p in cached]
    widget.request_shutdown()
    widget.refresh_thumbnails(paths)
    assert widget._persistent_thumb_cache_worker is None
    assert [p.read_bytes() for p in cached] == before
