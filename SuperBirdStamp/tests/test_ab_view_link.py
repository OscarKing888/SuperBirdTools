"""真实鼠标视野联动：双向缩放/平移、异步换帧、退出及独立焦点锁定。"""
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPixmap, QWheelEvent
from PyQt6.QtTest import QTest

from test_editor_dejitter import window, _APP
from test_reference_tracking import install_sequence
from test_editor_ab_preview import finish
from birdstamp.gui.edit_modes import EDIT_MODE_NONE


def setup(window, monkeypatch):
    install_sequence(window, monkeypatch)
    window.show()
    window._refresh_preview_label()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    for canvas in (ab.preview.canvas, window.preview_label.canvas):
        canvas.set_edit_mode(EDIT_MODE_NONE)
    ab.linked.click()
    return ab, ab.preview.canvas, window.preview_label.canvas


def wheel(canvas):
    pos = QPointF(canvas.contentsRect().center())
    event = QWheelEvent(pos, canvas.mapToGlobal(pos), QPoint(), QPoint(0, 240),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    _APP.sendEvent(canvas, event)


def pan(canvas):
    pos = canvas.contentsRect().center()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=pos)
    end = QPointF(pos + QPoint(25, 15))
    event = QMouseEvent(QEvent.Type.MouseMove, end, canvas.mapToGlobal(end),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    _APP.sendEvent(canvas, event)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end.toPoint())


def assert_linked(first, second):
    assert first.viewport_state()[0] == pytest.approx(second.viewport_state()[0])
    assert first.viewport_state()[1] == pytest.approx(second.viewport_state()[1])


def test_enabling_link_preserves_both_views_until_next_interaction(window, monkeypatch):
    ab, a, b = setup(window, monkeypatch)
    ab.linked.click()
    a.set_display_scale_percent(a._fit_scale() * 250)
    b.set_display_scale_percent(b._fit_scale() * 400)
    pan(b)
    before_a, before_b = a.viewport_state(), b.viewport_state()
    assert before_a != before_b

    ab.linked.click()
    assert a.viewport_state() == before_a
    assert b.viewport_state() == before_b
    wheel(a)
    assert_linked(a, b)


def test_zoom_pan_fit_and_decoded_resolution_upgrade_are_linked_both_ways(window, monkeypatch):
    ab, a, b = setup(window, monkeypatch)
    regions = window._dejitter_reference_regions
    monkeypatch.setattr(window, '_refresh_preview_label', lambda **kw: pytest.fail('视野联动不应重新渲染'))
    for source, target in ((a, b), (b, a)):
        source.set_display_scale_percent(source._fit_scale() * 400)
        wheel(source)
        assert_linked(source, target)
        before = source.viewport_state()
        pan(source)
        assert source.viewport_state()[1] != before[1]
        assert_linked(source, target)
    # 新帧到达/分辨率升级继承视野，不能把另一侧重置回适应窗口。
    before = b.viewport_state()
    pixmap = QPixmap(1000, 500)
    pixmap.fill(QColor('blue'))
    a.set_source_pixmap(pixmap, reset_view=True)
    assert_linked(a, b)
    assert b.viewport_state() == before
    ab.a_panel.fit.click()
    assert a.viewport_state()[0] == pytest.approx(1)
    assert b.viewport_state()[0] == pytest.approx(1)
    assert window._dejitter_reference_regions == regions
    assert not window._dejitter_manual_matches


def test_unlink_and_focus_center_restore_independent_viewports(window, monkeypatch):
    ab, a, b = setup(window, monkeypatch)
    a.set_display_scale_percent(a._fit_scale() * 400)
    ab.linked.click()
    before = b.viewport_state()
    wheel(a)
    pan(a)
    assert b.viewport_state() == before
    ab.center.setChecked(True)
    ab.linked.click()
    assert not ab.center.isChecked() and not window.auto_focus_center_check.isChecked()
    window.auto_focus_center_check.setChecked(True)
    assert not ab.linked.isChecked()
    assert b._auto_focus_center
    ab.linked.click()
    ab.enabled.setChecked(False)
    before = b.viewport_state()
    wheel(a)
    assert b.viewport_state() == before
