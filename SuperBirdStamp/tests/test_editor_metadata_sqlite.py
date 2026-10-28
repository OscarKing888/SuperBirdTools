"""SQLite snapshots and owned-thread metadata consumption in BirdStamp."""
import os
import threading
import time
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from app_common.exif_io import metadata_store
from app_common.file_browser._work_pool import BrowserWorkPool
from birdstamp.gui import editor as editor_module
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_metadata_store_watch import EditorMetadataStoreWatch

APP = QApplication.instance() or QApplication([])


class MetadataHarness(QWidget):
    _load_raw_metadata = BirdStampEditorWindow._load_raw_metadata
    _load_raw_metadata_batch = BirdStampEditorWindow._load_raw_metadata_batch
    _run_blocking_task_off_gui_thread = BirdStampEditorWindow._run_blocking_task_off_gui_thread

    def __init__(self):
        super().__init__()
        self.raw_metadata_cache = {}
        self.photo_list_metadata_cache = {}
        self._photo_list_metadata_pending_keys = set()
        self.current_path = None
        self.current_raw_metadata = {}

    def _process_image_export_ui_events(self):
        APP.processEvents()


@pytest.mark.parametrize("batch", [False, True])
def test_cold_direct_metadata_read_and_fallback_stay_off_gui(tmp_path, monkeypatch, batch):
    path = tmp_path / "中文.jpg"
    path.write_bytes(b"photo")
    gui_thread = threading.get_ident()
    calls = []
    def fallback(source):
        calls.append(threading.get_ident())
        return {"SourceFile": str(source), "Model": "测试相机"}
    def failed(*args, **kwargs):
        calls.append(threading.get_ident())
        raise OSError("ExifTool unavailable")
    monkeypatch.setattr(editor_module, "extract_metadata_with_xmp_priority", failed)
    monkeypatch.setattr(editor_module, "extract_many_with_xmp_priority", failed)
    monkeypatch.setattr(editor_module, "extract_many", failed)
    monkeypatch.setattr(editor_module, "extract_pillow_metadata", fallback)
    window = MetadataHarness()
    try:
        result = window._load_raw_metadata_batch([path]) if batch else window._load_raw_metadata(path)
        record = next(iter(result.values())) if batch else result
        assert record["Model"] == "测试相机"
        assert calls and all(thread != gui_thread for thread in calls)
    finally:
        window.deleteLater()
        APP.processEvents()


def test_batch_uses_new_committed_full_snapshot_before_old_editor_cache(tmp_path, monkeypatch):
    path = str(tmp_path / "中文.jpg")
    store = metadata_store.get_store()
    store.remember_many({path: {"SourceFile": path, "XMP-dc:Title": "旧鸟名", "Model": "相机"}}, profile="full").result()
    store.submit_edit(path, {"XMP-dc:Title": "白鹭"}).result()
    window = MetadataHarness()
    key = os.path.normcase(os.path.abspath(path))
    window.raw_metadata_cache[key] = {"XMP-dc:Title": "旧窗口鸟名"}
    monkeypatch.setattr(editor_module, "extract_many_with_xmp_priority", lambda *a, **k: pytest.fail("warm source read"))
    try:
        assert window._load_raw_metadata_batch([Path(path)])[key]["XMP-dc:Title"] == "白鹭"
    finally:
        window.deleteLater()
        APP.processEvents()


def test_store_watch_consumes_import_and_keeps_callbacks_on_gui(tmp_path, monkeypatch):
    from app_common.exif_io import reader
    path = os.path.normpath(str(tmp_path / "鸟.jpg"))
    gui = threading.get_ident()
    calls, delivered = [], []
    window = MetadataHarness()
    window._photo_item_map = {path: object()}
    window._preview_action_pool = BrowserWorkPool(3)
    window._queue_photo_list_metadata_batch = lambda records: delivered.append((threading.get_ident(), records))
    store = metadata_store.get_store()
    seq = store.changes_since(0)["seq"]
    store.remember_many({path: {"SourceFile": path, "XMP-dc:Title": "外部新鸟名"}}, profile="full").result()
    def read(paths, **kwargs):
        calls.append(threading.get_ident())
        return {p: {"XMP-dc:Title": "外部新鸟名"} for p in paths}
    monkeypatch.setattr(reader, "extract_many_with_xmp_priority", read)
    watch = EditorMetadataStoreWatch(window)
    watch._timer.stop()
    watch._sequence = seq
    try:
        watch._tick()
        deadline = time.monotonic() + 5
        while not watch._future.done() and time.monotonic() < deadline:
            time.sleep(.002)
        watch._tick()
        assert delivered and delivered[0][0] == gui
        assert delivered[0][1][path]["XMP-dc:Title"] == "外部新鸟名"
        assert calls and all(thread != gui for thread in calls)
        assert watch._future is not None  # The next poll starts on the same tick.
    finally:
        watch.request_shutdown()
        assert window._preview_action_pool.shutdown(timeout=5)
        window.deleteLater()
        APP.processEvents()


def test_loader_construction_does_not_resolve_source_files(monkeypatch, tmp_path):
    from birdstamp.gui.editor_photo_metadata_loader import EditorPhotoListMetadataLoader
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("GUI source resolve"))
    loader = EditorPhotoListMetadataLoader([tmp_path / "鸟.jpg"])
    assert loader._paths == [os.path.normpath(str(tmp_path / "鸟.jpg"))]
    loader.deleteLater()


def test_template_reads_new_committed_full_snapshot_without_xml(tmp_path, monkeypatch):
    from birdstamp.gui import template_context as context
    path = tmp_path / "鸟.jpg"
    store = metadata_store.get_store()
    store.remember_many({str(path): {"SourceFile": str(path), "XMP-dc:Title": "旧鸟名",
                                   "XMP-tiff:Model": "旧相机"}}, profile="full").result()
    store.submit_edit(str(path), {"XMP-dc:Title": "白鹭", "XMP-tiff:Model": ""}).result()
    info = context.PhotoInfo(path=path, raw_metadata={"XMP-dc:Title": "旧模板鸟名"})
    monkeypatch.setattr(context, "_resolve_sidecar_path", lambda *a: pytest.fail("template XML bypass"))
    values = context._metadata_with_xmp_priority(info)
    assert values["Title"] == "白鹭"
    assert values["Model"] == ""
