from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser import _permissions
from app_common.file_browser._workers import DirectoryScanWorker
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])


class _BlockedScan(DirectoryScanWorker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_event = threading.Event()
        self.release = threading.Event()

    def run(self):
        self.started_event.set()
        self.release.wait(5)


def test_viewer_shutdown_waits_for_canceled_scan_and_queued_finished(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS", superviewer_user_options.normalize_user_options(None))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for name, value in vars(_permissions).copy().items():
        if name.startswith("CURRENT_SUPERPICKY_"):
            monkeypatch.setattr(_permissions, name, value)
    monkeypatch.setattr(panel_module, "DirectoryScanWorker", _BlockedScan)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **kwargs: None)
    tags = tmp_path / "tags.cfg"
    tags.write_text("鸟类\n", encoding="utf-8")
    panel = SuperViewerTaggedFileListPanel(tag_config_path=tags)
    monkeypatch.setattr(panel, "_rebuild_views", lambda: None)
    workers = []
    try:
        for name in ("A", "B"):
            directory = tmp_path / name
            directory.mkdir()
            panel.load_directory(str(directory), force_reload=True)
            worker = panel._directory_scan_worker
            workers.append(worker)
            assert worker.started_event.wait(1)
        first, latest = workers
        assert first.isInterruptionRequested()
        panel.request_shutdown()
        assert all(worker in panel._tag_shutdown_workers for worker in workers)
        assert not panel.shutdown(wait_timeout_ms=0)
        assert not panel._tag_shutdown_complete

        for worker in workers:
            worker.release.set()
            assert worker.wait(1000)
        # Native thread completion alone does not release the scan owners.
        assert not panel.shutdown(wait_timeout_ms=0)
        assert panel.has_pending_directory_scans()
        deadline = time.monotonic() + 3
        while panel.has_pending_directory_scans() and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.003)
        assert not panel.has_pending_directory_scans()
        assert panel.shutdown(wait_timeout_ms=0)
        assert panel._tag_shutdown_complete
    finally:
        for worker in workers:
            worker.release.set()
            try:
                assert worker.wait(3000)
            except RuntimeError:
                pass
        deadline = time.monotonic() + 3
        while not panel.shutdown(wait_timeout_ms=0) and time.monotonic() < deadline:
            _APP.processEvents()
        assert panel.shutdown(wait_timeout_ms=0)
        panel.close()
        panel.deleteLater()
        _APP.processEvents()
