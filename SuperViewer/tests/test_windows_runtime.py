"""Windows Qt/Torch loading order, exercised in fresh interpreter processes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from SuperViewer.superviewer import windows_runtime as runtime


def test_preload_uses_system_paths_and_retains_handles(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(runtime, "_RUNTIME_HANDLES", {})
    system = tmp_path / "System32"
    system.mkdir()
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        (system / name).touch()
    loaded = []

    def load(path):
        loaded.append(Path(path))
        return object()

    monkeypatch.setattr(runtime.ctypes, "WinDLL", load, raising=False)
    runtime.preload_windows_runtime()
    handles = dict(runtime._RUNTIME_HANDLES)
    runtime.preload_windows_runtime()
    assert len(loaded) == 3 and all(path.parent == system for path in loaded)
    assert runtime._RUNTIME_HANDLES == handles


def test_missing_or_failed_runtime_does_not_abort_startup(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime, "_RUNTIME_HANDLES", {})
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    system = tmp_path / "System32"
    system.mkdir()
    (system / "msvcp140.dll").touch()

    def fail(path):
        raise OSError("runtime unavailable")

    monkeypatch.setattr(runtime.ctypes, "WinDLL", fail, raising=False)
    runtime.preload_windows_runtime()
    assert not runtime._RUNTIME_HANDLES
    assert "msvcp140.dll" in caplog.text and "runtime unavailable" in caplog.text
    monkeypatch.delenv("SystemRoot")
    runtime.preload_windows_runtime()


def test_non_windows_does_not_load_dlls(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime.ctypes, "WinDLL", lambda _: pytest.fail("Windows DLL on macOS"), raising=False)
    runtime.preload_windows_runtime()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL loading order")
@pytest.mark.parametrize("startup", [
    "import SuperViewer.entry",
    # PyInstaller/standalone main has no parent package.
    "sys.path.insert(0, str(Path('SuperViewer').resolve())); import main",
])
def test_viewer_startup_allows_late_torch_in_worker(tmp_path, startup):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["APP_COMMON_LOG_FILE"] = str(tmp_path / "viewer.log")
    # Do not accidentally pass because build-only sitecustomize preloaded DLLs.
    env.pop("PYTHONPATH", None)
    source = f"""
import sys
from pathlib import Path
{startup}
assert 'torch' not in sys.modules, 'Torch must remain lazy'
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QThread
app = QApplication([])
errors = []
class Worker(QThread):
    def run(self):
        try:
            import torch
            from ultralytics import YOLO
            assert torch.ones(2).sum().item() == 2
        except BaseException as exc:
            errors.append(repr(exc))
worker = Worker()
worker.start()
assert worker.wait(60000), 'Torch worker timed out'
assert not errors, errors
print('Qt then Torch/Ultralytics worker: OK')
"""
    result = subprocess.run([sys.executable, "-c", source], env=env,
                            cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "worker: OK" in result.stdout
