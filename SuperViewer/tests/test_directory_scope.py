"""真实窗口中的目录范围、过滤、刷新与设置恢复。"""
import pytest
from PIL import Image

from SuperViewer.tests.test_directory_selection_responsiveness import window, _wait_until


@pytest.mark.parametrize("mode", ["list", "thumb"])
def test_scope_toggle_filters_refresh_and_restore(window, tmp_path, monkeypatch, mode):
    directory = tmp_path / "照片"
    child = directory / "子目录"
    child.mkdir(parents=True)
    paths = [directory / "白鹭.JPG", child / "白鹭幼鸟.JPG"]
    for path in paths:
        Image.new("RGB", (32, 24)).save(path)
    panel = window._file_list
    # 目录扫描和模型更新真实运行；无关的元数据、缩略图写入不参与此测试。
    monkeypatch.setattr(panel, "_start_metadata_loader", lambda *_: None)
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


@pytest.mark.parametrize("mode", ["list", "thumb"])
def test_metadata_reused_for_scope_and_directory_return_but_refresh_reloads(
    window, tmp_path, monkeypatch, mode,
):
    from app_common.file_browser import _workers

    directory = tmp_path / "缓存照片"
    child = directory / "子目录"
    other = tmp_path / "其他目录"
    child.mkdir(parents=True)
    other.mkdir()
    paths = [directory / "白鹭.HIF", child / "幼鸟.HIF"]
    for path in paths:
        path.write_bytes(b"test HIF source")
    panel = window._file_list
    panel._view_mode = panel._MODE_LIST if mode == "list" else panel._MODE_THUMB
    monkeypatch.setattr(panel, "_schedule_persistent_thumb_cache_build", lambda *_: None)
    monkeypatch.setattr(panel, "_schedule_visible_thumbnail_update", lambda *a, **kw: None)
    monkeypatch.setattr(panel, "_emit_file_selected_for_path", lambda *a, **kw: None)
    reads = []

    def read(self, requested, **kwargs):
        reads.extend(requested)
        return {p: {"SourceFile": p, "XMP-dc:Title": "白鹭", "ExifIFD:ISO": 800}
                for p in requested}

    monkeypatch.setattr(_workers.MetadataLoader, "_read_metadata_batch_uncached", read)

    def wait_for(expected):
        _wait_until(lambda: not panel.has_pending_directory_scans()
                    and set(panel._all_files) == expected and panel._metadata_loader is None
                    and all(panel._meta_cache.get(p, {}).get("iso") == "800" for p in expected))

    all_paths = {str(p) for p in paths}
    window._on_directory_selected(str(directory))
    wait_for(all_paths)
    assert sorted(reads) == sorted(all_paths)
    checkbox = window._dir_browser._include_subdirectories_checkbox
    for _ in range(2):
        checkbox.click()
        wait_for({str(paths[0])})
        checkbox.click()
        wait_for(all_paths)
    assert len(reads) == 2

    window._on_directory_selected(str(other))
    wait_for(set())
    window._on_directory_selected(str(directory))
    wait_for(all_paths)
    assert len(reads) == 2
    panel.load_directory(str(directory), force_reload=True)
    wait_for(all_paths)
    assert len(reads) == 4


def test_metadata_memory_option_applies_saves_restores_and_cancel_keeps_cache(window, tmp_path, monkeypatch):
    import importlib
    from PyQt6.QtWidgets import QDialog
    from app_common import superviewer_user_options as options
    from SuperViewer.superviewer import bird_archive_ui

    main = importlib.import_module("SuperViewer.main")
    monkeypatch.setattr(bird_archive_ui, "settings_path", lambda: tmp_path / "archive.json")
    monkeypatch.setattr(main.QMessageBox, "information", lambda *args: None)
    cache = window._file_list._metadata_memory_cache
    assert cache.max_bytes == 256 * 1024 * 1024
    path = str(tmp_path / "白鹭.HIF")
    _, generation = cache.lookup(path, (1,))
    cache.store(path, (1,), {"title": "白鹭"}, generation)
    previous = options.get_runtime_user_options()
    requested = [512, QDialog.DialogCode.Accepted]

    def edit(dialog):
        dialog._spin_metadata_memory.setValue(requested[0])
        return requested[1]

    monkeypatch.setattr(main.SuperViewerUserOptionsDialog, "exec", edit)
    try:
        window._open_user_options_dialog()
        assert cache.max_bytes == 512 * 1024 * 1024
        assert cache.lookup(path, (1,)) == ({"title": "白鹭"}, generation)
        assert options.load_user_options()[options.KEY_METADATA_MEMORY_MB] == 512
        # 取消不修改当前额度或已保存值。
        requested[:] = [64, QDialog.DialogCode.Rejected]
        window._open_user_options_dialog()
        assert cache.max_bytes == 512 * 1024 * 1024
        assert options.load_user_options()[options.KEY_METADATA_MEMORY_MB] == 512
        # 模拟程序启动：清空运行时选项，再从用户文件恢复。
        options.apply_runtime_user_options(None)
        options.reload_runtime_user_options()
        restored = type(window)(initial_received_files=["skip-restore"])
        try:
            assert restored._file_list._metadata_memory_cache.max_bytes == 512 * 1024 * 1024
        finally:
            restored.close()
            _wait_until(lambda: restored._shutdown_finalized)
            restored.deleteLater()
    finally:
        options.apply_runtime_user_options(previous)


@pytest.mark.parametrize("mode", ["list", "thumb"])
def test_directory_return_displays_metadata_before_reconcile_scan(window, tmp_path, monkeypatch, mode):
    import threading
    from app_common.file_browser import _workers

    directory, other = tmp_path / "温缓存", tmp_path / "另一个目录"
    directory.mkdir()
    other.mkdir()
    photo = directory / "白鹭.HIF"
    photo.write_bytes(b"source")
    path = str(photo)
    panel = window._file_list
    panel._view_mode = panel._MODE_LIST if mode == "list" else panel._MODE_THUMB
    monkeypatch.setattr(panel, "_schedule_persistent_thumb_cache_build", lambda *_: None)
    monkeypatch.setattr(panel, "_schedule_visible_thumbnail_update", lambda *a, **kw: None)
    monkeypatch.setattr(panel, "_emit_file_selected_for_path", lambda *a, **kw: None)
    monkeypatch.setattr(_workers.MetadataLoader, "_read_metadata_batch_uncached",
                        lambda self, paths, **kw: {p: {"SourceFile": p, "XMP-dc:Title": "白鹭",
                                                       "ExifIFD:ISO": 800, "XMP-xmp:Rating": 5}
                                                  for p in paths})
    window._on_directory_selected(str(directory))
    _wait_until(lambda: not panel.has_pending_directory_scans() and panel._metadata_loader is None
                and panel._meta_cache.get(path, {}).get("iso") == "800")
    window._on_directory_selected(str(other))
    _wait_until(lambda: not panel.has_pending_directory_scans() and not panel._all_files)
    started, release = threading.Event(), threading.Event()

    def blocked_scan(self):
        started.set()
        release.wait(3)

    monkeypatch.setattr(_workers.DirectoryScanWorker, "run", blocked_scan)
    panel._filter_min_rating = 5
    try:
        window._on_directory_selected(str(directory))
        # No queued scan result or metadata validation has run at this point.
        assert panel._meta_cache[path]["iso"] == "800"
        assert panel._filtered_files == [path]
        assert started.wait(1)
    finally:
        release.set()
        _wait_until(lambda: not panel.has_pending_directory_scans())
