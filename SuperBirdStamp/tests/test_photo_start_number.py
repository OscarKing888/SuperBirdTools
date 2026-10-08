from __future__ import annotations

import time

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from birdstamp import config
from birdstamp.gui import editor, template_context
from birdstamp.gui.editor_photo_list import (
    PHOTO_COL_NAME, PHOTO_COL_ROW, PHOTO_COL_SEQ,
    PHOTO_LIST_PHOTO_INFO_ROLE, PHOTO_LIST_SEQUENCE_ROLE,
)
from birdstamp.gui.editor_utils import path_key
from birdstamp.photo_numbering import MAX_START_NUMBER, normalize_start_number
from birdstamp.workspace import read_workspace_json, write_workspace_json


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for name in (
        "_start_bird_detector_preload", "_run_deferred_startup_tasks",
        "_restart_photo_list_metadata_loader", "_schedule_workspace_photo_selection", "_on_photo_selected",
    ):
        monkeypatch.setattr(editor.BirdStampEditorWindow, name, lambda *args, **kwargs: None)
    instance = editor.BirdStampEditorWindow()
    try:
        yield instance
    finally:
        deadline = time.monotonic() + 5
        accepted = instance.close()
        while not accepted and time.monotonic() < deadline:
            _APP.processEvents()
            accepted = instance.close()
        assert accepted, "编辑器后台任务未及时结束"
        instance.deleteLater()
        _APP.processEvents()


def _add_photos(window, tmp_path, names=("翠鸟乙.png", "翠鸟甲.png")):
    paths = []
    for name in names:
        path = tmp_path / name
        Image.new("RGB", (16, 12), "green").save(path)
        window._append_photo_path_to_list(
            path, existing_keys=set(), default_settings=window._build_current_render_settings(),
        )
        paths.append(path)
    window.photo_list.resort()
    return paths


def _assert_numbers(window, expected):
    for row, number in enumerate(expected):
        item = window.photo_list.topLevelItem(row)
        assert item.text(PHOTO_COL_SEQ) == str(number)
        assert item.toolTip(PHOTO_COL_SEQ) == str(number)
        info = item.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE)
        assert info.editor_row_number == number
        for alias in ("row_number", "editor.row_number", "editor.index", "editor.sequence", "index", "sequence", "seq"):
            for source in ("editor", "auto"):
                provider = template_context.build_template_context_provider(source, alias)
                assert provider.get_text_content(info) == str(number)


def test_start_number_sort_remove_append_and_metadata(window, tmp_path):
    _add_photos(window, tmp_path)
    _assert_numbers(window, [1, 2])
    window.photo_start_number_spin.setValue(101)
    _assert_numbers(window, [101, 102])
    window.photo_list.header().setSortIndicator(PHOTO_COL_NAME, Qt.SortOrder.AscendingOrder)
    _assert_numbers(window, [101, 102])
    assert window.photo_list.topLevelItem(0).text(PHOTO_COL_NAME) == "翠鸟乙.png"
    window.photo_list.header().setSortIndicator(PHOTO_COL_NAME, Qt.SortOrder.DescendingOrder)
    _assert_numbers(window, [101, 102])
    assert window.photo_list.topLevelItem(0).text(PHOTO_COL_NAME) == "翠鸟甲.png"
    # 导入顺序是独立排序键，不因显示编号改变。
    assert window.photo_list.topLevelItem(0).data(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE) == 2
    window.photo_list.takeTopLevelItem(0)
    window.photo_list.refresh_row_numbers()
    _assert_numbers(window, [101])
    _add_photos(window, tmp_path, ("白鹭.png",))
    _assert_numbers(window, [101, 102])


def test_number_change_refreshes_preview_context_and_export_snapshots(window, tmp_path, monkeypatch):
    paths = _add_photos(window, tmp_path)
    info = window.photo_list.topLevelItem(0).data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE)
    window.current_path = paths[0]
    window.current_photo_info = info
    window.current_metadata_context = {"row_number": "1", "filename": paths[0].name}
    window._photo_export_dirty_keys.clear()
    window.photo_start_number_spin.setValue(51)
    assert window.current_metadata_context["row_number"] == "51"
    assert window.current_metadata_context["filename"] == paths[0].name
    assert window._photo_export_dirty_keys == {path_key(path) for path in paths}
    assert window._preview_debounce_timer.isActive()
    assert window._fast_metadata_context(info, {})["row_number"] == "51"

    seeds = window._build_video_export_job_seeds(paths)
    assert [seed.photo_info.editor_row_number for seed in seeds] == [51, 52]
    # 图片/GIF 共用渲染作业也使用同一编号，而非导出选区内重新从 1 开始。
    monkeypatch.setattr(window, "_load_raw_metadata_batch", lambda paths: {
        path_key(path): {"SourceFile": str(path)} for path in paths
    })
    jobs = window._build_export_render_jobs(paths[1:], precompute_crop_plans=False)
    assert jobs[0].metadata_context["row_number"] == "52"
    assert jobs[0].photo_info.editor_row_number == 52
    window.photo_start_number_spin.setValue(81)
    assert seeds[0].photo_info.editor_row_number == 51
    assert window.current_metadata_context["row_number"] == "81"


def test_workspace_roundtrip_and_legacy_default(window, tmp_path):
    _add_photos(window, tmp_path)
    window.photo_start_number_spin.setValue(301)
    workspace = tmp_path / "编号.birdstamp-workspace.json"
    write_workspace_json(workspace, window._collect_workspace_payload(workspace))
    payload = read_workspace_json(workspace)
    assert payload["editor_state"]["photo_start_number"] == 301

    for expected in (301, 1):
        if expected == 1:
            payload["editor_state"].pop("photo_start_number")
        window._restore_workspace_payload(
            payload, workspace, mark_as_current_workspace=False, autosave_after_restore=False,
        )
        deadline = time.monotonic() + 5
        while window._workspace_restore_in_progress() and time.monotonic() < deadline:
            _APP.processEvents()
        assert not window._workspace_restore_in_progress()
        assert window.photo_start_number_spin.value() == expected
        _assert_numbers(window, [expected, expected + 1])


@pytest.mark.parametrize("value,expected", [(None, 1), ("bad", 1), (-3, 1), (0, 1), ("250", 250), (10**20, MAX_START_NUMBER)])
def test_start_number_validation(value, expected):
    assert normalize_start_number(value) == expected


def test_move_buttons_numbering_append_and_workspace_round_trip(window, tmp_path):
    paths = _add_photos(window, tmp_path, ("甲.png", "乙.png", "丙.png"))
    window.photo_start_number_spin.setValue(101)
    current = window.photo_list.topLevelItem(1)
    window.photo_list.setCurrentItem(current)
    assert window.photo_move_up_button.isEnabled()
    assert window.photo_move_down_button.isEnabled()
    window._workspace_autosave_timer.stop()
    window.photo_move_up_button.click()
    assert window._list_photo_paths() == [paths[1], paths[0], paths[2]]
    assert window.photo_list.currentItem() is current
    assert not window.photo_move_up_button.isEnabled()
    assert window._workspace_autosave_timer.isActive()
    _assert_numbers(window, [101, 102, 103])

    window.photo_move_down_button.click()
    window.photo_move_down_button.click()
    assert window._list_photo_paths() == [paths[0], paths[2], paths[1]]
    assert not window.photo_move_down_button.isEnabled()
    extra = _add_photos(window, tmp_path, ("追加.png",))[0]
    expected = [paths[0], paths[2], paths[1], extra]
    assert window._list_photo_paths() == expected
    _assert_numbers(window, [101, 102, 103, 104])

    workspace = tmp_path / "手动排序.birdstamp-workspace.json"
    window._save_workspace_to_path(workspace)
    payload = read_workspace_json(workspace)
    window._restore_workspace_payload(
        payload, workspace, mark_as_current_workspace=False, autosave_after_restore=False,
    )
    deadline = time.monotonic() + 5
    while window._workspace_restore_in_progress() and time.monotonic() < deadline:
        _APP.processEvents()
    assert not window._workspace_restore_in_progress()
    assert window._list_photo_paths() == expected
    _assert_numbers(window, [101, 102, 103, 104])


def test_list_remove_action_updates_workspace_and_preserves_sources(window, tmp_path):
    paths = _add_photos(window, tmp_path, ("翠鸟甲.png", "翠鸟乙.png", "白鹭.png"))
    sidecar = paths[0].with_suffix(".xmp")
    sidecar.write_text("中文侧车内容", encoding="utf-8")
    window.photo_start_number_spin.setValue(21)
    window.photo_list.setCurrentItem(window.photo_list.topLevelItem(0))
    window.photo_list.topLevelItem(1).setSelected(True)
    for path in paths:
        window.raw_metadata_cache[path_key(path)] = {"Title": "翠鸟"}
    window.photo_list.remove_selected_action.trigger()
    assert window.photo_list.topLevelItemCount() == 1
    assert window.photo_list.topLevelItem(0).text(PHOTO_COL_NAME) == "白鹭.png"
    _assert_numbers(window, [21])
    assert all(path.exists() for path in paths)
    assert sidecar.read_text(encoding="utf-8") == "中文侧车内容"
    assert all(path_key(path) not in window.raw_metadata_cache for path in paths[:2])
    payload = window._collect_workspace_payload(tmp_path / "test.birdstamp-workspace.json")
    assert len(payload["photos"]) == 1
    window.photo_list.setCurrentItem(window.photo_list.topLevelItem(0))
    window.photo_list.remove_selected_action.trigger()
    assert window.photo_list.topLevelItemCount() == 0
    assert window.current_path == window.placeholder_path
    assert window.current_path not in paths
    assert not window._collect_workspace_payload(tmp_path / "empty.birdstamp-workspace.json")["photos"]
