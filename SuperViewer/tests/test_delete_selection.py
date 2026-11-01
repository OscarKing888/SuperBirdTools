"""Deletion prunes cached data and preserves the surviving visible photos."""
from concurrent.futures import Future
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._browser_core import _TREE_COL_NAME, _ThumbPixmapRole
from app_common.file_browser._metadata_commit import get_metadata_commit_bridge
from app_common.file_browser._panel import FileListPanel
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


def wait_for_trash(widget):
    wait_for(lambda: get_metadata_commit_bridge(widget).is_idle)


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
    paths = [str(tmp_path / f"照片{i}.jpg") for i in range(5)]
    for path in paths:
        Path(path).write_bytes(b"test photo")
    widget._current_dir = str(tmp_path)
    widget._all_files = paths
    widget._set_view_mode(request.param)
    widget._rebuild_views()
    widget.resize(700, 500)
    widget.show()
    wait_for(lambda: len(widget._filtered_files) == 5)
    yield widget, paths
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


@pytest.mark.parametrize("selected,current,failed,expected", [
    ([2], 2, [], 1),
    ([0], 0, [], 1),
    ([4], 4, [], 3),
    ([1, 2, 3], 3, [], 0),
    ([1, 3], 3, [], 2),
    ([0, 1, 2, 3, 4], 4, [], None),
    ([2], 2, [2], 2),
    ([1, 2], 2, [1], 1),
    ([1, 2], 2, [2], 2),
])
def test_delete_and_reload_selection(panel, monkeypatch, selected, current, failed, expected):
    widget, paths = panel
    widget.set_pending_selection([paths[i] for i in selected], paths[current])
    wait_for(lambda: widget._active_view_current_path() == paths[current])
    emitted = []
    widget.file_selected.connect(emitted.append)

    def trash(path):
        if path in [paths[i] for i in failed]:
            return False
        Path(path).unlink()
        return True

    monkeypatch.setattr(panel_module, "move_to_trash", trash)
    widget._move_paths_to_trash([paths[i] for i in selected])
    wait_for_trash(widget)
    remaining = 5 - len(set(selected) - set(failed))
    wait_for(lambda: len(widget._filtered_files) == remaining and widget._directory_scan_worker is None)
    target = paths[expected] if expected is not None else ""
    wait_for(lambda: widget._active_view_current_path() == target)
    assert widget._active_view_selected_paths() == ([target] if target else [])
    if set(selected) - set(failed) and target:
        assert emitted and set(emitted) == {target}


def test_delete_uses_filtered_descending_order(panel, monkeypatch):
    widget, paths = panel
    renamed = []
    for i, path in enumerate(paths):
        target = Path(path).with_name(f"{i}{'隐藏' if i == 2 else '保留'}.jpg")
        Path(path).rename(target)
        renamed.append(str(target))
    widget._all_files = paths = renamed
    widget._loaded_directory_recursive = widget._include_subdirectories
    widget._rebuild_views()
    widget._filter_edit.setText("保留")
    widget._tree_widget.sortByColumn(_TREE_COL_NAME, Qt.SortOrder.DescendingOrder)
    widget.set_pending_selection([paths[1]], paths[1])
    monkeypatch.setattr(panel_module, "move_to_trash", lambda path: (Path(path).unlink() or True))
    widget._move_paths_to_trash([paths[1]])
    wait_for_trash(widget)
    wait_for(lambda: widget._directory_scan_worker is None and widget._active_view_current_path() == paths[3])
    assert widget._active_view_selected_paths() == [paths[3]]


def test_delete_keeps_remaining_metadata_and_model_rows(panel, monkeypatch):
    widget, paths = panel
    records = {path: {"title": f"中文鸟名{i}", "iso": str(100 * (i + 1)), "rating": i}
               for i, path in enumerate(paths)}
    widget._meta_cache.update(records)
    widget._rebuild_views()
    model = (widget._thumb_list_model if widget._view_mode == widget._MODE_THUMB
             else widget._file_table_model)
    wait_for(lambda: model.rowCount() == len(paths))
    survivor = QPersistentModelIndex(model.index_for_path(paths[4]))
    pixmap_key = None
    if widget._view_mode == widget._MODE_THUMB:
        pixmap = QPixmap(32, 24)
        pixmap.fill(Qt.GlobalColor.green)
        widget._thumb_list_model.set_pixmap_for_path(paths[4], pixmap, 256)
        pixmap_key = pixmap.cacheKey()
    widget.set_pending_selection([paths[1], paths[2]], paths[2])
    resets, reloads, metadata_starts = [], [], []
    model.modelReset.connect(lambda: resets.append(True))
    monkeypatch.setattr(widget, "load_directory", lambda *a, **k: reloads.append((a, k)))
    monkeypatch.setattr(widget, "_start_metadata_loader", lambda *a, **k: metadata_starts.append((a, k)))
    # 一个删除失败时，其行与元数据必须保留，只剔除实际成功的照片。
    def trash(path):
        if path == paths[1]:
            return False
        Path(path).unlink()
        return True

    monkeypatch.setattr(panel_module, "move_to_trash", trash)
    widget._move_paths_to_trash([paths[1], paths[2]])
    wait_for_trash(widget)
    assert reloads == []
    assert metadata_starts == []
    assert resets == []
    assert paths[2] not in widget._all_files
    assert paths[2] not in widget._meta_cache
    for path in (paths[0], paths[1], paths[3], paths[4]):
        assert widget._meta_cache[path] is records[path]
        assert path in model.all_paths()
    assert survivor.isValid()
    assert survivor.data(Qt.ItemDataRole.UserRole) == paths[4]
    if pixmap_key is not None:
        assert survivor.data(_ThumbPixmapRole).cacheKey() == pixmap_key
    assert Path(paths[1]).exists()
    assert widget._active_view_current_path() == paths[1]


def test_late_metadata_and_tags_cannot_restore_deleted_photo(panel, monkeypatch):
    widget, paths = panel
    widget._meta_cache.update({path: {"iso": "100", "title": "原鸟名"} for path in paths})
    widget._photo_tag_cache.update({path: {"保留标签"} for path in paths})
    widget.set_pending_selection([paths[2]], paths[2])
    tag_worker = SimpleNamespace(_tag_generation_snapshot={})
    monkeypatch.setattr(panel_module, "move_to_trash", lambda path: (Path(path).unlink() or True))
    widget._move_paths_to_trash([paths[2]])
    wait_for_trash(widget)
    changed = []
    widget.photo_metadata_cache_updated.connect(lambda batch: changed.extend(batch))
    widget._on_metadata_batch_ready({
        paths[2]: {"iso": "800", "title": "已删旧结果"},
        paths[4]: {"iso": "400", "title": "正常新结果"},
    })
    previous = widget._photo_tag_loader
    widget._photo_tag_loader = tag_worker
    try:
        widget._on_photo_tag_cache_batch_ready(tag_worker, {paths[2]: {"迟到标签"}})
    finally:
        widget._photo_tag_loader = previous
    _APP.processEvents()
    assert paths[2] not in widget._meta_cache
    assert paths[2] not in widget._photo_tag_cache
    assert paths[2] not in changed
    assert paths[2] not in widget._all_files
    assert widget._meta_cache[paths[4]]["iso"] == "400"
    assert paths[4] in changed


def test_cache_cleanup_failure_still_removes_successfully_trashed_photo(panel, monkeypatch):
    from app_common.exif_io.metadata_store import get_store

    widget, paths = panel
    widget._meta_cache.update({path: {"title": "缓存鸟名"} for path in paths})
    widget.set_pending_selection([paths[2]], paths[2])
    failed_cleanup = Future()
    failed_cleanup.set_exception(OSError("模拟本地缓存数据库不可写"))
    monkeypatch.setattr(get_store(), "invalidate_paths", lambda _paths: failed_cleanup)
    monkeypatch.setattr(panel_module, "move_to_trash", lambda path: (Path(path).unlink() or True))
    widget._move_paths_to_trash([paths[2]])
    wait_for_trash(widget)
    assert not Path(paths[2]).exists()
    assert paths[2] not in widget._all_files
    assert paths[2] not in widget._filtered_files
    assert paths[2] not in widget._meta_cache
    assert widget._active_view_current_path() == paths[1]
    status = widget._meta_progress.format()
    assert "已移入回收站 1 个文件" in status
    assert "本地元数据缓存清理失败" in status
    assert "删除未完成" not in status


def test_same_directory_noop_keeps_deleted_metadata_protection(panel, monkeypatch):
    widget, paths = panel
    widget._loaded_directory_recursive = widget._include_subdirectories
    monkeypatch.setattr(panel_module, "move_to_trash", lambda path: (Path(path).unlink() or True))
    widget._move_paths_to_trash([paths[2]])
    wait_for_trash(widget)
    monkeypatch.setattr(widget, "_set_tag_config_directory", lambda _path: False)
    monkeypatch.setattr(widget, "_load_tag_config_if_changed", lambda **_kwargs: False)
    widget.load_directory(widget._current_dir)
    assert widget._directory_scan_worker is None
    assert os.path.normcase(paths[2]) in widget._trashed_metadata_paths
    widget._on_metadata_batch_ready({paths[2]: {"iso": "800", "title": "删除前的迟到元数据"}})
    assert paths[2] not in widget._meta_cache
    assert paths[2] not in widget._photo_tag_cache
    assert paths[2] not in widget._all_files


def test_stale_directory_snapshot_cannot_restore_deleted_metadata(panel, monkeypatch):
    widget, paths = panel
    snapshot = {
        "files": list(paths),
        "metadata": {path: {"iso": "800", "title": "删除前的目录快照"} for path in paths},
    }
    monkeypatch.setattr(panel_module, "move_to_trash", lambda path: (Path(path).unlink() or True))
    widget._move_paths_to_trash([paths[2]])
    wait_for_trash(widget)
    applied = []
    monkeypatch.setattr(widget, "_apply_directory_listing_result",
                        lambda _directory, files, *_args, **_kwargs: applied.append(files))
    widget._show_directory_snapshot(snapshot)
    assert applied == [[path for path in paths if path != paths[2]]]
    assert paths[2] not in widget._meta_cache
    assert widget._meta_cache[paths[4]]["iso"] == "800"
    # 不修改 worker 所持的旧结果，过滤只影响本次 GUI 应用。
    assert snapshot["files"] == paths
    assert paths[2] in snapshot["metadata"]


def test_late_scan_keeps_existing_metadata_loader_after_deletion(panel, monkeypatch):
    widget, paths = panel
    widget._directory_snapshot_shown = True
    widget._metadata_revalidate_pending = True
    previous_loader = widget._metadata_loader
    active_loader = SimpleNamespace(_paths=list(paths))
    widget._metadata_loader = active_loader
    started = []
    tag_requests = []
    monkeypatch.setattr(widget, "_start_metadata_loader", lambda files: started.append(files))
    monkeypatch.setattr(widget, "_start_photo_tag_cache_loader_if_needed",
                        lambda files, **_kwargs: tag_requests.append(list(files)))
    monkeypatch.setattr(panel_module, "move_to_trash", lambda path: (Path(path).unlink() or True))
    try:
        widget._move_paths_to_trash([paths[2]])
        wait_for_trash(widget)
        remaining = [path for path in paths if path != paths[2]]
        assert widget._all_files == remaining
        tag_requests.clear()
        # 扫描仍持有删除前的列表；现有元数据任务已覆盖全部幸存照片。
        widget._apply_directory_listing_result(
            widget._current_dir, list(paths), {}, None,
            recursive=widget._include_subdirectories,
        )
        assert widget._all_files == remaining
        assert started == []
        assert tag_requests == [remaining]
        assert widget._metadata_loader is active_loader
        assert not widget._metadata_revalidate_pending
    finally:
        widget._metadata_loader = previous_loader


def test_delete_completion_after_directory_switch_prunes_old_snapshot_only(panel, monkeypatch, tmp_path):
    widget, paths = panel
    original_dir = widget._current_dir
    options = dict(include_videos=widget.include_videos, use_report_db=widget._use_report_db)
    widget._directory_snapshots.remember(
        original_dir, widget._include_subdirectories,
        {"files": paths, "report_root": None, "report_cache": {}, "report_row_by_path": {},
         "metadata": {path: {"title": "原目录照片"} for path in paths}}, **options,
    )
    entered, proceed = threading.Event(), threading.Event()

    def trash(path):
        entered.set()
        if not proceed.wait(5):
            raise TimeoutError("test did not release deletion worker")
        Path(path).unlink()
        return True

    monkeypatch.setattr(panel_module, "move_to_trash", trash)
    widget._move_paths_to_trash([paths[2]])
    try:
        wait_for(entered.is_set)
        other_dir = tmp_path / "新目录"
        other_dir.mkdir()
        other = str(other_dir / "另一只鸟.jpg")
        Path(other).write_bytes(b"another photo")
        # 模拟已经完成的新目录扫描，避免测试依赖后台扫描的调度顺序。
        widget._current_dir = str(other_dir)
        widget._all_files = [other]
        metadata = {"title": "新目录元数据", "iso": "200"}
        widget._meta_cache = {other: metadata}
        widget._rebuild_views()
        widget.set_pending_selection([other], other)
        wait_for(lambda: widget._active_view_current_path() == other)
    finally:
        proceed.set()
    wait_for_trash(widget)
    assert widget._all_files == [other]
    assert widget._meta_cache == {other: metadata}
    assert widget._active_view_current_path() == other
    old_snapshot = widget._directory_snapshots.peek(original_dir, widget._include_subdirectories, **options)
    if old_snapshot is not None:
        assert old_snapshot["files"] == [path for path in paths if path != paths[2]]
        assert paths[2] not in old_snapshot["metadata"]
