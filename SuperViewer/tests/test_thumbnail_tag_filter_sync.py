"""Viewer tag filtering retains thumbnail rows while cached tags arrive."""
from __future__ import annotations

import os

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel, _permissions
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS", superviewer_user_options.normalize_user_options(None))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for name, value in vars(_permissions).copy().items():
        if name.startswith("CURRENT_SUPERPICKY_"):
            monkeypatch.setattr(_permissions, name, value)
    monkeypatch.setattr(_panel, "_shutdown_thumb_disk_writer", lambda **kwargs: None)
    monkeypatch.setattr(SuperViewerTaggedFileListPanel, "_schedule_visible_thumbnail_update", lambda self: None)
    monkeypatch.setattr(SuperViewerTaggedFileListPanel, "_emit_file_selected_for_path", lambda *args, **kwargs: None)
    tags = tmp_path / "tags.cfg"
    tags.write_text("飞行\n捕食\n", encoding="utf-8")
    widget = SuperViewerTaggedFileListPanel(tag_config_path=tags)
    widget.resize(900, 700)
    widget.show()
    try:
        yield widget
    finally:
        assert widget.shutdown()
        widget.close()
        widget.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize("partial", [False, True])
def test_streaming_tag_filter_preserves_pixmaps_selection_and_order(panel, tmp_path, partial):
    paths = [os.path.normpath(str(tmp_path / f"photo-{i:03d}.jpg")) for i in range(20)]
    panel._current_dir = str(tmp_path)
    panel._loaded_directory_recursive = True
    panel._all_files = paths
    panel._photo_tag_cache = {path: set() for path in paths}
    panel._active_tag_filters = {"飞行", "捕食"}
    panel._tag_filter_partial_match = partial
    matching = {"飞行中"} if partial else {"飞行", "捕食"}
    panel._photo_tag_cache[paths[10]] = matching
    panel._rebuild_views()
    _APP.processEvents()
    model = panel._thumb_list_model
    pixmap = QPixmap(8, 8)
    pixmap.fill()
    model.set_pixmap_for_path(paths[10], pixmap, panel._thumb_size)
    panel._list_widget.setCurrentIndex(model.index_for_path(paths[10]))
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    panel._begin_meta_apply_session(len(paths), paths)
    assert panel._meta_apply_needs_filter

    for index in (18, 2, 8):
        panel._photo_tag_cache[paths[index]] = matching
        panel._flush_photo_tag_filter_refresh()
        assert panel._list_widget.indexAt(QPoint(40, 40)).isValid()
        assert model.path_for_index(panel._list_widget.currentIndex()) == paths[10]

    assert model.all_paths() == [paths[i] for i in (2, 8, 10, 18)]
    assert model.has_current_pixmap(paths[10], panel._thumb_size)
    assert not resets
    panel._active_tag_filters.clear()
    panel._flush_meta_filter_refresh()
    assert not panel._meta_apply_needs_filter
