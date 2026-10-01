from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from SuperBirdUpdater import gui, runtime
from SuperBirdUpdater.common import CONFIG_NAME, INSTALLED_MANIFEST, atomic_json, read_json
from SuperBirdUpdater.sources import LocalSource

_APP = None


def test_automatic_interval_skip_and_manual_override(versions, tmp_path, monkeypatch):
    root, _, assets, current, candidate = versions
    state = tmp_path / "preferences"
    state.mkdir()
    atomic_json(root / CONFIG_NAME, {"source": "local", "directory": str(assets), "automatic_check": True})
    monkeypatch.setattr(gui, "platform_id", lambda: "linux")
    monkeypatch.setattr(gui, "architecture", lambda: "x86_64")
    controller = SimpleNamespace(root=root, state=state, automatic=True)
    assert gui.UpdateWindow._check(controller, lambda *_: None)[:2] == (current["version"], candidate["version"])
    # 第二个启动请求即使先前更新器已退出，也不会立即再提示。
    assert gui.UpdateWindow._check(controller, lambda *_: None) is None
    atomic_json(state / "preferences.json", {"last_check": 0, "skipped_commit": candidate["commit"]})
    assert gui.UpdateWindow._check(controller, lambda *_: None) is None
    controller.automatic = False
    assert gui.UpdateWindow._check(controller, lambda *_: None) is not None


def test_disabled_automatic_check_does_not_contact_source(versions, tmp_path, monkeypatch):
    root = versions[0]
    atomic_json(root / CONFIG_NAME, {"automatic_check": False})
    monkeypatch.setattr(gui, "platform_id", lambda: "linux")
    monkeypatch.setattr(gui, "architecture", lambda: "x86_64")
    monkeypatch.setattr(gui, "source_from_config", lambda *_: pytest.fail("unexpected network check"))
    controller = SimpleNamespace(root=root, state=tmp_path, automatic=True)
    assert gui.UpdateWindow._check(controller, lambda *_: None) is None


def test_waiter_preserves_original_photo_arguments(versions, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "launch_process", lambda command, **kwargs: calls.append(command))
    args = ["/tmp/中文 照片.jpg", "--file", "/tmp/second.png"]
    runtime.wait_and_relaunch(versions[0], "SuperViewer", args)
    assert calls[0][1:] == args


def test_window_retains_task_until_real_finished(versions, tmp_path, monkeypatch):
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui, "user_state", lambda root: tmp_path)
    monkeypatch.setattr(gui.UpdateWindow, "begin", lambda self: None)
    quits, completed = [], []
    monkeypatch.setattr(gui.UpdateWindow, "_quit", lambda self: quits.append(True))
    window = gui.UpdateWindow(versions[0])
    started, release = threading.Event(), threading.Event()
    def operation(progress):
        started.set()
        release.wait(5)
        return "finished"
    window._run(operation, lambda *_: completed.append(True))
    try:
        assert started.wait(2)
        task = window.task
        window.close()
        assert window.task is task and task.isRunning()
        assert window.cancel.is_set()
        release.set()
        deadline = time.monotonic() + 5
        while window.task is not None and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.005)
        assert window.task is None
        assert quits and not completed
    finally:
        release.set()
        if window.task:
            window.task.wait(5000)
        window.deleteLater()
        _APP.processEvents()
