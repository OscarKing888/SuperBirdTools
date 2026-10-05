"""Model preview docks in the trace window: buttons, docking, prompts, results, closing."""
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
from SuperViewer.superviewer.model_preview import BOX, POINTS

_APP = QApplication.instance() or QApplication([])


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def dialog(monkeypatch, tmp_path):
    rgb = np.full((1200, 1800, 3), 90, np.uint8)
    image = AnalysisImage(rgb, rgb[..., 1].astype(np.float32) / 255.0, True)
    cache = DecodedImageCache()
    cache.get_or_load(DecodedImageCache.key(str(tmp_path / "x.ARW"), "raw"), lambda: image)
    calls = {"detector": [], "sam": []}

    def fake_detector(img, params):
        calls["detector"].append(params)
        return pv.PreviewResult(params.model, "mps", 0.1, "全图", [pv.PreviewItem("bird", 0.66, (100, 100, 400, 300))])

    def fake_sam(img, params):
        calls["sam"].append(params)
        if not params.boxes and not params.points:
            raise ValueError("请先画框、点选，或使用检测框")
        mask = np.ones((10, 10), bool)
        return pv.PreviewResult(params.model, "mps", 0.2, "提示", [pv.PreviewItem("对象 1", 0.8, (100, 100, 300, 300),
                                                                                  mask, (0, 0, 1800, 1200))])

    monkeypatch.setattr(pv, "run_detector", fake_detector)
    monkeypatch.setattr(pv, "run_sam", fake_sam)
    d = BirdSharpnessTraceDialog(None, str(tmp_path / "x.ARW"))
    d.image_cache = cache
    d.trace = SimpleNamespace(result=SimpleNamespace(birds=[{"box": (50, 60, 250, 260)}, {"box": (900, 500, 1100, 700)}]))
    d.show()
    yield d, calls
    try:
        d.close()
    except RuntimeError:  # the test already closed it (WA_DeleteOnClose)
        pass
    _APP.processEvents()


def test_preview_buttons_live_only_where_there_is_a_photo(dialog) -> None:
    d, _calls = dialog
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


def test_detector_preview_docks_runs_and_lists_results(dialog, monkeypatch) -> None:
    d, calls = dialog
    import bird_sharpness.model_catalog as catalog

    monkeypatch.setattr(catalog, "locate", lambda name: "/models/" + name)
    width = d.width()
    panel = d.open_model_preview("detector", "yolo11x-seg.pt")
    assert not d.preview_host.isHidden() and len(d._preview_docks) == 1 and d.width() > width
    assert _wait(lambda: panel.result is not None)  # loads this window's decode and runs once
    assert calls["detector"][0].region is None and calls["detector"][0].imgsz == 640
    assert panel.results.rows[0].label == "#1 bird" and "yolo11x-seg.pt" in panel.status.text()
    s = panel._display[1]
    panel.view.zoom_to((200 * s, 100 * s, 800 * s, 500 * s))
    panel.scope.setCurrentIndex(1)
    panel.min_conf.setValue(30)
    panel.classes.setCurrentIndex(1)
    panel.result = None
    panel.run()
    assert _wait(lambda: panel.result is not None)
    p = calls["detector"][1]
    assert p.region is not None and p.min_conf == pytest.approx(0.3) and p.birds_only is False
    panel.results.cells[0][1].setFocus()


def test_sam_preview_takes_drawn_boxes_points_and_detected_birds(dialog, monkeypatch) -> None:
    d, calls = dialog
    import bird_sharpness.model_catalog as catalog

    monkeypatch.setattr(catalog, "locate", lambda name: "/models/" + name)
    det = d.open_model_preview("detector", "auto")
    sam = d.open_model_preview("sam", "sam2.1_t.pt")
    assert len(d._preview_docks) == 2  # side by side
    assert _wait(lambda: sam._image is not None and det.result is not None)
    assert sam.view.mode == BOX
    s = sam._display[1]
    sam.run()
    assert "请先画框" in sam.status.text() and calls["sam"] == []
    sam.view.box_drawn.emit((100 * s, 100 * s, 300 * s, 300 * s))
    sam.tool_buttons[POINTS].click()
    assert sam.view.mode == POINTS
    sam.view.point_added.emit(150 * s, 150 * s, True)
    sam.view.point_added.emit(250 * s, 250 * s, False)
    assert "1 个框，1 个保留点，1 个排除点" in sam.prompt_label.text()
    sam.run()
    assert _wait(lambda: sam.result is not None)
    p = calls["sam"][0]
    assert p.boxes[0] == pytest.approx((100, 100, 300, 300)) and [k for _x, _y, k in p.points] == [True, False]
    sam.result = None
    sam.use_detected_boxes()  # the trace's birds, one object each, run right away
    assert _wait(lambda: sam.result is not None)
    assert calls["sam"][1].boxes == ((50, 60, 250, 260), (900, 500, 1100, 700)) and calls["sam"][1].points == ()
    sam.clear_prompts()
    assert sam.boxes == [] and sam.points == []
    # closing one dock keeps the other; closing the window closes all
    d._preview_docks[0].close()
    _APP.processEvents()
    assert len(d._preview_docks) == 1 and not d.preview_host.isHidden()
    d._preview_docks[0].close()
    _APP.processEvents()
    assert not d._preview_docks and d.preview_host.isHidden()


def test_missing_model_is_offered_for_download_first(dialog, monkeypatch) -> None:
    d, _calls = dialog
    import bird_sharpness.model_catalog as catalog
    import SuperViewer.superviewer.bird_sharpness_trace_view as tv

    offered = []
    monkeypatch.setattr(catalog, "locate", lambda name: None)
    monkeypatch.setattr(tv, "download_models", lambda parent, names: offered.append(list(names)) or False)
    assert d.open_model_preview("sam", "sam2.1_l.pt") is None and offered == [["sam2.1_l.pt"]]
    assert not d._preview_docks


def test_closing_the_window_drops_late_results(dialog, monkeypatch) -> None:
    d, _calls = dialog
    import bird_sharpness.model_catalog as catalog

    monkeypatch.setattr(catalog, "locate", lambda name: "/models/" + name)
    panel = d.open_model_preview("detector", "auto")
    d.close()
    _APP.processEvents()
    assert not panel._alive and not d._preview_docks
    panel._on_done("run", pv.PreviewResult("x", "cpu", 0, "", []))  # a late result: ignored, no crash
