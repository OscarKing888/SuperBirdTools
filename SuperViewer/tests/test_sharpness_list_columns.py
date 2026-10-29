"""Viewer 三项清晰度列使用缓存，并保持浏览选择与两种视图的排序一致。"""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QAbstractItemView

from app_common import superviewer_user_options as options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._browser_core import _TREE_COL_BIRD_SHARP, _MetaBirdSharpRole
from app_common.file_browser._panel import FileListPanel
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setattr(options, "_RUNTIME_OPTIONS", options.normalize_user_options({}))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_a, **_kw: None)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **_kw: None)
    widget = SuperViewerTaggedFileListPanel(tag_config_path=tmp_path / "tags.cfg")
    yield widget
    widget.close_tag_store()
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def _meta(head=None, body=None, mean=None):
    return {"bird_sharpness_verdict": "sharp", "bird_sharpness_sigma": .62,
            "bird_sharpness_score": 828, "bird_sharpness_head_score": head,
            "bird_sharpness_body_score": body, "bird_sharpness_mean_score": mean}


def _populate(panel, tmp_path):
    paths = [str(tmp_path / name) for name in ("甲.jpg", "乙.jpg", "旧记录.jpg", "零分.jpg")]
    panel._current_dir = str(tmp_path)
    panel._all_files = paths
    panel._loaded_directory_recursive = True
    panel._meta_cache = dict(zip(paths, (_meta(10, 100, 55), _meta(100, 1, 50.5), _meta(), _meta(0, 0, 0))))
    panel._btn_list.click()
    panel._rebuild_views()
    return paths


def _tree_paths(panel):
    model = panel._tree_widget.model()
    return [panel._tree_path_from_index(model.index(row, 0)) for row in range(model.rowCount())]


def test_columns_are_adjacent_and_sort_values_match_across_views(panel, tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("列表分数只读缓存，不应加载缩略图或读取文件")

    monkeypatch.setattr(panel, "_start_thumbnail_loader", unexpected)
    paths = _populate(panel, tmp_path)
    model, header = panel._file_table_model, panel._tree_widget.header()
    head, body = model.head_score_column, model.body_score_column
    assert header.visualIndex(head) == header.visualIndex(_TREE_COL_BIRD_SHARP) + 1
    assert header.visualIndex(body) == header.visualIndex(head) + 1
    assert model.headerData(_TREE_COL_BIRD_SHARP, Qt.Orientation.Horizontal) == "鸟清晰"
    for column in (head, body):
        assert panel._sort_column_combo.findData(column) >= 0
        text = model.headerData(column, Qt.Orientation.Horizontal)
        assert panel._tree_widget.columnWidth(column) >= header.fontMetrics().horizontalAdvance(text) + 12
    assert [model.index_for_path(path, _TREE_COL_BIRD_SHARP).data() for path in paths] == ["55.0", "50.5", "—", "0.0"]
    for column, ascending in ((_TREE_COL_BIRD_SHARP, [paths[3], paths[1], paths[0], paths[2]]),
                              (head, [paths[3], paths[0], paths[1], paths[2]]),
                              (body, [paths[3], paths[1], paths[0], paths[2]])):
        header.setSortIndicator(column, Qt.SortOrder.AscendingOrder)
        assert _tree_paths(panel) == ascending
        panel._btn_thumb.click()
        assert panel._thumb_list_model.all_paths() == ascending
        panel._sort_order_button.click()
        assert panel._thumb_list_model.all_paths() == ascending[::-1]
        panel._btn_list.click()
        assert _tree_paths(panel) == ascending[::-1]
    # 三项数字不覆盖缩略图仍使用的原主判定/σ。
    thumb = panel._thumb_list_model.index_for_path(paths[0])
    assert thumb.data(_MetaBirdSharpRole).text() == "清晰 0.62"


def test_metadata_refresh_clears_parts_and_preserves_selection_and_column_width(panel, tmp_path):
    paths = _populate(panel, tmp_path)
    model, view = panel._file_table_model, panel._tree_widget
    head, body = model.head_score_column, model.body_score_column
    view.setColumnWidth(head, 145)
    index = view.model().index(1, 0)
    view.setCurrentIndex(index)
    selected = panel._tree_path_from_index(index)
    resets = []
    model.modelReset.connect(lambda: resets.append(True))
    panel.sync_metadata_edits_for_paths({paths[0]: _meta(0, 841, 420.5)})
    assert [model.index_for_path(paths[0], c).data() for c in (head, body, _TREE_COL_BIRD_SHARP)] == ["0", "841", "420.5"]
    panel.sync_metadata_edits_for_paths({paths[0]: {"bird_sharpness_head_score": "",
                                                 "bird_sharpness_body_score": "",
                                                 "bird_sharpness_mean_score": ""}})
    assert all(model.index_for_path(paths[0], c).data() == "—" for c in (head, body, _TREE_COL_BIRD_SHARP))
    assert panel._tree_path_from_index(view.currentIndex()) == selected
    assert not resets and view.columnWidth(head) == 145
    panel._btn_thumb.click()
    panel._btn_list.click()
    assert view.columnWidth(head) == 145


def test_narrow_list_can_scroll_to_columns_and_keyboard_selection_still_works(panel, tmp_path):
    _populate(panel, tmp_path)
    panel.resize(660, 430)
    panel.show()
    _APP.processEvents()
    view, model = panel._tree_widget, panel._file_table_model
    column = model.body_score_column
    index = view.model().index(0, column)
    view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
    _APP.processEvents()
    assert view.viewport().rect().contains(view.visualRect(index).center())
    view.setCurrentIndex(index)
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Down)
    _APP.processEvents()
    assert view.currentIndex().row() == 1
    assert model.index(0, column).isValid()
