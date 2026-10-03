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
    menu = a.overlays.grid_button.menu().actions()[0].menu()
    menu.aboutToShow.emit()
    assert len(menu.actions()) == 6
    next(action for action in menu.actions() if action.data() == 'crosshair').trigger()
    assert a.preview.composition_grid_mode() == 'crosshair'
    assert b.preview.composition_grid_mode() == 'none'
