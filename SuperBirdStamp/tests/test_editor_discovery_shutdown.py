from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PyQt6.QtCore import QThread
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication

from birdstamp import config
from birdstamp.gui import editor


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def discovery_window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-cache"))
    monkeypatch.setattr(editor.BirdStampEditorWindow, "_start_bird_detector_preload", lambda self: None)
    monkeypatch.setattr(editor.BirdStampEditorWindow, "_run_deferred_startup_tasks", lambda self: None)
    source = tmp_path / "found.png"
    Image.new("RGB", (8, 8), "blue").save(source)

    class ControlledDiscovery(editor._PhotoInputDiscoveryWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.started_work = threading.Event()
            self.finish_discovery = threading.Event()
            self.finish_thread = threading.Event()

        def run(self):
            # Queue a result before cancellation to exercise the sender guard.
            self.paths_ready.emit([source])
            self.started_work.set()
            self.finish_discovery.wait(5)
            self.finished_discovery.emit(1)
            self.finish_thread.wait(5)

    monkeypatch.setattr(editor, "_PhotoInputDiscoveryWorker", ControlledDiscovery)
    window = editor.BirdStampEditorWindow()
    window._start_photo_input_discovery([tmp_path])
    worker = window._photo_input_discovery_workers[0]
    assert worker.started_work.wait(1)
    try:
        yield window, worker
    finally:
        worker.finish_discovery.set()
        worker.finish_thread.set()
        try:
            assert QThread.wait(worker, 2000)
        except RuntimeError:  # Already deleted after QThread.finished.
            pass
        _APP.processEvents()
        window.close()
        window.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize("stopped_before_close", [False, True])
def test_close_waits_for_cancelled_discovery_without_blocking(discovery_window, monkeypatch, stopped_before_close):
    window, worker = discovery_window
    if stopped_before_close:
        # Simulate a timed-out clear-list wait without sleeping three seconds.
        monkeypatch.setattr(worker, "wait", lambda _timeout: False)
        assert not window._stop_photo_input_discovery_workers(wait=True)

    event = QCloseEvent()
    started = time.monotonic()
    window.closeEvent(event)

    assert time.monotonic() - started < 0.5
    assert not event.isAccepted()
    assert worker.isRunning()
    assert not window._photo_input_discovery_workers
    assert window._pending_photo_input_discovery_workers == [worker]
    assert not window._photo_input_discovery_import_options
    _APP.processEvents()
    assert window.photo_list.topLevelItemCount() == 0
    assert not window._received_photo_import_pending_paths

    worker.finish_discovery.set()
    worker.finish_thread.set()
    assert QThread.wait(worker, 2000)
    _APP.processEvents()
    assert not window._pending_photo_input_discovery_workers
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_logical_discovery_completion_keeps_thread_owned_until_finished(discovery_window, monkeypatch):
    window, worker = discovery_window
    # Keep this test about thread ownership, not metadata/preview work from
    # the queued sample path above.
    monkeypatch.setattr(window, "_auto_add_report_db_paths_for_photos", lambda paths: 0)
    monkeypatch.setattr(window, "_enqueue_received_photo_paths", lambda *args, **kwargs: None)
    worker.finish_discovery.set()
    deadline = time.monotonic() + 1
    while window._photo_input_discovery_workers and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    assert not window._photo_input_discovery_workers
    assert worker.isRunning()
    assert window._pending_photo_input_discovery_workers == [worker]

    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window._pending_photo_input_discovery_workers == [worker]
