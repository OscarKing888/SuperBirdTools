"""固定导出入口与停靠叠加面板的交互回归；沿用隔离配置的真实窗口。"""
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDockWidget, QScrollArea, QTabWidget
from PIL import Image

from test_overlay_editor import window, _APP
from test_overlay_layers import overlay_doc
from birdstamp.gui.editor_template_dialog import TemplateManagerDialog
from birdstamp.gui.editor_template import save_template_payload, default_template_payload
from birdstamp.gui.overlay_panel import OverlayPanel


def settle():
    for _ in range(4):
        _APP.processEvents()


def test_export_actions_stay_visible_when_scrolled_and_collapsed(window):
    window.resize(1120, 720)
    window.show()
    settle()
    bar = window.export_action_bar
    assert window.overlay_dock.isHidden()
    assert not window.left_scroll.isAncestorOf(window.overlay_panel)
    assert not window.left_scroll.isAncestorOf(bar)
    origin = window.export_current_btn.mapTo(window, QPoint())
    scroll = window.left_scroll.verticalScrollBar()
    assert scroll.maximum() > 0
    for value in (scroll.maximum(), 0):
        scroll.setValue(value)
        settle()
        assert window.export_current_btn.isVisible()
        assert window.export_current_btn.mapTo(window, QPoint()) == origin
        assert window.rect().contains(bar.mapTo(window, bar.rect().bottomRight()))
    window._export_section.set_expanded(False)
    settle()
    assert window.export_current_btn.isVisible()
    bar.settings_section.header_button.click()
    settle()
    assert not bar.settings_section.is_expanded()
    assert window.export_current_btn.isVisible()
    bar.settings_section.header_button.click()
    settle()
    assert bar.settings_section.is_expanded()
    assert not window._export_section.is_expanded()
    assert bar.settings_scroll.isAncestorOf(window.image_export_group)
    assert not window.left_scroll.isAncestorOf(window.image_export_group)
    assert window.image_export_group.isVisible()


def test_export_modes_busy_and_video_cancel_use_original_actions(window):
    window.show()
    settle()
    bar = window.export_action_bar
    window._set_selected_export_stage_id('export_gif', save=False)
    assert window.export_batch_btn.text() == '导出 GIF'
    assert window.export_current_btn.isHidden()
    assert window.export_batch_btn.isVisible()
    window._set_image_export_busy(True)
    assert not window.export_batch_btn.isEnabled()
    window._set_image_export_busy(False)
    window._set_selected_export_stage_id('export_video', save=False)
    settle()
    panel = window.video_export_panel
    assert panel.export_button.isVisible()
    assert bar.isAncestorOf(panel.export_button)
    panel.set_busy(True)
    assert panel.cancel_button.isVisible()
    assert not panel.export_button.isVisible()
    cancelled = []
    panel.cancelRequested.connect(lambda: cancelled.append(True))
    panel.cancel_button.click()
    assert cancelled == [True]
    panel.set_busy(False)
    window._set_selected_export_stage_id('export_image', save=False)
    assert window.export_current_btn.isVisible()
    assert window.export_current_btn.isEnabled()
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    settle()
    assert window.dejitter_export_btn.isVisible()
    assert bar.isAncestorOf(window.dejitter_export_btn)
    assert not window.dejitter_export_btn.isEnabled()  # 尚未分析整组。
    assert not window.export_current_btn.isVisible()
    window.export_tabs.setCurrentIndex(0)
    assert window.export_current_btn.isVisible()


@pytest.mark.parametrize("close_method", ["close", "escape"])
@pytest.mark.parametrize("floating", [False, True])
def test_overlay_dock_close_commits_text_and_preserves_history(window, overlay_doc, tmp_path, close_method, floating):
    # 不启动解码或检测，仅使用真实编辑器模型检查浮动/收起的编辑生命周期。
    window.current_path = tmp_path / '中文照片.png'
    window.overlay_panel.setEnabled(True)
    window.overlay_panel.set_document(overlay_doc, 'photo:compact', following=True)
    window.overlay_panel.select(overlay_doc['overlays'][1]['id'])
    window.resize(1120, 720)
    window.show()
    settle()
    window._reveal_overlay_panel()
    settle()
    dialog = window.overlay_dock
    dialog.setFloating(floating)
    settle()
    assert dialog.isVisible() and isinstance(dialog, QDockWidget)
    assert '中文照片.png' in dialog.context_label.text()
    assert dialog.findChild(QScrollArea) is not window.left_scroll
    before = window.overlay_panel.doc.copy()
    window.overlay_panel.text.setPlainText('收起时保留的中文')
    assert window.overlay_panel._text_timer.isActive()
    if close_method == "escape":
        _APP.setActiveWindow(dialog if floating else window)
        window.overlay_panel.text.setFocus()
        settle()
        QTest.keyClick(window.overlay_panel.text, Qt.Key.Key_Escape)
    else:
        dialog.close()
    assert dialog.isHidden()
    assert window.overlay_panel.selected()['text'] == '收起时保留的中文'
    assert not window.overlay_panel._text_timer.isActive()
    assert '已自定义' in window.overlay_summary.text()
    window._reveal_overlay_panel()
    window.overlay_panel.undo()
    assert window.overlay_panel.doc == before
    assert '跟随模板' in window.overlay_summary.text()


@pytest.mark.parametrize('width', [1120, 1420, 1800])
def test_editor_docks_between_settings_and_preview_and_returns_space(window, overlay_doc, width):
    window.overlay_panel.setEnabled(True)
    window.overlay_panel.set_document(overlay_doc, 'test:size')
    window.overlay_panel.select(overlay_doc['overlays'][1]['id'])
    window.resize(width, 900)
    window.show()
    settle()
    size = window.size()
    canvas_size = window.preview_label.size()
    window._reveal_overlay_panel()
    settle()
    dock = window.overlay_dock
    preview = dock.host.centralWidget()
    assert dock.isVisible() and not dock.isFloating()
    assert dock.host.dockWidgetArea(dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert window.left_scroll.mapTo(window, window.left_scroll.rect().topRight()).x() < dock.mapTo(window, QPoint()).x()
    assert dock.mapTo(window, dock.rect().topRight()).x() < preview.mapTo(window, QPoint()).x()
    assert window.size() == size
    assert dock.scroll.horizontalScrollBar().maximum() == 0
    panel = window.overlay_panel
    for control in (panel.add_button, panel.duplicate_button, panel.delete_button,
                    panel.undo_button, panel.redo_button, panel.edit_button):
        rect = control.rect().translated(control.mapTo(dock.scroll.viewport(), QPoint()))
        assert rect.left() >= 0 and rect.right() < dock.scroll.viewport().width()
    assert window.preview_label.width() >= 280
    assert window.export_current_btn.isVisible()
    dock.close()
    settle()
    assert window.preview_label.width() >= canvas_size.width()
    assert window.export_current_btn.isVisible()


def test_float_redock_and_reopen_keep_width_and_document(window, overlay_doc):
    window.resize(1800, 900)
    window.overlay_panel.setEnabled(True)
    window.overlay_panel.set_document(overlay_doc, 'test:dock')
    window.show()
    window._reveal_overlay_panel()
    settle()
    dock = window.overlay_dock
    dock.host.resizeDocks([dock], [450], Qt.Orientation.Horizontal)
    settle()
    dock_width = dock.width()
    dock.float_button.click()
    settle()
    assert dock.isFloating()
    assert dock.screen().availableGeometry().contains(dock.frameGeometry())
    assert dock.float_button.text() == '停靠'
    dock.close()
    window._reveal_overlay_panel()
    settle()
    assert dock.isFloating()  # 本次会话保留用户选择，重新打开不强行停靠。
    dock.float_button.click()
    settle()
    assert not dock.isFloating()
    assert abs(dock.width() - dock_width) <= 4
    assert window.overlay_panel.doc == overlay_doc
    dock.close()
    window._reveal_overlay_panel()
    settle()
    assert not dock.isFloating()
    assert abs(dock.width() - dock_width) <= 4


def test_template_and_photo_share_parallel_property_groups(window, overlay_doc, tmp_path, monkeypatch):
    monkeypatch.setattr(TemplateManagerDialog, '_load_preview_source', lambda self: None)
    folder = tmp_path / 'templates'
    folder.mkdir()
    save_template_payload(folder / 'test.json', default_template_payload())
    manager = TemplateManagerDialog(folder, Image.new('RGB', (800, 450)))
    try:
        assert type(manager.overlay_panel) is type(window.overlay_panel) is OverlayPanel
        for panel in (manager.overlay_panel, window.overlay_panel):
            panel.set_document(overlay_doc, 'test:parallel')
            panel.select(overlay_doc['overlays'][1]['id'])
            assert not panel.findChildren(QTabWidget)
            groups = [panel.property_groups[name] for name in ('内容', '布局', '效果')]
            layout = groups[0].parentWidget().layout()
            assert [layout.itemAt(i).widget() for i in range(3)] == groups
            assert all(not group.isHidden() for group in groups)
            assert panel.list.minimumHeight() == panel.list.maximumHeight() == 280
            panel.select(overlay_doc['overlays'][0]['id'])
            assert panel.property_groups['效果'].isHidden()
    finally:
        manager.close()
        manager.deleteLater()
        settle()


def test_focus_content_reveals_both_scroll_axes(window, overlay_doc):
    window.resize(1120, 720)
    panel = window.overlay_panel
    panel.setEnabled(True)
    panel.set_document(overlay_doc, 'test:focus')
    panel.select(overlay_doc['overlays'][1]['id'])
    window.show()
    window._reveal_overlay_panel()
    settle()
    horizontal = panel.property_scroll.horizontalScrollBar()
    assert horizontal.maximum() > 0
    horizontal.setValue(horizontal.maximum())
    window.overlay_dock.scroll.verticalScrollBar().setValue(0)
    panel.focus_content()
    settle()
    for scroll in (panel.property_scroll, window.overlay_dock.scroll):
        assert scroll.viewport().rect().contains(panel.text.mapTo(scroll.viewport(), panel.text.rect().center()))
    # 横向分组完整承载属性高度，不创建第二套纵向滚动。
    assert panel.property_scroll.verticalScrollBar().maximum() == 0


def test_property_height_tracks_layer_type_and_expanded_geometry(window, overlay_doc):
    panel = window.overlay_panel
    panel.setEnabled(True)
    panel.set_document(overlay_doc, 'test:height')
    window.resize(1420, 900)
    window.show()
    window._reveal_overlay_panel()
    for index in (1, 0, 1):
        panel.select(overlay_doc['overlays'][index]['id'])
        for expanded in (True, False):
            panel.advanced_geometry.setChecked(expanded)
            settle()
            scroll = panel.property_scroll
            assert scroll.verticalScrollBar().maximum() == 0
            assert scroll.widget().height() <= scroll.viewport().height()
            assert panel.properties.rect().contains(scroll.geometry())
            layout = panel.layout()
            property_index = layout.indexOf(panel.properties)
            following = layout.itemAt(property_index + 1).widget()
            assert following.y() > panel.properties.geometry().bottom()


def test_stage_folding_survives_reorder_and_keeps_parameters(window):
    from birdstamp.gui.editor_collapsible import CollapsibleSection

    window.show()
    settle()
    groups = window._pipeline_stage_option_groups
    assert all(isinstance(group, CollapsibleSection) for group in groups.values())
    before = window._current_pipeline_stage_enabled_map()
    value = window.max_edge_combo.currentData()
    resize = groups['resize_limit']
    resize.header_button.setFocus()
    QTest.keyClick(resize.header_button, Qt.Key.Key_Space)
    assert not resize.is_expanded()
    assert not window.max_edge_combo.isVisible()
    order = list(window._current_pipeline_stage_order())
    order[1], order[2] = order[2], order[1]
    window._set_pipeline_stage_order(order, save=False, mark_dirty=False)
    settle()
    assert not resize.is_expanded()
    assert resize.title().startswith('3.')
    assert window._current_pipeline_stage_enabled_map() == before
    resize.header_button.click()
    settle()
    assert window.max_edge_combo.isVisible()
    assert window.max_edge_combo.currentData() == value


@pytest.mark.parametrize('stage', ['export_image', 'export_gif', 'export_video'])
def test_footer_settings_collapse_preserves_values_and_bounds(window, stage):
    window.resize(1120, 720)
    window.show()
    window._set_selected_export_stage_id(stage, save=False)
    bar = window.export_action_bar
    window.gif_export_panel.fps_combo.setValue(17)
    window.video_export_panel.fps_combo.setCurrentText('24')
    settle()
    expanded_height = bar.height()
    assert bar.settings_scroll.height() <= 240
    assert window.rect().contains(bar.mapTo(window, bar.rect().bottomRight()))
    assert window.left_scroll.height() > 100
    for _ in range(3):
        bar.settings_section.header_button.click()
        settle()
        assert bar.height() < expanded_height
        assert bar.actions.isVisible()
        bar.settings_section.header_button.click()
        settle()
    assert window.gif_export_panel.fps_combo.value() == 17
    assert window.video_export_panel.fps_combo.currentText() == '24'
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    settle()
    assert bar.settings_scroll.isAncestorOf(window.dejitter_export_group)
    assert window.dejitter_export_group.isVisible()
    assert not window.image_export_group.isVisible()
    assert not window.video_export_panel.isVisible()
    window.export_tabs.setCurrentIndex(0)
    settle()
    assert window.video_export_panel.isVisible() == (stage == 'export_video')
    assert window.image_export_group.isVisible() == (stage != 'export_video')
