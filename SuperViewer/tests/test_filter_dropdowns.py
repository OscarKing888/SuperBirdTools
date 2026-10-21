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
    assert panel._rarity_filter_button.text() == "稀有度(2)"
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
    for control in (panel._lens_filter_combo, panel._species_filter_button,
                    panel._rarity_filter_button, panel._tag_filter_menu_button):
        control.setFont(font)
    host.resize(width, layout.heightForWidth(width))
    host.show()
    try:
        _APP.processEvents()
        controls = [panel._lens_filter_combo, panel._species_filter_button,
                    panel._rarity_filter_button, panel._tag_filter_menu_button]
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


@pytest.mark.parametrize("mode", [FileListPanel._MODE_LIST, FileListPanel._MODE_THUMB])
def test_species_multiselect_matches_list_aliases_and_combines_with_rarity(panel, tmp_path, mode):
    paths = seed(panel, tmp_path)
    aliases = [
        {"bird_species_cn": "白鹭"}, {"XMP-superpicky:bird_species_cn": "白鹭"},
        {"report.bird_species_cn": "苍鹭"}, {"XMP-dc:Title": "苍鹭"},
        {"title": "白鹭"}, {"Title": "大白鹭"},
        {"bird_species_cn": "苍鹭", "XMP-superpicky:bird_species_cn": "白鹭"},
    ]
    for path, meta in zip(paths, aliases):
        panel._meta_cache[path].update(meta)
    panel._set_view_mode(mode)
    panel.set_species_filters({"白鹭", "苍鹭"})
    assert panel._has_any_filter()
    assert visible(panel) == paths[:5] + [paths[6]]
    # 精确匹配鸟种，不把“大白鹭”归到“白鹭”；空鸟名不命中。
    panel.set_rarity_filters({"rare", "epic"})
    assert visible(panel) == [paths[4], paths[6]]
    assert panel._species_filter_options() == sorted({"白鹭", "苍鹭", "大白鹭"})
    panel.set_lens_filter("镜头甲")
    panel._on_tag_filter_toggled("飞行", True)
    panel._filter_edit.setText("白鹭_04 湿地")
    panel._filter_min_rating = 3
    panel._filter_pick = True
    assert panel._compute_filtered_files() == [paths[4]]
    panel.set_species_filters(set())
    assert panel._compute_filtered_files() == [paths[4]]


@pytest.mark.parametrize("mode", [FileListPanel._MODE_LIST, FileListPanel._MODE_THUMB])
def test_species_metadata_arrival_edits_and_late_batches(panel, tmp_path, mode):
    paths = seed(panel, tmp_path)
    panel._set_view_mode(mode)
    panel.set_species_filters({"白鹭"})
    assert visible(panel) == []
    panel._on_metadata_batch_ready({paths[0]: {"title": "白鹭"}, paths[1]: {"title": "苍鹭"}})
    panel._apply_meta_batch_tick()
    panel._flush_meta_filter_refresh()
    assert visible(panel) == [paths[0]]
    panel.sync_metadata_edits_for_paths({paths[0]: {"title": "苍鹭"}, paths[1]: {"title": "白鹭"}})
    assert visible(panel) == [paths[1]]
    panel._on_metadata_batch_ready({paths[0]: {"title": "白鹭"}, paths[1]: {"title": "苍鹭"}})
    panel._apply_meta_batch_tick()
    panel._flush_meta_filter_refresh()
    assert visible(panel) == [paths[1]]
    panel.set_species_filters(set())
    assert not panel._has_any_filter()
    assert visible(panel) == paths


def test_species_menu_search_keyboard_reopen_directory_change_and_clear(panel, tmp_path, monkeypatch):
    paths = seed(panel, tmp_path)
    panel._meta_cache[paths[0]]["title"] = "白鹭"
    panel._meta_cache[paths[1]]["report.bird_species_cn"] = "苍鹭"
    panel._meta_cache[paths[2]]["title"] = "大白鹭"

    def unexpected_read(*args, **kwargs):
        pytest.fail("鸟种与稀有度过滤不应触发同步元数据读取")

    monkeypatch.setattr(panel._meta_proxy, "read", unexpected_read)
    monkeypatch.setattr(panel._photo_tag_store, "load_tags_for_paths", unexpected_read)
    menu = panel._species_filter_button.menu()
    for _ in range(3):
        menu.popup(QPoint(100, 100))
        _APP.processEvents()
        actions = {a.data(): a for a in menu.actions() if a.isCheckable()}
        assert set(actions) == {"白鹭", "苍鹭", "大白鹭"}
        edit = menu.findChild(QLineEdit)
        edit.setText("白鹭")
        assert actions["白鹭"].isVisible() and not actions["苍鹭"].isVisible()
        if not actions["白鹭"].isChecked():
            QTest.mouseClick(menu, _LEFT, pos=menu.actionGeometry(actions["白鹭"]).center())
        edit.clear()
        if not actions["苍鹭"].isChecked():
            menu.setActiveAction(actions["苍鹭"])
            QTest.keyClick(menu, _KEY.Key_Space)
        assert menu.isVisible()
        assert panel._active_species_filters == {"白鹭", "苍鹭"}
        assert panel._species_filter_button.text() == "鸟种名(2)"
        edit.setText("不存在的鸟")
        assert any(a.isVisible() and a.text() == "没有匹配的鸟种名" for a in menu.actions())
        QTest.keyClick(menu, _KEY.Key_Escape)
    # 换目录后不沿用旧缓存中的未选鸟名，但已选项仍可见、可取消。
    panel._all_files = [paths[3]]
    menu.popup(QPoint(100, 100))
    _APP.processEvents()
    assert {a.data() for a in menu.actions() if a.isCheckable()} == {"白鹭", "苍鹭"}
    QTest.mouseClick(menu, _LEFT, pos=menu.actionGeometry(panel._species_filter_clear_action).center())
    assert not panel._active_species_filters
    assert panel._species_filter_button.text() == "全部鸟种名"
    assert not menu.isVisible()
    menu.popup(QPoint(100, 100))
    _APP.processEvents()
    assert any(a.text() == "当前照片暂无鸟种名" and not a.isEnabled() for a in menu.actions())
    menu.close()


@pytest.mark.parametrize("mode", [FileListPanel._MODE_LIST, FileListPanel._MODE_THUMB])
def test_species_status_variants_merge_into_one_option_and_match_all_photos(panel, tmp_path, mode):
    from copy import deepcopy

    paths = seed(panel, tmp_path)
    for path, name in zip(paths, ["反嘴鹬", "反嘴鹬（待确定 33%）", "反嘴鹬 (待确认 40%)",
                                  "反嘴鹬（疑似）", "白鹭", "白鹭（待确定 28%）", "大白鹭"]):
        panel._meta_cache[path]["title"] = name
    before = deepcopy(panel._meta_cache)
    panel._set_view_mode(mode)
    panel.set_species_filters({"反嘴鹬", "反嘴鹬（待确定 33%）"})
    assert panel._active_species_filters == {"反嘴鹬"}
    assert visible(panel) == paths[:4]
    menu = panel._species_filter_button.menu()
    menu.popup(QPoint(100, 100))
    _APP.processEvents()
    actions = {a.data(): a for a in menu.actions() if a.isCheckable()}
    assert set(actions) == {"反嘴鹬", "白鹭", "大白鹭"}
    assert actions["反嘴鹬"].isChecked()
    assert panel._species_filter_button.text() == "鸟种名(1)"
    QTest.mouseClick(menu, _LEFT, pos=menu.actionGeometry(actions["白鹭"]).center())
    assert visible(panel) == paths[:6]
    assert panel._species_filter_button.text() == "鸟种名(2)"
    assert panel._meta_cache == before
    menu.close()
    # 待确定变为已确认后仍属于同一个过滤集合；新到的待确定记录也可命中。
    panel.sync_metadata_edits_for_paths({paths[1]: {"title": "反嘴鹬"}})
    assert visible(panel) == paths[:6]
    panel._on_metadata_batch_ready({paths[7]: {"title": "反嘴鹬（待确定 12%）"}})
    panel._apply_meta_batch_tick()
    panel._flush_meta_filter_refresh()
    assert visible(panel) == paths[:6] + [paths[7]]
