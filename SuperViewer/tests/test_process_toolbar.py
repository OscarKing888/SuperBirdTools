from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QToolButton

from SuperViewer.superviewer import process_toolbar as module
from SuperViewer.superviewer.process_toolbar import PROCESS_TOOLBAR_OBJECT_NAME, ProcessToolbar


_APP = QApplication.instance() or QApplication([])


class _Controller:
    def __init__(self) -> None:
        self.busy = False
        self.calls: list[tuple] = []

    def start_for_paths(self, paths, **kwargs):
        self.calls.append(("start", paths, kwargs))

    def show_progress(self):
        self.calls.append(("progress",))

    def stop(self):
        self.calls.append(("stop",))

    def show_trace(self, path):
        self.calls.append(("trace", path))


class _Catalog:
    def __init__(self) -> None:
        self.applying = False
        self.opened: list[list[str]] = []

    def open(self, paths):
        self.opened.append(paths)


class _Files:
    def __init__(self, paths) -> None:
        self.paths = list(paths)
        self.writes_allowed = True
        self.write_checks: list[tuple] = []

    def get_selected_file_paths(self):
        return list(self.paths)

    def _file_writes_allowed(self, action, *, warn=False):
        self.write_checks.append((action, warn))
        return self.writes_allowed


@pytest.fixture
def toolbar_env(monkeypatch):
    messages: list[tuple] = []
    monkeypatch.setattr(module.QMessageBox, "information", lambda *args: messages.append(args))
    window = QMainWindow()
    files = _Files(["/photos/一.jpg", "/photos/二.jpg"])
    bird, sharp, denoise = _Controller(), _Controller(), _Controller()
    bird.catalog = _Catalog()
    toolbar = ProcessToolbar(window, files, bird, sharp, denoise)
    window.addToolBar(toolbar.toolbar)
    busy_menus: list[str] = []
    monkeypatch.setattr(toolbar, "_show_busy_menu", lambda task: busy_menus.append(task.task_name))
    window.show()
    _APP.processEvents()
    try:
        yield SimpleNamespace(window=window, files=files, bird=bird, sharp=sharp, denoise=denoise,
                              bar=toolbar, messages=messages, busy_menus=busy_menus)
    finally:
        toolbar.request_shutdown()
        window.close()
        window.deleteLater()
        _APP.processEvents()


def test_buttons_order_icons_and_dropdowns(toolbar_env) -> None:
    bar = toolbar_env.bar
    assert bar.toolbar.objectName() == PROCESS_TOOLBAR_OBJECT_NAME
    actions = [a for a in bar.toolbar.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == ["识别鸟种…", "选择候选鸟名…", "手动指定鸟名…", "鸟清晰度检测", "批量降噪"]
    assert all(not a.icon().isNull() for a in actions)
    assert [a.toolTip() for a in actions] == [a.text() for a in actions]
    for task, items in ((bar.identify, ["逐只识别…"]), (bar.sharpness, ["查看清晰度计算过程…"])):
        assert [a.text() for a in task.action.menu().actions()] == items
        button = bar.toolbar.widgetForAction(task.action)
        assert button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
        assert button.isVisible() and button.width() > button.height()


def test_buttons_start_tasks_for_selected_photos(toolbar_env) -> None:
    env, paths = toolbar_env, toolbar_env.files.paths
    QTest.mouseClick(env.bar.toolbar.widgetForAction(env.bar.candidates.action), Qt.MouseButton.LeftButton)
    env.bar.identify.action.trigger()
    env.bar.identify.action.menu().actions()[0].trigger()
    assert env.bird.calls == [
        ("start", paths, {"saved_candidates": True}),
        ("start", paths, {}),
        ("start", paths, {"per_bird": True}),
    ]
    env.bar.sharpness.action.trigger()
    env.bar.denoise.action.trigger()
    assert env.sharp.calls == [("start", paths, {})]
    assert env.denoise.calls == [("start", paths, {})]
    env.bar.sharpness.action.menu().actions()[0].trigger()
    assert env.sharp.calls[-1] == ("trace", paths[0])
    assert env.messages == []


def test_without_selection_asks_to_select_and_starts_nothing(toolbar_env) -> None:
    env = toolbar_env
    env.files.paths = []
    for action in (env.bar.identify.action, env.bar.sharpness.action, env.bar.denoise.action, env.bar.manual_name):
        action.trigger()
    assert env.bird.calls == env.sharp.calls == env.denoise.calls == []
    assert env.bird.catalog.opened == []
    assert len(env.messages) == 4
    assert all("请先在文件列表中选择照片" in message[2] for message in env.messages)


def test_busy_task_shows_badge_and_progress_or_stop_menu(toolbar_env) -> None:
    env = toolbar_env
    idle_icon = env.bar.sharpness.action.icon().cacheKey()
    env.sharp.busy = True
    env.bar.refresh()
    assert env.bar.sharpness.action.toolTip() == "鸟清晰度检测进行中：点击查看进度或停止"
    assert env.bar.sharpness.action.icon().cacheKey() != idle_icon
    env.bar.sharpness.action.trigger()
    assert env.busy_menus == ["鸟清晰度检测"]
    assert env.sharp.calls == []
    # 计算过程窗口只读，运行中仍可打开。
    env.bar.sharpness.action.menu().actions()[0].trigger()
    assert env.sharp.calls == [("trace", env.files.paths[0])]

    menu = env.bar.busy_menu(env.bar.sharpness)
    assert [a.text() for a in menu.actions()] == ["查看鸟清晰度检测进度", "停止鸟清晰度检测"]
    menu.actions()[0].trigger()
    menu.actions()[1].trigger()
    assert env.sharp.calls[-2:] == [("progress",), ("stop",)]

    env.bird.busy = True
    env.bar.refresh()
    env.bar.identify.action.menu().actions()[0].trigger()
    env.bar.candidates.action.trigger()
    assert env.busy_menus[-2:] == ["识鸟", "识鸟"]
    assert env.bird.calls == []

    env.sharp.busy = False
    env.bar.refresh()
    assert env.bar.sharpness.action.toolTip() == "鸟清晰度检测"


def test_manual_name_checks_writes_and_disables_while_applying(toolbar_env) -> None:
    env = toolbar_env
    env.files.writes_allowed = False
    env.bar.manual_name.trigger()
    assert env.bird.catalog.opened == []
    assert env.files.write_checks == [("手动指定鸟名", True)]
    env.files.writes_allowed = True
    env.bar.manual_name.trigger()
    assert env.bird.catalog.opened == [env.files.paths]
    env.bird.catalog.applying = True
    env.bar.refresh()
    assert not env.bar.manual_name.isEnabled()
    env.bird.catalog.applying = False
    env.bar.refresh()
    assert env.bar.manual_name.isEnabled()


def test_refresh_timer_tracks_tasks_started_elsewhere(toolbar_env) -> None:
    env = toolbar_env
    assert env.bar._timer.isActive()
    env.denoise.busy = True
    env.bar._timer.timeout.emit()
    assert env.bar.denoise.action.toolTip() == "降噪进行中：点击查看进度或停止"
    env.bar.request_shutdown()
    assert not env.bar._timer.isActive()
    assert not env.bar.toolbar.isEnabled()


def test_real_busy_menu_pops_under_button_and_closes(monkeypatch) -> None:
    from PyQt6.QtCore import QTimer

    window = QMainWindow()
    bird, sharp, denoise = _Controller(), _Controller(), _Controller()
    bird.catalog = _Catalog()
    bar = ProcessToolbar(window, _Files(["/photos/a.jpg"]), bird, sharp, denoise)
    window.addToolBar(bar.toolbar)
    window.show()
    _APP.processEvents()
    seen: list[tuple] = []

    def close_popup():
        popup = QApplication.activePopupWidget()
        if popup is not None:
            seen.append(([a.text() for a in popup.actions()], popup.geometry().top()))
            popup.actions()[1].trigger()
            popup.close()

    try:
        denoise.busy = True
        bar.refresh()
        QTimer.singleShot(50, close_popup)
        bar.denoise.action.trigger()
        button = bar.toolbar.widgetForAction(bar.denoise.action)
        assert seen and seen[0][0] == ["查看降噪进度", "停止降噪"]
        assert seen[0][1] >= button.mapToGlobal(button.rect().bottomLeft()).y()
        assert denoise.calls == [("stop",)]
    finally:
        bar.request_shutdown()
        window.close()
        window.deleteLater()
        _APP.processEvents()
