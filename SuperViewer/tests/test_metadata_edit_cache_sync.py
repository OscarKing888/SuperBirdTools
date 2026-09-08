import os
from types import SimpleNamespace

import pytest
from PIL import Image

from app_common.exif_io import PhotoMetaDataJSON, json_sidecar_path_for
from app_common.file_browser import FileListPanel
from app_common.file_browser._workers import MetadataLoader
from SuperViewer.main import MainWindow
from SuperViewer.superviewer.image_info_tab_image_info import _metadata_comment
from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def files(tmp_path, monkeypatch):
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n", encoding="utf-8")
    panel = SuperViewerTaggedFileListPanel(tag_config_path=config)
    monkeypatch.setattr(panel, "_file_writes_allowed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(panel, "_sidecar_writes_allowed", lambda *_args, **_kwargs: True)
    try:
        yield panel
    finally:
        panel.shutdown()
        panel.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize("layout", ["sibling", "central", "configured"])
@pytest.mark.parametrize("new_text", ["中文新备注", ""])
def test_comment_save_readback_and_late_batch_use_the_new_value(tmp_path, files, layout, new_text):
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (8, 6)).save(photo)
    original = photo.read_bytes()
    if layout != "sibling":
        state = tmp_path / ".superpicky"
        state.mkdir()
        if layout == "configured":
            (state / "config.ini").write_text("[sidecar]\ndir=资料/metadata\n", encoding="utf-8")
    path = str(photo)
    metadata = PhotoMetaDataJSON()
    assert metadata.write(path, {"XMP-dc:Description": "旧备注"})
    loader = MetadataLoader([], files._meta_proxy)
    files._meta_cache[path] = loader._parse_rec(metadata.read(path))
    window = SimpleNamespace(_file_list=files, _file_writes_allowed=lambda _path: True)
    try:
        assert MainWindow._save_photo_comment_from_info_panel(window, path, new_text)
        assert _metadata_comment(metadata.read(path)) == new_text
        assert _metadata_comment(files._meta_cache[path]) == new_text
        assert json_sidecar_path_for(path).is_file()
        assert photo.read_bytes() == original
        files._on_metadata_batch_ready({path: {"comment": "旧备注", "iso": "800"}})
        assert _metadata_comment(files._meta_cache[path]) == new_text
        assert files._meta_cache[path]["iso"] == "800"
        # Both real text filtering and the current panel consume canonical comment.
        files._filter_edit.setText(new_text or "旧备注")
        assert files._path_matches_active_filters(path) == bool(new_text)
    finally:
        loader.deleteLater()


def test_successful_json_rating_and_pick_survive_an_older_batch(tmp_path, files, monkeypatch):
    photo = tmp_path / "评级.png"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    # The source may be read-only while the central JSON directory is writable.
    monkeypatch.setattr(files, "_file_writes_allowed", lambda *_args, **_kwargs: False)
    assert files._apply_rating_state_via_exif([path], rating=0, pick=-1) == [path]
    fields = PhotoMetaDataJSON().read(path)
    assert int(fields["XMP-xmp:Rating"]) == 0
    assert int(fields["XMP-xmpDM:pick"]) == -1
    files._on_metadata_batch_ready({path: {"rating": 5, "pick": 1, "comment": "来自后台"}})
    assert files._meta_cache[path]["rating"] == 0
    assert files._meta_cache[path]["pick"] == -1
    assert files._meta_cache[path]["comment"] == "来自后台"


def test_failed_json_rating_write_does_not_override_later_metadata(tmp_path, files, monkeypatch):
    photo = tmp_path / "失败.png"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    monkeypatch.setattr(PhotoMetaDataJSON, "write", lambda *_args, **_kwargs: False)
    assert files._apply_rating_state_via_exif([path], rating=1) == []
    files._on_metadata_batch_ready({path: {"rating": 4}})
    assert files._meta_cache[path]["rating"] == 4


def test_field_overrides_expire_on_reload_or_directory_change(tmp_path, files, monkeypatch):
    photo = str(tmp_path / "白鹭.png")
    first = str(tmp_path)
    second = str(tmp_path / "other")
    files._current_dir = first
    monkeypatch.setattr(FileListPanel, "load_directory", lambda *_args, **_kwargs: None)
    assert files.sync_metadata_edit_for_path(photo, meta_updates={"comment": "刚保存"})
    files.load_directory(first)
    merged = files._merge_metadata_batch_with_photo_tag_cache({photo: {"comment": "旧值"}})
    assert merged[photo]["comment"] == "刚保存"
    files.load_directory(first, force_reload=True)
    assert files._merge_metadata_batch_with_photo_tag_cache({photo: {"comment": "重载"}})[photo]["comment"] == "重载"
    files.sync_metadata_edit_for_path(photo, meta_updates={"comment": "第二次"})
    files.load_directory(second)
    assert not files._local_metadata_updates_by_path


def test_local_overrides_are_per_path_and_preserve_newer_tags(tmp_path, files):
    first = str(tmp_path / "one.png")
    second = str(tmp_path / "two.png")
    files.sync_metadata_edit_for_path(first, meta_updates={"comment": "本地值"})
    files._photo_tag_cache[first] = {"飞行"}
    merged = files._merge_metadata_batch_with_photo_tag_cache({
        first: {"comment": "旧值", "tags": [], "iso": "100"},
        second: {"comment": "第二张"},
    })
    assert merged[first] == {"comment": "本地值", "tags": ["飞行"], "iso": "100"}
    assert merged[second] == {"comment": "第二张"}
    assert files._local_metadata_updates_by_path[os.path.normcase(first)]["comment"] == "本地值"
