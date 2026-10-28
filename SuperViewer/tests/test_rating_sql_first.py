"""Rating gestures acknowledge the local commit without waiting for XMP."""
import threading
import time
import sys

from app_common.exif_io import metadata_store
from app_common.file_browser import _panel
from SuperViewer.tests.test_directory_selection_responsiveness import window, _wait_until


def test_rating_gesture_does_not_wait_for_sql_or_source_and_updates_siblings(window, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    raw, jpeg = tmp_path / "白鹭.ARW", tmp_path / "白鹭.JPG"
    raw.write_bytes(b"raw")
    jpeg.write_bytes(b"jpeg")
    paths = [str(raw), str(jpeg)]
    panel = window._file_list
    panel._all_files = paths
    panel._meta_cache = {path: {"rating": 1, "pick": 0} for path in paths}
    panel._filtered_files = []
    panel._filter_min_rating = 5
    monkeypatch.setattr(panel, "_emit_file_selected_for_path", lambda *_a, **_k: None)
    monkeypatch.setattr(panel, "_schedule_visible_thumbnail_update", lambda *_a, **_k: None)
    monkeypatch.setattr(panel, "_schedule_persistent_thumb_cache_build", lambda *_a, **_k: None)
    monkeypatch.setattr(_panel, "find_same_stem_xmp_sidecar", lambda *_: (_ for _ in ()).throw(AssertionError("GUI sidecar discovery")))
    store = metadata_store.get_store()
    monkeypatch.setattr(store, "attach_pool", lambda *_: None)
    started, release = threading.Event(), threading.Event()

    def hold_sql(conn):
        started.set()
        assert release.wait(3)

    blocked = store.submit_sql(hold_sql)
    assert started.wait(1)
    try:
        before = time.monotonic()
        panel._set_rating_state_for_paths([paths[0]], rating=5)
        assert time.monotonic() - before < .3
        assert panel._meta_cache[paths[0]]["rating"] == 1
        assert not raw.with_suffix(".xmp").exists()
    finally:
        release.set()
        blocked.result(3)
    _wait_until(lambda: errors or all(panel._meta_cache[path]["rating"] == 5 for path in paths))
    assert not errors, [repr(error[1]) for error in errors]
    assert set(panel._filtered_files) == set(paths)
    assert store.pending_status([paths[0]])
    assert not raw.with_suffix(".xmp").exists()
