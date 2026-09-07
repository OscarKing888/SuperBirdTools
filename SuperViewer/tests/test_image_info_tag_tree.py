from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from SuperViewer.superviewer import image_info_tab_image_info as image_info
from SuperViewer.superviewer.photo_tags import TagTreeNode
from SuperViewer.superviewer.qt_compat import QApplication, QToolButton


_APP = QApplication.instance() or QApplication([])


def test_add_tag_menu_keeps_groups_and_excludes_already_selected_tags(tmp_path, monkeypatch):
    photo = tmp_path / "白鹭.jpg"
    photo.write_bytes(b"photo")
    tree = [TagTreeNode("鸟类", [TagTreeNode("白鹭"), TagTreeNode("苍鹭")])]
    panel = image_info.ImageInfoTabPanel_ImageInfo(
        lambda: ["白鹭", "苍鹭"], lambda _path: {"白鹭"},
        lambda *_args: None, lambda path, _name: path,
        available_tag_tree_provider=lambda: tree,
    )
    panel._current_photo_path = str(photo)
    panel._current_tags = {"白鹭"}
    selected = []
    monkeypatch.setattr(panel, "_set_current_tag", lambda tag, enabled: selected.append((tag, enabled)))

    def inspect_menu(menu, _position):
        groups = [action for action in menu.actions() if action.menu() is not None]
        assert [action.text() for action in groups] == ["鸟类"]
        leaves = [action for action in groups[0].menu().actions() if action.text() == "苍鹭"]
        assert len(leaves) == 1
        assert not any(action.text() == "白鹭" for action in groups[0].menu().actions())
        leaves[0].trigger()

    monkeypatch.setattr(image_info, "_exec_menu", inspect_menu)
    try:
        panel._show_add_tag_menu(QToolButton(panel))
        assert selected == [("苍鹭", True)]
    finally:
        panel.close()


def test_info_panel_without_tree_provider_keeps_flat_tags():
    panel = image_info.ImageInfoTabPanel_ImageInfo(
        lambda: ["飞行", "筑巢"], lambda _path: set(),
        lambda *_args: None, lambda path, _name: path,
    )
    try:
        tree = panel._load_available_tag_tree()
        assert [node.name for node in tree] == ["飞行", "筑巢"]
        assert all(node.is_leaf for node in tree)
    finally:
        panel.close()
