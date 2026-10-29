"""Viewer 清晰度三列只读缓存，按分数排序并保留共享列兼容。"""
import pytest
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QApplication

from app_common.bird_sharpness_fields import bird_sharpness_from_meta
from app_common.file_browser._browser_core import (
    _FILE_TABLE_HEADERS, _TREE_COL_BIRD_SHARP, _TREE_COL_CAMERA, _SortRole,
)
from app_common.file_browser._models import FileTableModel, FileTableSortProxyModel, _VIDEO_HEADERS
from SuperViewer.superviewer.file_drag_views import DragFileTableModel
from SuperViewer.superviewer.rarity_file_table import RarityFileTableModel
from SuperViewer.superviewer.sharpness_file_table import SharpnessFileTableModel


_APP = QApplication.instance() or QApplication([])
_Role = Qt.ItemDataRole


def _meta(head=None, body=None, mean=None):
    return {
        'bird_sharpness_verdict': 'sharp', 'bird_sharpness_sigma': .62,
        'bird_sharpness_head_score': head, 'bird_sharpness_body_score': body,
        'bird_sharpness_mean_score': mean,
    }


def _populate(model, meta_by_path):
    def unexpected_io(_):
        raise AssertionError('清晰度列不得读取文件或触发普通文件工具提示')
    model.rebuild(list(meta_by_path), meta_cache=meta_by_path,
                  tooltip_fn=unexpected_io, mismatch_fn=unexpected_io)


@pytest.mark.parametrize(('head', 'body', 'mean', 'expected'), [
    (40, 235, 137.5, ('137.5', '40', '235')),
    (0, 0, 0.0, ('0.0', '0', '0')),
    (0, 841, 420.5, ('420.5', '0', '841')),
    (None, 154, None, ('—', '—', '154')),
    (1000, None, None, ('—', '1000', '—')),
    (None, None, None, ('—', '—', '—')),
])
def test_stored_part_scores_display_zero_missing_and_half_points(head, body, mean, expected):
    model = SharpnessFileTableModel()
    _populate(model, {'白鹭.ARW': _meta(head, body, mean)})
    columns = (_TREE_COL_BIRD_SHARP, model.head_score_column, model.body_score_column)
    assert tuple(model.index(0, col).data() for col in columns) == expected
    for col in columns:
        index = model.index(0, col)
        assert index.data(_Role.ForegroundRole) is None
        assert '白鹭.ARW' in index.data(_Role.ToolTipRole)
        assert '0–1000' in index.data(_Role.ToolTipRole)
    assert '各占 50%' in model.headerData(_TREE_COL_BIRD_SHARP, Qt.Orientation.Horizontal, _Role.ToolTipRole)


def test_old_no_bird_and_never_analyzed_rows_do_not_substitute_legacy_score():
    model = SharpnessFileTableModel()
    _populate(model, {
        'legacy.jpg': {'bird_sharpness_verdict': 'sharp', 'bird_sharpness_sigma': .5,
                       'bird_sharpness_score': 1000, 'bird_sharpness_version': 'sbt-blur-v18'},
        'no-bird.jpg': {'bird_sharpness_verdict': 'no_bird', 'bird_sharpness_score': 999},
        'new.jpg': {},
    })
    for row in range(3):
        for column in (_TREE_COL_BIRD_SHARP, model.head_score_column, model.body_score_column):
            assert model.index(row, column).data() == '—'


@pytest.mark.parametrize('part', ['mean', 'head', 'body'])
@pytest.mark.parametrize('descending', [False, True])
def test_numeric_proxy_and_path_sort_agree_for_each_part(part, descending):
    model = SharpnessFileTableModel()
    values = {
        'a.jpg': _meta(100, 9, 54.5),
        'b.jpg': _meta(9, 400, 204.5),
        'c.jpg': _meta(0, 20, 10.0),
        'missing.jpg': _meta(),
    }
    _populate(model, values)
    column = {'mean': _TREE_COL_BIRD_SHARP, 'head': model.head_score_column,
              'body': model.body_score_column}[part]
    proxy = FileTableSortProxyModel()
    proxy.setSourceModel(model)
    proxy.sort(column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
    expected = sorted(values, key=lambda path: model.sort_key_for_path(path, values[path], column),
                      reverse=descending)
    assert [proxy.index(row, 0).data(_Role.UserRole) for row in range(4)] == expected
    numeric = [path for path in expected if path != 'missing.jpg']
    assert numeric == sorted(numeric, key=lambda path: values[path][f'bird_sharpness_{part}_score'],
                             reverse=descending)
    for row, path in enumerate(values):
        assert model.index(row, column).data(_SortRole) == model.sort_key_for_path(path, values[path], column)[0]


def test_incremental_refresh_updates_and_clears_all_three_columns():
    model = SharpnessFileTableModel()
    _populate(model, {'更新.jpg': _meta(500, 100, 300)})
    notifications = []
    model.dataChanged.connect(lambda left, right, roles: notifications.append((left.column(), right.column(), roles)))
    assert model.set_meta_for_path('更新.jpg', _meta(0, 841, 420.5))
    columns = (_TREE_COL_BIRD_SHARP, model.head_score_column, model.body_score_column)
    assert [model.index(0, col).data() for col in columns] == ['420.5', '0', '841']
    assert notifications[-1][0] <= min(columns) and notifications[-1][1] >= max(columns)
    assert model.set_meta_for_path('更新.jpg', _meta(None, 154, None))
    assert [model.index(0, col).data() for col in columns] == ['—', '—', '154']
    assert model.set_meta_for_path('更新.jpg', {})
    assert [model.index(0, col).data() for col in columns] == ['—'] * 3


@pytest.mark.parametrize('video_columns', [False, True])
def test_appended_columns_preserve_shared_camera_video_and_rarity_indices(video_columns):
    owner = QObject()
    owner.include_videos = video_columns
    shared = FileTableModel(owner)
    rarity = RarityFileTableModel(owner)
    viewer = SharpnessFileTableModel(owner)
    count = len(_FILE_TABLE_HEADERS) + (len(_VIDEO_HEADERS) if video_columns else 0)
    assert shared.columnCount() == count
    assert viewer.rarity_column == rarity.rarity_column == count
    assert viewer.head_score_column == rarity.columnCount()
    assert viewer.body_score_column == rarity.columnCount() + 1
    assert viewer.columnCount() == rarity.columnCount() + 2
    for column in range(count + 1):
        assert viewer.headerData(column, Qt.Orientation.Horizontal) == rarity.headerData(column, Qt.Orientation.Horizontal)
    assert viewer.headerData(viewer.head_score_column, Qt.Orientation.Horizontal) == '头部清晰度'
    assert viewer.headerData(viewer.body_score_column, Qt.Orientation.Horizontal) == '身体清晰度'
    _populate(viewer, {'bird.mp4': {'camera_model': 'ILCE-1M2', 'video_info': {'duration': 12, 'width': 3840, 'height': 2160},
                                  'gbif_rarity_100': 80}})
    assert viewer.index(0, _TREE_COL_CAMERA).data() == 'ILCE-1M2'
    if video_columns:
        assert viewer.index(0, len(_FILE_TABLE_HEADERS)).data() == '00:12'
        assert viewer.index(0, len(_FILE_TABLE_HEADERS) + 1).data() == '3840 × 2160'
    _populate(shared, {'old.jpg': _meta(40, 235, 137.5)})
    assert shared.index(0, _TREE_COL_BIRD_SHARP).data() == bird_sharpness_from_meta(_meta()).text()


def test_drag_model_retains_drag_flags_and_uses_part_columns():
    model = DragFileTableModel()
    _populate(model, {'bird.jpg': _meta(40, 235, 137.5)})
    assert model.index(0, _TREE_COL_BIRD_SHARP).data() == '137.5'
    assert model.flags(model.index(0, model.head_score_column)) & Qt.ItemFlag.ItemIsDragEnabled
