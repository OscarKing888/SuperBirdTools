"""Store changes update current memory filters without GUI disk work."""
import os
import threading
import time
import sys

import pytest

from app_common.exif_io import metadata_store
from app_common.file_browser import _workers
from SuperViewer.tests.test_directory_selection_responsiveness import window, _wait_until, _APP


@pytest.fixture(autouse=True)
def capture_qt_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    yield
    # Window shutdown can race the intentionally blocked background health
    # probe. Do not leak that temporary negative result into later test windows.
    from app_common.exif_io.exiftool_path import _is_usable_exiftool
    _is_usable_exiftool.cache_clear()
    assert not errors, [repr(error[1]) for error in errors]


def test_watch_consumes_other_connection_edit_import_and_sync_error(window, tmp_path, monkeypatch):
    panel = window._file_list
    panel._metadata_store_watch._timer.stop()
    path = str(tmp_path / "白鹭.jpg")
    panel._current_dir = str(tmp_path)
    panel._all_files = [path]
    panel._meta_cache = {path: {"rating": 1, "_metadata_revision": 0}}
    panel._filter_min_rating = 5
    monkeypatch.setattr(panel, "_schedule_visible_thumbnail_update", lambda *_a, **_k: None)
    monkeypatch.setattr(panel, "_schedule_persistent_thumb_cache_build", lambda *_a: None)
    monkeypatch.setattr(panel, "_emit_file_selected_for_path", lambda *_a: None)
    store = metadata_store.get_store()
    monkeypatch.setattr(store, "attach_pool", lambda *_a: None)
    other = metadata_store.MetadataStore(store.path, start_scheduler=False)
    try:
        other.remember_many({path: {"XMP-xmp:Rating": 1, "XMP-dc:Title": "旧标题"}}).result()
        receipt = other.submit_edit(path, {"XMP-xmp:Rating": 5, "XMP-dc:Title": "新中文标题"}).result()
        other.submit_sql(lambda conn: conn.execute("UPDATE outbox SET error='磁盘离线',attempts=1")).result()
        changed = []
        panel.photo_metadata_cache_updated.connect(lambda paths: changed.extend(paths))
        watch = panel._metadata_store_watch
        watch.poll()
        _wait_until(lambda: watch._future.done())
        watch.poll()
        assert panel._meta_cache[path]["rating"] == 5
        assert panel._meta_cache[path]["title"] == "新中文标题"
        assert path in changed
        assert "同步失败" in panel._meta_progress.format()
        assert "磁盘离线" in panel._meta_progress.toolTip()
        _wait_until(lambda: path in panel._filtered_files)
        # A delayed older worker snapshot must not undo the durable change.
        panel._on_metadata_batch_ready({path: {"rating": 1, "_metadata_revision": receipt.revision - 1}})
        assert panel._meta_cache[path]["rating"] == 5
    finally:
        other.close()


@pytest.mark.parametrize("mode", ["list", "thumb"])
def test_cached_first_paint_has_no_photo_volume_stat_or_automatic_preview(window, tmp_path, monkeypatch, mode):
    directory = tmp_path / "离线缓存"
    directory.mkdir()
    path = str(directory / "白鹭.HIF")
    panel = window._file_list
    panel._metadata_store_watch._timer.stop()
    panel._view_mode = panel._MODE_LIST if mode == "list" else panel._MODE_THUMB
    metadata = _workers.MetadataLoader._parse_rec({"ExifIFD:ISO": 800, "XMP-xmp:Rating": 5})
    panel._directory_snapshots.remember(str(directory), panel._include_subdirectories,
        {"files": [path], "metadata": {path: metadata}}, include_videos=panel.include_videos,
        use_report_db=panel._use_report_db)
    started, release = threading.Event(), threading.Event()
    def wait_scan(self):
        started.set()
        release.wait(5)
    monkeypatch.setattr(_workers.DirectoryScanWorker, "run", wait_scan)
    selected, probes = [], []
    panel.file_selected.connect(selected.append)
    original_stat = os.stat
    main_thread = threading.get_ident()

    def guarded_stat(target, *args, **kwargs):
        if threading.get_ident() == main_thread and str(target).startswith(str(directory)):
            probes.append(str(target))
            raise AssertionError("GUI photo-volume stat before cached paint")
        return original_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "stat", guarded_stat)
    try:
        window.show()
        before = time.monotonic()
        panel.load_directory(str(directory))
        for _ in range(5):
            _APP.processEvents()
        assert time.monotonic() - before < .5
        assert panel._filtered_files == [path]
        assert panel._meta_cache[path]["iso"] == "800"
        assert not selected
        assert not probes, probes
        assert started.wait(1)
    finally:
        monkeypatch.setattr(os, "stat", original_stat)
        release.set()
        _wait_until(lambda: not panel.has_pending_directory_scans())


def test_focus_candidates_are_lexical_and_worker_selects_existing_report_fallback(window, tmp_path, monkeypatch):
    panel = window._file_list
    displayed = str(tmp_path / "missing.jpg")
    raw = tmp_path / "actual.ARW"
    raw.write_bytes(b"raw")
    panel._report_row_by_path = {displayed: {"current_path": str(raw.with_suffix('.jpg')),
                                             "original_path": "old.ARW"}}
    with monkeypatch.context() as guarded:
        guarded.setattr(os.path, "isfile", lambda *_: (_ for _ in ()).throw(AssertionError("GUI stat")))
        mapping = panel._build_metadata_focus_source_paths([displayed])
    loader = _workers.MetadataLoader([displayed], object(), focus_source_paths=mapping)
    assert os.path.normcase(loader._resolve_focus_source_path(displayed)) == os.path.normcase(str(raw))


def test_watch_waits_for_sql_in_owned_pool_and_close_suppresses_ui(window, tmp_path, monkeypatch):
    panel = window._file_list
    panel._current_dir = str(tmp_path)
    watch = panel._metadata_store_watch
    watch._timer.stop()
    store = metadata_store.get_store()
    started, release = threading.Event(), threading.Event()
    def hold(conn):
        started.set()
        assert release.wait(3)
    blocked = store.submit_sql(hold)
    assert started.wait(1)
    applied = []
    monkeypatch.setattr(panel, "_apply_store_metadata_snapshot", applied.append)
    try:
        before = time.monotonic()
        watch.poll()
        assert time.monotonic() - before < .2
        assert not watch._future.done()
        watch.request_shutdown()
    finally:
        release.set()
        blocked.result(3)
    _wait_until(lambda: watch._future.done())
    watch.poll()
    assert not applied


def test_watch_import_does_not_replay_itself_or_clear_unchanged_tag_cache(window, tmp_path, monkeypatch):
    panel = window._file_list
    panel._metadata_store_watch._timer.stop()
    path = str(tmp_path / "导入.jpg")
    panel._current_dir, panel._all_files = str(tmp_path), [path]
    panel._meta_cache = {path: {"rating": 0, "_metadata_revision": 0}}
    monkeypatch.setattr(panel, "_schedule_visible_thumbnail_update", lambda *_a, **_k: None)
    store = metadata_store.get_store()
    store.remember_many({path: {"XMP-dc:Title": "外部导入", "XMP-dc:Subject": ["飞行"]}}).result()
    watch = panel._metadata_store_watch
    watch.poll()
    _wait_until(lambda: watch._future.done())
    watch.poll()
    assert panel._meta_cache[path]["title"] == "外部导入"
    assert panel._photo_tag_cache[path] == {"飞行"}
    seq = watch._seq
    _wait_until(lambda: watch._future.done())
    watch.poll()
    assert watch._seq == seq
    assert panel._photo_tag_cache[path] == {"飞行"}


def test_complete_ram_directory_still_reads_sql_before_slow_directory_scan(window, tmp_path, monkeypatch):
    panel = window._file_list
    panel._metadata_store_watch._timer.stop()
    directory = tmp_path / "完整内存缓存"
    directory.mkdir()
    path = str(directory / "白鹭.HIF")
    old = _workers.MetadataLoader._parse_rec({"ExifIFD:ISO": 100})
    panel._directory_snapshots.remember(str(directory), panel._include_subdirectories,
        {"files": [path], "metadata": {path: old}}, include_videos=panel.include_videos,
        use_report_db=panel._use_report_db)
    metadata_store.get_store().remember_many({path: {"ExifIFD:ISO": 1600}}).result()
    release = threading.Event()
    def scan(self):
        release.wait(3)
    def validate(*_args, **_kwargs):
        release.wait(3)
        return {}
    monkeypatch.setattr(_workers.DirectoryScanWorker, "run", scan)
    monkeypatch.setattr(_workers, "metadata_signatures", validate)
    try:
        panel.load_directory(str(directory))
        _wait_until(lambda: panel._meta_cache.get(path, {}).get("iso") == "1600")
        assert panel.has_pending_directory_scans()
        assert not release.is_set()
    finally:
        release.set()
        _wait_until(lambda: not panel.has_pending_directory_scans())
