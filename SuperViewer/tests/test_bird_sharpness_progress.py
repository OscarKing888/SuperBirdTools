"""鸟清晰度进度窗口：1–64 线程详情、滚动、固定操作和结果标签。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from collections import Counter
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QScrollArea

from SuperViewer.superviewer.bird_sharpness_progress import (
    BirdSharpnessProgressDialog,
    WorkerLane,
    WorkerLoad,
    WorkerLoadView,
)

_APP = QApplication.instance() or QApplication([])


def _load(capacity, busy, **extra):
    now = time.monotonic()
    lanes = tuple(WorkerLane(f"DSC{i:05d}.ARW", "detect", now - 0.5) if i < busy else None for i in range(capacity))
    return WorkerLoad(capacity=capacity, lanes=lanes, **extra)


@pytest.mark.parametrize('capacity', [6, 12, 64])
def test_lane_view_shows_every_worker_including_the_last_row(capacity) -> None:
    view = WorkerLoadView()
    try:
        view.resize(600, view.height())
        view.set_load(_load(capacity, capacity - 1))
        assert view.shows_lanes()
        assert view.headline() == f"工作线程：{capacity - 1} / {capacity} 个在工作"
        assert view.LANE_H >= view.fontMetrics().height()
        assert view.rect().contains(_lane_rect(view, capacity - 1))
        assert view.height() >= capacity * view.LANE_H
        image = view.grab().toImage()
        assert not image.isNull()
        if image.size() != view.size():
            image = image.scaled(view.size())  # geometry remains in logical pixels on high-DPI hosts
        last = image.copy(_lane_rect(view, capacity - 1))
        # The idle final lane is painted too, not just included in the size hint.
        assert last.pixelColor(11, last.height() // 2) != last.pixelColor(100, 1)
        lane_height = view.height()
        view.set_finished()
        assert view.load().busy == 0 and not view.shows_lanes()
        assert "已全部空闲" in view.headline()
        assert view.height() < lane_height
    finally:
        view.close()
        view.deleteLater()


def _lane_rect(view, index):
    return QRect(0, view.HEADER_H + 6 + index * (view.LANE_H + view.LANE_GAP),
                 view.width(), view.LANE_H)


def _settle():
    _APP.processEvents()
    QTest.qWait(15)
    _APP.processEvents()


def _close_dialog(dialog):
    dialog._running = False
    dialog._tick.stop()
    dialog.close()
    dialog.deleteLater()


def _show_last_lane(dialog, capacity):
    scroll = dialog.details_scroll
    rect = _lane_rect(dialog.load_view, capacity - 1)
    point = dialog.load_view.mapTo(scroll.widget(), rect.center())
    scroll.verticalScrollBar().setValue(point.y() - scroll.viewport().height() // 2)
    _settle()
    shown = dialog.load_view.mapTo(scroll.viewport(), rect.topLeft())
    assert scroll.viewport().rect().contains(QRect(shown, rect.size()))


@pytest.mark.parametrize('capacity', [12, 64])
def test_scroll_reaches_last_worker_and_load_updates_keep_position(capacity):
    dialog = BirdSharpnessProgressDialog(None, "检测")
    try:
        dialog.set_progress(10, 100, "DSC00010.ARW")
        dialog.set_load(_load(capacity, capacity, queued=capacity, pool_threads=capacity + 3))
        dialog.resize(560, 420)
        dialog.show()
        _settle()
        assert len(dialog.findChildren(QScrollArea)) == 1
        assert dialog.details_scroll.isAncestorOf(dialog.load_view)
        assert dialog.details_scroll.verticalScrollBar().maximum() > 0
        assert dialog.details_scroll.horizontalScrollBar().maximum() == 0
        fixed = [dialog.label, dialog.bar, dialog.stats, dialog.summary, dialog.button]
        before = [widget.geometry() for widget in fixed]
        assert all(not dialog.details_scroll.isAncestorOf(widget) for widget in fixed)
        _show_last_lane(dialog, capacity)
        position = dialog.details_scroll.verticalScrollBar().value()
        assert position > 0
        for busy in (capacity - 1, capacity, capacity // 2):
            dialog.set_load(_load(capacity, busy, queued=capacity, pool_threads=capacity + 3))
            dialog._on_tick()
            _settle()
            assert dialog.details_scroll.verticalScrollBar().value() == position
        assert [widget.geometry() for widget in fixed] == before
        assert dialog.rect().contains(dialog.button.geometry())
        stops = []
        dialog.cancel_requested.connect(lambda: stops.append(True))
        dialog.button.setFocus()
        QTest.keyClick(dialog.button, Qt.Key.Key_Space)
        assert stops == [True] and not dialog.button.isEnabled()
    finally:
        _close_dialog(dialog)


def test_preparing_zero_capacity_and_finished_states_have_no_worker_rows():
    view = WorkerLoadView()
    try:
        assert not view.shows_lanes() and "准备中" in view.headline()
        preparing_height = view.height()
        view.set_load(WorkerLoad(capacity=0))
        assert not view.shows_lanes() and view.height() == preparing_height
        view.set_load(_load(64, 0))
        assert view.shows_lanes()
        view.set_finished()
        assert not view.shows_lanes() and view.height() == preparing_height
    finally:
        view.close()
        view.deleteLater()


def test_dialog_stats_pool_note_and_summary() -> None:
    dialog = BirdSharpnessProgressDialog(None, "t", running_text="正在检测鸟清晰度…")
    try:
        assert dialog.load_view.isHidden()
        dialog.set_progress(0, 100, "")
        assert dialog.label.text() == "正在检测鸟清晰度…"
        dialog._started_at = time.monotonic() - 30
        dialog.set_progress(25, 100, "DSC04393.ARW")
        stats = dialog.stats.text()
        assert "速度 50 张/分" in stats and "剩余约 1:30" in stats and "DSC04393.ARW" in stats

        dialog.set_load(_load(4, 2, queued=6, pool_threads=12, pool_thumbnail_active=2))
        note = dialog.pool_note.text()
        assert "排队 6 张" in note and "缩略图 2" in note and "浏览优先" in note
        dialog.set_load(WorkerLoad(capacity=1, lanes=(None,), shared_pool=False))
        assert dialog.pool_note.text() == "顺序检测（未使用共享线程池）"

        dialog.set_counts(Counter(sharp=3, soft=1), skipped=2, write_failures=1)
        assert dialog.summary_text() == "清晰 3，失焦 1，已跳过 2，XMP 写入失败 1"
        assert "#2e9d4f" in dialog.summary.text()  # verdict colour chip

        dialog.mark_finished("检测完成。")
        assert dialog.button.text() == "关闭"
        assert not dialog._tick.isActive()
        assert not dialog.load_view.shows_lanes()
    finally:
        dialog._running = False
        dialog.close()


def test_dialog_stays_generic_for_jobs_without_worker_load() -> None:
    """Burst-info jobs reuse the window: no worker panel, own headline, plain summary."""
    dialog = BirdSharpnessProgressDialog(None, "连拍信息")
    try:
        dialog.set_status("正在计算连拍…")
        dialog.set_progress(0, 10, "")
        dialog.set_progress(4, 10, "a.ARW")
        assert dialog.label.text() == "正在计算连拍…"
        assert dialog.load_view.isHidden() and dialog.pool_note.isHidden()
        assert dialog.details_scroll.isHidden()
        dialog.set_summary("连拍 3 组 <b>")
        assert dialog.summary_text() == "连拍 3 组 <b>"
        assert "&lt;b&gt;" in dialog.summary.text()
        dialog.mark_finished("连拍信息计算完成。")
        assert dialog.button.text() == "关闭"
    finally:
        dialog._running = False
        dialog.close()


def test_info_panel_text_names_region_and_bird_count() -> None:
    from SuperViewer.superviewer.image_info_tab_image_info import _bird_sharpness_text

    assert _bird_sharpness_text({"bird_sharpness_verdict": "sharp", "bird_sharpness_sigma": "0.66",
                                 "bird_sharpness_region": "bird", "bird_sharpness_bird_count": "3"}) \
        == "清晰（鸟体区域，模糊半径 0.66px，3 只鸟中最清晰）"
    assert _bird_sharpness_text({"bird_sharpness_verdict": "no_bird", "bird_sharpness_sigma": "0.91",
                                 "bird_sharpness_region": "focus"}) == "无鸟（焦点区域，模糊半径 0.91px）"
    # v1 sidecars (no region/sigma fields) keep their old wording
    assert _bird_sharpness_text({"bird_sharpness_verdict": "soft", "bird_sharpness_head_sigma": "1.27"}) \
        == "失焦（模糊半径 1.27px）"
    assert _bird_sharpness_text({}) == ""


def test_progress_window_shows_per_stage_timing_for_parallel_jobs() -> None:
    from bird_sharpness.timing import PhotoTiming, TimingStats

    dialog = BirdSharpnessProgressDialog(None, "检测")
    try:
        assert dialog.timing.isHidden() and dialog.timing_text() == ""
        stats = TimingStats()
        stats.setup_s = 1.5
        dialog.set_progress(0, 4, "")
        dialog._started_at -= 2.0  # the job has been running for 2 s of wall clock
        for i, name in enumerate(("a", "b", "c")):  # three photos finished by 2 workers in 2 s
            stats.add(PhotoTiming(f"{name}.ARW", {"check": 0.05, "decode": 0.6, "detect": 0.3, "measure": 0.4,
                                                   "write": 0.05}, 1.4))
        stats.add(PhotoTiming("d.ARW", {"check": 0.02}, 0.02, skipped=True))
        dialog.set_progress(4, 4, "d.ARW")
        dialog.set_timing(stats)
        assert not dialog.timing.isHidden()
        text = dialog.timing_text()
        assert "平均每张 1.40 s" in text and "累计处理 4.20 s" in text and "加载模型 1.50 s" in text
        assert "并行后每张" in text and "解码：3 次" in text and "跳过 1 张" in text
        html_text = dialog.timing.text()
        for label in ("准备", "解码", "识别", "测量", "写入"):
            assert label in html_text
        assert "复检" not in html_text  # a stage nobody ran is not listed
        dialog.mark_finished("检测完成。")
        assert dialog.elapsed_seconds() is not None and "总用时" in dialog.timing_text()
    finally:
        dialog._tick.stop()
        dialog.deleteLater()


def _qa_palette(dark):
    palette = QPalette(_APP.palette())
    values = {
        QPalette.ColorRole.Window: '#25262a' if dark else '#f5f5f5',
        QPalette.ColorRole.Base: '#1b1c20' if dark else '#ffffff',
        QPalette.ColorRole.Text: '#eeeeee' if dark else '#202020',
        QPalette.ColorRole.WindowText: '#eeeeee' if dark else '#202020',
        QPalette.ColorRole.Button: '#35363b' if dark else '#eeeeee',
        QPalette.ColorRole.ButtonText: '#eeeeee' if dark else '#202020',
        QPalette.ColorRole.PlaceholderText: '#a9adb5' if dark else '#666666',
        QPalette.ColorRole.Mid: '#737780' if dark else '#a0a0a0',
        QPalette.ColorRole.Highlight: '#4a90d9',
    }
    for role, value in values.items():
        palette.setColor(role, QColor(value))
    return palette


@pytest.mark.parametrize('capacity', [6, 12, 64])
@pytest.mark.parametrize('dark', [False, True], ids=['light', 'dark'])
@pytest.mark.parametrize('large_font', [False, True], ids=['normal', 'small-16pt'])
def test_worker_details_render_all_lanes_and_keep_actions_reachable(capacity, dark, large_font):
    """Set SUPERBIRD_TEST_WORKER_QA_DIR to save offscreen top / last-row captures."""
    from bird_sharpness.timing import PhotoTiming, TimingStats

    previous_font = QFont(_APP.font())
    font = QFont(previous_font)
    if large_font:
        font.setPointSizeF(16)
    _APP.setFont(font)
    dialog = BirdSharpnessProgressDialog(None, "鸟清晰度检测", running_text="正在检测鸟清晰度…")
    try:
        dialog.setPalette(_qa_palette(dark))
        dialog.setAutoFillBackground(True)
        dialog.set_progress(28, 100, "DSC06627.ARW")
        dialog._started_at -= 48
        dialog._refresh_stats()
        dialog.set_counts(Counter(sharp=18, usable=5, soft=5), skipped=0, write_failures=0)
        stats = TimingStats()
        for index in range(3):
            stats.add(PhotoTiming(f"DSC{index:05d}.ARW",
                                 {'decode': 1.3, 'detect': .4, 'measure': .6, 'write': .1}, 2.4))
        dialog.set_timing(stats)
        now = time.monotonic()
        stages = ('decode', 'detect', 'recheck', 'measure', 'write')
        lanes = tuple(None if i == capacity - 2 else
                      WorkerLane(f"DSC{i + 6600:05d}.ARW", stages[i % len(stages)], now - .4 - i * .2)
                      for i in range(capacity))
        dialog.set_load(WorkerLoad(capacity=capacity, lanes=lanes, queued=12,
                                   pool_threads=capacity + 3, pool_thumbnail_active=1))
        dialog.resize(560 if large_font else 640, 420 if large_font else 640)
        dialog.show()
        _settle()
        dialog._tick.stop()  # deterministic capture; updates were exercised separately
        assert dialog.load_view.LANE_H >= dialog.load_view.fontMetrics().height() + 4
        assert dialog.load_view.rect().contains(_lane_rect(dialog.load_view, capacity - 1))
        assert dialog.details_scroll.horizontalScrollBar().maximum() == 0
        assert dialog.rect().contains(dialog.button.geometry())
        assert dialog.button.geometry().top() >= dialog.details_scroll.geometry().bottom()
        output = os.environ.get('SUPERBIRD_TEST_WORKER_QA_DIR')
        prefix = f'{capacity:02d}-{"dark" if dark else "light"}-{"small-16pt" if large_font else "normal"}'
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(output) / f'{prefix}-top.png'))
        _show_last_lane(dialog, capacity)
        assert dialog.rect().contains(dialog.button.geometry())
        if output:
            assert dialog.grab().save(str(Path(output) / f'{prefix}-last.png'))
        dialog.mark_finished("检测完成。")
        _settle()
        assert not dialog.load_view.shows_lanes()
        assert dialog.button.text() == "关闭" and dialog.button.isEnabled()
        assert dialog.rect().contains(dialog.button.geometry())
    finally:
        _close_dialog(dialog)
        _APP.setFont(previous_font)
