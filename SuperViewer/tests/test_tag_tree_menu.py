from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

try:
    from PyQt6.QtTest import QTest
except ImportError:
    from PyQt5.QtTest import QTest

from SuperViewer.superviewer.photo_tags import (
    PhotoTagConfig,
    TagTreeNode,
    iter_tag_tree_leaves,
    parse_tag_tree_text,
)
from SuperViewer.superviewer.qt_compat import (
    QApplication, QCheckBox, QLineEdit, QMenu, QPoint, QPushButton, Qt, QWidgetAction,
)
from SuperViewer.superviewer.tag_menu import add_filterable_tag_actions
from SuperViewer.superviewer.tagged_file_list import (
    SuperViewerTaggedFileListPanel,
    filter_text_tokens_match,
)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def tree_panel(qt_app, tmp_path: Path):
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("行为\n  飞行\n  捕食\n场景\n  湿地\n", encoding="utf-8")
    panel = SuperViewerTaggedFileListPanel(tag_config_path=cfg)
    try:
        yield panel
    finally:
        panel.shutdown()
        panel.close()
        qt_app.processEvents()


def _left_button():
    return Qt.MouseButton.LeftButton if hasattr(Qt, "MouseButton") else Qt.LeftButton


def _open_submenu(app, menu: QMenu, title: str) -> QMenu:
    action = next(action for action in menu.actions() if action.text() == title)
    submenu = action.menu()
    QTest.mouseMove(menu, menu.actionGeometry(action).center())
    deadline = time.monotonic() + 1.5
    while not submenu.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert submenu.isVisible()
    app.processEvents()
    return submenu


def _click_checkbox(box):
    QTest.mouseClick(box, _left_button(), pos=QPoint(12, box.height() // 2))


def test_tree_config_keeps_flat_compatibility_comments_unicode_and_leaf_identity(tmp_path: Path):
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("\ufeff# 标签配置\n最佳\n行为\n\t飞行\n\t捕食\n\t捕食\n场景\n  湿地\n  飞行\n", encoding="utf-8")
    config = PhotoTagConfig(cfg)
    tree, tags = config.load_tree_and_tags()
    assert [node.name for node in tree] == ["最佳", "行为", "场景"]
    assert tags == ["最佳", "飞行", "捕食", "湿地"]
    assert tree[0].is_leaf
    assert tree[1].is_group
    assert [node.name for node in tree[1].children] == ["飞行", "捕食"]
    assert config.load() == tags
    cfg.write_text("最佳\n飞行\n\n飞行\n捕食\n", encoding="utf-8")
    assert config.load() == ["最佳", "飞行", "捕食"]
    assert all(node.is_leaf for node in config.load_tree())
    assert PhotoTagConfig(tmp_path / "missing.cfg").load_tree() == []


def test_nested_tree_group_names_are_not_assignable_tags():
    tree = parse_tag_tree_text("行为\n  空中\n    飞行\n  捕食\n")
    assert tree == [TagTreeNode("行为", [TagTreeNode("空中", [TagTreeNode("飞行")]), TagTreeNode("捕食")])]
    assert iter_tag_tree_leaves(tree) == ["飞行", "捕食"]


def test_tree_config_reload_changes_groups_and_library_scope(tree_panel, tmp_path: Path):
    panel = tree_panel
    assert panel.available_photo_tags() == ["飞行", "捕食", "湿地"]
    assert panel._tag_filter_menu_button.text() == "全部标签"
    panel._tag_config.path.write_text("鸟类行为\n  飞行\n  捕食\n场景\n  湿地\n", encoding="utf-8")
    assert panel.available_photo_tag_tree()[0].name == "鸟类行为"
    other_library = tmp_path / "other"
    (other_library / ".superpicky").mkdir(parents=True)
    (other_library / ".superpicky" / "tags.cfg").write_text("分类\n  猛禽\n", encoding="utf-8")
    assert panel._set_tag_config_directory(other_library)
    assert panel.available_photo_tags() == ["猛禽"]
    assert panel.available_photo_tag_tree() == [TagTreeNode("分类", [TagTreeNode("猛禽")])]


def test_menu_preserves_flat_actions_and_prunes_tree_with_leaf_whitelist(qt_app):
    tree = parse_tag_tree_text("行为\n  飞行\n  捕食\n场景\n  湿地\n")
    calls = []
    flat = QMenu()
    nested = QMenu()
    try:
        actions = add_filterable_tag_actions(flat, ["飞行", "捕食", "飞行"], lambda *args: calls.append(args), checkable=True)
        assert [action.text() for action in actions] == ["飞行", "捕食"]
        actions[0].trigger()
        assert calls == [("飞行", True)]
        actions = add_filterable_tag_actions(nested, ["捕食"], lambda *args: calls.append(args), tag_tree=tree)
        groups = [action.menu() for action in nested.actions() if action.menu() is not None]
        assert [group.title() for group in groups] == ["行为"]
        assert [action.text() for action in actions] == ["捕食"]
        assert not groups[0].menuAction().isCheckable()
        assert add_filterable_tag_actions(QMenu(nested), [], lambda *args: None, tag_tree=tree) == []
    finally:
        flat.close()
        nested.close()


def test_nested_menu_search_restores_closed_submenu_leaves(qt_app):
    menu = QMenu()
    tree = parse_tag_tree_text("行为\n  空中\n    飞行\n    展翅\n场景\n  湿地\n")
    try:
        add_filterable_tag_actions(menu, None, lambda *args: None, tag_tree=tree, checkable=True, keep_open=True)
        edit = menu.findChild(QLineEdit)
        groups = {action.text(): action for action in menu.actions() if action.menu() is not None}
        boxes = {box.text(): box for box in menu.findChildren(QCheckBox)}
        edit.setText("missing")
        assert all(not action.isVisible() for action in groups.values())
        edit.setText("飞行")
        assert groups["行为"].isVisible()
        assert not groups["场景"].isVisible()
        assert not boxes["飞行"].isHidden()
        assert boxes["展翅"].isHidden()
        group = groups["行为"].menu()
        nested = next(action.menu() for action in group.actions() if action.menu() is not None)
        assert any(action.isVisible() for action in nested.actions() if isinstance(action, QWidgetAction))
        edit.setText("行为")
        assert groups["行为"].isVisible()
        assert not groups["场景"].isVisible()
        assert not boxes["飞行"].isHidden() and not boxes["展翅"].isHidden()
        edit.clear()
        assert all(action.isVisible() for action in groups.values())
        assert all(not box.isHidden() for box in boxes.values())
    finally:
        menu.close()


def test_real_checkbox_clicks_keep_menu_open_and_outside_click_closes_tree(qt_app):
    menu = QMenu()
    outside = QPushButton("outside")
    outside.resize(100, 40)
    outside.move(700, 400)
    outside.show()
    calls = []
    outside_clicks = []
    outside.clicked.connect(lambda: outside_clicks.append(True))
    try:
        add_filterable_tag_actions(
            menu, None, lambda *args: calls.append(args),
            tag_tree=parse_tag_tree_text("行为\n  飞行\n  捕食\n"),
            checkable=True, keep_open=True,
        )
        group = next(action for action in menu.actions() if action.menu() is not None)
        submenu = group.menu()
        menu.popup(QPoint(100, 100))
        qt_app.processEvents()
        QTest.mouseMove(menu, menu.actionGeometry(group).center())
        deadline = time.monotonic() + 1.5
        while not submenu.isVisible() and time.monotonic() < deadline:
            qt_app.processEvents()
            QTest.qWait(5)
        assert submenu.isVisible()
        qt_app.processEvents()
        boxes = {box.text(): box for box in submenu.findChildren(QCheckBox)}
        for tag in ("飞行", "捕食", "飞行"):
            box = boxes[tag]
            QTest.mouseClick(box, _left_button(), pos=QPoint(12, box.height() // 2))
        assert calls == [("飞行", True), ("捕食", True), ("飞行", False)]
        assert menu.isVisible() and submenu.isVisible()
        QTest.mouseClick(outside, _left_button())
        qt_app.processEvents()
        assert not menu.isVisible() and not submenu.isVisible()
        assert outside_clicks == [True]
    finally:
        menu.close()
        outside.close()


def test_context_menu_duplicate_leaves_follow_writes_and_failure_state(
    qt_app, tree_panel, tmp_path: Path, monkeypatch,
):
    panel = tree_panel
    panel._tag_config.path.write_text("行为\n  飞行\n场景\n  飞行\n", encoding="utf-8")
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"source")
    path = os.path.normpath(str(photo))
    panel._photo_tag_cache[path] = set()
    panel._meta_cache[path] = {"tags": []}
    monkeypatch.setattr(panel, "_refresh_metadata_state_for_paths", lambda paths: None)
    menu = QMenu()
    try:
        panel._add_photo_tag_menu_actions(menu, [path])
        menu.popup(QPoint(100, 100))
        qt_app.processEvents()
        tag_menu = _open_submenu(qt_app, menu, "打标签")
        first = _open_submenu(qt_app, tag_menu, "行为")
        boxes = tag_menu.findChildren(QCheckBox)
        assert len(boxes) == 2
        clear = next(action for action in tag_menu.actions() if action.text() == "清除所有TAG")
        assert not clear.isEnabled()
        _click_checkbox(first.findChild(QCheckBox))
        assert all(box.isChecked() for box in boxes)
        assert panel._photo_tag_store.get_tags(path) == {"飞行"}
        assert clear.isEnabled()
        second = _open_submenu(qt_app, tag_menu, "场景")
        _click_checkbox(second.findChild(QCheckBox))
        assert all(not box.isChecked() for box in boxes)
        assert panel._photo_tag_store.get_tags(path) == set()
        assert not clear.isEnabled()

        from SuperViewer.superviewer import tagged_file_list as tagged_module

        errors = []
        monkeypatch.setattr(panel._photo_tag_store._metadata, "write_subjects", lambda *args, **kwargs: False)
        monkeypatch.setattr(tagged_module.QMessageBox, "warning", lambda *args: errors.append(args))
        _click_checkbox(second.findChild(QCheckBox))
        assert all(not box.isChecked() for box in boxes)
        assert not clear.isEnabled()
        assert panel._photo_tag_store.get_tags(path) == set()
        assert len(errors) == 1
        assert menu.isVisible() and tag_menu.isVisible()
    finally:
        menu.close()


def test_menu_callback_exception_restores_checkbox_without_recursive_callbacks(qt_app):
    menu = QMenu()
    calls = []

    def deny_write(tag, checked):
        calls.append((tag, checked))
        raise OSError("test callback failure")

    try:
        add_filterable_tag_actions(
            menu, ["飞行"], deny_write,
            checkable=True, checked_provider=lambda tag: False, keep_open=True,
        )
        menu.popup(QPoint(100, 100))
        qt_app.processEvents()
        checkbox = menu.findChild(QCheckBox)
        _click_checkbox(checkbox)
        assert calls == [("飞行", True)]
        assert not checkbox.isChecked()
        assert menu.isVisible()
    finally:
        menu.close()


@pytest.mark.parametrize("tokens, expected", [
    (["DSC", "湿地", "飞行"], True),
    (["dsc", "飞"], True),
    (["DSC", "捕食"], False),
    ([" ", ""], True),
])
def test_mixed_filter_tokens_are_and_across_fields(tokens, expected):
    assert filter_text_tokens_match(tokens, name="DSC_0001.jpg", comment="清晨湿地", photo_tags=["飞行"]) is expected


def test_mixed_text_keeps_rating_pick_focus_filters_and_async_scope(tree_panel, monkeypatch, tmp_path: Path):
    panel = tree_panel
    path = os.path.normpath(str(tmp_path / "DSC_0001.jpg"))
    panel._meta_cache[path] = {"comment": "清晨湿地", "tags": ["飞行"], "rating": 4, "pick": 1}
    panel._photo_tag_cache[path] = {"飞行"}
    panel._filter_edit.blockSignals(True)
    panel._filter_edit.setText("dsc 湿地 飞行")
    panel._filter_min_rating = 4
    panel._filter_pick = True
    assert panel._path_matches_active_filters(path)
    panel._filter_min_rating = 5
    assert not panel._path_matches_active_filters(path)
    panel._filter_min_rating = 4
    panel._filter_reject = True
    assert not panel._path_matches_active_filters(path)
    panel._filter_reject = False
    panel._filter_focus_status = "missing"
    assert not panel._path_matches_active_filters(path)
    panel._filter_focus_status = ""
    panel._active_tag_filters = {"捕食"}
    assert not panel._path_matches_active_filters(path)
    panel._active_tag_filters.clear()
    calls = []
    monkeypatch.setattr(panel, "_start_photo_tag_cache_loader_if_needed", lambda paths, **kwargs: calls.append((list(paths), kwargs)))
    monkeypatch.setattr(SuperViewerTaggedFileListPanel.__bases__[0], "_refresh_filter_scope", lambda self: calls.append("base_scope"))
    panel._all_files = [path]
    panel._refresh_filter_scope()
    assert calls == [([path], {"reason": "tag_filter"}), "base_scope"]
