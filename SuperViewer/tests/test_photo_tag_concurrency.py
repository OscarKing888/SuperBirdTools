from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import tagged_file_list as tagged_module
from SuperViewer.superviewer.qt_compat import QApplication, QThread, pyqtSignal


@pytest.fixture
def tag_panel(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n捕食\n", encoding="utf-8")
    panel = tagged_module.SuperViewerTaggedFileListPanel(tag_config_path=config)
    try:
        yield app, panel
    finally:
        panel.shutdown()
        panel.close()
        app.processEvents()


def _wait_until(app, condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    app.processEvents()
    assert condition()


class _MetadataEmitter(QThread):
    batch_ready = pyqtSignal(object)


@pytest.mark.parametrize("clear", [False, True], ids=["add", "clear"])
def test_late_tag_batch_preserves_xmp_edit_and_other_batch_paths(
    tag_panel, tmp_path: Path, monkeypatch, clear: bool,
) -> None:
    _, panel = tag_panel
    edited = os.path.normpath(str(tmp_path / "edited.jpg"))
    untouched = os.path.normpath(str(tmp_path / "untouched.jpg"))
    Path(edited).write_bytes(b"source image")
    metadata = PhotoMetaDataXMP()
    assert metadata.write_subjects(edited, ["Lightroom", "飞行"] if clear else ["Lightroom"])
    monkeypatch.setattr(panel, "_refresh_metadata_state_for_paths", lambda paths: None)
    worker = tagged_module.PhotoTagCacheWorker([edited, untouched], allowed_tags=panel._available_tags)
    worker._tag_generation_snapshot = {
        os.path.normcase(edited): 0,
        os.path.normcase(untouched): 0,
    }
    panel._photo_tag_loader = worker
    updated = []
    panel.photo_tags_cache_updated.connect(lambda paths: updated.append(list(paths)))
    try:
        if clear:
            panel.clear_photo_tags_for_paths([edited])
            expected = set()
        else:
            panel.set_photo_tag_for_paths([edited], "捕食", True)
            expected = {"捕食"}
        updated.clear()
        panel._on_photo_tag_cache_batch_ready(worker, {edited: {"飞行"}, untouched: {"飞行"}})

        assert panel._photo_tag_cache[edited] == expected
        assert set(panel._meta_cache[edited]["tags"]) == expected
        assert panel._photo_tag_cache[untouched] == {"飞行"}
        assert updated == [[untouched]]
        assert set(metadata.read_subjects(edited)) == expected | {"Lightroom"}
        assert Path(edited).with_suffix(".xmp").is_file()
        assert not list(tmp_path.glob("*.superviewer.json"))
    finally:
        panel._photo_tag_loader = None
        worker.deleteLater()


def test_metadata_batches_protect_edits_keep_seed_and_reject_old_sender(
    tag_panel, tmp_path: Path, monkeypatch,
) -> None:
    _, panel = tag_panel
    edited = os.path.normpath(str(tmp_path / "edited.jpg"))
    untouched = os.path.normpath(str(tmp_path / "untouched.jpg"))
    panel._all_files = [edited, untouched]
    panel._photo_tag_cache = {edited: {"捕食"}}
    panel._meta_cache = {edited: {"tags": ["捕食"]}}
    panel._bump_photo_tag_generations([edited])
    queued = []
    updates = []
    monkeypatch.setattr(panel, "_enqueue_meta_apply", lambda batch: queued.append(batch))
    panel.photo_tags_cache_updated.connect(lambda paths: updates.append(list(paths)))
    current, previous = _MetadataEmitter(), _MetadataEmitter()
    current.batch_ready.connect(panel._on_metadata_batch_ready)
    previous.batch_ready.connect(panel._on_metadata_batch_ready)
    panel._metadata_loader = current
    try:
        previous.batch_ready.emit({untouched: {"tags": ["捕食"], "rating": 1}})
        assert queued == []
        assert updates == []
        assert untouched not in panel._photo_tag_cache
        current.batch_ready.emit({
            edited: {"tags": ["飞行"], "rating": 4},
            untouched: {"tags": ["飞行"], "rating": 2},
        })
        assert panel._photo_tag_cache == {edited: {"捕食"}, untouched: {"飞行"}}
        assert panel._meta_cache[edited] == {"tags": ["捕食"], "rating": 4}
        assert queued[-1][edited]["tags"] == ["捕食"]
        assert updates == [[untouched]]
        # An unedited cached path may still receive fresher external metadata.
        current.batch_ready.emit({untouched: {"tags": ["捕食"], "rating": 5}})
        assert panel._photo_tag_cache[untouched] == {"捕食"}
        panel._metadata_loader = None
        panel.request_shutdown()
        panel._metadata_loader = current
        current.batch_ready.emit({edited: {"tags": ["飞行"], "rating": 1}})
        assert panel._meta_cache[edited]["rating"] == 4
    finally:
        panel._metadata_loader = None
        current.deleteLater()
        previous.deleteLater()


@pytest.mark.parametrize("cancel_first", [False, True], ids=["pending", "directory_switch"])
def test_next_tag_batch_waits_for_actual_thread_finish(
    tag_panel, tmp_path: Path, monkeypatch, cancel_first: bool,
) -> None:
    app, panel = tag_panel
    first = os.path.normpath(str(tmp_path / "first.jpg"))
    second = os.path.normpath(str(tmp_path / "second.jpg"))
    summary_reached = threading.Event()
    release_first = threading.Event()
    starts = []

    class ControlledWorker(tagged_module.PhotoTagCacheWorker):
        def run(self) -> None:
            starts.append(list(self._paths))
            self._finished_processed = self._finished_total = len(self._paths)
            self.finished_summary.emit(len(self._paths), len(self._paths))
            if self._paths == [first]:
                summary_reached.set()
                release_first.wait(3.0)
            # Even a cancelled reader can already have queued a stale result.
            self.batch_ready.emit({path: {"飞行"} for path in self._paths})

    monkeypatch.setattr(tagged_module, "PhotoTagCacheWorker", ControlledWorker)
    try:
        panel._start_photo_tag_cache_loader_if_needed(
            [first], reason="test", allow_without_filters=True,
        )
        worker = panel._photo_tag_loader
        _wait_until(app, lambda: summary_reached.is_set() and panel._photo_tag_cache_done == 1)
        assert panel._photo_tag_loader is worker
        assert worker.isRunning()
        if cancel_first:
            panel._stop_photo_tag_cache_loader()
            assert panel._photo_tag_stopping_loader is worker
            assert worker in panel._pending_loaders
        panel._start_photo_tag_cache_loader_if_needed(
            [second], reason="test_pending", allow_without_filters=True,
        )
        app.processEvents()
        assert starts == [[first]]
        assert panel._photo_tag_pending_paths == [second]
        release_first.set()
        _wait_until(app, lambda: (
            panel._photo_tag_loader is None
            and panel._photo_tag_stopping_loader is None
            and not panel._photo_tag_pending_paths
        ))
        assert starts == [[first], [second]]
        assert panel._photo_tag_cache[second] == {"飞行"}
        assert (first in panel._photo_tag_cache) is (not cancel_first)
        assert worker not in panel._pending_loaders
    finally:
        release_first.set()


def test_tag_loader_ownership_survives_return_until_finished_slot(tag_panel) -> None:
    _, panel = tag_panel

    class ReturnedWorker:
        def isRunning(self):
            raise AssertionError("pending finished slot still owns the worker")

    worker = ReturnedWorker()
    panel._photo_tag_loader = worker
    try:
        panel._start_photo_tag_cache_loader_if_needed(
            ["one.jpg", "two.jpg"], reason="test", allow_without_filters=True,
        )
        panel._on_photo_tag_cache_finished(worker, 2, 2)
        assert panel._photo_tag_loader is worker
        assert panel._photo_tag_pending_paths == ["one.jpg", "two.jpg"]
    finally:
        panel._photo_tag_loader = None


def test_shutdown_stops_tag_refresh_and_does_not_start_pending_batch(tag_panel, monkeypatch) -> None:
    app, panel = tag_panel
    reached = threading.Event()
    release = threading.Event()
    starts = []

    class ControlledWorker(tagged_module.PhotoTagCacheWorker):
        def run(self) -> None:
            starts.append(list(self._paths))
            reached.set()
            release.wait(3.0)

    monkeypatch.setattr(tagged_module, "PhotoTagCacheWorker", ControlledWorker)
    try:
        panel._active_tag_filters = {"飞行"}
        panel._schedule_photo_tag_filter_refresh()
        panel._start_photo_tag_cache_loader_if_needed(["one.jpg"], reason="test")
        panel._start_photo_tag_cache_loader_if_needed(["two.jpg"], reason="pending")
        _wait_until(app, reached.is_set)
        panel.request_shutdown()
        assert panel._background_shutdown_requested
        assert not panel._photo_tag_filter_refresh_timer.isActive()
        release.set()
        _wait_until(app, lambda: panel._photo_tag_stopping_loader is None)
        assert starts == [["one.jpg"]]
        assert panel._photo_tag_pending_paths == []
        assert panel._photo_tag_cache == {}
    finally:
        release.set()
