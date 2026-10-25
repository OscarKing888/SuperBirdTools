"""标签页使用真实列表/缩略图多选，写入中文 XMP 并保留逐图撤销状态。"""
from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtTest import QTest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer.image_info_tab_tags import ImageInfoTabPanel_Tags
from SuperViewer.tests.test_directory_selection_responsiveness import window, _APP, _wait_until


@pytest.fixture(params=["list", "thumb"])
def selection_window(window, tmp_path, monkeypatch, request):
    files = window._file_list
    for method in ("_start_metadata_loader", "_schedule_persistent_thumb_cache_build",
                   "_schedule_visible_thumbnail_update", "_start_photo_tag_cache_loader_if_needed"):
        monkeypatch.setattr(files, method, lambda *a, **kw: None)
    files._tag_config.path.write_text("飞行\n捕食\n", encoding="utf-8")
    files._load_tag_config_if_changed(force=True)
    paths = []
    meta = PhotoMetaDataXMP()
    for name, subjects in (("甲", ["飞行", "库外关键词"]), ("乙", ["捕食"]), ("丙", ["飞行"])):
        photo = tmp_path / f"{name}.jpg"
        Image.new("RGB", (32, 24), "green").save(photo)
        path = str(photo)
        paths.append(path)
        assert meta.write_subjects(path, subjects)
        assert meta.write(path, {"XMP-dc:Description": "清晨湿地"})
        files._photo_tag_cache[path] = set(subjects) & {"飞行", "捕食"}
        files._meta_cache[path] = {"tags": list(files._photo_tag_cache[path])}
    files._current_dir = str(tmp_path)
    files._all_files = paths
    files._set_view_mode(files._MODE_LIST if request.param == "list" else files._MODE_THUMB)
    files._rebuild_views()
    window.resize(1600, 800)
    window.show()
    window.image_info_tabs.setCurrentWidget(window.tags_info_panel)
    _wait_until(lambda: len(files._filtered_files) == 3)
    view = files._tree_widget if request.param == "list" else files._list_widget
    index_for = files._tree_index_for_path if request.param == "list" else files._thumb_index_for_path
    _wait_until(lambda: all(index_for(p).isValid() for p in paths))
    model = view.selectionModel()
    flags = QItemSelectionModel.SelectionFlag
    model.setCurrentIndex(index_for(paths[0]), flags.ClearAndSelect | flags.Rows)
    # 只扩充选择，不改变 currentIndex：这是原先标签页漏掉的信号。
    model.select(index_for(paths[1]), flags.Select | flags.Rows)
    _wait_until(lambda: window.tags_info_panel.photo_label.text() == "已选择 2 张图片")
    return window, files, paths, model, index_for, meta


def test_batch_add_remove_clear_and_undo_keep_per_photo_membership(selection_window):
    window, files, paths, model, index_for, meta = selection_window
    panel = window.tags_info_panel
    originals = {p: Path(p).read_bytes() for p in paths}
    check = panel._tag_checks["飞行"]
    assert check.checkState() == Qt.CheckState.PartiallyChecked
    check.setFocus()
    QTest.keyClick(check, Qt.Key.Key_Space)
    assert check.checkState() == Qt.CheckState.Checked
    assert set(meta.read_subjects(paths[0], strict=True)) == {"飞行", "库外关键词"}
    assert set(meta.read_subjects(paths[1], strict=True)) == {"飞行", "捕食"}
    files.undo()
    _wait_until(lambda: check.checkState() == Qt.CheckState.PartiallyChecked)
    assert meta.read_subjects(paths[1], strict=True) == ["捕食"]
    files.redo()
    _wait_until(lambda: check.checkState() == Qt.CheckState.Checked)
    check.click()
    assert meta.read_subjects(paths[0], strict=True) == ["库外关键词"]
    assert meta.read_subjects(paths[1], strict=True) == ["捕食"]
    assert check.checkState() == Qt.CheckState.Unchecked
    files.undo()
    _wait_until(lambda: check.checkState() == Qt.CheckState.Checked)
    panel.btn_clear.click()
    assert meta.read_subjects(paths[0], strict=True) == ["库外关键词"]
    assert meta.read_subjects(paths[1], strict=True) == []
    assert not panel.btn_clear.isEnabled()
    files.undo()
    _wait_until(lambda: panel.btn_clear.isEnabled())
    assert set(meta.read_subjects(paths[1], strict=True)) == {"飞行", "捕食"}
    assert meta.read_subjects(paths[2], strict=True) == ["飞行"]
    assert set(files.selected_photo_paths()) == set(paths[:2])
    for path in paths:
        assert Path(path).read_bytes() == originals[path]
        assert meta.read(path)["XMP-dc:Description"] == "清晨湿地"


def test_selection_only_changes_hidden_tab_and_noncurrent_cache_refresh(selection_window):
    window, files, paths, model, index_for, meta = selection_window
    panel = window.tags_info_panel
    flags = QItemSelectionModel.SelectionFlag
    current = model.currentIndex()
    # 第二张标签加载完毕也要刷新汇总，而非仅检查预览图路径。
    files._photo_tag_cache[paths[1]] = {"飞行"}
    files.photo_tags_cache_updated.emit([paths[1]])
    _wait_until(lambda: panel._tag_checks["飞行"].checkState() == Qt.CheckState.Checked)
    window.image_info_tabs.setCurrentWidget(window.image_info_panel)
    model.select(index_for(paths[1]), flags.Deselect | flags.Rows)
    _APP.processEvents()
    window.image_info_tabs.setCurrentWidget(panel)
    _wait_until(lambda: panel.photo_label.text() == "甲.jpg")
    assert model.currentIndex() == current
    model.select(index_for(paths[2]), flags.Select | flags.Rows)
    _wait_until(lambda: panel.photo_label.text() == "已选择 2 张图片")
    assert panel._tag_checks["飞行"].checkState() == Qt.CheckState.Checked
    model.clearSelection()
    _wait_until(lambda: panel.photo_label.text() == "未选择图片")
    assert model.currentIndex() == current
    assert not panel.btn_clear.isEnabled()
    assert all(not check.isEnabled() for check in panel._tag_checks.values())


def test_playback_and_shutdown_cancel_pending_tag_refresh(selection_window, monkeypatch):
    window, files, paths, model, index_for, meta = selection_window
    panel = window.tags_info_panel
    _APP.processEvents()
    reads = []
    monkeypatch.setattr(panel, "_tags_for_path_provider", lambda p: reads.append(p) or set())
    panel.schedule_refresh()
    files._key_navigation_playback_active = True
    files.photo_selection_changed.emit()
    _APP.processEvents()
    assert reads == []
    files._key_navigation_playback_active = False
    panel.schedule_refresh()
    _wait_until(lambda: bool(reads))
    reads.clear()
    panel.schedule_refresh()
    panel.request_shutdown()
    _APP.processEvents()
    assert reads == []


def test_partial_failure_refreshes_truth_and_preserves_successful_undo(selection_window, monkeypatch):
    window, files, paths, model, index_for, meta = selection_window
    damaged = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><damaged'
    Path(paths[1]).with_suffix(".xmp").write_bytes(damaged)
    failures = []
    monkeypatch.setattr(files, "_show_tag_write_error", lambda action, exc: failures.append(str(exc)))
    check = window.tags_info_panel._tag_checks["捕食"]
    check.click()
    assert failures
    assert "捕食" in meta.read_subjects(paths[0], strict=True)
    assert Path(paths[1]).with_suffix(".xmp").read_bytes() == damaged
    files.undo()
    assert meta.read_subjects(paths[0], strict=True) == ["飞行", "库外关键词"]
    assert Path(paths[1]).with_suffix(".xmp").read_bytes() == damaged


def test_resolved_report_path_writes_source_and_refreshes_display_alias(selection_window):
    from app_common.file_browser._browser_core import _set_cached_actual_path

    window, files, paths, model, index_for, meta = selection_window
    old = Path(paths[0])
    actual = old.with_name("已移动的白鹭.jpg")
    old.rename(actual)
    old.with_suffix(".xmp").rename(actual.with_suffix(".xmp"))
    _set_cached_actual_path(str(old), str(actual))
    files._photo_tag_cache[str(actual)] = files._photo_tag_cache[str(old)].copy()
    panel = window.tags_info_panel
    panel.refresh_ui()
    assert set(panel.selected_photo_paths()) == {str(actual), paths[1]}
    panel._tag_checks["捕食"].click()
    assert "捕食" in meta.read_subjects(str(actual), strict=True)
    assert not old.with_suffix(".xmp").exists()
    assert files._photo_tag_cache[str(old)] == {"飞行", "捕食"}
    assert set(files._meta_cache[str(old)]["tags"]) == {"飞行", "捕食"}
    files.undo()
    assert meta.read_subjects(str(actual), strict=True) == ["飞行", "库外关键词"]
    assert files._photo_tag_cache[str(old)] == {"飞行"}


def test_standalone_single_photo_and_selection_snapshot(tmp_path):
    photo = str(tmp_path / "白鹭.jpg")
    Image.new("RGB", (16, 16)).save(photo)
    changes = []
    panel = ImageInfoTabPanel_Tags(lambda: ["飞行"], lambda p: set(),
                                 lambda *args: changes.append(args), lambda paths: None)
    try:
        panel.on_photo_selected(photo)
        panel._tag_checks["飞行"].click()
        assert changes == [([photo], "飞行", True)]
        selection = [photo, photo, str(tmp_path / "视频.mp4")]
        panel._selected_paths_provider = lambda: selection
        assert panel.selected_photo_paths() == [photo]
        selection.clear()
        panel._on_tag_toggled("飞行", True)
        assert len(changes) == 1
        assert panel.photo_label.text() == "未选择图片"
    finally:
        panel.close()
