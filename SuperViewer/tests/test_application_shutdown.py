"""Exercise the production event loop, not just QWidget.close/processEvents."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app_common.exif_io.exiftool_runner import hidden_subprocess_kwargs


@pytest.mark.parametrize("deferred", [False, True])
def test_application_exits_after_window_shutdown(tmp_path, deferred):
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path), str(int(deferred))],
        cwd=repo,
        env=dict(os.environ, PYTHONPATH=str(repo), QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        **hidden_subprocess_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state == {"finalized": True, "hidden_while_waiting": deferred,
                     "receiver_stopped": True, "exiftool_closed": True}


def _run_child(directory: Path, deferred: bool) -> None:
    import importlib
    import threading

    # Isolate all runtime state before constructing QApplication or MainWindow.
    os.environ["APPDATA"] = str(directory / "appdata")
    os.environ["LOCALAPPDATA"] = str(directory / "cache")
    os.environ["APP_COMMON_LOG_LEVEL"] = "ERROR"
    from app_common import superviewer_user_options as options
    from app_common.file_browser import _panel as panels
    from app_common.file_browser._workers import DirectoryScanWorker
    from SuperViewer.superviewer import paths_settings

    config = directory / "settings"
    config.mkdir()
    (config / paths_settings.CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    paths_settings._get_app_dir = lambda: str(config)
    paths_settings._get_user_state_dir = lambda: str(config / "state")
    options._get_app_dir = lambda: str(config)
    options._RUNTIME_OPTIONS = options.normalize_user_options(None)
    main = importlib.import_module("SuperViewer.main")
    main._get_app_dir = lambda: str(config)
    main.get_initial_file_list_from_argv = lambda: []
    state = {"finalized": False, "hidden_while_waiting": False,
             "receiver_stopped": False, "exiftool_closed": False}

    class Receiver:
        def __init__(self, *args):
            pass

        def start(self):
            return True

        def stop(self):
            state["receiver_stopped"] = True

    main.SingleInstanceReceiver = Receiver
    close_exiftool = main.close_exiftool_process

    def close_exiftool_and_record():
        close_exiftool()
        state["exiftool_closed"] = True

    main.close_exiftool_process = close_exiftool_and_record

    class BlockedScan(DirectoryScanWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.started_event = threading.Event()
            self.release = threading.Event()

        def run(self):
            self.started_event.set()
            self.release.wait(5)

    panels.DirectoryScanWorker = BlockedScan
    windows = []

    class Window(main.MainWindow):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            windows.append(self)

        def showMaximized(self):
            super().showMaximized()
            if deferred:
                self._file_list.load_directory(str(directory))
                self.scan = self._file_list._directory_scan_worker
                assert self.scan.started_event.wait(2)
            main.QTimer.singleShot(0, self.begin_close)
            main.QTimer.singleShot(3000, lambda: main.QApplication.instance().exit(77))

        def begin_close(self):
            self.close()
            if deferred:
                state["hidden_while_waiting"] = not self.isVisible()
                assert not self._shutdown_finalized
                # Release the real QThread via the event loop after close was deferred.
                main.QTimer.singleShot(0, self.scan.release.set)

    main.MainWindow = Window
    try:
        main.main()
    except SystemExit as exc:
        state["finalized"] = windows[0]._shutdown_finalized
        print(json.dumps(state), flush=True)
        raise SystemExit(exc.code)


if __name__ == "__main__":
    _run_child(Path(sys.argv[1]), bool(int(sys.argv[2])))
