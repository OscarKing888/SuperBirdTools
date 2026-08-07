# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from pathlib import Path

from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


def _shutdown_panel(app: QApplication, panel: SuperViewerTaggedFileListPanel) -> None:
    panel.request_shutdown()
    deadline = time.monotonic() + 3.0
    while not panel.shutdown(wait_timeout_ms=25) and time.monotonic() < deadline:
        app.processEvents()
    assert panel.shutdown(wait_timeout_ms=25)
    panel.close()
    app.processEvents()


def _make_panel(tmp_path: Path, tags: str = "打架\n捕食\n") -> tuple[QApplication, SuperViewerTaggedFileListPanel, list[str]]:
    app = QApplication.instance() or QApplication([])
    superpicky = tmp_path / ".superpicky"
    superpicky.mkdir(exist_ok=True)
    config_path = superpicky / "tags.cfg"
    config_path.write_text(tags, encoding="utf-8")
    paths: list[str] = []
    for index in range(2):
        path = tmp_path / f"img{index}.jpg"
        path.write_bytes(b"not an image")
        paths.append(os.path.normpath(str(path)))
    panel = SuperViewerTaggedFileListPanel(tag_config_path=config_path)
    panel._all_files = list(paths)
    return app, panel, paths


def test_set_tag_undo_redo_roundtrip(tmp_path: Path) -> None:
    app, panel, paths = _make_panel(tmp_path)
    signals: list[int] = []
    panel.command_history_changed.connect(lambda: signals.append(1))
    try:
        assert not panel.can_undo and not panel.can_redo

        panel.set_photo_tag_for_paths([paths[0]], "打架", True)
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == {"打架"}
        assert panel.can_undo and not panel.can_redo
        assert signals

        panel.undo()
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == set()
        assert not panel.can_undo and panel.can_redo

        panel.redo()
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == {"打架"}
        assert panel.can_undo and not panel.can_redo
    finally:
        _shutdown_panel(app, panel)


def test_clear_tags_undo_restores_snapshot(tmp_path: Path) -> None:
    app, panel, paths = _make_panel(tmp_path)
    try:
        panel.set_photo_tag_for_paths([paths[0]], "打架", True)
        panel.set_photo_tag_for_paths([paths[0]], "捕食", True)
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == {"打架", "捕食"}

        panel.clear_photo_tags_for_paths([paths[0]])
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == set()

        panel.undo()
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == {"打架", "捕食"}
    finally:
        _shutdown_panel(app, panel)


def test_context_menu_clear_all_tags_supports_undo(tmp_path: Path) -> None:
    """右键「清除所有TAG」与 clear_photo_tags_for_paths 同一历史路径。"""
    app, panel, paths = _make_panel(tmp_path)
    try:
        panel.set_photo_tag_for_paths([paths[0]], "打架", True)
        panel.set_photo_tag_for_paths([paths[0]], "捕食", True)
        panel._photo_tag_cache[paths[0]] = {"打架", "捕食"}

        # Same entry used by the context-menu action.
        panel.clear_photo_tags_for_paths([paths[0]])
        assert panel.can_undo
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == set()

        panel.undo()
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == {"打架", "捕食"}
        panel.redo()
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == set()
    finally:
        _shutdown_panel(app, panel)


def test_new_tag_command_clears_redo(tmp_path: Path) -> None:
    app, panel, paths = _make_panel(tmp_path)
    try:
        panel.set_photo_tag_for_paths([paths[0]], "打架", True)
        panel.undo()
        assert panel.can_redo

        panel.set_photo_tag_for_paths([paths[0]], "捕食", True)
        assert not panel.can_redo
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == {"捕食"}
    finally:
        _shutdown_panel(app, panel)


def test_noop_set_tag_does_not_push_history(tmp_path: Path) -> None:
    app, panel, paths = _make_panel(tmp_path)
    try:
        panel.set_photo_tag_for_paths([paths[0]], "打架", True)
        assert panel.can_undo
        # Clear history via scope-change helper to isolate noop case.
        panel._clear_command_history()
        assert not panel.can_undo

        panel.set_photo_tag_for_paths([paths[0]], "打架", True)
        assert not panel.can_undo
        assert panel.configured_tags_snapshot([paths[0]])[paths[0]] == {"打架"}
    finally:
        _shutdown_panel(app, panel)
