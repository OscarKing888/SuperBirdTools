import importlib
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser._workers import MetadataLoader
from SuperViewer.main import MainWindow
from SuperViewer.superviewer.image_info_tab_image_info import _metadata_comment
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
