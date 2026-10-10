"""Record Show events: a transient top-level is gone by the next event-loop tick."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from birdstamp import config
from birdstamp.gui.editor import BirdStampEditorWindow


_APP = QApplication.instance() or QApplication([])


class _TopLevelShows(QObject):
    def __init__(self):
        super().__init__()
        self.shown = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and watched.isWindow():
            self.shown.append((type(watched).__name__, watched.objectName(), watched.windowTitle()))
        return False


@pytest.fixture
def startup_window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for method in ("_start_bird_detector_preload", "_run_deferred_startup_tasks",
                   "_restart_photo_list_metadata_loader", "_schedule_async_bird_detect"):
        monkeypatch.setattr(BirdStampEditorWindow, method, lambda *a, **kw: None)
    recorder = _TopLevelShows()
    _APP.installEventFilter(recorder)
    window = None
    try:
        window = BirdStampEditorWindow()
        yield window, recorder
    finally:
        _APP.removeEventFilter(recorder)
        if window is not None:
            window.close()
            window.deleteLater()
        _APP.processEvents()


def test_construction_does_not_show_any_window(startup_window):
    window, recorder = startup_window
    assert recorder.shown == []
    assert not window.isVisible()
    assert all(not group.isWindow() for group in window._pipeline_stage_option_groups.values())


def test_stage_reorder_toggle_and_restore_never_show_detached_groups(startup_window, tmp_path):
    window, recorder = startup_window
    window.show()
    _APP.processEvents()
    recorder.shown.clear()
    groups = window._pipeline_stage_option_groups
    owners = {stage: group.parentWidget() for stage, group in groups.items()}
    overlay = groups["template_overlay"]
    window.text_scale_slider.setValue(125)
    for _ in range(2):
        overlay.header_button.setFocus()
        QTest.keyClick(overlay.header_button, Qt.Key.Key_Space)
    overlay.set_expanded(False)
    order = list(window._current_pipeline_stage_order())
    order[1:] = reversed(order[1:])
    window._set_pipeline_stage_order(order, save=False, mark_dirty=False)
    for enabled in (False, True, False, True):
        window._set_pipeline_stage_enabled_map({"template_overlay": enabled}, save=False, mark_dirty=False)
        assert overlay.isHidden() == (not enabled)
        assert all(group.parentWidget() is owners[stage] for stage, group in groups.items())
        assert not overlay.is_expanded()
        assert window.text_scale_slider.value() == 125
    workspace = tmp_path / "session.birdstamp-workspace.json"
    payload = window._collect_workspace_payload(workspace)
    window._restore_workspace_payload(payload, workspace, mark_as_current_workspace=False, autosave_after_restore=False)
    _APP.processEvents()
    layout = window.pipeline_stage_options_layout
    expected = [groups[stage] for stage in window._current_pipeline_stage_order()
                if window._is_pipeline_stage_enabled(stage)]
    assert [layout.itemAt(i).widget() for i in range(layout.count())] == expected
    assert all(group.parentWidget() is owners[stage] for stage, group in groups.items())
    assert recorder.shown == []
