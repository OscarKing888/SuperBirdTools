"""Successful deletion restores the preceding visible photo after async reload."""
from pathlib import Path
import time

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._browser_core import _TREE_COL_NAME
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
    wait_for(lambda: widget._directory_scan_worker is None and widget._active_view_current_path() == paths[3])
    assert widget._active_view_selected_paths() == [paths[3]]
