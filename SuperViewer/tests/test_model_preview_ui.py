"""Model chain in the trace window: windows, model / input switching, results handed on, closing."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from bird_sharpness import preview as pv
from bird_sharpness.image_source import AnalysisImage, DecodedImageCache
from SuperViewer.superviewer.bird_sharpness_params_form import AnalysisParamsForm
from SuperViewer.superviewer.bird_sharpness_trace_view import BirdSharpnessTraceDialog
from SuperViewer.superviewer.model_preview import (BOX, DETECTOR, INPUT_IMAGE, INPUT_PREVIOUS, INPUT_TRACE, POINTS,
                                                   SAM, USE_MASK)

_APP = QApplication.instance() or QApplication([])


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _idle(host):
    return _wait(lambda: host.display is not None and not any(s.busy or s.pending for s in host.stages))


@pytest.fixture
def dialog(monkeypatch, tmp_path):
    import bird_sharpness.model_catalog as catalog

    monkeypatch.setattr(catalog, "locate", lambda name: "/models/" + name)
    rgb = np.full((1200, 1800, 3), 90, np.uint8)
    image = AnalysisImage(rgb, rgb[..., 1].astype(np.float32) / 255.0, True)
    cache = DecodedImageCache()
    cache.get_or_load(DecodedImageCache.key(str(tmp_path / "x.ARW"), "raw"), lambda: image)
    calls = {"detector": [], "sam": [], "detector_on": [], "sam_on": []}
    fail = {"detector": False}
    mask = np.ones((10, 10), bool)

    def fake_detector(img, params):
        calls["detector"].append(params)
        if fail["detector"]:
            raise RuntimeError("模型坏了")
        return pv.PreviewResult(params.model, "mps", 0.1, "全图", [pv.PreviewItem("bird", 0.66, (100, 100, 400, 300)),
                                                                   pv.PreviewItem("bird", 0.2, (900, 500, 1000, 600))])

    def fake_sam(img, params):
        calls["sam"].append(params)
        return pv.PreviewResult(params.model, "mps", 0.2, "提示", [pv.PreviewItem("对象 1", 0.8, (100, 100, 300, 300),
                                                                                  mask, (0, 0, 1800, 1200))])

    def fake_detector_on(img, params, inputs, *, margin, mask_only):
        calls["detector_on"].append((params, list(inputs), margin, mask_only))
        return pv.PreviewResult(params.model, "mps", 0.1, "输入", [pv.PreviewItem("bird", 0.7, i.box, source=k)
                                                                   for k, i in enumerate(inputs, 1)])

    def fake_sam_on(img, model, inputs):
        calls["sam_on"].append((model, list(inputs)))
        return pv.PreviewResult(model, "mps", 0.2, "输入", [pv.PreviewItem(f"对象 {k}", 0.8, i.box, mask,
                                                                           (0, 0, 1800, 1200), k)
                                                            for k, i in enumerate(inputs, 1)])

    for name, fake in (("run_detector", fake_detector), ("run_sam", fake_sam), ("run_detector_on", fake_detector_on),
                       ("run_sam_on", fake_sam_on)):
        monkeypatch.setattr(pv, name, fake)
    d = BirdSharpnessTraceDialog(None, str(tmp_path / "x.ARW"))
    d.image_cache = cache
    d.trace = SimpleNamespace(result=SimpleNamespace(birds=[{"box": (50, 60, 250, 260)}, {"box": (900, 500, 1100, 700)}]))
    d.show()
    d.stack.setCurrentWidget(d.content)  # as once a trace is shown (the 「预览」 buttons live there)
    _APP.processEvents()
    yield d, calls, fail
    try:
        d.close()
    except RuntimeError:  # the test already closed it (WA_DeleteOnClose)
        pass
    _APP.processEvents()


def test_preview_buttons_live_only_where_there_is_a_photo(dialog) -> None:
    d, _calls, _fail = dialog
    form = d.params_form
    assert form.detector_preview_btn.isEnabled() and not form.sam_preview_btn.isEnabled()  # SAM 关闭
    form.sam_model.setCurrentIndex(form.sam_model.findData("sam2.1_t.pt"))
    assert form.sam_preview_btn.isEnabled()
    requested = []
    form.preview_requested.connect(lambda kind, model: requested.append((kind, model)))
    form.detector_preview_btn.click()
    assert requested[0] == ("detector", "auto")
    settings = AnalysisParamsForm()  # 设置 → 鸟清晰度: no photo
    assert not settings.detector_preview_btn.isEnabled() and "计算过程窗口" in settings.detector_preview_btn.toolTip()
    settings.deleteLater()


def test_preview_appends_a_detector_window_that_runs_on_the_photo(dialog) -> None:
    d, calls, _fail = dialog
    host = d.preview_host
    stage = d.open_model_preview("detector", "yolo11x-seg.pt")
    assert not host.isHidden() and host.stages == [stage] and d._body.sizes()[2] >= 300  # room for the window
    assert _idle(host) and stage.result is not None  # loads this window's decode and runs once
    assert stage.input == INPUT_IMAGE and calls["detector"][0].region is None and calls["detector"][0].imgsz == 640
    assert stage.results.rows[0].label == "#1 bird" and "yolo11x-seg.pt" in stage.status.text()
    assert host._docks[stage].windowTitle() == "① yolo11x-seg.pt"
    s = stage.display[1]
    stage.view.zoom_to((200 * s, 100 * s, 800 * s, 500 * s))
    stage.scope.setCurrentIndex(1)
    stage.min_conf.setValue(30)
    stage.classes.setCurrentIndex(1)
    stage.run_btn.click()
    assert _idle(host) and len(calls["detector"]) == 2
    p = calls["detector"][1]
    assert p.region is not None and p.min_conf == pytest.approx(0.3) and p.birds_only is False


def test_each_window_hands_its_results_to_the_next(dialog) -> None:
    d, calls, _fail = dialog
    host = d.preview_host
    yolo = d.open_model_preview("detector", "auto")
    sam = d.open_model_preview("sam", "sam2.1_t.pt")  # appended after the detector, chained onto it
    assert host.stages == [yolo, sam] and sam.input == INPUT_PREVIOUS
    assert sam.input_combo.currentText() == "上一窗口 ① 的结果"
    assert _idle(host)
    assert [i.box for i in calls["sam_on"][0][1]] == [(100, 100, 400, 300), (900, 500, 1000, 600)]
    assert sam.results.rows[1].value.endswith("来自输入 #2") and len(sam.view._prompt_items) == 2  # inputs drawn
    host.add_action.trigger()  # ＋ 添加窗口: after SAM comes a detector, fed SAM's cut-outs
    yolo2 = host.stages[2]
    assert yolo2.kind == DETECTOR and yolo2.model == "auto" and yolo2.input == INPUT_PREVIOUS
    assert yolo2.use.isVisibleTo(yolo2) and yolo2.margin.isVisibleTo(yolo2) and not yolo2.scope.isVisibleTo(yolo2)
    assert _idle(host)
    params, inputs, margin, mask_only = calls["detector_on"][-1]
    assert [i.source for i in inputs] == [1, 2] and inputs[0].mask is not None and margin == pytest.approx(0.3)
    assert mask_only is False
    yolo2.use.setCurrentIndex(yolo2.use.findData(USE_MASK))
    yolo2.margin.setValue(50)
    yolo2.run_btn.click()
    assert _idle(host) and calls["detector_on"][-1][2:] == (pytest.approx(0.5), True)
    # rerunning the first window reruns the rest in order
    n_sam, n_on = len(calls["sam_on"]), len(calls["detector_on"])
    yolo.run_btn.click()
    assert _idle(host) and len(calls["sam_on"]) == n_sam + 1 and len(calls["detector_on"]) == n_on + 1
    # without 自动传给下一窗口 the next window only says it is stale; 运行整条链 still runs everything
    host.auto_check.setChecked(False)
    yolo.run_btn.click()
    assert _idle(host) and len(calls["sam_on"]) == n_sam + 1 and "上一窗口已更新" in sam.status.text()
    host.run_all_action.trigger()
    assert _idle(host) and len(calls["sam_on"]) == n_sam + 2 and len(calls["detector_on"]) == n_on + 2


def test_switching_the_model_switches_its_parameters(dialog) -> None:
    d, calls, _fail = dialog
    host = d.preview_host
    stage = d.open_model_preview("detector", "auto")
    assert _idle(host) and stage.params_stack.currentIndex() == 0 and stage.scope.isVisibleTo(stage)
    stage.model_combo.setCurrentIndex(stage.model_combo.findData("sam2.1_b.pt"))
    assert stage.kind == SAM and stage.params_stack.currentIndex() == 1
    assert host._docks[stage].windowTitle() == "① sam2.1_b.pt"
    # the first window as SAM takes the trace's birds by default and runs right away
    assert stage.input == INPUT_TRACE and not stage.tools_box.isVisibleTo(stage) and _idle(host)
    assert [i.box for i in calls["sam_on"][0][1]] == [(50, 60, 250, 260), (900, 500, 1100, 700)]
    stage.input_combo.setCurrentIndex(stage.input_combo.findData(INPUT_IMAGE))  # manual prompts
    assert stage.tools_box.isVisibleTo(stage) and stage.view.mode == BOX and stage.result is None
    assert "原图（手动提示）" in stage.input_combo.currentText()
    s = stage.display[1]
    stage.view.box_drawn.emit((100 * s, 100 * s, 300 * s, 300 * s))
    stage.tool_buttons[POINTS].click()
    stage.view.point_added.emit(150 * s, 150 * s, True)
    stage.view.point_added.emit(250 * s, 250 * s, False)
    assert "1 个框，1 个保留点，1 个排除点" in stage.prompt_label.text()
    stage.run_btn.click()
    assert _idle(host) and stage.result is not None
    p = calls["sam"][0]
    assert p.model == "sam2.1_b.pt" and p.boxes[0] == pytest.approx((100, 100, 300, 300))
    assert [k for _x, _y, k in p.points] == [True, False]
    stage.clear_prompts()
    assert stage.boxes == [] and stage.points == []
    stage.model_combo.setCurrentIndex(stage.model_combo.findData("yolo26n.pt"))  # back to a detector
    assert stage.kind == DETECTOR and stage.input == INPUT_IMAGE  # the picked input stays
    assert stage.scope.isVisibleTo(stage) and not stage.use.isVisibleTo(stage) and _idle(host)
    assert calls["detector"][-1].model == "yolo26n.pt"
    stage.input_combo.setCurrentIndex(stage.input_combo.findData(INPUT_TRACE))  # zoom into the trace's birds
    assert stage.use.isVisibleTo(stage) and not stage.scope.isVisibleTo(stage) and _idle(host)
    assert [i.box for i in calls["detector_on"][-1][1]] == [(50, 60, 250, 260), (900, 500, 1100, 700)]


def test_moving_and_closing_windows_rewires_the_chain(dialog) -> None:
    d, calls, _fail = dialog
    host = d.preview_host
    a = d.open_model_preview("detector", "auto")
    b = d.open_model_preview("sam", "sam2.1_t.pt")
    c = d.open_model_preview("detector", "yolo11n.pt")
    assert _idle(host) and [s.input for s in host.stages] == [INPUT_IMAGE, INPUT_PREVIOUS, INPUT_PREVIOUS]
    assert not a.left_btn.isEnabled() and not c.right_btn.isEnabled()
    c.left_btn.click()  # a, c, b: c now takes the detector's boxes, b takes c's results
    assert host.stages == [a, c, b] and _idle(host)
    assert host._docks[c].windowTitle() == "② yolo11n.pt" and c.fed_by is a and b.fed_by is c
    a.right_btn.click()  # c, a, b: c has no previous window any more → the photo
    assert host.stages == [c, a, b] and c.input == INPUT_IMAGE and a.input == INPUT_PREVIOUS and _idle(host)
    n = len(calls["sam_on"])
    host._docks[a].close()  # b is chained onto c now and reruns
    _APP.processEvents()
    assert host.stages == [c, b] and _idle(host) and len(calls["sam_on"]) == n + 1 and b.fed_by is c
    host.close_all_action.trigger()
    _APP.processEvents()
    assert not host.stages and host.isHidden()


def test_a_failed_window_stops_the_ones_after_it(dialog) -> None:
    d, calls, fail = dialog
    host = d.preview_host
    fail["detector"] = True
    yolo = d.open_model_preview("detector", "auto")
    sam = d.open_model_preview("sam", "sam2.1_t.pt")
    assert _idle(host) and "模型坏了" in yolo.status.text() and "运行失败" in sam.status.text()
    assert calls["sam_on"] == [] and len(calls["detector"]) == 1  # no retry loop
    fail["detector"] = False
    yolo.run_btn.click()
    assert _idle(host) and len(calls["sam_on"]) == 1


def test_missing_model_is_offered_for_download_first(dialog, monkeypatch) -> None:
    d, _calls, _fail = dialog
    import bird_sharpness.model_catalog as catalog
    import SuperViewer.superviewer.bird_sharpness_trace_view as tv

    offered = []
    monkeypatch.setattr(catalog, "locate", lambda name: None)
    monkeypatch.setattr(tv, "download_models", lambda parent, names: offered.append(list(names)) or False)
    assert d.open_model_preview("sam", "sam2.1_l.pt") is None and offered == [["sam2.1_l.pt"]]
    assert not d.preview_host.stages and d.preview_host.isHidden()
    stage = d.open_model_preview("detector", "auto")  # the built-in detector needs no download
    stage.model_combo.setCurrentIndex(stage.model_combo.findData("yolo11x.pt"))  # declined → stays
    assert offered[-1] == ["yolo11x.pt"] and stage.model == "auto" and stage.model_combo.currentData() == "auto"


def test_closing_the_window_drops_late_results(dialog) -> None:
    d, _calls, _fail = dialog
    host = d.preview_host
    stage = d.open_model_preview("detector", "auto")
    d.close()
    _APP.processEvents()
    assert not host._alive and not host.stages
    host._on_done(stage, stage.generation, pv.PreviewResult("x", "cpu", 0, "", []))  # late: ignored, no crash
