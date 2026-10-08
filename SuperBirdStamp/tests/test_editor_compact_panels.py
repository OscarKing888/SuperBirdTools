"""固定导出入口与浮动叠加窗口的交互回归；沿用隔离配置的真实窗口。"""
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QDockWidget, QScrollArea, QTabWidget
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
    assert window.overlay_dialog.isHidden()
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
    bar.settings_button.click()
    settle()
    assert window._export_section.is_expanded()
    target = window.image_export_group
    assert window.left_scroll.viewport().rect().intersects(
        target.rect().translated(target.mapTo(window.left_scroll.viewport(), QPoint())))


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
def test_overlay_dialog_close_commits_text_and_preserves_history(window, overlay_doc, tmp_path, close_method):
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
    dialog = window.overlay_dialog
    assert dialog.isVisible() and isinstance(dialog, QDialog)
    assert not dialog.isModal()
    assert '中文照片.png' in dialog.context_label.text()
    assert dialog.findChild(QScrollArea) is not window.left_scroll
    before = window.overlay_panel.doc.copy()
    window.overlay_panel.text.setPlainText('收起时保留的中文')
    assert window.overlay_panel._text_timer.isActive()
    if close_method == "escape":
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
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


@pytest.mark.parametrize('width', [1120, 1800])
def test_editor_floats_near_left_without_resizing_preview(window, width):
    window.resize(width, 900)
    window.show()
    settle()
    canvas_size = window.preview_label.size()
    origin = window.left_scroll.mapToGlobal(QPoint(12, 12))
    window._reveal_overlay_panel()
    settle()
    dialog = window.overlay_dialog
    assert dialog.isVisible() and dialog.isWindow() and not dialog.isModal()
    assert not window.findChildren(QDockWidget)
    assert window.preview_label.size() == canvas_size
    bounds = dialog.screen().availableGeometry()
    expected_x = max(bounds.left() + 12, min(origin.x(), bounds.right() - 12 - dialog.width() + 1))
    assert abs(dialog.x() - expected_x) <= 4
    assert bounds.contains(dialog.frameGeometry())
    assert window.export_current_btn.isVisible()
    dialog.close()
    assert window.export_current_btn.isVisible()


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
