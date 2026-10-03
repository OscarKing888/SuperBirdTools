"""SuperViewer 的缩略图排序工具与列表表头使用同一个排序状态。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._browser_core import (
    _FILE_TABLE_HEADERS,
    _TREE_COL_NAME,
    _TREE_COL_SEQ,
    _TREE_COL_STAR,
)
from app_common.file_browser._panel import FileListPanel
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def panel(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **_kwargs: None)
    widget = SuperViewerTaggedFileListPanel(tag_config_path=tmp_path / "tags.cfg")
    yield widget
    widget.close_tag_store()
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def _paths_in_tree(panel) -> list[str]:
    model = panel._tree_widget.model()
    return [panel._tree_path_from_index(model.index(row, 0)) for row in range(model.rowCount())]


def test_sort_toolbar_is_viewer_opt_in_and_only_shown_in_thumbnail_mode(panel) -> None:
    assert FileListPanel.show_thumbnail_sort_controls is False
    shared = FileListPanel(create_filter_bar=False)
    try:
        assert shared._sort_bar is None
    finally:
        shared.close()
        shared.deleteLater()
    assert panel._sort_bar is not None
    assert not panel._sort_bar.isHidden()
    columns = [panel._sort_column_combo.itemData(index) for index in range(panel._sort_column_combo.count())]
    assert _TREE_COL_SEQ not in columns
    assert set(columns) >= set(range(len(_FILE_TABLE_HEADERS)))
    panel._btn_list.click()
    assert panel._sort_bar.isHidden()
    panel._btn_thumb.click()
    assert not panel._sort_bar.isHidden()


def test_toolbar_controls_and_header_stay_in_sync_and_sort_both_views(panel, tmp_path) -> None:
    paths = [str(tmp_path / name) for name in ("low.jpg", "high.jpg", "mid.jpg")]
    panel._current_dir = str(tmp_path)
    panel._all_files = paths
    panel._loaded_directory_recursive = True
    panel._meta_cache = {path: {"rating": rating} for path, rating in zip(paths, (1, 5, 3))}
    panel._rebuild_views()

    combo = panel._sort_column_combo
    combo.setCurrentIndex(combo.findData(_TREE_COL_STAR))
    assert panel._thumb_list_model.all_paths() == [paths[0], paths[2], paths[1]]
    panel._sort_order_button.click()
    descending = [paths[1], paths[2], paths[0]]
    assert panel._thumb_list_model.all_paths() == descending
    assert "降序" in panel._sort_order_button.text()
    panel._btn_list.click()
    assert _paths_in_tree(panel) == descending
    header = panel._tree_widget.header()
    assert header.sortIndicatorSection() == _TREE_COL_STAR
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

    header.setSortIndicator(_TREE_COL_NAME, Qt.SortOrder.AscendingOrder)
    expected = [paths[1], paths[0], paths[2]]
    assert combo.currentData() == _TREE_COL_NAME
    assert "升序" in panel._sort_order_button.text()
    assert _paths_in_tree(panel) == expected
    panel._btn_thumb.click()
    assert panel._thumb_list_model.all_paths() == expected

    # 序号没有排序语义，旧式表头的该信号也不能清除当前排序。
    panel._on_tree_sort_indicator_changed(_TREE_COL_SEQ, Qt.SortOrder.DescendingOrder)
    assert combo.currentData() == _TREE_COL_NAME
    assert panel._thumb_list_model.all_paths() == expected
    assert header.sortIndicatorSection() == _TREE_COL_NAME
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
