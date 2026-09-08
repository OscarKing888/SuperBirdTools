from types import SimpleNamespace

import pytest
from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal

from SuperViewer.main import MainWindow
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
from SuperViewer.superviewer.image_info_tab_tags import ImageInfoTabPanel_Tags
from SuperViewer.superviewer.image_info_tab_widget import ImageInfoTabWidget
from SuperViewer.superviewer.qt_compat import QApplication


_APP = QApplication.instance() or QApplication([])


class _MetadataSignals(QObject):
    updated = pyqtSignal(object)


@pytest.fixture
def info(tmp_path):
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (80, 60)).save(photo)
    path = str(photo)
    metadata = {"comment": "原始备注", "rating": 1}
    tags = {"飞行"}
    panel = ImageInfoTabPanel_ImageInfo(
        lambda: ["飞行", "捕食"], lambda _path: set(tags),
        lambda *_args: None, lambda path, _name: path,
        metadata_provider=lambda _path: dict(metadata),
    )
    tabs = ImageInfoTabWidget()
    tags_panel = ImageInfoTabPanel_Tags(
        lambda: ["飞行", "捕食"], lambda _path: set(tags),
        lambda *_args: None, lambda *_args: None,
    )
    tabs.add_info_panel(panel)
    tabs.add_info_panel(tags_panel)
    window = SimpleNamespace(
        _current_exif_path=path, _shutdown_requested=False,
        _file_list=SimpleNamespace(_selection_key_nav_hold_active=False),
        image_info_panel=panel,
        image_info_tabs=tabs,
    )
    signals = _MetadataSignals()
    signals.updated.connect(lambda paths: MainWindow._on_metadata_cache_updated(window, paths))
    tabs.on_photo_selected(path)
    try:
        yield window, panel, signals, path, metadata, tags
    finally:
        tabs.request_shutdown()
        tabs.deleteLater()
        signals.deleteLater()
        _APP.processEvents()


def _edit_state(edit):
    return (edit.text(), edit.cursorPosition(), edit.selectionStart(),
            edit.selectedText(), edit.isUndoAvailable())


def test_background_metadata_signal_preserves_both_drafts_and_updates_fields(info):
    _window, panel, signals, path, metadata, tags = info
    panel.comment_edit.insert("还未保存")
    panel.comment_edit.setSelection(1, 3)
    panel.filename_edit.insert("新名字")
    panel.filename_edit.setSelection(2, 2)
    comment_state = _edit_state(panel.comment_edit)
    name_state = _edit_state(panel.filename_edit)
    tags.clear()
    tags.add("捕食")
    metadata.update(comment="后台读取的备注", rating=4)

    signals.updated.emit([path])

    assert _edit_state(panel.comment_edit) == comment_state
    assert _edit_state(panel.filename_edit) == name_state
    assert panel._current_comment == "后台读取的备注"
    assert panel._current_tags == {"捕食"}
    assert panel.basic_rows["评分"].text() == "★★★★☆"


def test_clean_comment_updates_and_identical_refresh_preserves_selection(info):
    _window, panel, signals, path, metadata, _tags = info
    metadata["comment"] = "已保存的新备注"
    signals.updated.emit([path])
    assert panel.comment_edit.text() == "已保存的新备注"
    panel.comment_edit.setSelection(1, 3)
    state = _edit_state(panel.comment_edit)
    signals.updated.emit([path])
    assert _edit_state(panel.comment_edit) == state
    metadata["comment"] = ""
    signals.updated.emit([path])
    assert panel.comment_edit.text() == ""


@pytest.mark.parametrize("reason", ["other_photo", "panel_path", "shutdown", "playback"])
def test_background_refresh_rejects_noncurrent_or_stopped_updates(info, reason):
    window, panel, signals, path, metadata, _tags = info
    if reason == "other_photo":
        path += ".other"
    elif reason == "panel_path":
        panel.set_current_photo_path(path + ".other")
    elif reason == "shutdown":
        window._shutdown_requested = True
    else:
        window._file_list._selection_key_nav_hold_active = True
    metadata["comment"] = "不应显示"
    signals.updated.emit([path])
    assert panel.comment_edit.text() == "原始备注"


def test_direct_tag_refresh_keeps_drafts_without_metadata_read(info):
    _window, panel, _signals, _path, _metadata, tags = info
    panel.comment_edit.insert("草稿")
    panel.filename_edit.insert("草稿")
    before = _edit_state(panel.comment_edit), _edit_state(panel.filename_edit)
    panel._metadata_provider = lambda _path: pytest.fail("tag refresh must not read metadata")
    panel._set_tag_callback = lambda _paths, tag, enabled: tags.add(tag)
    panel._set_current_tag("捕食", True)
    assert (_edit_state(panel.comment_edit), _edit_state(panel.filename_edit)) == before
    assert panel._current_tags == {"飞行", "捕食"}


def test_hidden_metadata_refresh_waits_for_activation_and_preserves_drafts(info):
    window, panel, signals, path, metadata, tags = info
    tabs = window.image_info_tabs
    panel.comment_edit.selectAll()
    panel.comment_edit.insert("")
    panel.filename_edit.insert("草稿")
    panel.filename_edit.setSelection(1, 2)
    before = _edit_state(panel.comment_edit), _edit_state(panel.filename_edit)
    tabs.setCurrentIndex(1)
    reads = []
    panel._metadata_provider = lambda _path: reads.append(_path) or dict(metadata)
    panel._tags_for_path_provider = lambda _path: reads.append("tags") or set(tags)
    metadata.update(comment="后台备注", rating=4)
    tags.clear()
    tags.add("捕食")

    signals.updated.emit([path])
    signals.updated.emit([path])

    assert reads == []
    assert panel.basic_rows["评分"].text() == "★☆☆☆☆"
    tabs.setCurrentWidget(panel)
    assert reads == [path, "tags"]
    assert (_edit_state(panel.comment_edit), _edit_state(panel.filename_edit)) == before
    assert panel._current_comment == "后台备注"
    assert panel.basic_rows["评分"].text() == "★★★★☆"
    assert panel._current_tags == {"捕食"}
    tabs.setCurrentIndex(1)
    tabs.setCurrentWidget(panel)
    assert reads == [path, "tags"]


def test_new_photo_replaces_deferred_metadata_refresh_with_full_refresh(info, tmp_path):
    window, panel, signals, path, metadata, _tags = info
    tabs = window.image_info_tabs
    panel.comment_edit.insert("旧图草稿")
    panel.filename_edit.insert("旧图草稿")
    tabs.setCurrentIndex(1)
    signals.updated.emit([path])
    assert panel in tabs._pending_metadata_panels
    new_photo = tmp_path / "新照片.png"
    Image.new("RGB", (12, 9)).save(new_photo)
    window._current_exif_path = str(new_photo)
    metadata["comment"] = "新照片备注"
    tabs.on_photo_selected(str(new_photo))
    assert panel not in tabs._pending_metadata_panels
    assert panel in tabs._pending_panels
    signals.updated.emit([path])
    signals.updated.emit([str(new_photo)])

    tabs.setCurrentWidget(panel)

    assert panel.comment_edit.text() == "新照片备注"
    assert panel.filename_edit.text() == "新照片"
    assert panel.basic_rows["尺寸"].text() == "12 × 9"
    assert panel not in tabs._pending_panels
    assert panel not in tabs._pending_metadata_panels


def test_shutdown_discards_hidden_metadata_refresh(info):
    window, panel, signals, path, metadata, _tags = info
    tabs = window.image_info_tabs
    tabs.setCurrentIndex(1)
    signals.updated.emit([path])
    assert panel in tabs._pending_metadata_panels
    tabs.request_shutdown()
    assert not tabs._pending_metadata_panels
    metadata["comment"] = "关窗后不应读取"
    panel._metadata_provider = lambda _path: pytest.fail("closed tabs must not read metadata")

    signals.updated.emit([path])
    tabs.setCurrentWidget(panel)

    assert panel.comment_edit.text() == "原始备注"
    assert not tabs._pending_metadata_panels
