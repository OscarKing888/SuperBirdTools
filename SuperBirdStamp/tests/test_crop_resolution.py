import pytest
from PIL import Image
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication

from birdstamp.crop_resolution import (
    CropPixelContext, DEFAULT_TIERS, choose_snap_target, handle_point,
    normalize_snap_options, resolution_targets, tier_size,
)
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas

_APP = QApplication.instance() or QApplication([])
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


@pytest.mark.parametrize("ratio", [1.5, 2 / 3, 1, 16 / 9, 9 / 16])
@pytest.mark.parametrize("handle", HANDLES)
def test_targets_keep_integer_size_and_opposite_anchor(ratio, handle):
    context = CropPixelContext((6000, 4000), (999, 667), (13, 29, 17, 41))
    start = context.to_preview((0.2, 0.15, 0.8, 0.85))
    targets = resolution_targets(context, start, handle, ratio)
    assert len(targets) == 5
    before = context.pixel_box(start)
    for target, (_, edge) in zip(targets, DEFAULT_TIERS):
        assert target.size == tier_size(edge, ratio)
        assert context.crop_size(target.box) == target.size
        after = context.pixel_box(target.box)
        assert after == pytest.approx(tuple(round(v) for v in after))
        if "w" in handle:
            assert after[2] == pytest.approx(round(before[2]))
        if "e" in handle:
            assert after[0] == pytest.approx(round(before[0]))
        if "n" in handle:
            assert after[3] == pytest.approx(round(before[3]))
        if "s" in handle:
            assert after[1] == pytest.approx(round(before[1]))
        if handle in ("n", "s"):
            assert abs((after[0] + after[2] - before[0] - before[2]) / 2) <= 0.500001
        if handle in ("e", "w"):
            assert abs((after[1] + after[3] - before[1] - before[3]) / 2) <= 0.500001


def test_invalid_candidates_and_missing_dimensions():
    invalid = CropPixelContext((0, 100), (10, 10))
    assert not resolution_targets(invalid, (0, 0, 1, 1), "se", 1)
    context = CropPixelContext((100000, 100000), (1000, 1000))
    targets = resolution_targets(context, (0, 0, 1, 1), "se", 1)
    assert [t.label for t in targets] == ["4K"]
    assert not resolution_targets(context, (2, 2, 3, 3), "se", 1)


def test_config_defaults_and_validation(monkeypatch):
    from birdstamp.gui import editor_options
    monkeypatch.setattr(editor_options, "_load_builtin_editor_options_raw", lambda: {
        "crop_resolution_snap": {"tiers": [{"label": "custom", "short_edge": 600}],
                                 "enter_distance": 12, "leave_distance": 8}})
    assert editor_options.load_editor_options()["crop_resolution_snap"] == {
        "tiers": (("custom", 600),), "enter_distance": 12, "leave_distance": 12}
    options = normalize_snap_options({"tiers": [None, {}, {"label": "bad", "short_edge": -1}],
                                      "enter_distance": float("nan"), "leave_distance": "x"})
    assert options == normalize_snap_options(None)


@pytest.mark.parametrize("scale", [0.1, 0.25, 1.0, 2.0])
def test_hysteresis_uses_screen_distance_and_edge_axis_only(scale):
    context = CropPixelContext((6000, 4000), (600, 400))
    target = resolution_targets(context, (0, 0, 0.5, 0.5), "e", 1.5)[1]
    viewport = (6000 * scale, 4000 * scale)
    l, t, r, b = target.box
    def shifted(distance):
        return (l, t + 0.1, r + distance / viewport[0], b + 0.1)
    assert choose_snap_target(shifted(9), (target,), "e", viewport) == (target, True)
    assert choose_snap_target(shifted(12), (target,), "e", viewport) == (target, False)
    assert choose_snap_target(shifted(12), (target,), "e", viewport, previous=target) == (target, True)
    assert choose_snap_target(shifted(17), (target,), "e", viewport, previous=target) == (target, False)


@pytest.fixture
def canvas(tmp_path, monkeypatch):
    from birdstamp import config
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path)
    widget = EditorPreviewCanvas()
    widget.resize(900, 650)
    pixmap = QPixmap(1200, 800)
    pixmap.fill(Qt.GlobalColor.darkGray)
    widget.set_source_pixmap(pixmap, log_performance=False)
    widget.set_crop_pixel_context(CropPixelContext((6000, 4000), (1200, 800), ratio=1.5, source_key="bird"))
    widget.set_crop_ratio_constraint(1.5, False)
    widget.set_crop_effect_box((0.2, 0.2, 0.65, 0.65))
    widget.set_crop_edit_mode(True)
    widget.show()
    _APP.processEvents()
    yield widget
    widget.close()


def _mouse(canvas, kind, point, *, shift=False):
    press = kind == QEvent.Type.MouseButtonPress
    release = kind == QEvent.Type.MouseButtonRelease
    event = QMouseEvent(kind, QPointF(*point), QPointF(*point),
                        Qt.MouseButton.LeftButton if press or release else Qt.MouseButton.NoButton,
                        Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier)
    _APP.sendEvent(canvas, event)


def _key(canvas, pressed):
    _APP.sendEvent(canvas, QKeyEvent(QEvent.Type.KeyPress if pressed else QEvent.Type.KeyRelease,
                                   Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier if pressed
                                   else Qt.KeyboardModifier.NoModifier))


def _point(canvas, box, handle):
    return canvas._norm_to_widget(canvas._display_rect(), *handle_point(box, handle))


@pytest.mark.parametrize("handle", HANDLES)
def test_drag_snaps_all_handles_and_commits_once(canvas, handle):
    events = []
    canvas.crop_drag_finished.connect(lambda: events.append("finished"))
    canvas.crop_box_changed.connect(lambda box: events.append("drag" if canvas._dragging_handle else "commit"))
    target = resolution_targets(canvas._crop_pixel_context, canvas._crop_effect_box, handle, 1.5)[2]
    _mouse(canvas, QEvent.Type.MouseButtonPress, _point(canvas, canvas._crop_effect_box, handle))
    _mouse(canvas, QEvent.Type.MouseMove, _point(canvas, target.box, handle), shift=True)
    assert canvas._crop_resolution_snapped
    assert canvas._crop_pixel_context.crop_size(canvas._crop_effect_box) == (1620, 1080)
    assert canvas._crop_resolution_guide.label == "1080p"
    _mouse(canvas, QEvent.Type.MouseButtonRelease, _point(canvas, target.box, handle), shift=True)
    assert events[-2:] == ["finished", "commit"]
    assert events.count("commit") == 1
    assert canvas._crop_resolution_guide is None


def test_mid_drag_shift_release_idle_guide_and_free_ratio(canvas):
    context = CropPixelContext((6000, 4000), (1200, 800), source_key="bird")
    canvas.set_crop_pixel_context(context)
    canvas.set_crop_ratio_constraint(None, True)
    _key(canvas, True)
    assert canvas._crop_resolution_guide is not None
    assert not canvas._crop_resolution_snapped
    _key(canvas, False)
    target = resolution_targets(context, canvas._crop_effect_box, "se", 1.5)[2]
    _mouse(canvas, QEvent.Type.MouseButtonPress, _point(canvas, canvas._crop_effect_box, "se"))
    tx, ty = _point(canvas, target.box, "se")
    _mouse(canvas, QEvent.Type.MouseMove, (tx + 2, ty + 2))
    _key(canvas, True)
    assert canvas._crop_resolution_snapped
    assert context.crop_size(canvas._crop_effect_box) == (1620, 1080)
    _key(canvas, False)
    assert canvas._crop_resolution_guide is None
    assert context.crop_size(canvas._crop_effect_box) == (1620, 1080)
    _mouse(canvas, QEvent.Type.MouseMove, (tx + 30, ty + 2))
    assert context.pixel_ratio(canvas._crop_effect_box) != pytest.approx(1.5)


def test_center_pan_does_not_snap(canvas):
    before = canvas._crop_pixel_context.crop_size(canvas._crop_effect_box)
    x, y = _point(canvas, canvas._crop_effect_box, "")
    _mouse(canvas, QEvent.Type.MouseButtonPress, (x, y), shift=True)
    _mouse(canvas, QEvent.Type.MouseMove, (x + 10, y + 5), shift=True)
    assert canvas._crop_resolution_guide is None
    assert canvas._crop_pixel_context.crop_size(canvas._crop_effect_box) == before


@pytest.mark.parametrize("zoom", [50, 125, 200])
def test_canvas_zoom_keeps_ten_pixel_snap_distance(canvas, zoom):
    canvas.set_display_scale_percent(zoom)
    target = resolution_targets(canvas._crop_pixel_context, canvas._crop_effect_box, "e", 1.5)[2]
    _mouse(canvas, QEvent.Type.MouseButtonPress, _point(canvas, canvas._crop_effect_box, "e"))
    x, y = _point(canvas, target.box, "e")
    _mouse(canvas, QEvent.Type.MouseMove, (x + 9, y), shift=True)
    assert canvas._crop_resolution_snapped
    assert canvas._crop_pixel_context.crop_size(canvas._crop_effect_box) == target.size
    _mouse(canvas, QEvent.Type.MouseMove, (x + 17, y), shift=True)
    assert not canvas._crop_resolution_snapped


def test_labels_remain_inside_viewport_and_report_exact_tier(canvas):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QFontMetrics

    class Painter:
        def fontMetrics(self):
            return QFontMetrics(canvas.font())

        def setPen(self, *args):
            pass

        def setBrush(self, *args):
            pass

        def drawRoundedRect(self, rect, *args):
            assert QRectF(canvas.contentsRect()).contains(rect)

        def drawText(self, rect, *args):
            assert QRectF(canvas.contentsRect()).contains(rect)

    for rect in (QRectF(-100, -100, 400, 300), QRectF(700, 500, 500, 300), QRectF(100, 0, 500, 400)):
        for bottom in (False, True):
            canvas._draw_crop_resolution_label(Painter(), rect, "1080p · 1620 × 1080 px", QColor("white"), bottom=bottom)

    target = resolution_targets(canvas._crop_pixel_context, canvas._crop_effect_box, "se", 1.5)[2]
    canvas.set_crop_effect_box(target.box)
    labels = []
    canvas._draw_crop_resolution_label = lambda _painter, _rect, text, _color, **kw: labels.append(text)
    canvas.grab()
    assert "1620 × 1080 px · 1080p" in labels


@pytest.mark.parametrize("clear", ["focus", "mode", "source", "pixmap"])
def test_transient_state_is_cleared(canvas, clear):
    _mouse(canvas, QEvent.Type.MouseButtonPress, _point(canvas, canvas._crop_effect_box, "se"), shift=True)
    assert canvas._crop_resolution_guide is not None
    finished = []
    canvas.crop_drag_finished.connect(lambda: finished.append(True))
    if clear == "focus":
        _APP.sendEvent(canvas, QFocusEvent(QEvent.Type.FocusOut))
    elif clear == "mode":
        canvas.set_crop_edit_mode(False)
    elif clear == "source":
        canvas.set_crop_pixel_context(CropPixelContext((4000, 3000), (800, 600), source_key="other"))
    else:
        canvas.set_source_pixmap(QPixmap(80, 60))
    assert finished == [True]
    assert canvas._crop_resolution_guide is None
    assert canvas._dragging_handle is None
    assert not canvas._crop_shift_down


def test_ui_labels_never_enter_overlay_export_and_grid_remains(canvas):
    canvas.set_composition_grid_mode("thirds")
    before = canvas.render_source_pixmap_with_overlays().toImage()
    _key(canvas, True)
    canvas.grab()  # Exercise the QWidget-only paint layer.
    after = canvas.render_source_pixmap_with_overlays().toImage()
    assert before == after
    canvas.set_crop_pixel_context(None)
    assert canvas.render_source_pixmap_with_overlays().toImage() == before
    canvas.set_composition_grid_mode("none")
    assert canvas.render_source_pixmap_with_overlays().toImage() != before


@pytest.mark.parametrize("source_box", [(0.15, 0.1, 0.8, 0.8), (-0.2, -0.1, 0.7, 0.9)])
def test_real_render_and_workspace_roundtrip_match_snapped_size(tmp_path, source_box):
    from birdstamp.export_stage import VideoFrameJob, render_video_frame
    from birdstamp.workspace import read_workspace_json, write_workspace_json
    from birdstamp.gui.editor_core import _crop_plan_from_override, rescale_crop_plan
    context = CropPixelContext((1200, 800), (301, 199), (10, 20, 30, 40))
    target = resolution_targets(context, context.to_preview(source_box), "se", 1.5)[1]
    box = context.to_source(target.box)
    workspace = tmp_path / "crop.birdstamp-workspace.json"
    write_workspace_json(workspace, {"photos": [{"crop_box": box}]})
    restored = read_workspace_json(workspace)["photos"][0]["crop_box"]
    plan, pad = _crop_plan_from_override(*context.source_size, restored)
    preview_box, preview_pad = rescale_crop_plan(plan, pad, context.source_size, context.preview_size)
    rebuilt = CropPixelContext(context.source_size, context.preview_size, preview_pad)
    assert rebuilt.crop_size(preview_box) == target.size
    source = tmp_path / "source.png"
    with Image.new("RGB", context.source_size, "red") as image:
        image.save(source)
        job = VideoFrameJob(path=source, source_image=image, settings={
            "crop_box": restored, "ratio": "free", "center_mode": "custom", "max_long_edge": 0,
            "draw_text": False, "draw_banner": False, "draw_focus": False,
        }, raw_metadata={}, metadata_context={})
        job.crop_plan = _crop_plan_from_override(*context.source_size, restored)
        rendered = render_video_frame(job)
        try:
            exported = tmp_path / "crop.png"
            rendered.save(exported)
            with Image.open(exported) as reopened:
                assert reopened.size == target.size == context.crop_size(target.box)
        finally:
            rendered.close()
