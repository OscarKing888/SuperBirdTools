import importlib
import os
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.metadata_store import get_store
from app_common.file_browser._workers import MetadataLoader
from app_common.file_browser import FileListPanel
from SuperViewer.main import MainWindow
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo, _metadata_comment
from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
from SuperViewer.superviewer.metadata_edit_sync import sync_saved_xmp_edit


_APP = QApplication.instance() or QApplication([])
main = importlib.import_module("SuperViewer.main")


def wait_for(condition):
    deadline = time.monotonic() + 8
    while not condition():
        assert time.monotonic() < deadline
        _APP.processEvents()
        time.sleep(.002)
    _APP.processEvents()


@pytest.mark.parametrize("mode", [FileListPanel._MODE_LIST, FileListPanel._MODE_THUMB])
def test_paste_bird_name_keeps_new_pinyin_when_old_batch_arrives(tmp_path, mode):
    from app_common.bird_pinyin import stored_pinyin
    from app_common.bird_rarity import rarity_metadata
    from app_common.shooting_location import shooting_location

    photo = tmp_path / "中文鸟名.jpg"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=config)
    files._view_mode = mode
    files._copied_species_payload = {
        "bird_species_cn": "白头鹎", "bird_species_en": "Light-vented Bulbul",
        "gbif_rarity_100": 0, "iucn_category": "LC", "shooting_location": "深圳湾",
    }
    try:
        files._paste_species_to_paths([path])
        deadline = time.monotonic() + 8
        while files._species_paste_controller.busy:
            assert time.monotonic() < deadline
            _APP.processEvents()
            time.sleep(.001)
        files._on_metadata_batch_ready({path: {
            "title": "家燕", "pinyin_name": "jiā yàn",
            "pinyin_name_source": "家燕", "iso": "800",
            "gbif_rarity_100": 80, "iucn_category": "EN", "shooting_location": "旧地点",
        }})
        assert stored_pinyin(files.cached_photo_metadata_for_path(path)) == "bái tóu bēi"
        assert files._meta_cache[path]["iso"] == "800"
        assert rarity_metadata(files.cached_photo_metadata_for_path(path)) == (0, "LC")
        assert shooting_location(files.cached_photo_metadata_for_path(path)) == "深圳湾"
        assert get_store().flush_paths([path])
        assert stored_pinyin(PhotoMetaDataXMP().read(path)) == "bái tóu bēi"
    finally:
        files.shutdown()
        files.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize("entry", ["info", "exif"])
@pytest.mark.parametrize("new_text", ["中文新备注", ""])
def test_comment_save_updates_parsed_cache_and_readback(tmp_path, entry, new_text, monkeypatch):
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (8, 6)).save(photo)
    path = os.path.normpath(str(photo))
    xmp = PhotoMetaDataXMP()
    assert xmp.write_description(path, "原来的备注")
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n", encoding="utf-8")
    panel = SuperViewerTaggedFileListPanel(tag_config_path=config)
    loader = MetadataLoader([], panel._meta_proxy)
    panel._meta_cache[path] = loader._parse_rec(xmp.read(path))
    harness = SimpleNamespace(
        _file_list=panel,
        _file_writes_allowed=lambda _path: True,
        _current_exif_path=path,
        exif_info_panel=SimpleNamespace(refresh_current_photo=lambda: None),
    )
    monkeypatch.setattr(main.QMessageBox, "critical", lambda *args: pytest.fail(str(args)))
    try:
        assert _metadata_comment(panel._meta_cache[path]) == "原来的备注"
        if entry == "info":
            future = MainWindow._save_photo_comment_from_info_panel(harness, path, new_text)
        else:
            future = MainWindow._save_exif_value(harness, main.META_IFD_NAME,
                main.META_DESCRIPTION_TAG_ID, new_text, "原来的备注")
        wait_for(future.done)
        future.result()
        assert get_store().flush_paths([path])
        assert _metadata_comment(xmp.read(path)) == new_text
        assert _metadata_comment(panel._meta_cache[path]) == new_text
        # A batch that read before the edit must still fill unrelated fields
        # without resurrecting its old comment after the successful save.
        panel._on_metadata_batch_ready({path: {"comment": "原来的备注", "iso": "800"}})
        assert _metadata_comment(panel._meta_cache[path]) == new_text
        assert panel._meta_cache[path]["iso"] == "800"
    finally:
        panel.shutdown()
        panel.deleteLater()
        loader.deleteLater()
        _APP.processEvents()


def test_exif_subject_edit_refreshes_filters_and_rejects_stale_tag_results(tmp_path, monkeypatch):
    photo = tmp_path / "标签.png"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n捕食\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=config)
    messages = []
    monkeypatch.setattr(main, "QMessageBox", SimpleNamespace(
        information=lambda *_args: messages.append("saved"),
        critical=lambda *_args: messages.append("failed"),
    ))
    refreshes = []
    window = SimpleNamespace(
        _current_exif_path=path, _file_list=files,
        exif_info_panel=SimpleNamespace(refresh_current_photo=lambda: refreshes.append(path)),
    )
    try:
        files.set_photo_tag_for_paths([path], "飞行", True)
        wait_for(lambda: not files._command_history.busy)
        assert files.can_undo
        generation = files._photo_tag_generation(path)
        future = MainWindow._save_exif_value(window, None, None, "捕食", "飞行", "XMP-dc:Subject")
        wait_for(future.done)
        future.result()
        assert messages == []
        assert refreshes == [path]
        assert get_store().flush_paths([path])
        assert PhotoMetaDataXMP().read_subjects(path, strict=True) == ["捕食"]
        assert files.photo_tags_for_path(path) == {"捕食"}
        assert files._photo_tag_generation(path) > generation
        assert not files.can_undo
        files._active_tag_filters = {"捕食"}
        assert files._path_matches_active_filters(path)
        files._active_tag_filters = {"飞行"}
        assert not files._path_matches_active_filters(path)
        assert files._merge_metadata_batch_with_photo_tag_cache({path: {"tags": ["飞行"]}})[path]["tags"] == ["捕食"]
    finally:
        files.shutdown()
        files.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize("key,value,field,expected", [
    ("XMP-dc:Description", "新备注", "comment", "新备注"),
    ("XMP-dc:Description", "", "comment", ""),
    ("IFD0:DocumentName", "新鸟名", "bird_species_cn", "新鸟名"),
    ("XMP:Rating", "0", "rating", 0),
    ("XMP-xmpDM:pick", "-1", "pick", -1),
    ("EXIF:ISO", "1600", "iso", "1600"),
    ("EXIF:ExposureTime", "0.001", "shutter", "1/1000s"),
    ("EXIF:Model", "中文相机", "camera_model", "中文相机"),
    ("EXIF:LensModel", "中文镜头", "lens_model", "中文镜头"),
])
def test_generic_xmp_edits_update_canonical_fields_without_source_read(tmp_path, key, value, field, expected):
    photo = tmp_path / "元数据.png"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    receipt = get_store().submit_edit(path, {key: value}).result()
    calls = []
    files = SimpleNamespace(sync_metadata_edit_for_path=lambda path, **kwargs: calls.append(kwargs["meta_updates"]))
    sync_saved_xmp_edit(files, path, key, record=receipt.record)
    assert calls[0][field] == expected


def test_metadata_edit_overrides_expire_on_directory_change_or_explicit_reload(tmp_path, monkeypatch):
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=config)
    first = str(tmp_path)
    second = str(tmp_path / "other")
    photo = str(tmp_path / "photo.png")
    monkeypatch.setattr(FileListPanel, "load_directory", lambda *args, **kwargs: None)
    try:
        files._current_dir = first
        files.sync_metadata_edit_for_path(photo, meta_updates={"comment": "编辑值"})
        files.load_directory(first)
        assert files._merge_metadata_batch_with_photo_tag_cache({photo: {"comment": "磁盘值"}})[photo]["comment"] == "编辑值"
        files.load_directory(first, force_reload=True)
        assert files._merge_metadata_batch_with_photo_tag_cache({photo: {"comment": "磁盘值"}})[photo]["comment"] == "磁盘值"
        files.sync_metadata_edit_for_path(photo, meta_updates={"comment": "另一次编辑"})
        files.load_directory(second)
        assert files._local_metadata_updates_by_path == {}
    finally:
        files.shutdown()
        files.deleteLater()
        _APP.processEvents()


def test_background_tag_signal_keeps_comment_and_filename_drafts(tmp_path):
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n捕食\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=config)
    metadata_reads = []
    panel = ImageInfoTabPanel_ImageInfo(
        lambda: ["飞行", "捕食"], files.photo_tags_for_path,
        lambda *_args: None, lambda path, _name: path,
        metadata_provider=lambda _path: metadata_reads.append(_path) or {"comment": "原始备注"},
    )
    window = SimpleNamespace(
        _current_exif_path=path, image_info_panel=panel, tags_info_panel=None,
        image_info_tabs=SimpleNamespace(currentWidget=lambda: panel),
    )
    files.photo_tags_cache_updated.connect(lambda paths: MainWindow._on_photo_tags_cache_updated(window, paths))
    try:
        files._photo_tag_cache[path] = {"飞行"}
        panel.on_photo_selected(path)
        panel.comment_edit.setPlainText("尚未保存的中文备注")
        panel.comment_edit.insertPlainText("继续编辑")
        panel.filename_edit.setText("尚未保存的文件名")
        panel.filename_edit.setCursorPosition(4)
        comment = panel.comment_edit.toPlainText()
        cursor = panel.comment_edit.textCursor().position()
        undo_available = panel.comment_edit.document().isUndoAvailable()
        metadata_reads.clear()

        files._photo_tag_cache[path] = {"捕食"}
        files.photo_tags_cache_updated.emit([path])

        assert panel._current_tags == {"捕食"}
        assert panel.comment_edit.toPlainText() == comment
        assert panel.comment_edit.textCursor().position() == cursor
        assert panel.comment_edit.document().isUndoAvailable() == undo_available
        assert panel.filename_edit.text() == "尚未保存的文件名"
        assert panel.filename_edit.cursorPosition() == 4
        assert metadata_reads == []
    finally:
        files.shutdown()
        panel.deleteLater()
        files.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize("tag_key,old_value,new_value,field", [
    ("XMP-dc:Description", "先前备注", "最新备注", "comment"),
    ("XMP-dc:Subject", ["飞行"], ["捕食"], "tags"),
])
def test_delayed_edit_receipt_cannot_replace_newer_store_snapshot(
    tmp_path, monkeypatch, tag_key, old_value, new_value, field,
):
    photo = tmp_path / "迟到回执.jpg"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("飞行\n捕食\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=cfg)
    files._metadata_store_watch._timer.stop()
    store = get_store()
    monkeypatch.setattr(store, "attach_pool", lambda *_: None)
    monkeypatch.setattr(files, "_refresh_metadata_state_for_paths", lambda *_: None)
    try:
        first = store.submit_edit(path, {tag_key: old_value}).result(5)
        latest = store.submit_edit(path, {tag_key: new_value}).result(5)
        files._meta_cache[path] = MetadataLoader._parse_rec(latest.record)
        files._photo_tag_cache[path] = set(latest.record.get("tags", []))
        updates = []
        files.photo_metadata_cache_updated.connect(updates.append)
        sync_saved_xmp_edit(files, path, tag_key, record=first.record)
        assert files._meta_cache[path][field] == new_value
        assert files._meta_cache[path]["_metadata_revision"] == latest.revision
        assert not updates
    finally:
        files.shutdown()
        files.deleteLater()
        _APP.processEvents()


def test_edit_receipt_keeps_revision_in_local_cache(tmp_path, monkeypatch):
    photo = tmp_path / "版本.jpg"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    files = SuperViewerTaggedFileListPanel(tag_config_path=tmp_path / "tags.cfg")
    files._metadata_store_watch._timer.stop()
    monkeypatch.setattr(files, "_refresh_metadata_state_for_paths", lambda *_: None)
    try:
        receipt = get_store().submit_edit(path, {"XMP-dc:Description": "中文备注"}).result(5)
        sync_saved_xmp_edit(files, path, "XMP-dc:Description", record=receipt.record)
        assert files._meta_cache[path]["_metadata_revision"] == receipt.revision
    finally:
        files.shutdown()
        files.deleteLater()
        _APP.processEvents()


def test_receipt_version_check_uses_repaired_actual_path_without_disk_io(tmp_path, monkeypatch):
    from app_common.file_browser._browser_core import _set_cached_actual_path
    from SuperViewer.superviewer.metadata_edit_sync import metadata_receipt_is_current

    source = str(tmp_path / "实际照片.jpg")
    display = str(tmp_path / "报告旧路径.jpg")
    _set_cached_actual_path(display, source)
    first = get_store().submit_edit(source, {"XMP-dc:Description": "先前备注"}).result(5)
    get_store().submit_edit(source, {"XMP-dc:Description": "最新备注"}).result(5)
    with monkeypatch.context() as guarded:
        guarded.setattr(os, "stat", lambda *_a, **_k: pytest.fail("GUI receipt accessed photo disk"))
        assert not metadata_receipt_is_current(SimpleNamespace(_meta_cache={}), display, first.revision)
