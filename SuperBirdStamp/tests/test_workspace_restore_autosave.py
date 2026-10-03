from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication

from birdstamp import config
from birdstamp.gui import editor, editor_workspace
from birdstamp.workspace import read_workspace_json, write_workspace_json


_APP = QApplication.instance() or QApplication([])


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
    try:
        yield instance
    finally:
        instance.close()
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

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
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
    window._save_workspace_to_path(workspace_path)

    assert len(read_workspace_json(autosave_path)["photos"]) == 2
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


def test_parameter_change_autosaves_named_workspace(window, tmp_path):
    named = tmp_path / "工作区" / "白鹭.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    original = _text_scale(named)

    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    assert window._workspace_autosave_timer.isActive()
    window._autosave_workspace_now()

    assert _text_scale(named) != original
    assert _text_scale(named) == _text_scale(window._workspace_autosave_path())
    assert read_workspace_json(window._workspace_autosave_path())["current_workspace_path"] == str(named)
    assert "current_workspace_path" not in read_workspace_json(named)


def test_untitled_parameter_change_only_updates_session_autosave(window, tmp_path):
    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    window._autosave_workspace_now()

    assert window._workspace_path is None
    assert "current_workspace_path" not in read_workspace_json(window._workspace_autosave_path())
    assert not list(tmp_path.glob("*.birdstamp-workspace.json"))


def test_opening_workspace_does_not_rewrite_it(window, tmp_path):
    named = tmp_path / "打开.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    window._workspace_path = None
    original = named.read_bytes()

    window._open_workspace_path(named)
    assert window._workspace_path == named
    window._autosave_workspace_now()

    assert named.read_bytes() == original


def test_switching_workspace_flushes_pending_parameter_change(window, tmp_path):
    first = tmp_path / "甲.birdstamp-workspace.json"
    second = tmp_path / "乙.birdstamp-workspace.json"
    window._save_workspace_to_path(second)
    window._save_workspace_to_path(first)
    original = _text_scale(first)

    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    window._open_workspace_path(second)

    assert window._workspace_path == second
    assert _text_scale(first) != original


def test_startup_autosave_restore_keeps_named_workspace_current(window, tmp_path):
    named = tmp_path / "恢复.birdstamp-workspace.json"
    window._save_workspace_to_path(named)
    window.text_scale_slider.setValue(window.text_scale_slider.value() + 25)
    window._autosave_workspace_now()
    payload = read_workspace_json(window._workspace_autosave_path())
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (8, 8), (20, 40, 80)).save(photo)
    payload["photos"] = [{"path": str(photo)}]
    write_workspace_json(window._workspace_autosave_path(), payload)
    window._workspace_path = None

    assert window._restore_autosave_workspace_on_startup()
    while window._workspace_restore_in_progress():
        window._process_workspace_restore_photo_batch()

    assert window._workspace_path == named
    assert window.windowTitle().startswith(f"{named} - ")
