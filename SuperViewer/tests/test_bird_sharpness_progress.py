"""鸟清晰度进度窗口：线程负载视图（逐线程 / 汇总）、统计与结果标签。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from collections import Counter

from PyQt6.QtWidgets import QApplication

from SuperViewer.superviewer.bird_sharpness_progress import (
    MAX_LANES,
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


def test_lane_view_shows_each_worker_up_to_limit_then_aggregates() -> None:
    view = WorkerLoadView()
    view.set_load(_load(4, 3))
    assert view.shows_lanes()
    assert view.headline() == "工作线程：3 / 4 个在工作"
    lane_height = view.height()
    view.set_load(_load(MAX_LANES + 4, 9))
    assert not view.shows_lanes()
    assert view.headline() == f"工作线程：9 / {MAX_LANES + 4} 个在工作"
    assert view.height() < lane_height
    view.set_finished()
    assert view.load().busy == 0 and not view.shows_lanes()
    assert "已全部空闲" in view.headline()
    image = view.grab()  # painting must not raise for any state
    assert not image.isNull()


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
