"""Real-window scope toggling, filtering, refresh and persisted settings."""
import time

import pytest
from PIL import Image

from SuperViewer.superviewer import paths_settings
from SuperViewer.tests.test_preview_info_sync import _APP, window


def _wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.003)
    assert predicate(), "directory scope did not settle"


def test_directory_scope_defaults_on_and_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(tmp_path / "user"))
    assert paths_settings.load_include_subdirectories_from_settings() is True
    paths_settings._save_settings({"label": "中文配置"})
    for enabled in (False, True):
        paths_settings.save_include_subdirectories_to_settings(enabled)
        assert paths_settings.load_include_subdirectories_from_settings() is enabled
        assert paths_settings._load_settings()["label"] == "中文配置"


@pytest.mark.parametrize("mode", ["list", "thumb"])
def test_scope_toggle_filters_refresh_and_restore(window, tmp_path, monkeypatch, mode):
    directory = tmp_path / "照片"
    child = directory / "子目录"
    child.mkdir(parents=True)
    paths = [directory / "白鹭.JPG", child / "白鹭幼鸟.JPG"]
    for path in paths:
        Image.new("RGB", (32, 24)).save(path)
    panel = window._file_list
    # Exercise real scanning and model updates without unrelated decoders/cache writers.
    monkeypatch.setattr(panel, "_start_metadata_loader", lambda *_: None)
    monkeypatch.setattr(panel, "_start_photo_tag_cache_loader_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(panel, "_schedule_persistent_thumb_cache_build", lambda *_: None)
    monkeypatch.setattr(panel, "_schedule_visible_thumbnail_update", lambda *a, **kw: None)
    monkeypatch.setattr(panel, "_emit_file_selected_for_path", lambda *a, **kw: None)
    panel._view_mode = panel._MODE_LIST if mode == "list" else panel._MODE_THUMB
    checkbox = window._dir_browser._include_subdirectories_checkbox
    assert checkbox.isChecked()

    def wait_for(expected):
        _wait_until(lambda: not panel.has_pending_directory_scans() and set(panel._all_files) == expected)
        assert set(panel._filtered_files) == expected
        model = panel._file_table_model if mode == "list" else panel._thumb_list_model
        _wait_until(lambda: model.rowCount() == len(expected))

    window._on_directory_selected(str(directory))
    wait_for({str(path) for path in paths})
    checkbox.click()
    wait_for({str(paths[0])})
    panel._filter_edit.setText("白鹭")
    panel._refresh_filter_scope()
    assert not panel._include_subdirectories
    wait_for({str(paths[0])})
    panel.load_directory(str(directory), force_reload=True)
    wait_for({str(paths[0])})

    # Tag filtering must not silently re-enable recursive scanning either.
    panel._photo_tag_cache = {str(path): {"飞行"} for path in paths}
    panel._active_tag_filters = {"飞行"}
    panel._refresh_filter_scope()
    wait_for({str(paths[0])})
    panel._active_tag_filters.clear()

    restored = type(window)(initial_received_files=["skip-restore"])
    try:
        assert not restored._dir_browser._include_subdirectories_checkbox.isChecked()
        assert not restored._file_list._include_subdirectories
    finally:
        restored.close()
        _wait_until(lambda: restored._shutdown_finalized)
        restored.deleteLater()
    checkbox.click()
    wait_for({str(path) for path in paths})
    checkbox.click()
    window._on_directory_selected(str(child))
    wait_for({str(paths[1])})
