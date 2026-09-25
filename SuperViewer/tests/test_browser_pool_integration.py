import threading

from app_common.file_browser import _panel as browser
from app_common.file_browser._workers import MetadataLoader
from SuperViewer.tests.test_directory_selection_responsiveness import window, _wait_until


def test_directory_metadata_reuses_pool_and_rejects_old_results(window, tmp_path, monkeypatch):
    panel = window._file_list
    monkeypatch.setattr(browser, '_metadata_loader_worker_count', lambda: 1)
    monkeypatch.setattr(browser, '_thumbnail_loader_worker_count', lambda: 3)
    monkeypatch.setattr(browser, '_persistent_thumb_cache_worker_count', lambda: 1)
    old = str(tmp_path / '旧目录.jpg')
    new = str(tmp_path / '新目录.jpg')
    entered = threading.Event()
    release = threading.Event()

    def read(loader, paths):
        if paths == [old]:
            entered.set()
            release.wait(3)
        return {path: {'comment': '中文备注'} for path in paths}, {}, len(paths)

    monkeypatch.setattr(MetadataLoader, '_read_parse_chunk', read)
    try:
        panel._start_metadata_loader([old])
        _wait_until(entered.is_set)
        pool = panel._browser_work_pool
        assert pool.metadata_workers == 2  # Even a stored setting of 1 is clamped.
        panel._start_metadata_loader([new])
        _wait_until(lambda: new in panel._meta_cache)
        assert panel._browser_work_pool is pool
        assert old not in panel._meta_cache
        release.set()
        _wait_until(lambda: not panel._live_pool_loaders)
        assert old not in panel._meta_cache
    finally:
        release.set()


def test_window_close_retains_running_pool_work(window):
    pool = window._file_list._get_browser_work_pool()
    entered = threading.Event()
    release = threading.Event()

    def slow_decode():
        entered.set()
        release.wait(3)

    try:
        future = pool.submit(slow_decode)
        _wait_until(entered.is_set)
        window.close()
        assert not window._shutdown_finalized
        assert window._file_list.has_pending_pool_work()
        release.set()
        _wait_until(lambda: window._shutdown_finalized)
        assert future.done() and pool.is_finished()
    finally:
        release.set()


def test_finished_thumbnail_work_lends_capacity_to_metadata(window):
    panel = window._file_list
    panel._view_mode = panel._MODE_THUMB
    pool = panel._get_browser_work_pool()
    panel._persistent_thumb_cache_done = 10
    panel._persistent_thumb_cache_total = 10
    panel._sync_shared_pool_budget()
    assert not pool._thumbnail_mode
    panel._persistent_thumb_cache_pending_paths = ['new.jpg']
    panel._sync_shared_pool_budget()
    assert pool._thumbnail_mode
    panel._persistent_thumb_cache_pending_paths = []
