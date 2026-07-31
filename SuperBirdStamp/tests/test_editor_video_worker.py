from __future__ import annotations

import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from birdstamp.export_stage import VideoExportOptions
from birdstamp.gui import editor_video_panel
from birdstamp.gui.editor_video_panel import VideoExportJobSeed, VideoExportWorker


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _wait_until(
    app: QApplication,
    predicate,
    *,
    timeout: float = 3.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def _seed(path: Path) -> VideoExportJobSeed:
    return VideoExportJobSeed(
        path=path,
        settings={"draw_banner": False, "draw_text": False, "draw_focus": False},
        raw_metadata={"SourceFile": str(path)},
        metadata_complete=False,
    )


def test_video_worker_prepares_metadata_and_context_off_gui_thread(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    source_path = (tmp_path / "source.jpg").resolve(strict=False)
    source_path.write_bytes(b"not-decoded-by-this-test")
    output_path = tmp_path / "output.mp4"
    main_thread_id = threading.get_ident()
    observed: dict[str, object] = {}

    def load_metadata(paths, *, mode: str):
        observed["metadata_thread"] = threading.get_ident()
        assert mode == "auto"
        return {
            Path(paths[0]).resolve(strict=False): {
                "SourceFile": str(source_path),
                "XMP-dc:Title": "worker title",
            }
        }

    def build_context(photo_info, raw_metadata):
        observed["context_thread"] = threading.get_ident()
        return {"title": str(raw_metadata.get("XMP-dc:Title") or "")}

    def fake_export(jobs, options, **kwargs):
        observed["export_thread"] = threading.get_ident()
        observed["jobs"] = jobs
        return options.output_path

    monkeypatch.setattr(editor_video_panel, "extract_many_with_xmp_priority", load_metadata)
    monkeypatch.setattr(editor_video_panel.editor_utils, "build_metadata_context", build_context)
    monkeypatch.setattr(editor_video_panel, "export_video", fake_export)

    worker = VideoExportWorker(
        jobs=[],
        job_seeds=[_seed(source_path)],
        options=VideoExportOptions(output_path=output_path),
    )
    succeeded: list[str] = []
    worker.exportSucceeded.connect(succeeded.append)
    worker.start()
    try:
        assert _wait_until(app, lambda: not worker.isRunning())
        app.processEvents()
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(3000)

    assert succeeded == [str(output_path)]
    assert observed["metadata_thread"] != main_thread_id
    assert observed["context_thread"] != main_thread_id
    assert observed["export_thread"] != main_thread_id
    jobs = observed["jobs"]
    assert isinstance(jobs, list) and len(jobs) == 1
    assert jobs[0].raw_metadata["XMP-dc:Title"] == "worker title"
    assert jobs[0].metadata_context == {"title": "worker title"}


def test_video_worker_cancel_during_metadata_load_keeps_gui_responsive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _app()
    source_path = (tmp_path / "slow-source.jpg").resolve(strict=False)
    source_path.write_bytes(b"not-decoded-by-this-test")
    entered = threading.Event()
    release = threading.Event()
    export_called = threading.Event()

    def slow_metadata_load(paths, *, mode: str):
        entered.set()
        assert release.wait(timeout=3.0)
        return {}

    def fail_export(*args, **kwargs):
        export_called.set()
        raise AssertionError("cancelled preparation must not start export")

    monkeypatch.setattr(editor_video_panel, "extract_many_with_xmp_priority", slow_metadata_load)
    monkeypatch.setattr(editor_video_panel, "export_video", fail_export)

    worker = VideoExportWorker(
        jobs=[],
        job_seeds=[_seed(source_path)],
        options=VideoExportOptions(output_path=tmp_path / "cancelled.mp4"),
    )
    cancelled: list[str] = []
    worker.exportCancelled.connect(cancelled.append)
    timer = QTimer()
    heartbeats: list[int] = []
    timer.timeout.connect(lambda: heartbeats.append(1))
    timer.start(10)
    worker.start()
    try:
        assert _wait_until(app, entered.is_set)
        worker.cancel()
        heartbeat_deadline = time.monotonic() + 0.15
        while time.monotonic() < heartbeat_deadline:
            app.processEvents()
            time.sleep(0.005)
        assert len(heartbeats) >= 3
        release.set()
        assert _wait_until(app, lambda: not worker.isRunning())
        app.processEvents()
    finally:
        release.set()
        timer.stop()
        if worker.isRunning():
            worker.cancel()
            worker.wait(3000)

    assert cancelled
    assert export_called.is_set() is False
