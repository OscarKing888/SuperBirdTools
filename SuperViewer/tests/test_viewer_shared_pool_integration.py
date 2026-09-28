from __future__ import annotations

import os
import time

from PIL import Image
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.file_browser import _permissions
from app_common.file_browser._browser_core import (
    _persistent_thumb_cache_worker_count, _thumbnail_loader_worker_count,
)
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(.003)
    assert predicate()


def test_viewer_reuses_one_action_pool_across_directories_and_joins_it(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS",
                        superviewer_user_options.normalize_user_options(None))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for name, value in vars(_permissions).copy().items():
        if name.startswith("CURRENT_SUPERPICKY_"):
            monkeypatch.setattr(_permissions, name, value)
    tags = tmp_path / "tags.cfg"
    tags.write_text("鸟类\n", encoding="utf-8")
    first = tmp_path / "first"
    second = tmp_path / "second"
    for directory in (first, second):
        directory.mkdir()
        Image.new("RGB", (32, 24), "green").save(directory / "照片.jpg")

    panel = SuperViewerTaggedFileListPanel(tag_config_path=tags)
    try:
        pool = panel._get_browser_work_pool()
        assert pool is not None
        assert pool.max_workers == max(_thumbnail_loader_worker_count(),
                                       pool.metadata_workers + _persistent_thumb_cache_worker_count())
        panel.load_directory(str(first), force_reload=True)
        _wait_until(lambda: os.path.normpath(str(first / "照片.jpg")) in panel._filtered_files)
        panel.load_directory(str(second), force_reload=True)
        _wait_until(lambda: os.path.normpath(str(second / "照片.jpg")) in panel._filtered_files)
        assert panel._get_browser_work_pool() is pool
    finally:
        panel.request_shutdown()
        _wait_until(lambda: panel.shutdown(wait_timeout_ms=0))
        assert pool.is_finished()
        panel.close()
        panel.deleteLater()
        _APP.processEvents()
