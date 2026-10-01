from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import pytest

from SuperBirdUpdater.common import INSTALLED_MANIFEST, UpdateError, atomic_json
from SuperBirdUpdater.coordinator import apply_update
from SuperBirdUpdater.download import prepare
from SuperBirdUpdater.install import state_dir
from SuperBirdUpdater.locking import FileLock
from SuperBirdUpdater.manifest import load
from SuperBirdUpdater.runtime import active_instances, process_alive
from SuperBirdUpdater.sources import LocalSource

_APP = None


@pytest.fixture
def qt_app():
    global _APP
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


CHILD = '''
import json, os, sys, uuid
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtCore import QTimer
from SuperBirdUpdater.bridge import AppEndpoint
from SuperBirdUpdater.common import atomic_json
root, name, busy = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "busy"
app = QApplication([])
window = QMainWindow()
token = uuid.uuid4().hex
data = {"root": str(root.resolve()), "app": name, "pid": os.getpid(), "server": "sbt-test-"+token, "token": token}
endpoint = AppEndpoint(window, data, lambda: "export busy" if busy else "")
window.show()
atomic_json(root / ".superbird-update" / "instances" / (token + ".json"), data)
QTimer.singleShot(20000, app.quit)
app.exec()
'''


@pytest.fixture
def processes(versions):
    children = []
    def start(name, busy=False):
        child = subprocess.Popen([sys.executable, "-c", CHILD, str(versions[0]), name, "busy" if busy else "ready"],
                                 env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        children.append(child)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if child.poll() is not None:
                pytest.fail(child.stderr.read().decode())
            found = [i for i in active_instances(versions[0]) if i["pid"] == child.pid]
            if found:
                return child, found[0]
            time.sleep(0.01)
        pytest.fail("IPC child did not register")
    yield start
    for child in children:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        child.stderr.close()


def test_real_ipc_ready_prepare_resume(processes, qt_app):
    from SuperBirdUpdater.bridge import request_instance
    child, instance = processes("SuperViewer")
    assert request_instance(instance, "status")["ready"]
    assert request_instance(instance, "prepare")["ready"]
    assert request_instance(instance, "resume")["ready"]
    assert process_alive(child.pid)
    assert not request_instance(instance, "close")["ready"]  # 必须先 prepare。


def test_busy_app_prevents_closing_either_app(versions, tmp_path, processes, qt_app):
    from SuperBirdUpdater.bridge import request_instance
    viewer, _ = processes("SuperViewer")
    stamp, _ = processes("SuperBirdStamp", busy=True)
    root, _, assets, current, candidate = versions
    cache = tmp_path / "cache"
    prepare(root, candidate, LocalSource(assets, "linux", "x86_64"), cache)
    with pytest.raises(UpdateError, match="export busy"):
        apply_update(root, candidate, cache, request=request_instance, restart=False)
    assert viewer.poll() is None and stamp.poll() is None
    assert load(root / INSTALLED_MANIFEST) == current


def test_install_waits_for_real_child_exit(versions, tmp_path, processes, qt_app):
    from SuperBirdUpdater.bridge import request_instance
    first, _ = processes("SuperViewer")
    second, _ = processes("SuperBirdStamp")
    # 父进程负责回收测试子进程；协调器仍通过 PID 检查真实生命周期。
    watchers = [threading.Thread(target=p.wait) for p in (first, second)]
    for watcher in watchers:
        watcher.start()
    root, _, assets, _, candidate = versions
    cache = tmp_path / "cache"
    prepare(root, candidate, LocalSource(assets, "linux", "x86_64"), cache)
    restarted = apply_update(root, candidate, cache, request=request_instance, restart=False, shutdown_timeout=5)
    assert restarted == ["SuperBirdStamp", "SuperViewer"]
    assert first.poll() == second.poll() == 0
    assert load(root / INSTALLED_MANIFEST) == candidate
    for watcher in watchers:
        watcher.join(timeout=2)


def test_admission_lock_blocks_other_process(tmp_path):
    path = tmp_path / "launch.lock"
    code = "from pathlib import Path; from SuperBirdUpdater.locking import FileLock; import sys; lock=FileLock(Path(sys.argv[1])); print(lock.acquire()); lock.close()"
    with FileLock(path):
        result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=5)
        assert result.stdout.strip() == "False"
    result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=5)
    assert result.stdout.strip() == "True"


def test_source_startup_disabled(monkeypatch):
    from SuperBirdUpdater import runtime
    monkeypatch.setattr(runtime, "_registration", None)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert runtime.admit_startup("SuperViewer") is None
