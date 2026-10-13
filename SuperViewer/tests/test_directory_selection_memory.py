"""Directory return restores a photo, safely across rescan and incremental views."""
from pathlib import Path
import time

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._browser_core import _TREE_COL_NAME
from app_common.file_browser._panel import FileListPanel
from app_common.report_db import ReportDB
from SuperViewer.superviewer.directory_selection import DirectorySelectionCache
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def photos(directory, count=8):
    directory.mkdir()
    paths = [str(directory / f"照片{i:03}.jpg") for i in range(count)]
    for path in paths:
        Image.new("RGB", (16, 12), "green").save(path)
    return paths


@pytest.fixture(params=[FileListPanel._MODE_LIST, FileListPanel._MODE_THUMB])
def panel(request, tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *a, **k: None)
    monkeypatch.setattr(FileListPanel, "_start_metadata_loader", lambda *a, **k: None)
    monkeypatch.setattr(FileListPanel, "_schedule_persistent_thumb_cache_build", lambda *a, **k: None)
    monkeypatch.setattr(SuperViewerTaggedFileListPanel, "_start_photo_tag_cache_loader_if_needed", lambda *a, **k: None)
    widget = SuperViewerTaggedFileListPanel(tag_config_path=tmp_path / "tags.cfg")
    widget._use_report_db = False
    widget._set_view_mode(request.param)
    widget.resize(700, 500)
    widget.show()
    yield widget
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def active_view(panel):
    return panel._list_widget if panel._view_mode == panel._MODE_THUMB else panel._tree_widget


def ready(panel):
    dirty = panel._thumb_model_dirty if panel._view_mode == panel._MODE_THUMB else panel._tree_view_dirty
    return not panel._directory_scan_workers and panel._pending_directory_listing_result is None and not dirty


def load(panel, directory):
    panel.load_directory(str(directory))
    wait_for(lambda: ready(panel))


def choose(panel, path):
    # 使用真实视图 currentChanged，覆盖点击/方向键共用的选择入口。
    index = panel._thumb_index_for_path(path) if panel._view_mode == panel._MODE_THUMB else panel._tree_index_for_path(path)
    active_view(panel).setCurrentIndex(index)
    wait_for(lambda: panel._active_view_current_path() == path)


def test_return_remembers_each_directory_and_scrolls_to_photo(panel, tmp_path):
    a, b = tmp_path / "目录甲", tmp_path / "目录乙"
    paths_a, paths_b = photos(a, 45), photos(b)
    load(panel, a)
    choose(panel, paths_a[40])
    load(panel, b)
    choose(panel, paths_b[5])
    emitted = []
    panel.file_selected.connect(emitted.append)
    load(panel, a)
    assert panel._active_view_selected_paths() == [paths_a[40]]
    assert emitted == [paths_a[40]]
    view = active_view(panel)
    wait_for(lambda: view.viewport().rect().intersects(view.visualRect(view.currentIndex())))
    assert view.verticalScrollBar().value() > 0
    load(panel, b)
    assert panel._active_view_current_path() == paths_b[5]


@pytest.mark.parametrize("deleted,expected", [([0, 1], 6), ([6], 7), ([5, 6, 7], 4), (list(range(8)), None)])
def test_external_deletion_prefers_path_then_clamps_row(panel, tmp_path, deleted, expected):
    a, b = tmp_path / "甲", tmp_path / "乙"
    paths = photos(a)
    photos(b)
    load(panel, a)
    choose(panel, paths[6])
    load(panel, b)
    for row in deleted:
        Path(paths[row]).unlink()
    emitted = []
    panel.file_selected.connect(emitted.append)
    load(panel, a)
    target = paths[expected] if expected is not None else ""
    assert panel._active_view_current_path() == target
    assert emitted == ([target] if target else [])


def test_view_switch_and_descending_filter_use_current_display_order(panel, tmp_path):
    a, b = tmp_path / "甲", tmp_path / "乙"
    paths = photos(a)
    photos(b)
    load(panel, a)
    panel._on_tree_sort_indicator_changed(_TREE_COL_NAME, Qt.SortOrder.DescendingOrder)
    choose(panel, paths[5])  # 降序位置 2。
    load(panel, b)
    panel._set_view_mode(panel._MODE_THUMB if panel._view_mode == panel._MODE_LIST else panel._MODE_LIST)
    Path(paths[5]).unlink()
    load(panel, a)
    assert panel._active_view_current_path() == paths[4]
    load(panel, b)
    panel._filter_edit.setText("003")
    load(panel, a)
    assert panel._active_view_current_path() == paths[3]


def test_incremental_population_does_not_select_first_or_override_user(panel, tmp_path, monkeypatch):
    a, b = tmp_path / "甲", tmp_path / "乙"
    paths = photos(a, 60)
    photos(b)
    load(panel, a)
    choose(panel, paths[-1])
    load(panel, b)
    monkeypatch.setattr(panel_module, "_THUMB_MODEL_APPEND_BATCH_SIZE", 2)
    emitted = []
    panel.file_selected.connect(emitted.append)
    load(panel, a)
    assert emitted == [paths[-1]]
    load(panel, b)
    panel.load_directory(str(a))
    # 收到扫描结果时只填完首批；模拟用户先选了另一张，记忆不能抢回选择。
    def select_during_population(_paths):
        if panel.get_current_dir() == str(a):
            choose(panel, paths[1])
    panel.files_loaded.connect(select_during_population)
    wait_for(lambda: ready(panel))
    assert panel._active_view_current_path() == paths[1]


def test_rapid_switch_back_without_finished_scan_keeps_previous_memory(panel, tmp_path):
    a, b = tmp_path / "甲", tmp_path / "乙"
    paths = photos(a)
    photos(b)
    load(panel, a)
    choose(panel, paths[6])
    load(panel, b)
    panel.load_directory(str(a))
    panel.load_directory(str(b))
    panel.load_directory(str(a))
    wait_for(lambda: ready(panel))
    assert panel._active_view_current_path() == paths[6]


@pytest.mark.parametrize("delete_all", [False, True])
def test_report_root_and_child_memory_skip_stale_deleted_rows(panel, tmp_path, delete_all):
    root = tmp_path / "报告目录"
    root.mkdir()
    child = root / "子目录"
    paths = photos(child)
    db = ReportDB(str(root))
    try:
        for path in paths:
            db.insert_photo({
                "filename": Path(path).stem,
                "original_path": "DCIM/" + Path(path).name,
                "current_path": str(Path(path).relative_to(root).with_suffix(".xmp")),
                "bird_species_cn": "白鹭",
            })
    finally:
        db.close()
    database = root / ".superpicky/report.db"
    before = database.read_bytes()
    panel._use_report_db = True
    load(panel, root)
    choose(panel, paths[6])
    report_cache = panel._report_full_cache
    load(panel, child)
    choose(panel, paths[2])
    for path in paths if delete_all else [paths[6]]:
        Path(path).unlink()
    emitted = []
    panel.file_selected.connect(emitted.append)
    load(panel, root)
    assert panel._active_view_current_path() == ("" if delete_all else paths[7])
    assert emitted == ([] if delete_all else [paths[7]])
    assert panel._report_full_cache == report_cache
    load(panel, child)
    assert panel._active_view_current_path() == ("" if delete_all else paths[2])
    assert database.read_bytes() == before


def test_same_directory_refresh_keeps_selected_photo(panel, tmp_path):
    directory = tmp_path / "甲"
    paths = photos(directory)
    load(panel, directory)
    choose(panel, paths[6])
    Path(paths[0]).unlink()
    panel.load_directory(str(directory), force_reload=True)
    wait_for(lambda: ready(panel))
    assert panel._active_view_current_path() == paths[6]


@pytest.mark.parametrize("before_load", [False, True])
def test_explicit_requested_photo_takes_priority(panel, tmp_path, before_load):
    a, b = tmp_path / "甲", tmp_path / "乙"
    paths = photos(a)
    photos(b)
    load(panel, a)
    choose(panel, paths[6])
    load(panel, b)
    if before_load:
        panel.set_pending_selection([paths[2]], apply_immediately=False)
    panel.load_directory(str(a))
    if not before_load:
        panel.set_pending_selection([paths[2]], apply_immediately=False)
    wait_for(lambda: ready(panel))
    assert panel._active_view_current_path() == paths[2]


def test_selection_cache_is_bounded_and_normalizes_paths(tmp_path):
    cache = DirectorySelectionCache(max_entries=2)
    a, b, c = (str(tmp_path / name) for name in ("甲", "乙", "丙"))
    cache.remember(a, "first.jpg", 1)
    cache.remember(b, "second.jpg", 2)
    assert cache.get(str(Path(a) / ".")).row == 1
    cache.remember(c, "third.jpg", 3)
    assert cache.get(b) is None
    cache.remember(a, "", -1)
    assert cache.get(a).path == "first.jpg"
    assert cache.get(a).resolve([], lambda _: True) == ""
    # 即使报告仍返回已删除文件，恢复也跳过不存在的源文件。
    assert cache.get(a).resolve(["first.jpg", "gone.jpg", "alive.jpg"], lambda path: path == "alive.jpg") == "alive.jpg"
