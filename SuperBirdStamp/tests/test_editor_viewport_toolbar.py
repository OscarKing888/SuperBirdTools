"""A/B 视口各自拥有显示选项，工作区兼容旧的公共选项字段。"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from test_editor_dejitter import window, _APP
from test_editor_preview_sources import _prepare, _source
from test_reference_tracking import wait_until


def test_independent_icons_overlays_and_workspace(window, monkeypatch, tmp_path):
    monkeypatch.setattr('birdstamp.gui.bird_detect_worker.detect_primary_bird_box', lambda image: None)
    source = _source(tmp_path)
    _prepare(window, monkeypatch, [source])
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    wait_until(lambda: ab.worker is None and not ab.pending)
    a, b = ab.a_panel, ab.b_panel
    for viewport in (a, b):
        assert viewport.overlays.parent() is viewport.toolbar
        for button in (viewport.source_button, viewport.center, viewport.fit, viewport.zoom_button,
                       viewport.overlays.focus, viewport.overlays.bird, viewport.overlays.crop,
                       viewport.overlays.grid_button):
            assert not button.icon().isNull()
            assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
            assert button.accessibleName() and button.toolTip()
    b.overlays.focus.setChecked(True)
    a.overlays.focus.setChecked(False)
    a.overlays.bird.setChecked(False)
    a.overlays.crop.setChecked(False)
    a.overlays.alpha.setValue(77)
    a.overlays.grid.setCurrentIndex(a.overlays.grid.findData('thirds'))
    a.overlays.width.setCurrentIndex(a.overlays.width.findData(3))
    b.overlays.grid.setCurrentIndex(b.overlays.grid.findData('none'))
    assert not a.preview.canvas._show_focus_box and b.preview.canvas._show_focus_box
    assert not a.preview.canvas._show_bird_box and b.preview.canvas._show_bird_box
    assert window._build_preview_overlay_options(a.overlays).crop_effect_alpha == 77
    assert window._build_preview_overlay_options().crop_effect_alpha == 160
    state = window._collect_workspace_preview_state()
    assert state['a_overlays']['composition_grid_mode'] == 'thirds'
    assert state['composition_grid_mode'] == 'none'
    a.overlays.focus.setChecked(True)
    window._apply_workspace_preview_state(state)
    assert not a.overlays.focus.isChecked() and b.overlays.focus.isChecked()
    assert a.overlays.width.currentData() == 3 and b.overlays.width.currentData() == 1
    # 旧工作区迁移：两侧从旧公共设置初始化，以后可各自调整。
    state.pop('a_overlays')
    window._apply_workspace_preview_state(state)
    assert a.overlays.state() == b.overlays.state()


def test_a_bird_detection_uses_worker_and_actual_pixel_geometry(window, monkeypatch, tmp_path):
    import threading
    from app_common.raw_preview_geometry import map_camera_focus_box
    calls = []
    monkeypatch.setattr('birdstamp.gui.bird_detect_worker.detect_primary_bird_box',
        lambda image: calls.append(threading.get_ident()) or (.3, .35, .7, .65))
    source = _source(tmp_path)
    _prepare(window, monkeypatch, [source])
    ab = window.ab_preview
    ab.a_panel.overlays.bird.setChecked(False)
    ab.enabled.setChecked(True)
    wait_until(lambda: ab.worker is None and not ab.pending)
    window._bird_box_cache.clear()
    ab.camera_crop_box = (.1, .2, .9, .8)
    ab.a_panel.overlays.bird.setChecked(True)
    wait_until(lambda: ab.bird_worker is None)
    assert calls and all(value != threading.get_ident() for value in calls)
    cached = window._bird_box_cache[window._source_signature(source)]
    assert map_camera_focus_box(cached, ab.camera_crop_box) == (.3, .35, .7, .65)
    window._bird_box_cache.clear()
    calls.clear()
    monkeypatch.setattr(window, '_sequence_fast_preview_active', lambda: True)
    ab._schedule_bird_overlay()
    assert ab.bird_worker is None and not calls


def test_custom_grid_width_roundtrips_both_viewports_and_respects_crop(window):
    from PyQt6.QtGui import QColor
    a, b = window.ab_preview.a_panel.overlays, window.ab_preview.b_panel.overlays
    a.grid_menu.width_slider.setValue(7)
    b.grid_menu.width_edit.setText('11')
    b.grid_menu.width_edit.editingFinished.emit()
    state = window._collect_workspace_preview_state()
    a.grid_menu.width_slider.setValue(1)
    b.grid_menu.width_slider.setValue(1)
    window._apply_workspace_preview_state(state)
    assert a.width.currentData() == 7 and a.grid_menu.width_edit.text() == '7'
    assert b.width.currentData() == 11 and b.grid_menu.width_slider.value() == 11
    canvas = window.preview_label.canvas
    pixmap = QPixmap(120, 120)
    pixmap.fill(QColor('black'))
    canvas.set_source_pixmap(pixmap, log_performance=False)
    canvas.set_crop_effect_box((.25, .25, .75, .75))
    canvas.set_show_crop_effect(False)
    canvas.set_composition_grid_mode('thirds')
    image = canvas.render_source_pixmap_with_overlays().toImage()
    assert image.pixelColor(54, 60).red() > 0
    assert image.pixelColor(20, 60).red() == 0
