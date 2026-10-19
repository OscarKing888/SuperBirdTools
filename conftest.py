"""Repository-wide pytest setup.

GUI tests construct real SuperViewer / SuperBirdStamp windows. Without a headless
Qt platform those windows pop up on the desktop (macOS Cocoa / Windows) while
someone is using the apps, so every test run defaults to ``offscreen``. This file
is imported before any test module, i.e. before PyQt creates a QApplication.

- An explicit ``QT_QPA_PLATFORM`` in the environment is respected.
- ``SUPERBIRD_TEST_SHOW_WINDOWS=1`` (or ``run_tests.sh --show-windows``) keeps
  the native platform for debugging a GUI test visually.
"""

import os
import pytest

SHOW_WINDOWS_ENV = "SUPERBIRD_TEST_SHOW_WINDOWS"


def _show_windows_requested() -> bool:
    return os.environ.get(SHOW_WINDOWS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


if not _show_windows_requested():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_metadata_sqlite_cache(tmp_path_factory, monkeypatch):
    """元数据测试始终使用临时 SQLite，并在删除目录前排空写队列/关闭连接。"""
    from app_common.exif_io import metadata_sqlite
    cache = metadata_sqlite.MetadataSQLiteCache(tmp_path_factory.mktemp("metadata-cache") / "metadata.sqlite3")
    monkeypatch.setenv("SUPERBIRD_METADATA_CACHE_DIR", str(cache.path.parent))
    monkeypatch.setattr(metadata_sqlite, "_default", cache)
    yield cache
    metadata_sqlite.close_cache()
    cache.close()
