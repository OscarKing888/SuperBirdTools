"""Theme changes repaint existing file/filter controls without loading or resetting data."""
import os

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem

from SuperViewer.superviewer.ui_theme import install_ui_theme
from SuperViewer.tests.test_thumbnail_tag_filter_sync import panel, _APP


@pytest.mark.parametrize("view_mode", [0, 1])
@pytest.mark.parametrize("width", [520, 1100])
def test_live_theme_keeps_rows_filters_selection_and_cached_thumbnails(panel, tmp_path, monkeypatch, view_mode, width):
    paths = [os.path.normpath(str(tmp_path / f"照片{i}.jpg")) for i in range(3)]
    panel._all_files = paths
    panel._photo_tag_cache = {path: {"飞行"} for path in paths}
    panel._active_tag_filters = {"飞行"}
    panel._rebuild_tag_filter_bar()
    panel._rebuild_views()
    panel._set_view_mode(view_mode)
    panel.resize(width, 700)
    _APP.processEvents()
    assert panel.select_display_path_silently(paths[1])
    from PyQt6.QtGui import QPixmap
    pixmap = QPixmap(8, 8)
    pixmap.fill(QColor("#ff0000"))
    panel._thumb_list_model.set_pixmap_for_path(paths[1], pixmap, panel._thumb_size)
    widgets = tuple(panel._tag_filter_buttons.values())
    checked = [widget.isChecked() for widget in widgets]
    selected = panel.get_selected_display_path()
    row_counts = (panel._file_table_model.rowCount(), panel._thumb_list_model.rowCount())
    resets, selections = [], []
    panel._file_table_model.modelReset.connect(lambda: resets.append("table"))
    panel._thumb_list_model.modelReset.connect(lambda: resets.append("thumb"))
    panel.file_selected.connect(selections.append)
    for name in ("_rebuild_tag_filter_bar", "_refresh_filter_scope", "_create_metadata_loader"):
        monkeypatch.setattr(panel, name, lambda *a, **k: pytest.fail("theme changed data or scheduled I/O"))
    manager = install_ui_theme(_APP)
    previous = manager.scheme
    manager.add_listener(panel.apply_theme)
    rendered = {}
    try:
        for scheme in ("light", "dark", "light"):
            manager.refresh(scheme)
            _APP.processEvents()
            assert panel._file_list_theme_scheme == scheme
            assert (panel._list_widget.palette().base().color().lightness() > 128) == (scheme == "light")
            assert (panel._tree_widget.palette().text().color().lightness() < 128) == (scheme == "light")
            assert (panel._filter_edit.palette().base().color().lightness() > 128) == (scheme == "light")
            assert (panel._filter_edit.palette().text().color().lightness() < 128) == (scheme == "light")
            assert tuple(panel._tag_filter_buttons.values()) == widgets
            assert [widget.isChecked() for widget in widgets] == checked
            assert panel.get_selected_display_path() == selected
            assert row_counts == (panel._file_table_model.rowCount(), panel._thumb_list_model.rowCount())
            assert panel._thumb_list_model.has_current_pixmap(paths[1], panel._thumb_size)
            assert not resets and not selections
            # Render an uncached thumbnail: its placeholder must follow the active palette.
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, 160, 180)
            option.palette = panel._list_widget.palette()
            option.state = QStyle.StateFlag.State_Enabled
            image = QImage(160, 180, QImage.Format.Format_RGB32)
            image.fill(option.palette.base().color())
            painter = QPainter(image)
            panel._list_widget.itemDelegate().paint(painter, option, panel._thumb_list_model.index(0, 0))
            painter.end()
            rendered[scheme] = image.pixelColor(80, 60)
            assert rendered[scheme] == option.palette.alternateBase().color()
            for button in (*widgets, panel._btn_filter_reject, panel._star_btns[0], panel._btn_filter_rating_menu):
                assert button.styleSheet()
        assert rendered["light"].lightness() > rendered["dark"].lightness()
    finally:
        manager.remove_listener(panel.apply_theme)
        manager.refresh(previous)


def test_tag_bar_rebuild_uses_current_light_theme(panel):
    manager = install_ui_theme(_APP)
    previous = manager.scheme
    try:
        manager.refresh("light")
        _APP.processEvents()
        panel._available_tags = [f"标签{i}" for i in range(12)]
        panel._active_tag_filters = {"标签0"}
        panel._rebuild_tag_filter_bar()
        assert "#137333" in panel._tag_filter_buttons["标签0"].styleSheet()
        assert "#5f6368" in panel._tag_filter_menu_button.styleSheet()
        assert "#b3261e" in panel._tag_filter_clear_button.styleSheet()
        panel._available_tags = []
        panel._rebuild_tag_filter_bar()
        assert "#70757a" in panel._tag_filter_empty.styleSheet()
    finally:
        manager.refresh(previous)
