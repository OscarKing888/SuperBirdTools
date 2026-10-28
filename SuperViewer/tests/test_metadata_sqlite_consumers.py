"""Consumer contracts: commit before UI, cached complete reads, sidecar priority."""
import os
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from app_common.exif_io import metadata_store, reader
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser._metadata_commit import get_metadata_commit_bridge
from SuperViewer.main import MainWindow
from SuperViewer.superviewer import exif_helpers
from SuperViewer.superviewer.image_info_tab_image_info import _metadata_comment
from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


APP = QApplication.instance() or QApplication([])


def wait_for(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline
        APP.processEvents()
        time.sleep(.002)
    APP.processEvents()


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = metadata_store.MetadataStore(tmp_path / "durable.sqlite3", start_scheduler=False)
    monkeypatch.setattr(metadata_store, "get_store", lambda: instance)
    yield instance
    instance.close()


def photo(tmp_path):
    path = tmp_path / "中文照片.jpg"
    exif = Image.Exif()
    exif[269], exif[270], exif[272] = "Old title", "Old comment", "Old camera"
    Image.new("RGB", (12, 8)).save(path, exif=exif)
    return str(path)


def test_full_metadata_cache_hit_never_extracts_original_again(tmp_path, store, monkeypatch):
    path = photo(tmp_path)
    assert PhotoMetaDataXMP().write_description(path, "中文侧车备注")
    first = reader.extract_metadata_with_xmp_priority(path)
    assert first["Description"] == "中文侧车备注"
    monkeypatch.setattr(reader, "extract_many", lambda *args, **kwargs: pytest.fail("warm read extracted source"))
    second = reader.extract_metadata_with_xmp_priority(path)
    assert second["Description"] == "中文侧车备注"
    assert second.get("IFD0:Model", second.get("Model")) == "Old camera"


def test_exif_tab_uses_sidecar_then_committed_edit_before_sync(tmp_path, store, monkeypatch):
    path = photo(tmp_path)
    assert PhotoMetaDataXMP().write(path, {"XMP-dc:Title": "旧侧车鸟名", "XMP-dc:Description": "旧侧车注释"})
    first = exif_helpers.load_all_exif(path)
    assert next(row[4] for row in first if row[:2] == ("Meta", "Title")) == "旧侧车鸟名"
    receipt = store.submit_edit(path, {"XMP-dc:Title": "中文新鸟名", "XMP-dc:Description": "中文新注释"}).result()
    assert receipt.record["XMP-dc:Title"] == "中文新鸟名"
    assert PhotoMetaDataXMP().read(path)["XMP-dc:Title"] == "旧侧车鸟名"
    monkeypatch.setattr(exif_helpers, "run_exiftool_json", lambda *args: pytest.fail("cached EXIF hit original"))
    rows = exif_helpers.load_all_exif(path)
    assert next(row[4] for row in rows if row[:2] == ("Meta", "Title")) == "中文新鸟名"
    assert next(row[4] for row in rows if row[:2] == ("Meta", "Description")) == "中文新注释"
    assert store.flush_paths([path])
    assert PhotoMetaDataXMP().read(path)["XMP-dc:Title"] == "中文新鸟名"


def test_comment_commit_is_async_and_does_not_write_sidecar_on_gui(tmp_path, store, monkeypatch):
    path = photo(tmp_path)
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=config)
    window = SimpleNamespace(_file_list=files, _file_writes_allowed=lambda _path: True)
    try:
        monkeypatch.setattr(PhotoMetaDataXMP, "write", lambda *args: pytest.fail("GUI wrote XML"))
        future = MainWindow._save_photo_comment_from_info_panel(window, path, "本地已保存")
        assert not future.done()
        assert _metadata_comment(files._meta_cache.get(path, {})) != "本地已保存"
        wait_for(future.done)
        future.result()
        assert _metadata_comment(files._meta_cache[path]) == "本地已保存"
        assert not os.path.exists(os.path.splitext(path)[0] + ".xmp")
        assert store.peek(path)["comment"] == "本地已保存"
    finally:
        files.shutdown()
        files.deleteLater()
        APP.processEvents()


def test_tags_undo_redo_uses_local_commits_and_preserves_literal_subjects(tmp_path, store):
    path = photo(tmp_path)
    assert PhotoMetaDataXMP().write_subjects(path, ["Lightroom", "行为;特殊"])
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n捕食\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=config)
    try:
        files.set_photo_tag_for_paths([path], "飞行", True)
        wait_for(lambda: not files._command_history.busy)
        assert files.photo_tags_for_path(path) == {"飞行"}
        assert files.can_undo
        assert PhotoMetaDataXMP().read_subjects(path) == ["Lightroom", "行为;特殊"]
        files.undo()
        wait_for(lambda: not files._command_history.busy)
        assert files.photo_tags_for_path(path) == set()
        assert files.can_redo
        files.redo()
        wait_for(lambda: not files._command_history.busy)
        assert files.photo_tags_for_path(path) == {"飞行"}
        assert store.flush_paths([path])
        assert set(PhotoMetaDataXMP().read_subjects(path)) == {"Lightroom", "行为;特殊", "飞行"}
    finally:
        files.shutdown()
        files.deleteLater()
        APP.processEvents()


def test_tag_filter_reuses_shared_metadata_without_xml_worker(tmp_path, store, monkeypatch):
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=config)
    path = photo(tmp_path)
    try:
        files._meta_cache[path] = {"tags": ["飞行"]}
        files._active_tag_filters = {"飞行"}
        monkeypatch.setattr(PhotoMetaDataXMP, "read_subjects", lambda *args, **kwargs: pytest.fail("filter parsed XML"))
        files._start_photo_tag_cache_loader_if_needed([path], reason="test")
        assert files._photo_tag_loader is None
        assert files._path_matches_active_filters(path)
    finally:
        files.shutdown()
        files.deleteLater()
        APP.processEvents()


def test_exif_sql_first_response_precedes_blocked_source_validation(tmp_path, store, monkeypatch):
    import threading
    from SuperViewer.superviewer.image_info_tab_exif import ImageInfoTabPanel_EXIF
    path = photo(tmp_path)
    store.remember_many({path: {"SourceFile": path, "XMP-dc:Title": "SQLite 鸟名"}}, profile="full-display").result()
    started, gate = threading.Event(), threading.Event()
    def validate(*args):
        started.set()
        assert gate.wait(5)
        return [("Meta", "Title", "Meta", "Title", "磁盘新鸟名", "磁盘新鸟名", None)]
    panel = ImageInfoTabPanel_EXIF(validate, lambda *args: None)
    try:
        original_isfile = os.path.isfile
        monkeypatch.setattr(os.path, "isfile", lambda target: pytest.fail("GUI source stat")
                            if os.path.normpath(target) == path else original_isfile(target))
        panel.on_photo_selected(path)
        wait_for(lambda: started.is_set() and bool(panel.last_rows()))
        assert next(row[4] for row in panel.last_rows() if row[:2] == ("Meta", "Title")) == "SQLite 鸟名"
        gate.set()
        wait_for(lambda: panel._loader is None)
        assert panel.last_rows()[0][4] == "磁盘新鸟名"
    finally:
        gate.set()
        panel.shutdown()
        panel.deleteLater()
        APP.processEvents()


def test_metadata_bridge_bulk_backpressure_above_sql_queue_capacity(tmp_path, store, monkeypatch):
    import threading
    from app_common.file_browser._work_pool import BrowserWorkPool
    from SuperViewer.superviewer.qt_compat import QWidget
    owner = QWidget()
    pool = BrowserWorkPool(3)
    owner.metadata_work_pool = lambda: pool
    original = store.submit_edit
    submitted_from = []
    def submit(*args, **kwargs):
        submitted_from.append(threading.get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "submit_edit", submit)
    callbacks = []
    bridge = get_metadata_commit_bridge(owner)
    try:
        future = bridge.submit_edits([(str(tmp_path / f"{i}.jpg"), {"XMP-xmp:Rating": 3}, None)
                                      for i in range(2100)], lambda values: callbacks.append(len(values)))
        wait_for(future.done, timeout=30)
        assert len(future.result()) == 2100 and callbacks == [2100]
        assert all(thread != threading.get_ident() for thread in submitted_from)
    finally:
        bridge.shutdown(wait=True)
        pool.shutdown(timeout=5)
        owner.deleteLater()
        APP.processEvents()


def test_metadata_bridge_shutdown_settles_accepted_commit_without_ui(tmp_path, store, monkeypatch):
    import threading
    from SuperViewer.superviewer.qt_compat import QWidget
    entered, gate = threading.Event(), threading.Event()
    blocker = store.submit_sql(lambda conn: (entered.set(), gate.wait(5)))
    assert entered.wait(2)
    owner = QWidget()
    bridge = get_metadata_commit_bridge(owner)
    callbacks = []
    try:
        future = bridge.submit_edits([(str(tmp_path / "鸟.jpg"), {"XMP-dc:Title": "白鹭"}, None)], callbacks.append)
        bridge.request_shutdown()
        assert not future.done()
        gate.set()
        blocker.result(timeout=5)
        receipt = future.result(timeout=5)[0]
        assert receipt.record["XMP-dc:Title"] == "白鹭"
        assert callbacks == []
    finally:
        gate.set()
        bridge.shutdown(wait=True)
        owner.deleteLater()
        APP.processEvents()


def test_explicit_xmp_clears_do_not_reveal_embedded_camera_and_title():
    merged = {"IFD0:Model": "原相机", "Model": "", "XMP-tiff:Model": "",
              "IFD0:DocumentName": "原鸟名", "XMP-dc:Title": "", "ExifIFD:ISO": 800,
              "XMP-exif:ISOSpeedRatings": ""}
    reader._overlay_xmp_aliases(merged)
    reader._overlay_generic_aliases(merged)
    assert merged["Model"] == "" and merged["ISO"] == "" and merged["Title"] == ""
    rows = exif_helpers.exif_rows_from_metadata(merged)
    assert next(row[4] for row in rows if row[:2] == ("Meta", "Title")) == ""


def test_warm_tag_membership_commit_never_touches_source(tmp_path, store, monkeypatch):
    from SuperViewer.superviewer.photo_tags import commit_photo_tag_states
    path = str(tmp_path / "远程照片.jpg")
    store.remember_many({path: {"SourceFile": path, "tags": ["飞行", "未知关键词"],
                                "XMP-dc:Subject": ["飞行", "未知关键词"]}}, profile="browser").result()
    monkeypatch.setattr(PhotoMetaDataXMP, "read_subjects", lambda *a, **k: pytest.fail("warm tag XML"))
    monkeypatch.setattr(reader, "full_metadata_signatures", lambda *a, **k: pytest.fail("warm tag stat"))
    monkeypatch.setattr(os.path, "isfile", lambda *a: pytest.fail("warm tag source stat"))
    result = commit_photo_tag_states({path: {"飞行": False, "捕食": True}}, allowed_tags=["飞行", "捕食"])
    assert result.failed_paths == {}
    assert result.current_tags[path] == {"捕食"}
    assert result.inverse_states[path] == {"飞行": True, "捕食": False}


def test_metadata_bridge_queued_batch_cancel_settles_without_false_success(tmp_path, store):
    from concurrent.futures import Future, CancelledError
    from SuperViewer.superviewer.qt_compat import QWidget
    class QueuedPool:
        def submit_action(self, *args, **kwargs):
            self.future = Future()
            return self.future
    pool = QueuedPool()
    owner = QWidget()
    owner.metadata_work_pool = lambda: pool
    bridge = get_metadata_commit_bridge(owner)
    callbacks = []
    try:
        future = bridge.submit_edits([(str(tmp_path / f"{i}.jpg"), {"XMP-xmp:Rating": 4}, None)
                                      for i in range(2)], callbacks.append)
        bridge.request_shutdown()
        assert pool.future.cancel()
        with pytest.raises(CancelledError):
            future.result(timeout=1)
        assert callbacks == [] and store.pending_status() == []
    finally:
        bridge.shutdown(wait=True)
        owner.deleteLater()
        APP.processEvents()
