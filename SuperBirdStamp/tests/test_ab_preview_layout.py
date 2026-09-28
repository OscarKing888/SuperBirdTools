"""真实窗口几何回归：各模式、空图、播放条、字体和分栏变化均保持 A/B 对齐。"""
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont

from test_editor_dejitter import window, _APP
from test_editor_ab_preview import finish
from test_reference_tracking import install_sequence, wait_until
from test_dejitter_tab import setup_tab, analyze
from test_sequence_transport import populate


def geometry(widget, root):
    point = widget.mapTo(root, QPoint(0, 0))
    return point.x(), point.y(), widget.width(), widget.height()


def assert_aligned(window):
    ab = window.ab_preview
    def aligned():
        if ab.geometry_timer.isActive():
            return False
        for a, b in ((ab.a_panel.toolbar, ab.b_panel.toolbar),
                     (ab.a_panel.viewport_frame, ab.b_panel.viewport_frame),
                     (ab.preview.canvas, window.preview_label.canvas),
                     (ab.preview._status_label, window.preview_label._status_label)):
            left, right = geometry(a, window), geometry(b, window)
            if left[1] != right[1] or left[3] != right[3]:
                return False
        return True
    wait_until(aligned)
    for name in ('mode', 'name_label'):
        assert getattr(ab.a_panel, name).width() == getattr(ab.b_panel, name).width()


def test_matching_rows_on_empty_loading_single_and_compare_views(window):
    window.show()
    ab = window.ab_preview
    assert ab.a_panel.isHidden()
    assert not ab.b_panel.toolbar.isHidden()
    assert ab.b_panel.filename.parentWidget() is ab.b_panel.toolbar
    assert ab.b_panel.name_label.parentWidget() is ab.b_panel.toolbar
    assert 'transparent' in ab.b_panel.viewport_frame.styleSheet()
    assert window.auto_focus_center_check.parentWidget() is ab.b_panel.toolbar
    assert window.preview_scale_combo.parentWidget() is ab.b_panel.toolbar
    ab.enabled.setChecked(True)
    assert_aligned(window)
    assert '#2196f3' in ab.b_panel.viewport_frame.styleSheet()
    assert 'transparent' in ab.a_panel.viewport_frame.styleSheet()
    assert ab.b_panel.name_label.text() == 'B · 当前'
    assert ab.a_panel.filename.parentWidget() is ab.a_panel.toolbar
    assert ab.a_panel.name_label.parentWidget() is ab.a_panel.toolbar
    ab.activate('a')
    assert '#2196f3' in ab.a_panel.viewport_frame.styleSheet()
    assert 'transparent' in ab.b_panel.viewport_frame.styleSheet()
    assert ab.a_panel.name_label.text() == 'A · 当前'
    assert_aligned(window)
    assert not ab.a_panel.fit.isEnabled()
    assert not ab.a_panel.scale.isEnabled()
    ab.mode.setCurrentIndex(1)  # 未分析只显示占位，不隐藏局部工具栏。
    assert_aligned(window)
    assert not ab.a_panel.toolbar.isHidden()
    assert not ab.b_panel.toolbar.isHidden()
    ab.enabled.setChecked(False)
    assert ab.a_panel.isHidden()
    assert not ab.b_panel.toolbar.isHidden()
    assert 'transparent' in ab.b_panel.viewport_frame.styleSheet()
    ab.enabled.setChecked(True)
    assert_aligned(window)


def test_modes_transport_resize_and_font_changes_keep_canvas_edges_aligned(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    window.show()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    for tab, view, a_mode in ((0, 'edit', 0), (0, 'edit', 1),
                             (1, 'edit', 0), (1, 'edit', 1),
                             (1, 'result', 0), (1, 'result', 1)):
        window.export_tabs.setCurrentIndex(tab)
        window._set_dejitter_view(view)
        ab.mode.setCurrentIndex(a_mode)
        finish(ab)
        assert_aligned(window)
        assert ab.b_mode.currentText() == ('原图' if view == 'edit' else '去抖动成片')
        assert ab.b_mode.isEnabled()
        assert ab.mode.parentWidget() is ab.center.parentWidget() is ab.a_panel.toolbar
        assert ab.b_mode.parentWidget() is window.auto_focus_center_check.parentWidget() is ab.b_panel.toolbar
        assert not ab.a_panel.toolbar.isHidden() and not ab.b_panel.toolbar.isHidden()
    assert not window.show_crop_effect_check.isEnabled()
    assert not window.crop_effect_alpha_slider.isEnabled()
    ab.mode.setCurrentIndex(0)
    finish(ab)
    assert window.show_crop_effect_check.isEnabled()
    window.show_crop_effect_check.setChecked(False)
    assert not window.crop_effect_alpha_slider.isEnabled()
    assert_aligned(window)
    for size in ((1420, 920), (1900, 1000), (1120, 720)):
        window.resize(*size)
        ab.splitter.setSizes([400, 700])
        assert_aligned(window)
    font = QFont(window.font())
    font.setPointSize(font.pointSize() + 3)
    window.setFont(font)
    assert_aligned(window)
    # 状态内容长短和加载/错误提示只改变文本，不改变画布边界。
    ab.preview.set_source_mode('成片待分析 · 很长的错误提示' * 8)
    assert_aligned(window)


def test_each_viewport_zoom_fit_and_focus_lock_are_independent_without_rendering(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    window.show()
    window._refresh_preview_label()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    assert_aligned(window)
    assert ab.a_panel.fit.isEnabled() and ab.b_panel.fit.isEnabled()
    a, b = ab.preview.canvas, window.preview_label.canvas
    ab.center.setChecked(True)
    window.auto_focus_center_check.setChecked(False)
    assert a._auto_focus_center and not b._auto_focus_center
    before_b = b.current_display_scale_percent()
    index = ab.a_panel.scale.findData(200)
    ab.a_panel.scale.setCurrentIndex(index)
    ab.a_panel.scale.activated.emit(index)
    assert a.current_display_scale_percent() == pytest.approx(200)
    assert b.current_display_scale_percent() == pytest.approx(before_b)
    b.set_display_scale_percent(300)
    monkeypatch.setattr(window, '_refresh_preview_label', lambda **kw: pytest.fail('适应窗口不能重新渲染'))
    ab.a_panel.fit.click()
    assert a._zoom == pytest.approx(1)
    assert a._auto_focus_center
    assert b.current_display_scale_percent() == pytest.approx(300)
    ab.b_panel.fit.click()
    assert b._zoom == pytest.approx(1)
    assert not b._auto_focus_center


def test_b_mode_control_follows_global_tabs_without_changing_a(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    analyze(window)
    window.show()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    ab.b_mode.setCurrentIndex(0)
    ab.b_mode.activated.emit(0)
    assert window._dejitter_view == 'edit'
    assert window.dejitter_view_tabs.currentIndex() == 0
    ab.b_mode.setCurrentIndex(1)
    ab.b_mode.activated.emit(1)
    assert window._sequence_result_mode()
    assert ab.mode.currentIndex() == 0
    assert ab.path == paths[0]
    assert_aligned(window)
