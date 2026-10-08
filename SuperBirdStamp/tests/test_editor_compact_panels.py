"""固定导出入口与独立叠加侧栏的交互回归；沿用隔离配置的真实窗口。"""
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QScrollArea

from test_overlay_editor import window, _APP
from test_overlay_layers import overlay_doc


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


def test_overlay_dock_close_commits_text_and_preserves_history(window, overlay_doc, tmp_path):
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
    dock = window.overlay_dock
    assert dock.isVisible() and dock.isFloating()
    assert '中文照片.png' in dock.context_label.text()
    assert dock.findChild(QScrollArea) is not window.left_scroll
    assert dock.scroll.horizontalScrollBar().maximum() == 0
    before = window.overlay_panel.doc.copy()
    window.overlay_panel.text.setPlainText('收起时保留的中文')
    assert window.overlay_panel._text_timer.isActive()
    dock.close()
    assert dock.isHidden()
    assert window.overlay_panel.selected()['text'] == '收起时保留的中文'
    assert not window.overlay_panel._text_timer.isActive()
    assert '已自定义' in window.overlay_summary.text()
    window._reveal_overlay_panel()
    window.overlay_panel.undo()
    assert window.overlay_panel.doc == before
    assert '跟随模板' in window.overlay_summary.text()


def test_wide_window_docks_editor_and_keeps_export_available(window):
    window.resize(1800, 900)
    window.show()
    settle()
    window._reveal_overlay_panel()
    settle()
    assert not window.overlay_dock.isFloating()
    assert window.overlay_dock.isVisible()
    assert window.export_current_btn.isVisible()
    assert window.rect().contains(window.export_action_bar.mapTo(
        window, window.export_action_bar.rect().bottomRight()))
    window.overlay_dock.close()
    assert window.export_current_btn.isVisible()
