"""自动焦点居中：视口锁定、异步像素升级、独立成片与工作区。"""
import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QPixmap, QWheelEvent

from test_editor_dejitter import window, _APP
from test_dejitter_tab import setup_tab, analyze
from test_sequence_transport import populate, key
from test_reference_tracking import wait_until
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas, EditorPreviewOverlayState
from birdstamp.gui.editor_utils import path_key


@pytest.fixture
def canvas():
    widget = EditorPreviewCanvas()
    widget.resize(640, 480)
    widget.show()
    _APP.processEvents()
    widget.set_auto_focus_center(True)
    yield widget
    widget.close()


def pixmap(width=1200, height=800):
    result = QPixmap(width, height)
    result.fill(Qt.GlobalColor.black)
    return result


def test_focus_stays_centered_through_zoom_resize_and_hidden_focus_box(canvas):
    canvas.set_source_pixmap(pixmap())
    canvas.apply_overlay_state(EditorPreviewOverlayState(focus_box=(.88, .08, .92, .12)))
    canvas.set_show_focus_box(False)
    canvas.set_display_scale_percent(200)
    assert canvas._view_center_ratio() == pytest.approx((.9, .1))
    event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    canvas.wheelEvent(event)
    assert canvas._view_center_ratio() == pytest.approx((.9, .1))
    canvas.resize(800, 600)
    _APP.processEvents()
    assert canvas._view_center_ratio() == pytest.approx((.9, .1))
    assert not canvas._can_pan()
    canvas.set_auto_focus_center(False)
    assert canvas._can_pan()


@pytest.mark.parametrize('focus', [None, (float('nan'), 0, 1, 1), (.8, .1, .2, .5)])
def test_missing_focus_centers_image_and_loading_placeholder_preserves_zoom(canvas, focus):
    canvas.set_source_pixmap(pixmap())
    canvas.set_focus_box((.1, .2, .3, .4))
    canvas.set_display_scale_percent(200)
    zoom = canvas._zoom
    canvas.set_source_pixmap(None, reset_view=True)
    assert canvas._zoom == pytest.approx(zoom)
    canvas.apply_overlay_state(EditorPreviewOverlayState(focus_box=focus))
    canvas.set_source_pixmap(pixmap(512, 342), reset_view=True)
    assert canvas._zoom == pytest.approx(zoom)
    assert canvas._view_center_ratio() == pytest.approx((.5, .5))
    canvas.set_focus_box((.6, .7, .8, .9))  # 延迟到达的元数据也立即重新居中。
    assert canvas._view_center_ratio() == pytest.approx((.7, .8))
    canvas.set_source_pixmap(pixmap(2048, 1365), preserve_view=True, preserve_scale=True)
    assert canvas._zoom == pytest.approx(zoom)
    assert canvas._view_center_ratio() == pytest.approx((.7, .8))


def test_toolbar_workspace_roundtrip_and_no_export_setting_changes(window):
    assert window.auto_focus_center_check.text() == '自动焦点居中'
    assert not window.auto_focus_center_check.isChecked()
    before = window._build_current_render_settings()
    window.auto_focus_center_check.setChecked(True)
    assert window.preview_label.canvas._auto_focus_center
    saved = window._collect_workspace_preview_state()
    assert saved['auto_focus_center']
    window.auto_focus_center_check.setChecked(False)
    window._apply_workspace_preview_state(saved)
    assert window.auto_focus_center_check.isChecked()
    assert window.preview_label.canvas._auto_focus_center
    assert before == window._build_current_render_settings()
    window._apply_workspace_preview_state({})
    assert not window.auto_focus_center_check.isChecked()  # 旧工作区使用配置默认值。
    assert not window.preview_label.canvas._auto_focus_center


def test_result_playback_uses_transformed_focus_and_retains_zoom(window, monkeypatch):
    from PyQt6.QtCore import QEvent
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    window.auto_focus_center_check.setChecked(True)
    window.show_focus_box_check.setChecked(False)
    canvas = window.preview_label.canvas
    canvas.set_display_scale_percent(200)
    zoom = canvas._zoom
    monkeypatch.setattr(window, '_start_preview_decode_worker', lambda *args: pytest.fail('播放不能解码原图'))
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *args: pytest.fail('播放不能启动识别'))
    key(window, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    assert window.current_path == paths[1]
    left, top, right, bottom = canvas._focus_box
    expected = ((left + right) / 2, (top + bottom) / 2)
    assert canvas._view_center_ratio() == pytest.approx(expected)
    assert canvas._zoom == pytest.approx(zoom)
    assert not canvas._show_focus_box
    key(window, QEvent.Type.KeyRelease, Qt.Key.Key_Down)
    wait_until(lambda: path_key(paths[1]) in window._sequence_frames and window._sequence_worker is None)
    assert canvas._view_center_ratio() == pytest.approx(expected)
    assert canvas._zoom == pytest.approx(zoom)


def test_grid_overlay_export_is_independent_of_centering(canvas, tmp_path):
    canvas.set_source_pixmap(pixmap(120, 120))
    canvas.set_crop_effect_box((.25, .25, .75, .75))
    canvas.set_composition_grid_mode('thirds')
    canvas.set_show_focus_box(False)
    before = canvas.render_source_pixmap_with_overlays().toImage()
    canvas.apply_overlay_state(EditorPreviewOverlayState(focus_box=(.1, .1, .2, .2),
                                                         crop_effect_box=(.25, .25, .75, .75)))
    after = canvas.render_source_pixmap_with_overlays().toImage()
    assert after == before
    assert after.pixelColor(20, 60).red() == 0
    assert after.pixelColor(50, 60).red() > 0
    assert canvas.save_source_pixmap_with_overlays(str(tmp_path / '网格.png'), 'PNG')


def test_edit_quick_frames_reuse_analysis_focus_without_metadata_io(window, monkeypatch):
    from PyQt6.QtCore import QEvent
    from birdstamp.gui import editor_core
    paths, _, seeds = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    window.dejitter_view_tabs.setCurrentIndex(0)
    wait_until(lambda: window._preview_decode_worker is None)
    window.auto_focus_center_check.setChecked(True)
    window.current_raw_metadata = {}
    window.raw_metadata_cache.clear()
    window.photo_list_metadata_cache.clear()
    monkeypatch.setattr(window, '_start_preview_decode_worker', lambda *args: pytest.fail('长按不能解码原图'))
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *args: pytest.fail('长按不能启动识别'))
    key(window, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    canvas = window.preview_label.canvas
    focus = editor_core.resolve_focus_box_after_processing(seeds[1].raw_metadata,
                source_width=200, source_height=160, crop_box=None, apply_ratio_crop=False)
    assert canvas._view_center_ratio() == pytest.approx(((focus[0]+focus[2])/2, (focus[1]+focus[3])/2))
    assert len(canvas.reference_regions()) == 2
    window.sequence_transport.stop(commit=False)


def test_reference_handle_edit_remains_available_with_focus_lock(canvas):
    from PyQt6.QtCore import QEvent
    from test_reference_region_handles import send
    from birdstamp.gui.edit_modes import EDIT_MODE_REFERENCE_REGION
    canvas.set_source_pixmap(pixmap(600, 400))
    canvas.set_focus_box((.3, .3, .5, .5))
    canvas.set_reference_regions(((.3, .3, .5, .5), (.6, .6, .8, .8)))
    canvas.set_show_reference_regions(True)
    canvas.set_edit_mode(EDIT_MODE_REFERENCE_REGION)
    canvas.set_display_scale_percent(150)
    send(canvas, QEvent.Type.MouseButtonPress, (.5, .4))
    send(canvas, QEvent.Type.MouseButtonRelease, (.55, .4))
    assert canvas.reference_regions()[0] == pytest.approx((.3, .3, .55, .5))
    assert canvas.reference_regions()[1] == (.6, .6, .8, .8)
    assert canvas._view_center_ratio() == pytest.approx((.4, .4))
