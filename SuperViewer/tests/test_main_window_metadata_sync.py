import importlib
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser._workers import MetadataLoader
from SuperViewer.main import MainWindow
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo, _metadata_comment
from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


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
    finally:
        panel.shutdown()
        panel.deleteLater()
        loader.deleteLater()
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
