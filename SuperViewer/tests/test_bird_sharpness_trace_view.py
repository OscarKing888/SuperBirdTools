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
from PyQt6.QtCore import QEvent, QObject, pyqtSignal
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
        # several birds: every bird's steps in turn, after an overview of all birds
        per_bird = ["bird", "head", "edges", "distribution"]
        assert dialog.bird_combo.isVisible() and dialog.bird_combo.count() == 3
        assert dialog.bird_combo.currentText() == "全部鸟（逐只）"
        assert [s.key for s in dialog.steps] == ["decode", "detect", "birds", *per_bird, *per_bird, "result"]
        assert [s.bird for s in dialog.steps[3:11]] == [0] * 4 + [1] * 4
        assert len(dialog.chip_group.buttons()) == 12 and "#2" in dialog.chip_group.button(8).text()
        overview = dialog.steps[2]
        assert overview.image.ndim == 3 and len(overview.bird_rows) == 2
        assert sum("最佳" in row.label for row in overview.bird_rows) == 1
        dialog.go(9, force=True)
        assert "鸟 #2 · 边缘筛选" in dialog.step_title.text()

        # one bird only: its own steps
        best = next(i for i in range(dialog.bird_combo.count()) if "最佳" in dialog.bird_combo.itemText(i))
        dialog.bird_combo.setCurrentIndex(best)
        keys = [s.key for s in dialog.steps]
        assert keys == ["decode", "detect", "birds", *per_bird, "result"]
        assert len(dialog.chip_group.buttons()) == 8
        assert dialog.steps[dialog.index].key == "edges"  # kept the step
        assert dialog.verdict_chip.text().strip() == "清晰"

        dialog.go(0, force=True)
        assert not dialog.prev_btn.isEnabled()
        _key(dialog, Qt.Key.Key_Right)
        assert dialog.index == 1 and dialog.slider.value() == 1 and dialog.chip_group.button(1).isChecked()
        dialog.next_btn.click()
        assert dialog.index == 2
        _key(dialog, Qt.Key.Key_End)
        assert dialog.index == 7 and not dialog.next_btn.isEnabled()
        dialog.slider.setValue(5)
        assert dialog.index == 5 and dialog.step_title.text().startswith("步骤 6 / 8")
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
        other = 1 if best == 2 else 2
        dialog.bird_combo.setCurrentIndex(other)
        assert dialog.steps[dialog.index].key == "edges"
        assert dialog.steps[dialog.index].frame == f"bird{dialog.bird_combo.currentData()}"
        # back to all birds: lands on that bird's same step
        viewed = dialog.bird_combo.currentData()
        dialog.bird_combo.setCurrentIndex(0)
        assert dialog.steps[dialog.index].key == "edges" and dialog.steps[dialog.index].bird == viewed
    finally:
        dialog.close()
        _APP.processEvents()


def test_bird_list_shows_swatches_and_hover_highlights_the_box(monkeypatch) -> None:
    trace = _trace(monkeypatch)
    dialog = BirdSharpnessTraceDialog(None, "x.ARW")
    try:
        dialog.show()
        dialog.set_trace(trace)
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content and dialog.steps)
        birds = dialog.bird_list
        for key in ("detect", "birds", "result"):  # every bird list uses the same widget
            dialog.go(next(i for i, s in enumerate(dialog.steps) if s.key == key), force=True)
            step = dialog.steps[dialog.index]
            assert birds.isVisibleTo(dialog) and birds.rows == step.bird_rows and len(birds.cells) == 2
            assert dialog.view_a.highlight_rect() is None
            swatch, label, value = birds.cells[1]
            assert step.bird_rows[1].color in swatch.text() and label.text() == step.bird_rows[1].label
            QApplication.sendEvent(value, QEvent(QEvent.Type.Enter))
            rect = dialog.view_a.highlight_rect()
            assert rect is not None and (rect.left(), rect.top()) == pytest.approx(step.bird_rows[1].box[:2])
            assert all(w.styleSheet() for w in birds.cells[1]) and not birds.cells[0][0].styleSheet()
            QApplication.sendEvent(value, QEvent(QEvent.Type.Leave))
            assert dialog.view_a.highlight_rect() is None and not label.styleSheet()
            QApplication.sendEvent(swatch, QEvent(QEvent.Type.Enter))
            assert dialog.view_a.highlight_rect() is not None and birds.hovered_row == 1
        # a step without birds hides the list and drops the highlight; old labels no longer react
        stale = birds.cells[0][2]
        dialog.go(0, force=True)
        assert not birds.isVisibleTo(dialog) and dialog.view_a.highlight_rect() is None
        QApplication.sendEvent(stale, QEvent(QEvent.Type.Enter))
        assert dialog.view_a.highlight_rect() is None and birds.hovered_row is None
    finally:
        dialog.close()
        _APP.processEvents()


def test_compare_view_highlights_the_hovered_bird(monkeypatch) -> None:
    trace = _trace(monkeypatch)
    dialog = BirdSharpnessTraceDialog(None, "x.ARW")
    try:
        dialog.show()
        dialog.set_trace(trace)
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content and dialog.steps)
        index = {s.key: i for i, s in reversed(list(enumerate(dialog.steps)))}  # first step of each key
        dialog.compare_btn.setChecked(True)
        assert _wait(lambda: dialog.view_b.isVisible())
        dialog.go(len(dialog.steps) - 1, force=True)  # 结论
        result = dialog.steps[dialog.index]

        def compare_with(key):
            dialog.compare_combo.setCurrentIndex(dialog.compare_combo.findData(index[key]))
            return dialog.steps[index[key]]

        def hover(r):
            QApplication.sendEvent(dialog.bird_list.cells[r][2], QEvent(QEvent.Type.Enter))

        def leave(r):
            QApplication.sendEvent(dialog.bird_list.cells[r][2], QEvent(QEvent.Type.Leave))

        # other frame, same bird listed there: its tile in the 逐只鸟 overview
        overview = compare_with("birds")
        assert dialog.view_b.highlight_rect() is None
        hover(1)
        rect = dialog.view_b.highlight_rect()
        tile = next(r for r in overview.bird_rows if r.bird == result.bird_rows[1].bird)
        assert rect is not None and (rect.left(), rect.top()) == pytest.approx(tile.box[:2])
        # switching the compare step while hovering follows the bird
        detect = compare_with("detect")
        box = next(r for r in detect.bird_rows if r.bird == result.bird_rows[1].bird).box
        assert (dialog.view_b.highlight_rect().left(), dialog.view_b.highlight_rect().top()) == \
            pytest.approx(box[:2], abs=1.0)
        # same frame without a bird list: the main view's box
        compare_with("decode")
        assert dialog.view_b.highlight_rect() == dialog.view_a.highlight_rect()
        # a bird's own crop cannot place the box
        compare_with("bird")
        assert dialog.view_b.highlight_rect() is None and dialog.view_a.highlight_rect() is not None
        compare_with("decode")
        leave(1)
        assert dialog.view_b.highlight_rect() is None and dialog.view_a.highlight_rect() is None
        # changing the main step drops both highlights
        hover(0)
        assert dialog.view_b.highlight_rect() is not None
        dialog.go(0, force=True)
        assert dialog.view_b.highlight_rect() is None and dialog.view_a.highlight_rect() is None
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
    decodes = []
    installed = analyzer_mod.load_analysis_image
    monkeypatch.setattr(analyzer_mod, "load_analysis_image", lambda p: decodes.append(p) or installed(p))
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
        assert dialog.source_combo.currentData() == "raw"  # starts on RAW; switch inside the window
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content, timeout=20)
        assert [s.key for s in dialog.steps][-1] == "result"
        assert not Path("/photos/a.xmp").exists()  # read-only
        if pool is not None:
            assert pool.snapshot()["completed"] >= 1
        assert len(decodes) == 1 and len(dialog.image_cache) == 1
        # 按此参数重新计算: same window, other parameters, no second RAW decode
        dialog.max_birds_spin.setValue(1)
        dialog.rerun_btn.click()
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content, timeout=20)
        assert len(decodes) == 1
        assert dict(dialog.steps[0].metrics)["解码耗时"].startswith("复用本窗口已解码的图像")
        cache = dialog.image_cache
        dialog.close()
        assert len(cache) == 0  # freed with the window
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


def _source_metric(dialog) -> str:
    return dict(dialog.steps[0].metrics)["图像来源"]


def test_switching_image_source_recomputes_from_those_pixels(stub_trace_env, monkeypatch) -> None:
    from bird_sharpness import image_source as image_source_mod
    from bird_sharpness.image_source import AnalysisImage, SOURCE_JPEG

    analyzer, _gate = stub_trace_env
    stub = analyzer_mod.load_analysis_image("x")
    jpeg_calls = []

    def fake_jpeg(path):
        jpeg_calls.append(path)
        return AnalysisImage(stub.rgb8, stub.gray, False, None, SOURCE_JPEG, path)

    monkeypatch.setattr(image_source_mod, "load_embedded_jpeg", fake_jpeg)
    window = QWidget()
    controller = BirdSharpnessController(window, _FakeFileList(None))
    monkeypatch.setattr(controller, "analyzer", lambda: analyzer)
    try:
        dialog = controller.show_trace("/photos/a.ARW", "raw")
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content, timeout=20)
        assert "RAW" in _source_metric(dialog) or "位图" in _source_metric(dialog)
        dialog.source_combo.setCurrentIndex(dialog.source_combo.findData(SOURCE_JPEG))
        assert dialog.stack.currentWidget() is dialog.loading  # recomputing
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content, timeout=20)
        assert "相机内嵌 JPEG" in _source_metric(dialog) and jpeg_calls
        dialog.close()
        assert _wait(controller.is_shutdown_done)
    finally:
        controller.request_shutdown()
        window.deleteLater()
        _APP.processEvents()


class _FakeDenoise(QObject):
    output_ready = pyqtSignal(str, str)
    batch_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.busy = False
        self.started = []

    def start_for_paths(self, paths):
        self.started.append(list(paths))
        return True


@pytest.mark.parametrize("succeeds", [True, False])
def test_denoised_source_denoises_first_when_missing(stub_trace_env, monkeypatch, succeeds) -> None:
    from bird_sharpness import image_source as image_source_mod
    from bird_sharpness.image_source import AnalysisImage, SOURCE_DENOISED

    analyzer, _gate = stub_trace_env
    stub = analyzer_mod.load_analysis_image("x")
    found = {}
    monkeypatch.setattr(image_source_mod, "load_image_file",
                        lambda p, *, source, camera_crop=None: AnalysisImage(stub.rgb8, stub.gray, False, camera_crop,
                                                                             source, p))
    window = QWidget()
    controller = BirdSharpnessController(window, _FakeFileList(None))
    monkeypatch.setattr(controller, "analyzer", lambda: analyzer)
    monkeypatch.setattr(controller, "_denoised_lookup", lambda path: found.get(os.path.normpath(path)))
    denoise = _FakeDenoise()
    controller.set_denoise_controller(denoise)
    try:
        dialog = controller.show_trace("/photos/a.ARW", SOURCE_DENOISED)
        assert _wait(lambda: denoise.started, timeout=20)
        assert denoise.started == [[dialog.path]] and "正在降噪" in dialog.loading.text()
        if succeeds:
            found[os.path.normpath(dialog.path)] = type("Found", (), {"path": "/out/a_denoised.tif", "camera_crop": None})
            denoise.output_ready.emit(dialog.path, "/out/a_denoised.tif")
            assert _wait(lambda: dialog.stack.currentWidget() is dialog.content, timeout=20)
            assert "降噪成片" in _source_metric(dialog)
            assert dict(dialog.steps[0].metrics)["图像文件"] == "a_denoised.tif"
        else:
            denoise.batch_finished.emit()
            assert "没有生成成片" in dialog.loading.text()
        dialog.close()
        assert _wait(controller.is_shutdown_done)
    finally:
        controller.request_shutdown()
        window.deleteLater()
        _APP.processEvents()


def test_params_tab_reruns_this_window_and_saves_defaults(monkeypatch) -> None:
    from app_common import superviewer_user_options as opts

    trace = _trace(monkeypatch)
    dialog = BirdSharpnessTraceDialog(None, "x.ARW", params={"max_birds": 4, "edge_estimator": "standard"})
    rerun, saved = [], []
    dialog.params_changed.connect(lambda d: rerun.append(dict(d.params)))
    dialog.save_defaults_requested.connect(lambda d: saved.append(d.selected_params()))
    try:
        assert [dialog.side_tabs.tabText(i) for i in range(dialog.side_tabs.count())] == ["步骤", "参数"]
        assert dialog.max_birds_spin.value() == 4 and dialog.estimator_combo.currentData() == "standard"
        dialog.set_trace(trace)
        assert "标准" in dialog.params_status.text() and "上限 4 只" in dialog.params_status.text()
        dialog.max_birds_spin.setValue(0)
        dialog.estimator_combo.setCurrentIndex(dialog.estimator_combo.findData("dense"))
        assert "第 40 百分位" in dialog.estimator_note.text()
        # no-bird tiling: defaults shown, centre controls follow the manual-focus switch
        form = dialog.tile_form
        assert (form.full_tile.value(), form.mf_tile.value(), form.mf_center_percent.value()) == (1024, 256, 50)
        assert "全图 1024 px" in dialog.params_status.text()
        form.mf_tile.setValue(128)
        form.mf_sharpest.setValue(20)
        form.mf_center.setChecked(False)
        assert not form.mf_tile.isEnabled() and form.full_tile.isEnabled()
        form.mf_center.setChecked(True)
        # enhanced bird search + models: the same form as 设置 → 鸟清晰度
        pf = dialog.params_form
        assert not pf.enh_grid.isEnabled() and "就绪" in pf.models_status.text()
        pf.enh_mode.setCurrentIndex(pf.enh_mode.findData("manual"))
        pf.enh_conf.setValue(60)
        assert pf.enh_grid.isEnabled()
        from bird_sharpness.params import AnalysisParams

        expected = {**AnalysisParams().as_params(), "edge_estimator": "dense", "mf_tile": 128,
                    "mf_sharpest_percent": 20, "enh_mode": "manual", "enh_min_conf_percent": 60}
        dialog.rerun_btn.click()
        assert rerun == [expected]
        assert dialog.stack.currentWidget() is dialog.loading
        dialog.save_defaults_btn.click()
        assert saved == [expected]
        # a model that is not installed: offered for download first, no rerun when declined
        import SuperViewer.superviewer.bird_sharpness_trace_view as tv

        offered = []
        monkeypatch.setattr(tv, "missing_models", lambda params: ["yolo26x-seg.pt"])
        monkeypatch.setattr(tv, "download_models", lambda parent, names: offered.append(list(names)) or False)
        dialog.rerun_btn.click()
        assert offered == [["yolo26x-seg.pt"]] and len(rerun) == 1
    finally:
        dialog.deleteLater()
        opts.apply_runtime_user_options(None)


def test_trace_parameters_stay_in_their_window(monkeypatch, tmp_path) -> None:
    """A trace window's parameters never change the shared analyzer used for batch detection."""
    from app_common import superviewer_user_options as opts

    controller = BirdSharpnessController(QWidget(), _FakeFileList(None))
    shared = controller.analyzer()
    dialog = BirdSharpnessTraceDialog(None, "x.ARW", params={"max_birds": 2, "edge_estimator": "dense"})
    seen, tiles = [], []
    dialog.tile_form.mf_tile.setValue(64)
    dialog.params = dialog.selected_params()

    class _Action:
        def __init__(self, analyzer, *a, **k):
            seen.append((analyzer.max_birds, analyzer.edge_estimator, analyzer is shared))
            tiles.append(analyzer.tile_options)

        def execute(self):
            from types import SimpleNamespace

            return SimpleNamespace(needs_denoise=False, trace=None, cancelled=True, error="")

    import bird_sharpness.actions as actions_mod

    monkeypatch.setattr(actions_mod, "BirdSharpnessTraceAction", _Action)
    stored = []
    monkeypatch.setattr(opts, "save_user_options", lambda data, path=None: stored.append(dict(data)) or opts.normalize_user_options(data))
    monkeypatch.setattr(controller, "_show_message", lambda text: None)
    try:
        controller._run_trace(dialog, "raw")
        assert seen == [(2, "dense", False)]
        assert tiles[0].mf_tile == 64 and shared.tile_options.mf_tile == 256
        assert (shared.max_birds, shared.edge_estimator) == (0, "standard")
        dialog.max_birds_spin.setValue(5)
        controller._save_trace_params(dialog)
        assert stored and stored[-1][opts.KEY_BIRD_SHARPNESS_MAX_BIRDS] == 5
        assert stored[-1][opts.KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR] == "dense"
        assert stored[-1][opts.KEY_BIRD_SHARPNESS_MF_TILE] == 64 and stored[-1][opts.KEY_BIRD_SHARPNESS_MF_CENTER] == 1
        assert controller.analyzer().max_birds == 5 and controller.analyzer().edge_estimator == "dense"
        assert controller.analyzer().tile_options.mf_tile == 64
    finally:
        opts.apply_runtime_user_options(None)
        for request in list(controller._trace_requests):
            controller._cancel_trace(request)
        controller.request_shutdown()
        _wait(controller.is_shutdown_done)
        dialog.deleteLater()


def _shown_pixels(view) -> np.ndarray:
    image = view._item.pixmap().toImage().convertToFormat(view._item.pixmap().toImage().Format.Format_RGB888)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    return np.frombuffer(ptr, np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width() * 3] \
        .reshape(image.height(), image.width(), 3).copy()


def test_focal_plane_toggle_tints_focused_pixels_red(monkeypatch) -> None:
    trace = _trace(monkeypatch)
    dialog = BirdSharpnessTraceDialog(None, "x.ARW")
    try:
        dialog.show()
        dialog.set_trace(trace)
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content and dialog.steps)

        def red():
            shown = _shown_pixels(dialog.view_a)
            return int(((shown[..., 0] > 200) & (shown[..., 1] < 90) & (shown[..., 2] < 90)).sum())

        sharp = next(i for i, s in enumerate(dialog.steps) if s.key == "bird" and trace.birds[s.bird].best)
        dialog.go(sharp, force=True)
        plain = red()
        assert not dialog.peaking_spin.isEnabled() and not dialog.peaking_slider.isEnabled()
        dialog.view_a.scale(3.0, 3.0)
        zoom = dialog.view_a.zoom_factor()
        dialog.peaking_btn.setChecked(True)
        assert dialog.peaking_spin.isEnabled() and red() > plain + 200
        assert dialog.view_a.zoom_factor() == pytest.approx(zoom)  # toggling keeps the view
        assert "焦平面" in dialog.legend.text()
        assert dialog.peaking_slider.isEnabled() and dialog.peaking_slider.value() == 85
        loose = red()
        dialog.peaking_slider.setValue(50)  # dragging the slider sets the typed value too
        assert dialog.peaking_spin.value() == pytest.approx(0.5) and red() < loose
        dialog.peaking_spin.setValue(1.23)  # typing moves the slider
        assert dialog.peaking_slider.value() == 123 and red() > loose
        assert "1.23" in dialog.legend.text()
        # follows navigation and the compare view
        dialog.peaking_spin.setValue(0.85)
        dialog.compare_btn.setChecked(True)
        assert _wait(lambda: dialog.view_b.isVisible())
        dialog.go(sharp + 1, force=True)
        assert red() > plain + 200
        compare = dialog.steps[dialog._compare_index()]  # 上一步 = the sharp bird's pixels
        assert np.array_equal(_shown_pixels(dialog.view_b), dialog._step_image(compare))
        assert not np.array_equal(dialog._step_image(compare), compare.image)
        # the bird overview has no per-pixel map: unchanged, legend says so
        dialog.go(next(i for i, s in enumerate(dialog.steps) if s.key == "birds"), force=True)
        assert np.array_equal(_shown_pixels(dialog.view_a), dialog.steps[dialog.index].image)
        assert "没有逐像素图" in dialog.legend.text()
        dialog.peaking_btn.setChecked(False)
        dialog.go(sharp, force=True)
        assert red() == plain
    finally:
        dialog.close()
        _APP.processEvents()


def test_chain_results_open_a_trace_on_a_temporary_image(stub_trace_env, monkeypatch) -> None:
    """A model chain window's 「测清晰度」: its results measured as birds on the photo's own pixels."""
    import cv2

    from bird_sharpness import preview as pv
    from bird_sharpness.models import FOUND_GIVEN

    analyzer, _gate = stub_trace_env
    decodes = []
    installed = analyzer_mod.load_analysis_image
    monkeypatch.setattr(analyzer_mod, "load_analysis_image", lambda p: decodes.append(p) or installed(p))
    window = QWidget()
    controller = BirdSharpnessController(window, _FakeFileList(None))
    monkeypatch.setattr(controller, "analyzer", lambda: analyzer)
    try:
        dialog = controller.show_trace("/photos/a.ARW")
        assert _wait(lambda: dialog.stack.currentWidget() is dialog.content, timeout=20)
        photo = dialog.image_cache.latest("raw")
        mask = np.zeros((1200, 1800), bool)
        cv2.circle(mask.view(np.uint8), (900, 600), 300, 1, -1)
        item = pv.PreviewItem("对象 1", 0.8, (600, 300, 1200, 900), mask, (0, 0, 1800, 1200))
        image, given, _region = pv.analysis_input(photo, [item], "模型链 ② sam2.1_t.pt 的 1 个结果")
        dialog.analyze_pixels_requested.emit(dialog, image, given, "模型链 ② sam2.1_t.pt 的 1 个结果")
        temp = next(w for w in QApplication.topLevelWidgets()
                    if isinstance(w, BirdSharpnessTraceDialog) and w.given_input is not None)
        assert "临时图" in temp.windowTitle() and not temp.source_combo.isEnabled()
        assert _wait(lambda: temp.stack.currentWidget() is temp.content, timeout=20)
        assert len(decodes) == 1  # the temporary window decodes nothing
        result = temp.trace.result
        assert result.bird_count == 1 and result.birds[0]["found_by"] == FOUND_GIVEN
        assert "模型链 ②" in dict(temp.steps[1].metrics)["识别模型"]
        assert temp._preview_image() is image  # a chain opened there works on the temporary image
        temp.max_birds_spin.setValue(1)
        temp.rerun_btn.click()  # 按此参数重新计算: same temporary image, same birds
        assert _wait(lambda: temp.stack.currentWidget() is temp.content, timeout=20)
        assert len(decodes) == 1 and temp.trace.result.birds[0]["found_by"] == FOUND_GIVEN
        temp.close()
        dialog.close()
        assert _wait(controller.is_shutdown_done)
    finally:
        controller.request_shutdown()
        window.deleteLater()
        _APP.processEvents()
