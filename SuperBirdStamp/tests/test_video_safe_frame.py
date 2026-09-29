import pytest
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from birdstamp import config
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas
from birdstamp.gui.editor_video_panel import VideoExportPanel
from birdstamp.gui.video_safe_frame import inscribed_safe_frame


_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((4680, 2160), (10, 100.7692307692, 300, 138.4615384615)),
        ((2160, 4680), (90.7692307692, 20, 138.4615384615, 300)),
        ((1080, 1080), (10, 20, 300, 300)),
    ],
)
def test_safe_frame_is_centered_inside_crop(size, expected):
    assert inscribed_safe_frame((10, 20, 300, 300), size) == pytest.approx(expected)
    assert inscribed_safe_frame((10, 20, 0, 300), size) is None


def _select_size(panel, mode, *, width=None, height=None):
    for index in range(panel.frame_size_combo.count()):
        data = panel.frame_size_combo.itemData(index) or {}
        if (data.get("mode") == mode and (width is None or data.get("width") == width)
                and (height is None or data.get("height") == height)):
            panel.frame_size_combo.setCurrentIndex(index)
            return
    pytest.fail(f"missing video size: {mode} {width} x {height}")


def test_panel_size_changes_include_orientation_custom_and_restore():
    panel = VideoExportPanel()
    emitted = []
    panel.frameSizeChanged.connect(emitted.append)
    _select_size(panel, "auto")
    _select_size(panel, "preset", width=4680, height=2160)
    assert panel.current_safe_frame_size() == (4680, 2160)
    assert emitted[-1] == (4680, 2160)

    panel.orientation_buttons["portrait"].setChecked(True)
    assert panel.current_safe_frame_size() == (2160, 4680)
    assert emitted[-1] == (2160, 4680)

    _select_size(panel, "custom")
    panel.frame_width_spin.setValue(1200)
    panel.frame_height_spin.setValue(800)
    assert emitted[-1] == (1200, 800)

    state = panel.current_state()
    panel.set_state({**state, "custom_width": 1000, "custom_height": 1000})
    assert emitted[-1] == (1000, 1000)
    panel.frame_width_spin.setValue(1001)
    assert emitted[-1] == (1002, 1000)
    _select_size(panel, "auto")
    assert emitted[-1] is None
    panel.close()


def test_preview_guide_tracks_panel_and_stays_out_of_export(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path)
    canvas = EditorPreviewCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(400, 300)
    pixmap.fill(QColor("black"))
    canvas.set_source_pixmap(pixmap, log_performance=False)
    canvas.set_crop_effect_box((0.1, 0.1, 0.9, 0.9))
    canvas.show()
    _APP.processEvents()
    panel = VideoExportPanel()
    panel.frameSizeChanged.connect(canvas.set_video_safe_frame_size)

    before_export = canvas.render_source_pixmap_with_overlays().toImage()
    before_display = canvas.grab().toImage()
    _select_size(panel, "auto")
    _select_size(panel, "preset", width=4680, height=2160)
    rect = canvas._video_safe_frame_rect(canvas._display_rect())
    assert rect.width() / rect.height() == pytest.approx(4680 / 2160)
    labels = []
    original_label_painter = canvas._draw_crop_resolution_label
    canvas._draw_crop_resolution_label = lambda _p, _r, text, _c, **_kw: labels.append(text)
    assert canvas.grab().toImage() != before_display
    assert labels == ["视频安全框 · 4680 × 2160"]
    canvas._draw_crop_resolution_label = original_label_painter
    assert canvas.render_source_pixmap_with_overlays().toImage() == before_export

    canvas.set_display_scale_percent(150)
    scaled_rect = canvas._video_safe_frame_rect(canvas._display_rect())
    assert scaled_rect.width() / scaled_rect.height() == pytest.approx(4680 / 2160)
    assert canvas.render_source_pixmap_with_overlays().toImage() == before_export

    _select_size(panel, "auto")
    assert canvas._video_safe_frame_size is None
    panel.close()
    canvas.close()
