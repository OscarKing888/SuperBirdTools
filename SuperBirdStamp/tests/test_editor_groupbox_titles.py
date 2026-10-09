"""分组标题在紧凑布局、主题切换和字体放大后仍完整且不被内容覆盖。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication, QGroupBox, QStyle, QStyleFactory, QStyleOptionGroupBox, QWidget,
)

from birdstamp import config
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_template_dialog import TemplateManagerDialog


_APP = QApplication.instance() or QApplication([])


def _assert_titles_clear(root: QWidget, font_px: int) -> set[str]:
    titles = set()
    for group in root.findChildren(QGroupBox):
        if not group.isVisible() or not group.title():
            continue
        option = QStyleOptionGroupBox()
        group.initStyleOption(option)
        title_rect = group.style().subControlRect(
            QStyle.ComplexControl.CC_GroupBox, option,
            QStyle.SubControl.SC_GroupBoxLabel, group,
        )
        message = f"{group.title()}: title={title_rect}, contents={group.contentsRect()}"
        assert group.font().pixelSize() == font_px
        assert group.rect().contains(title_rect), message
        assert title_rect.height() >= option.fontMetrics.height(), message
        assert title_rect.width() >= option.fontMetrics.horizontalAdvance(group.title()), message
        # 内容区本身必须留出完整标题行，不能依赖各面板偶然较大的 layout margin。
        assert group.contentsRect().top() > title_rect.bottom(), message
        for child in group.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if child.isVisible():
                assert not title_rect.intersects(child.geometry()), message
        titles.add(group.title())
    return titles


@pytest.mark.parametrize("style_name", QStyleFactory.keys())
@pytest.mark.parametrize("font_px", [13, 24])
def test_all_editor_group_titles_clear_content(tmp_path, monkeypatch, style_name, font_px):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for name in ("_start_bird_detector_preload", "_run_deferred_startup_tasks",
                 "_restart_photo_list_metadata_loader", "_schedule_async_bird_detect"):
        monkeypatch.setattr(BirdStampEditorWindow, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(TemplateManagerDialog, "_load_preview_source", lambda self: None)
    monkeypatch.setattr(TemplateManagerDialog, "_preview_source_bird_box", lambda self: None)
    original_style = _APP.style().objectName()
    original_palette = QPalette(_APP.palette())
    window = dialog = None
    try:
        _APP.setStyle(style_name)
        window = BirdStampEditorWindow()
        window.resize(1500, 950)
        window.show()
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        with Image.new("RGB", (160, 120), "gray") as placeholder:
            dialog = TemplateManagerDialog(template_dir, placeholder, parent=window)

        for dark in (False, True):
            palette = QPalette(_APP.palette())
            for role, value in (
                (QPalette.ColorRole.Window, "#202020" if dark else "#eeeeee"),
                (QPalette.ColorRole.Base, "#181818" if dark else "#ffffff"),
                (QPalette.ColorRole.Text, "#ffffff" if dark else "#111111"),
                (QPalette.ColorRole.WindowText, "#ffffff" if dark else "#111111"),
                (QPalette.ColorRole.Button, "#303030" if dark else "#eeeeee"),
                (QPalette.ColorRole.ButtonText, "#ffffff" if dark else "#111111"),
            ):
                palette.setColor(role, QColor(value))
            _APP.setPalette(palette)
            window._apply_system_adaptive_style()
            window.setStyleSheet(window.styleSheet() + f"\nQWidget {{ font-size: {font_px}px; }}")
            window._reveal_overlay_panel()
            titles = set()
            window.export_tabs.setCurrentIndex(0)
            for stage_id in window.export_stage_buttons:
                window._set_selected_export_stage_id(stage_id, save=False)
                _APP.processEvents()
                assert (window.palette().color(QPalette.ColorRole.Window).lightness() < 128) == dark
                titles.update(_assert_titles_clear(window, font_px))
            window.export_tabs.setCurrentIndex(1)
            _APP.processEvents()
            titles.update(_assert_titles_clear(window, font_px))
            assert {"处理管线", "图片导出", "GIF 选项", "视频导出", "1. 方式", "4. 导出", "叠加层属性"} <= titles
            assert window.export_action_bar.isVisible()

            # 模板管理器继承同一套样式；不允许只修主窗口里已知的两个分组。
            dialog.show()
            _APP.processEvents()
            assert {"当前模板", "叠加层属性", "内容", "布局", "预览"} <= _assert_titles_clear(dialog, font_px)
            dialog.hide()
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        if window is not None:
            window.close()
            window.deleteLater()
        _APP.processEvents()
        _APP.setStyle(original_style)
        _APP.setPalette(original_palette)
