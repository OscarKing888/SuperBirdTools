import importlib
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser._workers import MetadataLoader
from app_common.file_browser import FileListPanel
from SuperViewer.main import MainWindow
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo, _metadata_comment
from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
from SuperViewer.superviewer.metadata_edit_sync import sync_saved_xmp_edit


_APP = QApplication.instance() or QApplication([])
main = importlib.import_module("SuperViewer.main")


@pytest.mark.parametrize("entry", ["info", "exif"])
@pytest.mark.parametrize("new_text", ["中文新备注", ""])
def test_comment_save_updates_parsed_cache_and_readback(tmp_path, entry, new_text):
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
    )
    try:
        assert _metadata_comment(panel._meta_cache[path]) == "原来的备注"
        if entry == "info":
            assert MainWindow._save_photo_comment_from_info_panel(harness, path, new_text)
        else:
            assert MainWindow._write_xmp_meta_value(harness, path, main.META_DESCRIPTION_TAG_ID, new_text)
            MainWindow._sync_metadata_after_exif_save(harness, path, main.META_DESCRIPTION_TAG_ID, new_text)
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
        assert files.can_undo
        generation = files._photo_tag_generation(path)
        MainWindow._save_exif_value(window, None, None, "捕食", "飞行", "XMP-dc:Subject")
        assert messages == ["saved"]
        assert refreshes == [path]
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
    metadata = PhotoMetaDataXMP()
    assert metadata.write(path, {key: value})
    calls = []
    files = SimpleNamespace(sync_metadata_edit_for_path=lambda path, **kwargs: calls.append(kwargs["meta_updates"]))
    sync_saved_xmp_edit(files, path, key)
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
