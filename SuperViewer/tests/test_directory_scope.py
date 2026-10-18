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
