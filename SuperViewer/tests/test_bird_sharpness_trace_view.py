"""清晰度计算过程窗口：步骤导航、对照同步、多鸟切换、右键入口、共享池执行与关闭取消。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bird_sharpness" / "tests"))

import bird_sharpness.models as bs_models  # noqa: E402
from bird_sharpness import analyzer as analyzer_mod  # noqa: E402
from bird_sharpness.analyzer import BirdSharpnessAnalyzer  # noqa: E402
from bird_sharpness.trace import AnalysisTracer  # noqa: E402
from test_bird_sharpness import _StubModels, _install_image, _no_focus, _scene  # noqa: E402

from SuperViewer.superviewer.bird_sharpness_controller import BirdSharpnessController  # noqa: E402
from SuperViewer.superviewer.bird_sharpness_trace_view import BirdSharpnessTraceDialog  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _trace(monkeypatch, birds=((450, 600, 260, 1.8), (1350, 600, 260, 0.3))):
    _install_image(monkeypatch, _scene(list(birds)))
    tracer = AnalysisTracer()
    BirdSharpnessAnalyzer(_StubModels([b[:3] for b in birds], full_w=1800),
                          focus_provider=_no_focus).analyze("x.ARW", tracer=tracer)
    return tracer.trace


def _key(dialog, key):
    dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def test_dialog_steps_navigation_compare_and_bird_switch(monkeypatch) -> None:
    trace = _trace(monkeypatch)
    dialog = BirdSharpnessTraceDialog(None, "x.ARW")
    try:
        dialog.show()
        assert dialog.stack.currentWidget() is dialog.loading
        dialog.set_trace(trace)
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content and dialog.steps)
        keys = [s.key for s in dialog.steps]
        assert keys == ["decode", "detect", "bird", "head", "edges", "distribution", "result"]
        assert len(dialog.chip_group.buttons()) == 7
        assert dialog.verdict_chip.text().strip() == "清晰"
        assert dialog.bird_combo.isVisible() and dialog.bird_combo.count() == 2
        assert "最佳" in dialog.bird_combo.currentText()

        dialog.go(0, force=True)
        assert not dialog.prev_btn.isEnabled()
        _key(dialog, Qt.Key.Key_Right)
        assert dialog.index == 1 and dialog.slider.value() == 1 and dialog.chip_group.button(1).isChecked()
        dialog.next_btn.click()
        assert dialog.index == 2
        _key(dialog, Qt.Key.Key_End)
        assert dialog.index == 6 and not dialog.next_btn.isEnabled()
        dialog.slider.setValue(4)
        assert dialog.index == 4 and dialog.step_title.text().startswith("步骤 5 / 7")
        assert dialog.metrics_grid.count() > 0 and dialog.zoom_region_btn.isEnabled()

        # compare: previous step, same coordinate frame -> zoom/pan follow
        dialog.compare_btn.setChecked(True)
        assert _wait(lambda: dialog.view_b.isVisible())
        dialog.view_a.scale(2.0, 2.0)
        dialog.view_a.view_changed.emit()
        assert dialog.view_b.zoom_factor() == pytest.approx(dialog.view_a.zoom_factor(), rel=1e-6)
        dialog.sync_btn.setChecked(False)
        dialog.view_a.scale(2.0, 2.0)
        dialog.view_a.view_changed.emit()
        assert dialog.view_b.zoom_factor() != pytest.approx(dialog.view_a.zoom_factor(), rel=1e-6)

        # switching bird keeps the same step and shows that bird's frame
        other = 1 - dialog.bird_combo.currentIndex()
        dialog.bird_combo.setCurrentIndex(other)
        assert dialog.steps[dialog.index].key == "edges"
        assert dialog.steps[dialog.index].frame == f"bird{dialog.bird_combo.currentData()}"
    finally:
        dialog.close()
        _APP.processEvents()


def test_single_bird_hides_selector_and_errors_are_shown(monkeypatch) -> None:
    trace = _trace(monkeypatch, birds=((900, 600, 300, 0.3),))
    dialog = BirdSharpnessTraceDialog(None, "y.ARW")
    try:
        dialog.set_trace(trace)
        assert not dialog.bird_combo.isVisibleTo(dialog) and not dialog.bird_label.isVisibleTo(dialog)
        other = BirdSharpnessTraceDialog(None, "z.ARW")
        other.set_error("找不到鸟体识别模型")
        assert "找不到鸟体识别模型" in other.loading.text()
        other.close()
    finally:
        dialog.close()
        _APP.processEvents()


class _FakeFileList:
    def __init__(self, pool=None):
        self.pool = pool
        self.extenders = []

    def add_file_context_menu_extender(self, callback):
        self.extenders.append(callback)

    def background_work_pool(self):
        return self.pool

    def _resolve_source_path_for_action(self, path):
        return path


@pytest.fixture
def stub_trace_env(monkeypatch):
    monkeypatch.setattr(bs_models, "check_runtime", lambda: None)
    _install_image(monkeypatch, _scene([(900, 600, 300, 0.3)]))
    gate = threading.Event()
    gate.set()
    analyzer = BirdSharpnessAnalyzer(_StubModels([(900, 600, 300)], full_w=1800), focus_provider=_no_focus)
    original = analyzer.analyze

    def gated(path, **kw):
        assert gate.wait(5)
        return original(path, **kw)

    analyzer.analyze = gated
    return analyzer, gate


@pytest.mark.parametrize("use_pool", [True, False])
def test_context_menu_opens_trace_computed_as_worker_action(stub_trace_env, monkeypatch, use_pool) -> None:
    from app_common.file_browser._work_pool import BrowserWorkPool

    analyzer, _gate = stub_trace_env
    pool = BrowserWorkPool(4, 2, analysis_workers=1) if use_pool else None
    window = QWidget()
    file_list = _FakeFileList(pool)
    controller = BirdSharpnessController(window, file_list)
    monkeypatch.setattr(controller, "analyzer", lambda: analyzer)
    try:
        menu = QMenu()
        controller.extend_file_menu(menu, ["/photos/a.ARW", "/photos/b.ARW"])
        action = next(a for a in menu.actions() if a.text() == "查看清晰度计算过程…")
        opened = []
        original_show = controller.show_trace
        monkeypatch.setattr(controller, "show_trace", lambda p: opened.append(original_show(p)))
        action.trigger()
        dialog = opened[0]
        assert dialog.path == os.path.normpath("/photos/a.ARW") or dialog.path == "/photos/a.ARW"
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content, timeout=20)
        assert [s.key for s in dialog.steps][-1] == "result"
        assert not Path("/photos/a.xmp").exists()  # read-only
        if pool is not None:
            assert pool.snapshot()["completed"] >= 1
        dialog.close()
        assert _wait(controller.is_shutdown_done)
    finally:
        controller.request_shutdown()
        if pool is not None:
            pool.shutdown(timeout=5)
        window.deleteLater()
        _APP.processEvents()


def test_closing_window_cancels_pending_trace_and_shutdown_waits(stub_trace_env, monkeypatch) -> None:
    from app_common.file_browser._work_pool import BrowserWorkPool

    analyzer, gate = stub_trace_env
    gate.clear()
    pool = BrowserWorkPool(4, 2, analysis_workers=1)
    window = QWidget()
    controller = BirdSharpnessController(window, _FakeFileList(pool))
    monkeypatch.setattr(controller, "analyzer", lambda: analyzer)
    try:
        running = controller.show_trace("/photos/a.ARW")
        queued = controller.show_trace("/photos/b.ARW")  # waits behind the 1 analysis slot
        assert _wait(lambda: pool.snapshot()["analysis_active"] == 1)
        queued.close()
        assert _wait(lambda: pool.snapshot()["analysis_queued"] == 0)
        controller.request_shutdown()
        assert not controller.is_shutdown_done()  # the running trace still owns a pool thread
        gate.set()
        assert _wait(controller.is_shutdown_done, timeout=20)
    finally:
        gate.set()
        pool.shutdown(timeout=5)
        window.deleteLater()
        _APP.processEvents()
