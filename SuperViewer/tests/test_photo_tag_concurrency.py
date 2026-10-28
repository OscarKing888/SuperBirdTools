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
        _wait_until(QApplication.instance(), lambda: not panel._command_history.busy)
        from app_common.exif_io.metadata_store import get_store
        assert get_store().flush_paths([edited])
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


def test_tag_lookup_uses_owned_shared_loader_once(tag_panel, tmp_path, monkeypatch):
    _, panel = tag_panel
    first, second = (os.path.normpath(str(tmp_path / name)) for name in ("first.jpg", "second.jpg"))
    owner = object()
    started = []
    def start(paths):
        started.append(list(paths))
        panel._metadata_loader = owner
    monkeypatch.setattr(panel, "_start_metadata_loader", start)
    monkeypatch.setattr(tagged_module, "PhotoTagCacheWorker", lambda *a, **k: pytest.fail("second XML loader"))
    try:
        panel._start_photo_tag_cache_loader_if_needed([first], reason="test", allow_without_filters=True)
        panel._start_photo_tag_cache_loader_if_needed([second], reason="test", allow_without_filters=True)
        assert started == [[first]]
        assert panel._metadata_loader is owner
        assert panel._photo_tag_loader is None
    finally:
        panel._metadata_loader = None


def test_tag_lookup_seeds_memory_without_new_metadata_work(tag_panel, tmp_path, monkeypatch):
    _, panel = tag_panel
    path = os.path.normpath(str(tmp_path / "cached.jpg"))
    panel._meta_cache[path] = {"tags": ["飞行", "未知关键字"]}
    monkeypatch.setattr(panel, "_start_metadata_loader", lambda *a: pytest.fail("warm metadata reloaded"))
    panel._start_photo_tag_cache_loader_if_needed([path], reason="filter")
    assert panel._photo_tag_cache[path] == {"飞行"}
    assert panel._photo_tag_loader is None


def test_shutdown_stops_tag_refresh_and_cannot_start_shared_loader(tag_panel, monkeypatch):
    _, panel = tag_panel
    panel._active_tag_filters = {"飞行"}
    panel._schedule_photo_tag_filter_refresh()
    panel.request_shutdown()
    monkeypatch.setattr(panel, "_start_metadata_loader", lambda *a: pytest.fail("work after shutdown"))
    panel._start_photo_tag_cache_loader_if_needed(["one.jpg"], reason="late")
    assert not panel._photo_tag_filter_refresh_timer.isActive()
    assert panel._photo_tag_loader is None
    assert panel._photo_tag_cache == {}


@pytest.mark.parametrize("missing_tags", [False, True], ids=["complete", "partial"])
def test_large_directory_tag_batches_do_not_rescan_listing(tag_panel, tmp_path, monkeypatch, missing_tags):
    """Eight-photo worker batches must remain independent of directory size."""
    _, panel = tag_panel

    class CountedListing(list):
        traversals = 0

        def __iter__(self):
            self.traversals += 1
            return super().__iter__()

    paths = [os.path.normpath(str(tmp_path / f"photo-{i:05d}.jpg")) for i in range(25682)]
    panel._all_files = listing = CountedListing(paths)
    # Exercise the real metadata/tag callbacks without file I/O or model paint.
    monkeypatch.setattr(panel, "_enqueue_meta_apply", lambda batch: None)
    monkeypatch.setattr(panel, "_finish_meta_apply", lambda: None)
    current, previous = _MetadataEmitter(), _MetadataEmitter()
    current.batch_ready.connect(panel._on_metadata_batch_ready)
    current.finished.connect(panel._on_metadata_loader_finished)
    previous.finished.connect(panel._on_metadata_loader_finished)
    panel._metadata_loader = current
    try:
        for start in range(0, len(paths), 8):
            current.batch_ready.emit({path: {"tags": ["飞行"]} for path in paths[start:start + 8]})
            assert listing.traversals == 0, "metadata batch scanned the full directory"
        assert len(panel._photo_tag_cache) == len(paths)
        assert panel._photo_tag_cache[paths[-1]] == {"飞行"}
        if missing_tags:
            panel._photo_tag_cache.pop(paths[-1])
        previous.finished.emit()
        assert listing.traversals == 0
        assert panel._metadata_loader is current
        current.finished.emit()
        assert listing.traversals == 1
        assert panel._photo_tag_cache_complete is (not missing_tags)
        assert panel._metadata_loader is None
        panel.request_shutdown()
        current.finished.emit()
        assert listing.traversals == 1
    finally:
        panel._metadata_loader = None
        current.deleteLater()
        previous.deleteLater()


@pytest.mark.parametrize("use_actual_path", [False, True])
def test_delayed_tag_commit_preserves_newer_tags_and_filter(tag_panel, tmp_path, monkeypatch, use_actual_path):
    from app_common.exif_io.metadata_store import get_store
    from app_common.file_browser._browser_core import _set_cached_actual_path
    from app_common.file_browser._workers import MetadataLoader
    from SuperViewer.superviewer.photo_tags import commit_photo_tag_states

    _, panel = tag_panel
    panel._metadata_store_watch._timer.stop()
    source = str(tmp_path / "原图.jpg")
    display = str(tmp_path / "旧路径.jpg") if use_actual_path else source
    Path(source).write_bytes(b"original photo")
    if use_actual_path:
        _set_cached_actual_path(display, source)
    panel._all_files = [display]
    monkeypatch.setattr(panel, "_refresh_metadata_state_for_paths", lambda *_: None)
    store = get_store()
    monkeypatch.setattr(store, "attach_pool", lambda *_: None)
    result = commit_photo_tag_states({source: {"飞行": True}}, allowed_tags=panel._available_tags)
    assert not result.failed_paths
    latest = store.submit_edit(source, {"XMP-dc:Subject": ["捕食"]}).result(5)
    panel._meta_cache[display] = MetadataLoader._parse_rec(latest.record)
    panel._photo_tag_cache[display] = {"捕食"}
    panel._active_tag_filters = {"捕食"}
    # Reproduce the alias mapping done by the queued callback.
    if use_actual_path:
        for mapping in (result.current_tags, result.inverse_states, result.revisions):
            mapping[display] = mapping.pop(source)
    updated = []
    panel.photo_tags_cache_updated.connect(updated.append)
    panel._sync_photo_tag_result(result)
    assert panel._photo_tag_cache[display] == {"捕食"}
    assert panel._meta_cache[display]["tags"] == ["捕食"]
    assert panel._path_matches_active_filters(display)
    assert not updated


def test_tag_receipt_records_revision_before_next_loader_batch(tag_panel, tmp_path, monkeypatch):
    from SuperViewer.superviewer.photo_tags import commit_photo_tag_states

    _, panel = tag_panel
    panel._metadata_store_watch._timer.stop()
    path = str(tmp_path / "标签版本.jpg")
    Path(path).write_bytes(b"original photo")
    panel._all_files = [path]
    monkeypatch.setattr(panel, "_refresh_metadata_state_for_paths", lambda *_: None)
    result = commit_photo_tag_states({path: {"飞行": True}}, allowed_tags=panel._available_tags)
    panel._sync_photo_tag_result(result)
    assert panel._meta_cache[path]["_metadata_revision"] == result.revisions[path]
    panel._on_metadata_batch_ready({path: {"tags": [], "rating": 0,
                                         "_metadata_revision": result.revisions[path] - 1}})
    assert panel._photo_tag_cache[path] == {"飞行"}
