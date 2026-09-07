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
