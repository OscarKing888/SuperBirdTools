# -*- coding: utf-8 -*-
"""鸟体分析只在稳定选择执行；快切、A/B 和退出都保留明确的请求所有权。"""
from concurrent.futures import Future
from types import SimpleNamespace
import threading
import time

import pytest

from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._work_pool import BrowserWorkPool
from app_common.file_browser._work_policy import WorkKind
from SuperViewer.superviewer.bird_body_controller import BirdBodyController, _source_key
from SuperViewer.superviewer.qt_compat import QApplication


_APP = QApplication.instance() or QApplication([])
_BOX = (0.1, 0.2, 0.8, 0.9)


class _Panel:
    def __init__(self):
        self.box = None
        self.enabled = False
        self.paints = []

    def set_bird_box(self, box):
        self.box = box
        self.paints.append(box)

    def set_show_bird_box(self, enabled):
        self.enabled = enabled


class _Action(WorkerAction):
    def __init__(self, path, *, cancelled):
        super().__init__(cancelled=cancelled)
        self.path = path

    def execute(self):
        raise AssertionError("the GUI must not execute detection")


class _Pool:
    def __init__(self):
        self.submitted = []

    def submit_action(self, action, *, kind):
        assert isinstance(action, WorkerAction)
        assert kind == WorkKind.ANALYSIS
        future = Future()
        # 已开始的推理不能靠 Future.cancel() 假装已退出。
        future.set_running_or_notify_cancel()
        self.submitted.append((action, future))
        return future

    def cancel(self, future):
        future.cancel()

    def complete(self, index, box=_BOX, error=""):
        self.submitted[index][1].set_result(SimpleNamespace(
            result=SimpleNamespace(box=box), cancelled=False, error=error))


def _wait(predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.002)
    return bool(predicate())


@pytest.fixture
def env():
    pool = _Pool()
    controller = BirdBodyController(pool_provider=lambda: pool, debounce_ms=5,
                                    max_cache_entries=2, action_factory=_Action)
    controller.set_enabled(True)
    yield controller, pool, _Panel(), _Panel()
    controller.shutdown()
    for _action, future in pool.submitted:
        if not future.done():
            future.set_result(None)
    assert _wait(controller.is_shutdown_done)
    controller.deleteLater()


def test_fast_frames_only_read_memory_and_final_release_debounces(env, monkeypatch):
    controller, pool, panel, _ = env

    def fail_stat(*_args, **_kwargs):
        raise AssertionError("hot preview must not inspect source/XMP files")

    # 未缓存连按期间既不建 action，也不访问文件系统。
    with monkeypatch.context() as patch:
        patch.setattr("os.stat", fail_stat)
        for name in ("a.ARW", "b.ARW", "c.ARW"):
            controller.show_source(panel, name, committed=False)
            controller._submit_pending()
    assert not pool.submitted
    assert panel.box is None
    assert panel._bird_body_source_path == "c.ARW"
    controller.show_source(panel, "c.ARW", committed=True)
    controller.show_source(panel, "d.ARW", committed=True)
    assert _wait(lambda: len(pool.submitted) == 1)
    assert pool.submitted[0][0].path == "d.ARW"
    assert _wait(lambda: True)


def test_no_bird_is_a_cache_hit_during_playback_and_cache_is_bounded(env):
    controller, pool, panel, _ = env
    for index, path in enumerate(("a.jpg", "b.jpg", "c.jpg")):
        controller.show_source(panel, path)
        assert _wait(lambda: len(pool.submitted) == index + 1)
        pool.complete(index, None if index == 2 else _BOX)
        assert _wait(lambda: not controller._requests)
    assert len(controller._cache) == 2
    assert _source_key("a.jpg") not in controller._cache
    assert controller._cache[_source_key("c.jpg")].box is None
    controller.show_source(panel, "b.jpg", committed=False)
    assert panel.box == _BOX
    controller.show_source(panel, "c.jpg", committed=False)
    assert panel.box is None
    controller._submit_pending()
    assert len(pool.submitted) == 3


def test_ab_same_source_deduplicates_and_late_source_cannot_cross_viewports(env):
    controller, pool, left, right = env
    controller.show_source(left, "a.jpg")
    controller.show_source(right, "a.jpg")
    assert _wait(lambda: len(pool.submitted) == 1)
    controller.show_source(right, "b.jpg")
    assert _wait(lambda: len(pool.submitted) == 2)
    pool.complete(0)
    assert _wait(lambda: left.box == _BOX)
    assert right.box is None
    other_box = (0.3, 0.4, 0.6, 0.7)
    pool.complete(1, other_box)
    assert _wait(lambda: right.box == other_box)
    assert left.box == _BOX


def test_playback_cancels_running_detection_and_late_result_is_discarded(env):
    controller, pool, panel, _ = env
    controller.show_source(panel, "a.jpg")
    assert _wait(lambda: len(pool.submitted) == 1)
    controller.set_playback_active(True)
    action, future = pool.submitted[0]
    assert action.is_cancelled()
    assert not future.done()
    controller.show_source(panel, "a.jpg", committed=False)
    pool.complete(0)
    assert _wait(lambda: not controller._requests)
    assert panel.box is None
    assert not controller._cache
    controller.show_source(panel, "a.jpg", committed=True)
    assert _wait(lambda: len(pool.submitted) == 2)


def test_shutdown_keeps_worker_ownership_until_queued_completion(env):
    controller, pool, panel, _ = env
    controller.show_source(panel, "a.jpg")
    assert _wait(lambda: len(pool.submitted) == 1)
    controller.shutdown()
    assert not controller.is_shutdown_done()
    thread = threading.Thread(target=lambda: pool.complete(0))
    thread.start()
    thread.join()
    assert not controller.is_shutdown_done()
    assert _wait(controller.is_shutdown_done)
    assert panel.box is None


def test_clear_panel_drops_original_source_and_cancels_request(env):
    controller, pool, panel, _ = env
    controller.show_source(panel, "original.NEF")
    assert _wait(lambda: len(pool.submitted) == 1)
    controller.clear_panel(panel)
    assert panel._bird_body_source_path == ""
    assert panel.box is None
    assert pool.submitted[0][0].is_cancelled()


def test_mixed_ab_video_never_creates_analysis_or_sidecar_request(env):
    controller, pool, left, right = env
    controller.show_source(left, "a.jpg")
    controller.show_source(right, "bird.mp4")
    assert _wait(lambda: len(pool.submitted) == 1)
    assert pool.submitted[0][0].path == "a.jpg"
    assert right._bird_body_source_path == ""
    assert id(right) not in controller._views


def test_failed_write_keeps_detected_box_and_reports_error(env):
    controller, pool, panel, _ = env
    messages = []
    controller.status_changed.connect(messages.append)
    controller.show_source(panel, "a.jpg")
    assert _wait(lambda: len(pool.submitted) == 1)
    pool.complete(0, error="XMP 写入失败")
    assert _wait(lambda: panel.box == _BOX)
    assert any("XMP 写入失败" in message for message in messages)
    controller._submit_pending()
    assert len(pool.submitted) == 1


def test_invalidate_cancels_old_generation_and_revalidates_source(env):
    controller, pool, panel, _ = env
    controller.show_source(panel, "a.jpg")
    assert _wait(lambda: len(pool.submitted) == 1)
    controller.invalidate_paths(["a.jpg"])
    pool.complete(0)
    assert _wait(lambda: len(pool.submitted) == 2)
    assert panel.box is None
    pool.complete(1)
    assert _wait(lambda: panel.box == _BOX)


def test_shared_pool_executes_analysis_outside_gui_and_queues_display_update():
    gui_thread = threading.get_ident()
    worker_threads = []
    paint_threads = []

    class Action(_Action):
        def execute(self):
            worker_threads.append(threading.get_ident())
            return SimpleNamespace(result=SimpleNamespace(box=_BOX), error="", cancelled=False)

    class Panel(_Panel):
        def set_bird_box(self, box):
            paint_threads.append(threading.get_ident())
            super().set_bird_box(box)

    pool = BrowserWorkPool(4, metadata_workers=2, analysis_workers=1)
    controller = BirdBodyController(pool_provider=lambda: pool, debounce_ms=0, action_factory=Action)
    panel = Panel()
    try:
        controller.set_enabled(True)
        controller.show_source(panel, "鸟体.jpg")
        assert _wait(lambda: panel.box == _BOX)
        assert worker_threads and gui_thread not in worker_threads
        assert set(paint_threads) == {gui_thread}
    finally:
        controller.shutdown()
        pool.shutdown(timeout=2)
        assert _wait(controller.is_shutdown_done)
        assert pool.is_finished()
        controller.deleteLater()


def test_per_view_toggle_only_schedules_enabled_side_and_reuses_result(env):
    controller, pool, left, right = env
    controller.set_panel_enabled(left, False)
    controller.set_panel_enabled(right, True)
    controller.show_source(left, 'left.jpg')
    controller.show_source(right, 'right.jpg')
    assert _wait(lambda: len(pool.submitted) == 1)
    assert pool.submitted[0][0].path == 'right.jpg'
    assert not left.enabled and right.enabled
    pool.complete(0)
    assert _wait(lambda: right.box == _BOX)
    controller.set_panel_enabled(left, True)
    assert right.box == _BOX
    assert _wait(lambda: len(pool.submitted) == 2)
    controller.set_panel_enabled(right, False)
    assert right.box is None and left.enabled
    pool.complete(1)
    assert _wait(lambda: left.box == _BOX)


def test_per_view_toggle_survives_directory_clear(env):
    controller, pool, left, right = env
    controller.set_panel_enabled(left, False)
    controller.set_panel_enabled(right, True)
    controller.clear_panel(left)
    controller.clear_panel(right)
    controller.show_source(left, 'next/left.jpg')
    controller.show_source(right, 'next/right.jpg')
    assert not left.enabled and right.enabled
    assert _wait(lambda: len(pool.submitted) == 1)
    assert pool.submitted[0][0].path == 'next/right.jpg'
