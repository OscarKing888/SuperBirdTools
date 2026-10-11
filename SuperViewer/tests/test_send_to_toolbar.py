from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QHelpEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QToolBar, QToolTip

from app_common.send_to_app import icon as send_icon
from SuperViewer.superviewer.send_to_toolbar import (
    SEND_TO_TOOLBAR_OBJECT_NAME,
    create_send_to_toolbar,
    rebuild_send_to_toolbar,
)


_APP = QApplication.instance() or QApplication([])


def test_toolbar_lists_each_external_app_with_icon_and_sends_it(tmp_path):
    window = QMainWindow()
    toolbar = create_send_to_toolbar(window)
    window.addToolBar(toolbar)
    sent: list[dict] = []
    apps = [
        {"name": "SuperBirdStamp.app", "path": str(tmp_path / "missing.app"), "app_id": "birdstamp"},
        {"name": "照片编辑器", "path": ""},
    ]
    window.show()
    try:
        actions = rebuild_send_to_toolbar(toolbar, sent.append, apps)
        _APP.processEvents()
        assert window.findChildren(QToolBar, SEND_TO_TOOLBAR_OBJECT_NAME) == [toolbar]
        assert toolbar.isVisible()
        assert [a.text() for a in toolbar.actions()] == ["SuperBirdStamp", "照片编辑器"]
        assert toolbar.actions() == actions
        assert all(not a.icon().isNull() for a in actions)
        # Hovering a button shows the app name (bundle/exe suffix dropped).
        assert [a.toolTip() for a in actions] == ["SuperBirdStamp", "照片编辑器"]
        for action, expected in zip(actions, ["SuperBirdStamp", "照片编辑器"]):
            button = toolbar.widgetForAction(action)
            center = button.rect().center()
            QApplication.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, center, button.mapToGlobal(center)))
            assert QToolTip.isVisible()
            assert QToolTip.text() == expected
            QToolTip.hideText()
        QTest.mouseClick(toolbar.widgetForAction(actions[0]), Qt.MouseButton.LeftButton)
        assert sent == [apps[0]]

        # Rebuild after settings change replaces buttons instead of appending.
        rebuild_send_to_toolbar(toolbar, sent.append, apps[1:])
        assert [a.text() for a in toolbar.actions()] == ["照片编辑器"]

        rebuild_send_to_toolbar(toolbar, sent.append, [])
        assert toolbar.actions() == []
        assert not toolbar.isVisible()
    finally:
        window.close()
        window.deleteLater()
        _APP.processEvents()


def test_external_app_icon_prefers_existing_app_path(tmp_path, monkeypatch):
    exe = tmp_path / ("Editor.app" if send_icon.sys.platform == "darwin" else "Editor.exe")
    if exe.suffix == ".app":
        exe.mkdir()
    else:
        exe.write_bytes(b"")
    assert send_icon.resolve_app_icon_path({"path": str(exe)}) == str(exe)
    assert send_icon.resolve_app_icon_path({"path": str(tmp_path / "missing.exe")}) == ""
    assert send_icon.resolve_app_icon_path({"path": ""}) == ""
    assert not send_icon.external_app_icon({"name": "", "path": ""}).isNull()


def test_send_uses_all_selected_files(tmp_path, monkeypatch):
    main = importlib.import_module("SuperViewer.main")
    photos = [tmp_path / "一.jpg", tmp_path / "二.jpg"]
    for photo in photos:
        photo.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(main, "send_files_to_app", lambda paths, app, base_directory="": calls.append((paths, app, base_directory)))
    fake = SimpleNamespace(
        _file_list=SimpleNamespace(
            get_selected_file_paths=lambda: [str(p) for p in photos] + [str(tmp_path / "gone.jpg")],
            get_current_dir=lambda: str(tmp_path),
        ),
        _current_exif_path=str(photos[0]),
    )
    app = {"name": "X", "path": "/x"}
    main.MainWindow._send_to_external_app(fake, app)
    assert calls == [([str(p) for p in photos], app, str(tmp_path))]
