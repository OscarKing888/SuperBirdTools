# -*- coding: utf-8 -*-
"""多选过滤的数据源、实窗交互与布局回归。"""
import time

import pytest
try:
    from PyQt6.QtCore import QPoint, Qt, QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QCheckBox, QLineEdit, QWidget
except ImportError:
    from PyQt5.QtCore import QPoint, Qt, QTimer
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QCheckBox, QLineEdit, QWidget

from app_common import superviewer_user_options as options
from app_common.file_browser._panel import FileListPanel
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
from SuperViewer.superviewer.ui_theme import build_palette

_APP = QApplication.instance() or QApplication([])
_KEY = getattr(Qt, "Key", Qt)
_LEFT = getattr(Qt, "MouseButton", Qt).LeftButton


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setattr(options, "_RUNTIME_OPTIONS", options.normalize_user_options({}))
    monkeypatch.setattr(FileListPanel, "_schedule_visible_thumbnail_update", lambda *_: None)
    monkeypatch.setattr(FileListPanel, "_emit_file_selected_for_path", lambda *_: None)
    monkeypatch.setattr(FileListPanel, "_schedule_selection_visibility_restore", lambda *_, **__: None)
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("飞行\n捕食\n湿地\n", encoding="utf-8")
    panel = SuperViewerTaggedFileListPanel(tag_config_path=cfg)
    panel._current_dir = str(tmp_path)
    panel._loaded_directory_recursive = True
    panel.resize(680, 480)
    panel.show()
    yield panel
    panel.shutdown()
    panel.close()
    panel.deleteLater()
    _APP.processEvents()


def seed(panel, tmp_path):
    scores = [0, 7.99, 8, 24.99, 25, 49.99, 50, 74.99, 75, 100, None, -1]
    paths = [str(tmp_path / f"白鹭_{i:02}.jpg") for i in range(len(scores))]
    panel._all_files = paths
    panel._meta_cache = {path: {"gbif_rarity_100": score, "LensModel": "镜头甲",
                                "rating": 3, "pick": 1, "comment": "湿地"}
                         for path, score in zip(paths, scores)}
    panel._photo_tag_cache = {path: {"飞行", "湿地"} for path in paths}
    panel._rebuild_views()
    return paths


def settle(panel):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        _APP.processEvents()
        if not (panel._thumb_model_dirty or panel._tree_view_dirty):
            break
        if panel._view_mode == panel._MODE_LIST:
            panel._populate_tree_model_batch()
            if not panel._tree_view_dirty:
                break
        else:
            panel._populate_thumb_model_batch()
            if not panel._thumb_model_dirty:
                break


def visible(panel):
    settle(panel)
    if panel._view_mode == panel._MODE_THUMB:
        return panel._thumb_list_model.all_paths()
    model = panel._tree_widget.model()
    return [panel._tree_path_from_index(model.index(i, 0)) for i in range(model.rowCount())]


def test_multiselect_boundaries_unknown_and_combined_filters(panel, tmp_path):
    paths = seed(panel, tmp_path)
    for level, expected in (("common", paths[:2]), ("uncommon", paths[2:4]),
                            ("rare", paths[4:6]), ("epic", paths[6:8]),
                            ("legendary", paths[8:10]), ("unknown", paths[10:])):
        panel.set_rarity_filters({level})
        assert visible(panel) == expected
    panel.set_rarity_filters({"rare", "legendary"})
    assert visible(panel) == paths[4:6] + paths[8:10]
    panel._btn_list.click()
    assert visible(panel) == paths[4:6] + paths[8:10]
    panel._meta_cache[paths[4]]["LensModel"] = "镜头乙"
    panel.set_lens_filter("镜头乙")
    panel._on_tag_filter_toggled("飞行", True)
    panel._filter_edit.setText("白鹭 湿地")
    panel._filter_min_rating = 3
    panel._filter_pick = True
    assert panel._compute_filtered_files() == [paths[4]]
    panel._on_tag_filter_toggled("捕食", True)
    panel._on_tag_exact_match_toggled(True)
    assert panel._compute_filtered_files() == []
    panel._clear_tag_filters()
    assert panel._compute_filtered_files() == [paths[4]]
    panel.set_rarity_filters(set())
    assert panel._compute_filtered_files() == [paths[4]]


@pytest.mark.parametrize("mode", [FileListPanel._MODE_LIST, FileListPanel._MODE_THUMB])
def test_metadata_arrival_and_edits_update_filter_membership(panel, tmp_path, mode):
    paths = seed(panel, tmp_path)
    panel._set_view_mode(mode)
    panel.set_rarity_filters({"legendary"})
    assert visible(panel) == paths[8:10]
    panel._on_metadata_batch_ready({paths[0]: {"gbif_rarity_100": 90}})
    panel._apply_meta_batch_tick()
    panel._flush_meta_filter_refresh()
    assert visible(panel) == [paths[0], *paths[8:10]]
    panel.sync_metadata_edits_for_paths({paths[8]: {"gbif_rarity_100": 0}})
    assert visible(panel) == [paths[0], paths[9]]
    # 鸟名过期的等级须按未知处理；0 分仍是普通。
    panel._meta_cache[paths[9]].update(title="白鹭", birdid_rarity_source="苍鹭")
    panel._refresh_metadata_state_for_paths([paths[9]])
    assert visible(panel) == [paths[0]]


def test_rarity_menu_mouse_keyboard_reopen_clear_and_custom_labels(panel, monkeypatch):
    monkeypatch.setattr(panel, "_refresh_filter_scope", lambda: None)
    # 相同的自定义名称仍按稳定等级键选择，不能把名称当业务身份。
    options.apply_runtime_user_options({"rarity_badge_rare_text": "珍鸟", "rarity_badge_epic_text": "珍鸟"})
    menu = panel._rarity_filter_button.menu()
    menu.popup(QPoint(100, 100))
    _APP.processEvents()
    actions = {a.data(): a for a in menu.actions() if a.isCheckable()}
    assert actions["rare"].text() == actions["epic"].text() == "珍鸟"
    QTest.mouseClick(menu, _LEFT, pos=menu.actionGeometry(actions["rare"]).center())
    menu.setActiveAction(actions["epic"])
    QTest.keyClick(menu, _KEY.Key_Space)
    assert menu.isVisible()
    assert panel._active_rarity_filters == {"rare", "epic"}
    assert panel._rarity_filter_button.text() == "稀有等级(2)"
    QTest.keyClick(menu, _KEY.Key_Escape)
    assert not menu.isVisible()
    menu.popup(QPoint(100, 100))
    _APP.processEvents()
    assert {a.data() for a in menu.actions() if a.isChecked()} == {"rare", "epic"}
    QTest.mouseClick(menu, _LEFT, pos=menu.actionGeometry(panel._rarity_filter_clear_action).center())
    assert panel._active_rarity_filters == set()
    assert not menu.isVisible()


def test_tags_dropdown_search_multiselect_exact_mode_and_repeated_open(panel, monkeypatch):
    monkeypatch.setattr(panel, "_refresh_filter_scope", lambda: None)
    menu = panel._tag_filter_menu_button.menu()
    for _ in range(3):
        menu.popup(QPoint(100, 100))
        _APP.processEvents()
        boxes = {box.text(): box for box in menu.findChildren(QCheckBox)}
        for tag in ("飞行", "捕食"):
            if not boxes[tag].isChecked():
                QTest.mouseClick(boxes[tag], _LEFT, pos=QPoint(12, boxes[tag].height() // 2))
        assert menu.isVisible()
        assert panel._active_tag_filters == {"飞行", "捕食"}
        edit = menu.findChild(QLineEdit)
        edit.setText("湿地")
        assert boxes["飞行"].isHidden() and not boxes["湿地"].isHidden()
        edit.clear()
        assert not boxes["飞行"].isHidden() and boxes["飞行"].isChecked()
        menu.setActiveAction(panel._tag_filter_exact_match_action)
        QTest.keyClick(menu, _KEY.Key_Space)
        assert menu.isVisible()
        QTest.keyClick(menu, _KEY.Key_Escape)
    assert panel._tag_filter_partial_match is False
    menu.popup(QPoint(100, 100))
    _APP.processEvents()
    QTest.mouseClick(menu, _LEFT, pos=menu.actionGeometry(panel._tag_filter_clear_action).center())
    assert not panel._active_tag_filters
    assert not panel._tag_filter_partial_match


def test_dropdown_button_open_and_cancel_preserves_active_state(panel, monkeypatch):
    monkeypatch.setattr(panel, "_refresh_filter_scope", lambda: None)
    button = panel._rarity_filter_button
    opened = []
    for levels in (set(), {"rare", "legendary"}):
        panel.set_rarity_filters(levels)

        def close_popup():
            opened.append(button.menu().isVisible())
            button.menu().close()

        QTimer.singleShot(0, close_popup)
        QTest.mouseClick(button, _LEFT)
        assert panel._active_rarity_filters == levels
        assert button.isChecked() == bool(levels)
    assert opened == [True, True]


@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("width,font_size", [(260, 13), (520, 13), (260, 20), (900, 20)])
def test_filter_geometry_wraps_without_clipping(panel, scheme, width, font_size):
    # 独立渲染同一个实际过滤栏，避免旧评级栏的最小宽度影响本栏的几何检查。
    host = panel._filter_controls
    panel.layout().removeWidget(host)
    host.setParent(None)
    original_palette = _APP.palette()
    _APP.setPalette(build_palette(scheme))
    layout = panel._tag_filter_bar
    font = host.font()
    font.setPointSize(font_size)
    host.setFont(font)
    for control in (panel._lens_filter_combo, panel._rarity_filter_button, panel._tag_filter_menu_button):
        control.setFont(font)
    host.resize(width, layout.heightForWidth(width))
    host.show()
    try:
        _APP.processEvents()
        controls = [panel._lens_filter_combo, panel._rarity_filter_button, panel._tag_filter_menu_button]
        for control in controls:
            assert host.rect().contains(control.geometry())
            assert control.width() >= control.minimumSizeHint().width()
            assert control.height() >= control.sizeHint().height()
        for a, b in zip(controls, controls[1:]):
            assert not a.geometry().intersects(b.geometry())
    finally:
        # 将控件归还原宿主，避免关闭独立容器后留下悬空的面板字段。
        host.hide()
        panel.layout().insertWidget(0, host)
        _APP.setPalette(original_palette)
