# -*- coding: utf-8 -*-
from __future__ import annotations

from SuperViewer.superviewer.image_info_tab_base import ImageInfoTabPanel
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
from SuperViewer.superviewer.image_info_tab_tags import ImageInfoTabPanel_Tags
from SuperViewer.superviewer.image_info_tab_widget import ImageInfoTabWidget
from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.ui_theme import (
    apply_app_palette,
    detect_color_scheme,
    install_ui_theme,
    panel_colors,
)


class _ThemeProbePanel(ImageInfoTabPanel):
    tab_title = "probe"

    def __init__(self) -> None:
        self.apply_theme_count = 0
        self.refresh_count = 0
        self.last_theme_section_title = ""
        super().__init__()

    def create_ui(self) -> None:
        return

    def refresh_ui(self):
        self.refresh_count += 1
        return self.current_photo_path()

    def apply_theme(self, colors=None) -> None:
        theme = colors or panel_colors()
        self.apply_theme_count += 1
        self.last_theme_section_title = theme.section_title


def test_panel_colors_differ_for_dark_and_light() -> None:
    dark = panel_colors("dark")
    light = panel_colors("light")
    assert dark.section_title != light.section_title
    assert dark.preview_bg != light.preview_bg
    assert dark.chip_bg != light.chip_bg
    assert dark.separator != light.separator


def test_apply_app_palette_updates_window_luminance() -> None:
    from SuperViewer.superviewer.qt_compat import QPalette

    app = QApplication.instance() or QApplication([])
    role = QPalette.ColorRole.Window if hasattr(QPalette, "ColorRole") else QPalette.Window
    apply_app_palette(app, "light")
    light_color = app.palette().color(role)
    apply_app_palette(app, "dark")
    dark_color = app.palette().color(role)
    light_luma = (0.299 * light_color.red() + 0.587 * light_color.green() + 0.114 * light_color.blue()) / 255.0
    dark_luma = (0.299 * dark_color.red() + 0.587 * dark_color.green() + 0.114 * dark_color.blue()) / 255.0
    assert light_luma > 0.5
    assert dark_luma < 0.5
    assert detect_color_scheme(app) in {"dark", "light"}


def test_image_info_apply_theme_updates_section_and_chip_styles() -> None:
    app = QApplication.instance() or QApplication([])
    panel = ImageInfoTabPanel_ImageInfo(
        available_tags_provider=lambda: ["鸟种", "飞行"],
        tags_for_path_provider=lambda path: {"鸟种"},
        set_tag_callback=lambda paths, tag, checked: None,
        rename_callback=lambda path, new_stem: path,
    )
    try:
        panel.set_current_photo_path("demo.jpg")
        panel._current_tags = {"鸟种"}
        panel.apply_theme(panel_colors("light"))
        assert panel._section_title_labels
        assert "5f6368" in panel._section_title_labels[0].styleSheet().lower()
        assert "dadce0" in panel._separator_lines[0].styleSheet().lower()
        chip = panel.tags_layout.itemAt(0).widget()
        assert chip is not None
        assert "e8eaed" in chip.styleSheet().lower()

        panel.apply_theme(panel_colors("dark"))
        assert "b8b8b8" in panel._section_title_labels[0].styleSheet().lower()
        assert "303238" in panel._separator_lines[0].styleSheet().lower()
        chip = panel.tags_layout.itemAt(0).widget()
        assert chip is not None
        assert "2b2d31" in chip.styleSheet().lower()
    finally:
        panel.close()


def test_tags_panel_apply_theme_updates_muted_labels() -> None:
    app = QApplication.instance() or QApplication([])
    panel = ImageInfoTabPanel_Tags(
        available_tags_provider=lambda: [],
        tags_for_path_provider=lambda path: set(),
        set_tag_callback=lambda paths, tag, checked: None,
        clear_tags_callback=lambda paths: None,
    )
    try:
        panel.apply_theme(panel_colors("light"))
        assert "5f6368" in panel.photo_label.styleSheet().lower()
        assert "70757a" in panel.empty_label.styleSheet().lower()
        panel.apply_theme(panel_colors("dark"))
        assert "aaaaaa" in panel.photo_label.styleSheet().lower().replace(" ", "")
        assert "888888" in panel.empty_label.styleSheet().lower().replace(" ", "") or "#888" in panel.empty_label.styleSheet().lower()
    finally:
        panel.close()


def test_tab_widget_broadcasts_theme_without_refreshing_data() -> None:
    app = QApplication.instance() or QApplication([])
    manager = install_ui_theme(app)
    tabs = ImageInfoTabWidget()
    first = _ThemeProbePanel()
    second = _ThemeProbePanel()
    try:
        first_count = first.apply_theme_count
        second_count = second.apply_theme_count
        tabs.add_info_panel(first)
        tabs.add_info_panel(second)
        assert first.apply_theme_count == first_count + 1
        assert second.apply_theme_count == second_count + 1

        before_refresh_first = first.refresh_count
        before_refresh_second = second.refresh_count
        before_theme_first = first.apply_theme_count
        before_theme_second = second.apply_theme_count

        tabs.apply_theme("light")
        assert first.apply_theme_count == before_theme_first + 1
        assert second.apply_theme_count == before_theme_second + 1
        assert first.refresh_count == before_refresh_first
        assert second.refresh_count == before_refresh_second
        assert first.last_theme_section_title == panel_colors("light").section_title
        assert second.last_theme_section_title == panel_colors("light").section_title

        manager.refresh("dark")
        assert first.last_theme_section_title == panel_colors("dark").section_title
        assert second.last_theme_section_title == panel_colors("dark").section_title
        assert first.refresh_count == before_refresh_first
        assert second.refresh_count == before_refresh_second
    finally:
        tabs.close()
        first.close()
        second.close()
