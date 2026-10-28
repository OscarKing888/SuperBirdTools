"""Late metadata deliveries cannot roll an editor back to older snapshots."""
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from app_common.exif_io import metadata_store
from birdstamp.gui import editor as editor_module
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_utils import path_key


APP = QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = metadata_store.MetadataStore(tmp_path / "store.sqlite3", start_scheduler=False)
    monkeypatch.setattr(metadata_store, "get_store", lambda: instance)
    yield instance
    instance.close()


class MetadataHarness(QWidget):
    _load_raw_metadata = BirdStampEditorWindow._load_raw_metadata
    _load_raw_metadata_batch = BirdStampEditorWindow._load_raw_metadata_batch
    _run_blocking_task_off_gui_thread = BirdStampEditorWindow._run_blocking_task_off_gui_thread
    _queue_photo_list_metadata_batch = BirdStampEditorWindow._queue_photo_list_metadata_batch
    _apply_photo_list_metadata_batch = BirdStampEditorWindow._apply_photo_list_metadata_batch
    _prepare_photo_metadata_snapshot = BirdStampEditorWindow._prepare_photo_metadata_snapshot

    def __init__(self, path):
        super().__init__()
        self.paths = {path_key(path)}
        self.raw_metadata_cache = {}
        self.photo_list_metadata_cache = {}
        self._photo_list_metadata_pending_keys = set(self.paths)
        self._photo_list_metadata_forced_refresh_keys = set()
        self._photo_list_metadata_discard_results = False
        self._photo_list_metadata_updates = OrderedDict()
        self._photo_list_metadata_apply_timer = SimpleNamespace(start=lambda: None)
        self.photo_render_overrides = {}
        self.current_path = path
        self.current_raw_metadata = {}
        self.displayed = []

    def _process_image_export_ui_events(self):
        APP.processEvents()

    def _find_photo_item_by_path(self, path):
        return path_key(path) in self.paths

    def _forget_photo_item(self, path):
        key = path_key(path)
        self.raw_metadata_cache.pop(key, None)
        self.photo_list_metadata_cache.pop(key, None)

    def _begin_photo_list_item_display_batch(self):
        pass

    def _end_photo_list_item_display_batch(self, **kwargs):
        pass

    def _update_photo_list_item_display(self, path, *, raw_metadata, **kwargs):
        self.displayed.append(raw_metadata.get("Title"))

    def _apply_current_photo_metadata(self, path, metadata, **kwargs):
        self.current_raw_metadata = metadata

    def _maybe_apply_pending_workspace_photo_selection(self):
        pass


@pytest.fixture
def editor(tmp_path):
    path = (tmp_path / "白鹭.jpg").resolve()
    instance = MetadataHarness(path)
    try:
        yield instance, path
    finally:
        instance.deleteLater()
        APP.processEvents()


def record(path, revision, title, **overrides):
    result = {"SourceFile": str(path), "Title": title,
              "_metadata_read_ok": True, "_metadata_complete": True, **overrides}
    if revision is not None:
        result["_metadata_revision"] = revision
    return result


def test_queue_preserves_newest_revision_before_any_rows_are_applied(editor, store):
    window, path = editor
    window._queue_photo_list_metadata_batch({str(path): record(path, 2, "最新")})
    window._queue_photo_list_metadata_batch({str(path): record(path, 1, "晚到旧值")})
    window._queue_photo_list_metadata_batch({str(path): record(path, None, "无版本旧值")})
    assert window._photo_list_metadata_updates[path_key(path)][1]["Title"] == "最新"


def test_apply_rejects_late_old_unversioned_and_incomplete_snapshots(editor, store):
    window, path = editor
    window._apply_photo_list_metadata_batch({str(path): record(path, 2, "最新")})
    window._apply_photo_list_metadata_batch({str(path): record(path, 1, "晚到旧值")})
    window._apply_photo_list_metadata_batch({str(path): record(path, None, "无版本旧值")})
    window._apply_photo_list_metadata_batch({str(path): record(
        path, 2, "读取失败", _metadata_read_ok=False, _metadata_complete=False,
    )})
    assert window.displayed == ["最新"]
    assert window.current_raw_metadata["Title"] == "最新"
    assert window.raw_metadata_cache[path_key(path)]["Title"] == "最新"


def test_new_partial_edit_after_invalidation_merges_without_erasing_camera(editor, store):
    window, path = editor
    old = store.remember_many(
        {str(path): {"SourceFile": str(path), "Title": "旧鸟名", "Model": "原相机"}}, profile="full",
    ).result()[str(path)]
    window._apply_photo_list_metadata_batch({str(path): old})
    store.note_sidecar_write(str(path)).result()
    committed = store.submit_edit(str(path), {"title": "最新白鹭"}).result().record
    assert committed["_metadata_complete"] is False
    window._queue_photo_list_metadata_batch({str(path): committed})
    queued = window._photo_list_metadata_updates[path_key(path)][1]
    window._apply_photo_list_metadata_batch({str(path): queued})
    assert window.current_raw_metadata["Title"] == "最新白鹭"
    assert window.current_raw_metadata["Model"] == "原相机"
    assert window.current_raw_metadata["_metadata_complete"] is False
    assert path_key(path) in window._photo_list_metadata_pending_keys


def test_apply_rechecks_revision_committed_after_queue_without_sql_on_gui(editor, store, monkeypatch):
    window, path = editor
    old = store.remember_many(
        {str(path): {"SourceFile": str(path), "Title": "旧值", "Model": "相机"}}, profile="full",
    ).result()[str(path)]
    window._queue_photo_list_metadata_batch({str(path): old})
    store.submit_edit(str(path), {"XMP-dc:Title": "SQLite 已提交白鹭"}).result()
    pending = window._photo_list_metadata_updates[path_key(path)][1]
    monkeypatch.setattr(store, "read_many", lambda *a, **k: pytest.fail("GUI queried SQLite"))
    monkeypatch.setattr(Path, "stat", lambda *a, **k: pytest.fail("GUI probed photo disk"))
    window._apply_photo_list_metadata_batch({str(path): pending})
    assert window.displayed == []
    window._apply_photo_list_metadata_batch({str(path): store.peek(str(path), profile="full")})
    assert window.displayed == ["SQLite 已提交白鹭"]


def test_removed_photo_is_rejected_at_queue_and_apply(editor, store):
    window, path = editor
    incoming = record(path, 1, "旧目录")
    window._queue_photo_list_metadata_batch({str(path): incoming})
    window.paths.clear()
    window._apply_photo_list_metadata_batch({str(path): incoming})
    window._photo_list_metadata_updates.clear()
    window._queue_photo_list_metadata_batch({str(path): incoming})
    assert window.displayed == []
    assert not window._photo_list_metadata_updates
    assert not window.raw_metadata_cache


def test_current_complete_snapshot_protects_against_unversioned_loader(editor, store):
    window, path = editor
    window.current_raw_metadata = record(path, 2, "当前预览")
    window._apply_photo_list_metadata_batch({str(path): record(path, None, "旧加载器")})
    assert window.current_raw_metadata["Title"] == "当前预览"
    assert window.displayed == []


def test_new_revision_guard_never_resolves_paths_on_gui(editor, store, monkeypatch):
    window, path = editor
    incoming = record(path, 1, "无需磁盘查询")
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("metadata guard resolved source"))
    assert window._prepare_photo_metadata_snapshot(path, incoming)["Title"] == "无需磁盘查询"


@pytest.mark.parametrize("batch", [False, True])
def test_failed_process_cache_is_retried_on_next_metadata_request(editor, store, monkeypatch, batch):
    window, path = editor
    calls = []

    def read(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            result = {"SourceFile": str(path), "_metadata_read_ok": False, "_metadata_complete": False}
        else:
            result = {"SourceFile": str(path), "Model": "恢复后的相机", "_metadata_read_ok": True}
        return {path: result} if batch else result

    monkeypatch.setattr(editor_module, "extract_metadata_with_xmp_priority", read)
    monkeypatch.setattr(editor_module, "extract_many_with_xmp_priority", read)
    load = lambda: (window._load_raw_metadata_batch([path])[path_key(path)] if batch
                    else window._load_raw_metadata(path))
    assert load()["_metadata_read_ok"] is False
    assert path_key(path) in window._photo_list_metadata_pending_keys
    assert load()["Model"] == "恢复后的相机"
    assert len(calls) == 2
    assert path_key(path) not in window._photo_list_metadata_pending_keys
