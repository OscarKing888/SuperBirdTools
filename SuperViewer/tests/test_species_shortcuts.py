"""鸟名快捷键的真实按键分发、焦点边界及中文 XMP 回读。"""
import time

from PIL import Image
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from app_common import superviewer_user_options
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.file_browser import FileListPanel, _panel as panel_module
from SuperViewer.superviewer.file_context_menu import build_file_context_menu, species_shortcut_sequence
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        _APP.processEvents()
        QTest.qWait(5)


@pytest.fixture(params=[FileListPanel._MODE_LIST, FileListPanel._MODE_THUMB], ids=["list", "thumbnail"])
def panel(request, tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *_a, **_k: None)
    # 本测试只检查按键与选择；隔离与之无关的延迟滚动，避免跨 fixture 回调旧控件。
    monkeypatch.setattr(FileListPanel, "_schedule_selection_visibility_restore", lambda *_a, **_k: None)
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_a, **_k: None)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **_k: None)
    tags = tmp_path / ".superpicky" / "tags.cfg"
    tags.parent.mkdir()
    tags.write_text("飞行\n", encoding="utf-8")
    widget = SuperViewerTaggedFileListPanel(tag_config_path=tags)
    store = PhotoMetaDataXMP()
    paths = []
    for i, name in enumerate(("家燕", "白头鹎", "麻雀")):
        path = str(tmp_path / f"照片{i}.jpg")
        Image.new("RGB", (16, 12)).save(path)
        assert store.write(path, {"XMP-dc:Title": name, "XMP-superpicky:bird_species_cn": name,
                                  "XMP-dc:Description": "保留备注"})
        paths.append(path)
    widget._current_dir = str(tmp_path)
    widget._all_files = paths
    widget._meta_cache = {path: {"title": name} for path, name in zip(paths, ("家燕", "白头鹎", "麻雀"))}
    widget._set_view_mode(request.param)
    widget._rebuild_views()
    widget.show()
    widget.activateWindow()
    view = widget._tree_widget if request.param == widget._MODE_LIST else widget._list_widget
    view.setFocus()
    wait_for(view.hasFocus)
    yield widget, view, paths, store
    widget.shutdown()
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def press(view, kind):
    key = Qt.Key.Key_C if kind == "copy" else Qt.Key.Key_V
    QTest.keyClick(view, key, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    _APP.processEvents()


def select(panel, view, paths, current):
    panel.set_pending_selection(paths, current)
    wait_for(lambda: set(panel._active_view_selected_paths()) == set(paths))
    view.setFocus()
    wait_for(view.hasFocus)


def test_copy_current_and_paste_selected_photos_via_keyboard(panel):
    widget, view, paths, store = panel
    select(widget, view, paths[:2], paths[1])
    press(view, "paste")  # 尚未复制时不写入。
    assert store.read(paths[0])["Title"] == "家燕"
    press(view, "copy")
    assert QApplication.clipboard().text() == "白头鹎"
    assert widget._copied_species_payload["bird_species_cn"] == "白头鹎"
    targets = [paths[0], paths[2]]
    select(widget, view, targets, paths[2])
    press(view, "paste")
    for path in targets:
        saved = store.read(path)
        assert saved["Title"] == saved["bird_species_cn"] == "白头鹎"
        assert saved["Description"] == "保留备注"


def test_shortcuts_respect_text_focus_and_no_selection(panel, monkeypatch):
    widget, view, paths, _store = panel
    calls = []
    monkeypatch.setattr(widget, "_copy_species_from_path", lambda path: calls.append(("copy", path)))
    monkeypatch.setattr(widget, "_paste_species_to_paths", lambda paths: calls.append(("paste", paths)))
    select(widget, view, paths[:1], paths[0])
    # 过滤框和视图内的行编辑器都不应触发照片元数据操作。
    editor = QLineEdit(view.viewport())
    editor.show()
    for target in (widget._filter_edit, editor):
        target.setFocus()
        wait_for(target.hasFocus)
        press(target, "copy")
        press(target, "paste")
    assert calls == []
    view.setFocus()
    wait_for(view.hasFocus)
    view.clearSelection()
    press(view, "copy")
    press(view, "paste")
    assert calls == []
    select(widget, view, paths[:1], paths[0])
    press(view, "copy")
    assert calls == [("copy", paths[0])]


def test_menu_displays_matching_shortcuts_and_keeps_disabled_paste(panel):
    widget, _view, paths, _store = panel
    menu = build_file_context_menu(widget, paths, paths[0], log_prefix="shortcut-test")
    try:
        copy = next(a for a in menu.actions() if a.text().startswith("复制鸟名"))
        paste = next(a for a in menu.actions() if a.text().startswith("粘贴鸟名"))
        for action, kind in ((copy, "copy"), (paste, "paste")):
            assert action.shortcut() == species_shortcut_sequence(kind)
            assert action.isShortcutVisibleInContextMenu()
        assert copy.isEnabled() and not paste.isEnabled()
    finally:
        menu.deleteLater()
