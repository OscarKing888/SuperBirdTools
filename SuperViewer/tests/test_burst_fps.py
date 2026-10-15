"""FPS 输入到实际连拍分组：小数、高速、边界、菜单及确认/取消。"""
import os

import pytest
from PyQt6.QtCore import QLocale, QPoint, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QScrollArea, QWidget

from app_common import burst_info
from SuperViewer.superviewer import burst_info_controller as controller_module
from SuperViewer.superviewer.burst_fps import (
    ALL_CAMERA_FPS, CAMERA_FPS_PRESETS, DEFAULT_BURST_FPS,
    MAX_BURST_FPS, MIN_BURST_FPS, fps_to_max_gap_ms,
)
from SuperViewer.superviewer.burst_info_controller import BurstInfoController, BurstInfoOptionsDialog

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("fps", [0.2, 0.7, 2.5, 4, 12.5, 16.7, 23.97, 30, 60, 120, 195, 1000])
def test_fps_threshold_keeps_boundary_and_splits_slower_shots(fps, tmp_path):
    # Real epoch timestamps exercise float cancellation, especially 120/195 FPS.
    interval = 1.0 / fps
    offsets = [i * interval for i in range(4)]
    next_start = offsets[-1] + 2 * interval
    offsets.extend(next_start + i * interval for i in range(4))
    times = {
        os.path.normpath(str(tmp_path / f"鸟_{i}.jpg")): burst_info.CaptureTime(1_700_000_000 + t, True)
        for i, t in enumerate(offsets)
    }
    plan, stats = burst_info.plan_bursts(times, times, max_gap_ms=fps_to_max_gap_ms(fps))
    assert stats.groups == 2 and stats.burst_shots == 8
    assert list(plan.values()) == [(group, pos) for group in (1, 2) for pos in range(1, 5)]


@pytest.mark.parametrize("fps", [0, -1, 0.19, 1000.01, float("nan"), float("inf"), -float("inf")])
def test_reject_invalid_fps(fps):
    with pytest.raises(ValueError, match="FPS"):
        fps_to_max_gap_ms(fps)


def test_default_and_seconds_only_fallback_are_preserved(tmp_path):
    assert DEFAULT_BURST_FPS == 4
    assert fps_to_max_gap_ms(DEFAULT_BURST_FPS) == burst_info.DEFAULT_MAX_GAP_MS
    times = {str(tmp_path / f"{i}.jpg"): burst_info.CaptureTime(1_700_000_000 + i, False) for i in range(4)}
    _, stats = burst_info.plan_bursts(times, times, max_gap_ms=fps_to_max_gap_ms(195))
    assert stats.groups == 1 and stats.burst_shots == 4


@pytest.fixture
def dialog():
    widget = BurstInfoOptionsDialog(None, "照片/连拍目录", True, DEFAULT_BURST_FPS, 4)
    widget.fps_spin.setLocale(QLocale.c())
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def test_presets_cover_brands_and_reach_the_editor(dialog):
    def leaves(menu):
        for action in menu.actions():
            if action.menu():
                yield from leaves(action.menu())
            elif not action.isSeparator():
                yield action

    brand_names = {label for label, _ in CAMERA_FPS_PRESETS}
    assert len(brand_names) == 12
    assert {12.5, 16.7, 75, 90, 100, 120, 195}.issubset(ALL_CAMERA_FPS)
    menus = {action.text(): action.menu() for action in dialog.presets_menu.actions() if action.menu()}
    assert brand_names.issubset(menus)
    for brand, speeds in CAMERA_FPS_PRESETS:
        assert tuple(sorted(set(speeds))) == speeds
        actions = list(leaves(menus[brand]))
        assert tuple(a.data() for a in actions) == speeds
        for action in actions:
            action.trigger()
            fps, count = dialog.values()
            assert fps == action.data() and count == 4
            assert MIN_BURST_FPS <= fps <= MAX_BURST_FPS
    all_menu = menus["全部档位（按速度）"]
    all_values = [a.data() for a in leaves(all_menu)]
    assert tuple(all_values) == ALL_CAMERA_FPS


def test_custom_keyboard_input_and_preset_checks(dialog):
    dialog.show()
    dialog.fps_spin.setFocus()
    dialog.fps_spin.selectAll()
    QTest.keyClicks(dialog.fps_spin, "23.97")
    QTest.keyClick(dialog.fps_spin, Qt.Key.Key_Tab)
    assert dialog.values() == (23.97, 4)
    assert "41.719 ms" in dialog.gap_hint.text()
    dialog._sync_preset_checks()
    assert not any(a.isChecked() for a in dialog._preset_actions)
    dialog.fps_spin.setValue(120)
    dialog._sync_preset_checks()
    assert all(a.isChecked() == (a.data() == 120) for a in dialog._preset_actions)
    # Closing/reopening menus does not change the custom value or the group size.
    dialog.fps_spin.setValue(7.25)
    dialog.count_spin.setValue(6)
    for _ in range(3):
        dialog.presets_menu.aboutToShow.emit()
        dialog.presets_menu.hide()
    assert dialog.values() == (7.25, 6)


def test_controller_commits_fps_only_on_accept_and_passes_full_precision(monkeypatch):
    window = QWidget()
    controller = BurstInfoController(window, object())
    jobs = []
    monkeypatch.setattr(controller, "start", lambda job: jobs.append(job) or True)
    seen = []
    results = iter([(QDialog.DialogCode.Accepted, 120.0, 6), (QDialog.DialogCode.Rejected, 195.0, 9)])

    def interact(dialog):
        seen.append(dialog.values())
        outcome, fps, count = next(results)
        dialog.fps_spin.setValue(fps)
        dialog.count_spin.setValue(count)
        return outcome

    monkeypatch.setattr(controller_module.BurstInfoOptionsDialog, "exec", interact)
    try:
        assert controller.prompt_and_start("照片", True)
        assert not controller.prompt_and_start("别的目录", False)
        assert seen == [(4.0, 4), (120.0, 6)]
        assert controller.fps == 120 and controller.min_count == 6
        assert len(jobs) == 1
        assert jobs[0].max_gap_ms == pytest.approx(8.333333333333334)
        assert jobs[0].recursive and jobs[0].directory == "照片" and jobs[0].min_count == 6
    finally:
        window.deleteLater()
        _APP.processEvents()


def test_enter_accepts_and_escape_cancels(dialog):
    dialog.fps_spin.setValue(16.7)
    buttons = dialog.findChild(QDialogButtonBox)
    QTimer.singleShot(0, lambda: QTest.keyClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.Key.Key_Return))
    assert dialog.exec() == QDialog.DialogCode.Accepted
    assert dialog.values() == (16.7, 4)
    QTimer.singleShot(0, lambda: QTest.keyClick(dialog, Qt.Key.Key_Escape))
    assert dialog.exec() == QDialog.DialogCode.Rejected


def test_preset_menu_supports_keyboard_without_starting_calculation(dialog):
    dialog.show()
    dialog.presets_menu.popup(dialog.mapToGlobal(QPoint(0, 0)))
    common_action = dialog.presets_menu.actions()[0]
    dialog.presets_menu.setActiveAction(common_action)
    QTest.keyClick(dialog.presets_menu, Qt.Key.Key_Right)
    common = common_action.menu()
    common.setActiveAction(next(a for a in common.actions() if a.data() == 20))
    QTest.keyClick(common, Qt.Key.Key_Return)
    assert dialog.values() == (20.0, 4)
    assert dialog.isVisible() and dialog.result() != QDialog.DialogCode.Accepted


def test_large_font_small_window_scrolls_without_compressing_text():
    original = _APP.font()
    font = _APP.font()
    font.setPointSize(24)
    _APP.setFont(font)
    dialog = BurstInfoOptionsDialog(None, "照片/连拍目录", True, DEFAULT_BURST_FPS, 4)
    try:
        dialog.resize(420, 260)
        dialog.show()
        _APP.processEvents()
        for label in dialog.findChildren(QLabel):
            if label.wordWrap():
                assert label.hasHeightForWidth()
                assert label.height() >= label.heightForWidth(label.width())
                assert label.parentWidget().rect().contains(label.geometry())
        scroll = dialog.findChild(QScrollArea)
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.ensureWidgetVisible(dialog.count_spin)
        _APP.processEvents()
        count_top = dialog.count_spin.mapTo(scroll.viewport(), QPoint(0, 0))
        assert 0 <= count_top.y() < scroll.viewport().height()
        buttons = dialog.findChild(QDialogButtonBox)
        assert dialog.rect().contains(buttons.geometry())
        assert dialog.values() == (4.0, 4)
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.setFont(original)
        _APP.processEvents()
