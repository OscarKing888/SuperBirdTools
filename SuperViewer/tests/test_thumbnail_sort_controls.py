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


def test_thumbnail_identity_grid_incremental_metadata_and_hover(panel, tmp_path, monkeypatch):
    from PyQt6.QtCore import QEvent, QPoint
    from PyQt6.QtGui import QHelpEvent
    from SuperViewer.superviewer.thumbnail_metadata import BirdThumbnailDelegate, _BirdDetailsRole
    from app_common.file_browser._browser_core import _MetaSpeciesCnRole

    path = str(tmp_path / "白鹭.jpg")
    panel._current_dir = str(tmp_path)
    panel._all_files = [path]
    panel._meta_cache = {path: {"title": "白鹭", "gbif_rarity_100": 0}}
    panel._rebuild_views()
    index = panel._thumb_list_model.index(0, 0)
    assert index.data(_MetaSpeciesCnRole) == "白鹭"
    panel._on_metadata_batch_ready({path: {"title": "黑脸琵鹭", "gbif_rarity_100": 80,
                                          "iucn_category": "EN", "shooting_location": "深圳湾"}})
    panel._apply_meta_batch_tick()
    assert index.data(_BirdDetailsRole).score == 80
    assert index.data(_MetaSpeciesCnRole) == "黑脸琵鹭"
    for size in (128, 256):
        panel._thumb_size = size
        panel._update_thumb_display()
        assert panel._list_widget.gridSize() == BirdThumbnailDelegate.grid_size(size)
        assert panel._list_widget.iconSize().width() == size
    shown = []
    monkeypatch.setattr(panel_module.QToolTip, "showText", lambda _pos, text, _widget: shown.append(text))
    monkeypatch.setattr(panel, "_find_thumb_index_for_tooltip", lambda _pos: index)
    event = QHelpEvent(QEvent.Type.ToolTip, QPoint(10, 10), QPoint(10, 10))
    assert panel.eventFilter(panel._list_widget.viewport(), event)
    assert "黑脸琵鹭" in shown[0] and "深圳湾" in shown[0] and "GBIF 80/100" in shown[0]


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


def test_rarity_column_badges_sort_refresh_and_unknown_stays_empty(panel, tmp_path, monkeypatch):
    from app_common.file_browser._browser_core import _TREE_COL_SPECIES, _SortRole
    from SuperViewer.superviewer.rarity_file_table import RarityBadgeDelegate

    monkeypatch.setattr(superviewer_user_options, '_RUNTIME_OPTIONS',
                        superviewer_user_options.normalize_user_options({}))
    paths = [str(tmp_path / name) for name in ('未知.jpg', '零分.jpg', '稀有.jpg', '传奇.jpg')]
    panel._current_dir = str(tmp_path)
    panel._all_files = paths
    panel._loaded_directory_recursive = True
    panel._meta_cache = {path: {'gbif_rarity_100': score} for path, score in zip(paths, (None, 0, 25, 99))}
    panel._btn_list.click()
    # 列表构建/绘制只使用已有元数据，不生成缩略图或读取文件提示。
    def unexpected(*_args, **_kwargs):
        raise AssertionError('稀有度列表不应读取文件或生成缩略图')
    monkeypatch.setattr(panel, '_start_thumbnail_loader', unexpected)
    panel._rebuild_views()
    model = panel._file_table_model
    col = model.rarity_column
    assert model.headerData(col, Qt.Orientation.Horizontal) == '稀有度'
    assert panel._tree_widget.header().visualIndex(col) == panel._tree_widget.header().visualIndex(_TREE_COL_SPECIES) + 1
    assert isinstance(panel._tree_widget.itemDelegateForColumn(col), RarityBadgeDelegate)
    assert _shared_table_headers() == _FILE_TABLE_HEADERS
    assert [model.index_for_path(path, col).data() for path in paths] == ['', '普通', '稀有', '传奇']
    assert model.index_for_path(paths[0], col).data(Qt.ItemDataRole.ToolTipRole) == ''
    assert '0/100' in model.index_for_path(paths[1], col).data(Qt.ItemDataRole.ToolTipRole)
    assert panel._tree_widget.columnWidth(col) == 88
    panel._tree_widget.header().setSortIndicator(col, Qt.SortOrder.AscendingOrder)
    ascending = [paths[1], paths[2], paths[3], paths[0]]
    assert _paths_in_tree(panel) == ascending
    panel._btn_thumb.click()
    assert panel._thumb_list_model.all_paths() == ascending
    panel._sort_order_button.click()
    assert panel._thumb_list_model.all_paths() == ascending[::-1]
    panel._btn_list.click()
    assert _paths_in_tree(panel) == ascending[::-1]

    # 同一缓存更新入口即时改变徽章；无效值清空，不能把 0 分误判为空。
    panel.sync_metadata_edits_for_paths({paths[2]: {'gbif_rarity_100': 60}})
    assert model.index_for_path(paths[2], col).data() == '史诗'
    panel.sync_metadata_edits_for_paths({paths[2]: {'gbif_rarity_100': ''}})
    assert model.index_for_path(paths[2], col).data() == ''
    superviewer_user_options.apply_runtime_user_options({'rarity_badge_common_text': '常见'})
    monkeypatch.setattr(FileListPanel, 'apply_user_options', lambda self: None)
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    panel.apply_user_options()
    assert model.index_for_path(paths[1], col).data() == '常见'
    assert model.index_for_path(paths[1], col).data(_SortRole) == (False, 0)
    assert not resets


def _shared_table_headers():
    from app_common.file_browser._models import FileTableModel
    model = FileTableModel()
    return [model.headerData(col, Qt.Orientation.Horizontal) for col in range(model.columnCount())]
