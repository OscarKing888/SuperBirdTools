from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

from birdstamp import config
from birdstamp.gui import editor, editor_workspace
from birdstamp.workspace import WorkspaceFormatError, read_workspace_json, write_workspace_json


_APP = QApplication.instance() or QApplication([])


def _finish_close(window):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        event = QCloseEvent()
        window.closeEvent(event)
        if event.isAccepted():
            return
        _APP.processEvents()
        time.sleep(0.005)
    pytest.fail("window background tasks did not finish closing")


@pytest.fixture
def window(tmp_path, monkeypatch):
    # Patch the shared config root before construction: template seeding,
    # export preferences, remembered paths and autosave must all stay in tmp.
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-cache"))
    monkeypatch.setattr(editor.BirdStampEditorWindow, "_start_bird_detector_preload", lambda self: None)
    monkeypatch.setattr(editor.BirdStampEditorWindow, "_run_deferred_startup_tasks", lambda self: None)
    monkeypatch.setattr(editor.BirdStampEditorWindow, "_restart_photo_list_metadata_loader", lambda self: None)
    monkeypatch.setattr(editor.BirdStampEditorWindow, "_schedule_workspace_photo_selection", lambda *args, **kwargs: None)
    monkeypatch.setattr(editor_workspace, "_WORKSPACE_RESTORE_PHOTO_BATCH_MAX", 1)
    instance = editor.BirdStampEditorWindow()
    monkeypatch.setattr(instance, "_begin_exiftool_shutdown", instance._exiftool_shutdown_done.set)
    try:
        yield instance
    finally:
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
        _finish_close(instance)
        instance.deleteLater()
        _APP.processEvents()


def _start_partial_restore(window, tmp_path: Path):
    workspace_path = tmp_path / "session.birdstamp-workspace.json"
    payload = window._collect_workspace_payload(workspace_path)
    payload["photos"] = []
    for index in range(2):
        path = tmp_path / f"photo-{index}.png"
        Image.new("RGB", (8, 8), (index * 100, 40, 80)).save(path)
        payload["photos"].append({"path": str(path)})
    autosave_path = window._workspace_autosave_path()
    write_workspace_json(autosave_path, payload)
    window._restore_workspace_payload(
        payload, workspace_path,
        mark_as_current_workspace=False, autosave_after_restore=False,
    )
    window._process_workspace_restore_photo_batch()
    assert window.photo_list.topLevelItemCount() == 1
    assert len(window._workspace_restore_pending_entries) == 1
    return autosave_path, workspace_path, payload


def test_close_during_restore_preserves_complete_autosave(window, tmp_path):
    autosave_path, _, _ = _start_partial_restore(window, tmp_path)
    original = autosave_path.read_bytes()

    _finish_close(window)
    assert autosave_path.read_bytes() == original
    assert not window._workspace_restore_photo_timer.isActive()
    assert not window._workspace_autosave_timer.isActive()
    assert not window._workspace_restore_pending_entries
    assert window._workspace_restore_context is None
    # A previously queued UI/autosave callback cannot save the partial list.
    window._schedule_workspace_autosave()
    window._autosave_workspace_now()
    assert not window._workspace_autosave_timer.isActive()
    assert autosave_path.read_bytes() == original


def test_partial_restore_cannot_overwrite_autosave_or_manual_workspace(window, tmp_path):
    autosave_path, workspace_path, payload = _start_partial_restore(window, tmp_path)
    write_workspace_json(workspace_path, payload)
    original_autosave = autosave_path.read_bytes()
    original_workspace = workspace_path.read_bytes()

    window._autosave_workspace_now()
    window._save_workspace_to_path(workspace_path)

    assert autosave_path.read_bytes() == original_autosave
    assert workspace_path.read_bytes() == original_workspace


def test_completed_restore_resumes_manual_and_automatic_saving(window, tmp_path):
    autosave_path, workspace_path, _ = _start_partial_restore(window, tmp_path)
    window._process_workspace_restore_photo_batch()
    assert not window._workspace_restore_in_progress()
    assert window._workspace_autosave_enabled()

    window._autosave_workspace_now()
    assert len(read_workspace_json(autosave_path)["photos"]) == 2
    window._save_workspace_to_path(workspace_path)

    assert not autosave_path.exists()
    assert len(read_workspace_json(workspace_path)["photos"]) == 2


def test_recent_workspace_menu_tracks_saved_and_opened_files(window, tmp_path):
    first = tmp_path / "甲" / "编辑.birdstamp-workspace.json"
    second = tmp_path / "乙" / "编辑.birdstamp-workspace.json"
    window._autosave_workspace_now()
    assert not window.recent_workspaces_menu.isEnabled()
    assert window.windowTitle().startswith("Untitled* - ")

    window._save_workspace_to_path(first)
    assert window.windowTitle().startswith(f"{first} - ")
    window._save_workspace_to_path(second)
    assert window.windowTitle().startswith(f"{second} - ")
    actions = window.recent_workspaces_menu.actions()
    assert [action.toolTip() for action in actions] == [str(second), str(first)]
    assert all("编辑.birdstamp-workspace.json" in action.text() for action in actions)
    assert actions[0].text() != actions[1].text()

    actions[1].trigger()
    assert window._workspace_path == first
    assert window.windowTitle().startswith(f"{first} - ")
    assert window._load_editor_export_state_value("recent_workspace_paths") == [str(first), str(second)]
    second.unlink()
    window._refresh_recent_workspace_menu()
    assert [action.toolTip() for action in window.recent_workspaces_menu.actions()] == [str(first)]


def test_recent_workspace_failed_or_cancelled_load_does_not_change_history(window, tmp_path, monkeypatch):
    first = tmp_path / "first.birdstamp-workspace.json"
    second = tmp_path / "second.birdstamp-workspace.json"
    window._save_workspace_to_path(first)
    window._save_workspace_to_path(second)
    history = window._load_editor_export_state_value("recent_workspace_paths")

    monkeypatch.setattr(window, "_confirm_replace_workspace_session", lambda: False)
    window.recent_workspaces_menu.actions()[1].trigger()
    assert window._workspace_path == second
    assert window._load_editor_export_state_value("recent_workspace_paths") == history

    monkeypatch.setattr(window, "_confirm_replace_workspace_session", lambda: True)
    monkeypatch.setattr(window, "_show_error", lambda title, message: None)
    first.write_text("{invalid", encoding="utf-8")
    window.recent_workspaces_menu.actions()[1].trigger()
    assert window._workspace_path == second
    assert window._load_editor_export_state_value("recent_workspace_paths") == history


def test_recent_workspace_history_is_bounded_and_deduplicated(tmp_path):
    raw = [str(tmp_path / f"{index}.json") for index in range(12)]
    paths = editor_workspace._recent_workspace_paths(raw, newest=tmp_path / "3.json")
    assert len(paths) == 10
    assert paths[0] == (tmp_path / "3.json").resolve()
    assert paths.count((tmp_path / "3.json").resolve()) == 1


def test_recent_workspace_menu_includes_preexisting_last_workspace(window, tmp_path):
    previous = tmp_path / "previous.birdstamp-workspace.json"
    write_workspace_json(previous, window._collect_workspace_payload(previous))
    window._save_editor_export_state_value("last_workspace_path", str(previous))

    window._refresh_recent_workspace_menu()

    assert [action.toolTip() for action in window.recent_workspaces_menu.actions()] == [str(previous)]


def _text_scale(path: Path) -> float:
    return read_workspace_json(path)["editor_state"]["current_render_settings"]["text_scale"]


def test_parameter_change_marks_named_workspace_dirty_without_autosaving(window, tmp_path):
    named = tmp_path / "工作区" / "白鹭.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    original = named.read_bytes()

    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    assert not window._workspace_autosave_timer.isActive()
    window._autosave_workspace_now()

    assert named.read_bytes() == original
    assert window._workspace_dirty
    assert window.windowTitle().startswith(f"{named}* - ")
    assert not window._workspace_autosave_path().exists()
    assert window.save_workspace()
    assert named.read_bytes() != original
    assert not window._workspace_dirty
    assert window.windowTitle().startswith(f"{named} - ")


def test_untitled_parameter_change_only_updates_session_autosave(window, tmp_path):
    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    assert window._workspace_autosave_timer.isActive()
    window._autosave_workspace_now()

    assert window._workspace_path is None
    assert "current_workspace_path" not in read_workspace_json(window._workspace_autosave_path())
    assert not list(tmp_path.glob("*.birdstamp-workspace.json"))


def test_untitled_close_flushes_pending_autosave_without_prompt(window, monkeypatch):
    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    expected = window.text_scale_slider.value() / 100
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("untitled close prompted"))
    _finish_close(window)
    assert _text_scale(window._workspace_autosave_path()) == expected


def test_opening_workspace_does_not_rewrite_it(window, tmp_path):
    named = tmp_path / "打开.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    window._workspace_path = None
    original = named.read_bytes()

    window._open_workspace_path(named)
    assert window._workspace_path == named
    window._autosave_workspace_now()

    assert named.read_bytes() == original


def test_switching_workspace_saves_when_requested(window, tmp_path, monkeypatch):
    first = tmp_path / "甲.birdstamp-workspace.json"
    second = tmp_path / "乙.birdstamp-workspace.json"
    window._save_workspace_to_path(second)
    window._save_workspace_to_path(first)
    original = _text_scale(first)

    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    window._open_workspace_path(second)

    assert window._workspace_path == second
    assert _text_scale(first) != original


def test_legacy_named_autosave_is_recovered_dirty_without_overwriting_file(window, tmp_path):
    named = tmp_path / "恢复.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    original = named.read_bytes()
    payload = window._collect_workspace_payload(window._workspace_autosave_path())
    payload["current_workspace_path"] = str(named)
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (8, 8), (20, 40, 80)).save(photo)
    payload["photos"] = [{"path": str(photo)}]
    write_workspace_json(window._workspace_autosave_path(), payload)
    window._workspace_path = None

    assert window._restore_autosave_workspace_on_startup()
    while window._workspace_restore_in_progress():
        window._process_workspace_restore_photo_batch()

    assert window._workspace_path == named
    assert window.windowTitle().startswith(f"{named}* - ")
    window._autosave_workspace_now()
    assert window._workspace_dirty
    assert named.read_bytes() == original


@pytest.mark.parametrize("action", ["close", "new", "clear", "open"])
@pytest.mark.parametrize("answer", ["Save", "Discard", "Cancel"])
def test_dirty_named_workspace_transition(window, tmp_path, monkeypatch, action, answer):
    named = tmp_path / "白鹭.birdstamp-workspace.json"
    other = tmp_path / "翠鸟.birdstamp-workspace.json"
    window._save_workspace_to_path(other)
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (8, 8), "blue").save(photo)
    window._append_photo_path_to_list(photo, existing_keys=set(), default_settings=window._build_current_render_settings())
    window._save_workspace_to_path(named)
    original = named.read_bytes()
    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    changed_scale = window.text_scale_slider.value()
    questions = []

    def choose(*args):
        questions.append(args)
        return getattr(QMessageBox.StandardButton, answer)

    monkeypatch.setattr(QMessageBox, "question", choose)
    if action == "close":
        event = QCloseEvent()
        window.closeEvent(event)
        if answer != "Cancel":
            _finish_close(window)
        else:
            assert not event.isAccepted()
        assert window._workspace_autosave_shutdown == (answer != "Cancel")
        assert window.collage.closed == (answer != "Cancel")
    elif action == "new":
        window.action_new_workspace.trigger()
    elif action == "clear":
        window._clear_photos()
    else:
        window._open_workspace_path(other)

    assert len(questions) == 1
    assert str(named) in questions[0][2]
    assert (named.read_bytes() != original) == (answer == "Save")
    if answer == "Cancel":
        assert window._workspace_path == named
        assert window._workspace_dirty
        assert window.text_scale_slider.value() == changed_scale
        assert window._list_photo_paths() == [photo]
    elif action in ("new", "clear"):
        assert window._workspace_path is None
        assert window._list_photo_paths() == []
        defaults = window._new_workspace_defaults["editor_state"]
        assert window.text_scale_slider.value() == round(defaults["current_render_settings"]["text_scale"] * 100)
        window._autosave_workspace_now()
        assert read_workspace_json(window._workspace_autosave_path())["photos"] == []
    elif action == "open":
        assert window._workspace_path == other
        assert not window._workspace_dirty


@pytest.mark.parametrize("action", ["close", "new", "clear", "open"])
def test_save_failure_aborts_workspace_transition(window, tmp_path, monkeypatch, action):
    named = tmp_path / "保存失败.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    original = named.read_bytes()
    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda *args: errors.append(args))

    def fail(*args):
        raise WorkspaceFormatError("模拟磁盘写入失败")

    monkeypatch.setattr(editor_workspace, "write_workspace_json", fail)
    if action == "close":
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert not window.collage.closed
        assert not window._workspace_autosave_shutdown
    elif action == "new":
        window.new_workspace()
    elif action == "clear":
        window._clear_photos()
    else:
        window._open_workspace_path(tmp_path / "other.json")
    assert len(errors) == 1
    assert window._workspace_path == named
    assert window._workspace_dirty
    assert named.read_bytes() == original


def test_close_retries_only_ask_once_and_do_not_autosave_discarded_edits(window, tmp_path, monkeypatch):
    named = tmp_path / "重试关闭.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    original = named.read_bytes()
    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: questions.append(args) or QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(window._preview_action_pool, "is_finished", lambda: False)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    monkeypatch.setattr(window._preview_action_pool, "is_finished", lambda: True)
    _finish_close(window)
    assert len(questions) == 1
    assert named.read_bytes() == original
    assert not window._workspace_autosave_path().exists()


def test_clean_named_workspace_does_not_prompt_even_with_photos(window, tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (8, 8), "blue").save(photo)
    window._append_photo_path_to_list(photo, existing_keys=set(), default_settings=window._build_current_render_settings())
    window._save_workspace_to_path(tmp_path / "clean.json")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("clean workspace prompted"))
    window.new_workspace()
    assert window._workspace_path is None


def test_empty_untitled_autosave_wins_over_previous_named_workspace(window, tmp_path):
    named = tmp_path / "旧工作区.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    window.new_workspace()
    window._autosave_workspace_now()
    assert window._restore_startup_workspace()
    assert window._workspace_path is None
    assert window.photo_list.topLevelItemCount() == 0


def test_save_as_cancellation_preserves_untitled_autosave(window, monkeypatch):
    window._autosave_workspace_now()
    original = window._workspace_autosave_path().read_bytes()
    monkeypatch.setattr(editor_workspace.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    assert not window.save_workspace()
    assert window._workspace_path is None
    assert window._workspace_autosave_path().read_bytes() == original


def test_successful_save_removes_stale_untitled_autosave(window, tmp_path):
    window._autosave_workspace_now()
    assert window._workspace_autosave_path().exists()
    named = tmp_path / "已命名.birdstamp-workspace.json"
    assert window._save_workspace_to_path(named)
    assert not window._workspace_autosave_path().exists()
    assert not window._workspace_autosave_timer.isActive()
    assert window._restore_startup_workspace()
    assert window._workspace_path == named


def test_restored_selection_does_not_mark_named_workspace_dirty(window, tmp_path, monkeypatch):
    photo = tmp_path / "选择.png"
    Image.new("RGB", (8, 8), "blue").save(photo)
    window._append_photo_path_to_list(photo, existing_keys=set(), default_settings=window._build_current_render_settings())
    named = tmp_path / "已保存.json"
    window._save_workspace_to_path(named)
    monkeypatch.setattr(window, "_start_preview_decode_worker", lambda *args, **kwargs: None)
    window._pending_workspace_current_item = window.photo_list.topLevelItem(0)
    window._pending_workspace_selected_paths = [photo]

    window._apply_pending_workspace_photo_selection()

    assert window.current_path == photo
    assert not window._workspace_dirty
    assert window.windowTitle().startswith(f"{named} - ")


def test_partial_named_restore_close_preserves_previous_complete_autosave(window, tmp_path):
    window._save_workspace_to_path(tmp_path / "当前.json")
    autosave_path, _, _ = _start_partial_restore(window, tmp_path)
    original = autosave_path.read_bytes()
    _finish_close(window)
    assert autosave_path.read_bytes() == original
