"""视口图标与 A/B 选项隔离，包括真实网格像素和叠加导出。"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PIL import Image
from SuperViewer.tests.test_directory_selection_responsiveness import window


def test_viewport_icons_and_independent_overlay_export(window, tmp_path, monkeypatch):
    source = tmp_path / '白鹭.jpg'
    Image.new('RGB', (120, 90)).save(source)
    monkeypatch.setattr(window, '_update_preview_focus_box', lambda *a, **kw: None)
    window._on_file_selected_from_list(str(source))
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    a, b = ab.a_panel, ab.b_panel
    for viewport in (a, b):
        for button in (viewport.source_button, viewport.center, viewport.fit, viewport.zoom_button,
                       viewport.overlays.focus, viewport.overlays.bird, viewport.overlays.grid_button):
            assert not button.icon().isNull()
            assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
            assert button.accessibleName() and button.toolTip()
    assert a.overlays.parent() is a.toolbar and b.overlays.parent() is b.toolbar
    a.overlays.focus.setChecked(False)
    assert not a.preview._show_focus_enabled and b.preview._show_focus_enabled
    b.overlays.grid.setCurrentIndex(b.overlays.grid.findData('none'))
    a.overlays.grid.setCurrentIndex(a.overlays.grid.findData('thirds'))
    a.overlays.width.setCurrentIndex(a.overlays.width.findData(4))
    assert a.preview.composition_grid_mode() == 'thirds'
    assert b.preview.composition_grid_mode() == 'none'
    assert a.preview.canvas._composition_grid_line_width == 4
    for viewport in (a, b):
        pixmap = QPixmap(120, 90)
        pixmap.fill(Qt.GlobalColor.black)
        viewport.preview.canvas.set_source_pixmap(pixmap, log_performance=False)
    assert a.preview.canvas.render_source_pixmap_with_overlays().toImage().pixelColor(40, 45).red() > 0
    assert b.preview.canvas.render_source_pixmap_with_overlays().toImage().pixelColor(40, 45).red() == 0
    target = tmp_path / 'grid.png'
    assert a.preview.save_source_pixmap_with_overlays(str(target))
    with Image.open(target) as image:
        assert image.getpixel((40, 45))[0] > 0
    # 菜单提供全部模式，点击单选后仍只改变这一侧。
    menu = a.overlays.grid_button.menu()
    menu.aboutToShow.emit()
    assert len([action for action in menu.actions() if action.isCheckable()]) == 6
    next(action for action in menu.actions() if action.data() == 'crosshair').trigger()
    assert a.preview.composition_grid_mode() == 'crosshair'
    assert b.preview.composition_grid_mode() == 'none'


def test_custom_width_changes_pixels_and_persists(window, tmp_path, monkeypatch):
    from SuperViewer.superviewer.exif_helpers import load_preview_grid_line_width_from_settings
    source = tmp_path / 'custom.jpg'
    Image.new('RGB', (120, 90)).save(source)
    window._on_file_selected_from_list(str(source))
    controls = window.ab_preview.b_panel.overlays
    controls.grid.setCurrentIndex(controls.grid.findData('thirds'))
    narrow = window.preview_panel.canvas.render_source_pixmap_with_overlays().toImage()
    controls.grid_menu.width_edit.setText('9')
    controls.grid_menu.width_edit.editingFinished.emit()
    wide = window.preview_panel.canvas.render_source_pixmap_with_overlays().toImage()
    assert narrow.pixelColor(43, 45).red() == 0
    assert wide.pixelColor(43, 45).red() > 0
    assert window.preview_panel.canvas._composition_grid_line_width == 9
    assert load_preview_grid_line_width_from_settings() == 9
    assert window.ab_preview.a_panel.overlays.width.currentData() != 9

    from SuperViewer.tests.test_directory_selection_responsiveness import _wait_until
    restored = type(window)(initial_received_files=['skip-restore'])
    try:
        assert restored.combo_preview_grid_line_width.currentData() == 9
        assert restored.ab_preview.b_panel.overlays.grid_menu.width_slider.value() == 9
        assert restored.preview_panel.canvas._composition_grid_line_width == 9
    finally:
        restored.close()
        _wait_until(lambda: restored._shutdown_finalized)
        restored.deleteLater()
