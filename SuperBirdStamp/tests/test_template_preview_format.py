"""横竖画幅试览不写模板，布局与构图线使用同一个临时裁切计划。"""
import copy

import pytest
from PIL import Image
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication
from birdstamp import config
from birdstamp.gui import editor_template as templates
from birdstamp.gui.editor_template_dialog import TemplateManagerDialog
from birdstamp.gui.template_preview_format import template_preview_settings

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('ratio', [None, 'free', 'no_crop', 1.5, 2/3])
@pytest.mark.parametrize('orientation', ['landscape', 'portrait'])
def test_temporary_settings_never_mutate_saved_crop(ratio, orientation):
    payload = dict(ratio=ratio, crop_box=[.1, .2, .9, .8], center_mode='custom', fields=[{'tag': 'bird'}])
    original = copy.deepcopy(payload)
    result = template_preview_settings(payload, (1200, 800), orientation=orientation)
    expected = 1.5 if orientation == 'landscape' else 2/3
    assert result['ratio'] == pytest.approx(expected)
    assert result['crop_box'] is None
    assert payload == original
    assert template_preview_settings(payload, (1200, 800)) == original


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    monkeypatch.setattr(TemplateManagerDialog, '_load_preview_source', lambda self: None)
    monkeypatch.setattr(TemplateManagerDialog, '_preview_source_bird_box', lambda self: None)
    folder = tmp_path / 'templates'
    folder.mkdir()
    payload = dict(name='画幅测试', ratio='no_crop', center_mode='image', fields=[],
                   max_long_edge=0, crop_padding_top=0, crop_padding_bottom=0,
                   crop_padding_left=0, crop_padding_right=0)
    templates.save_template_payload(folder / '画幅测试.json', payload)
    source = Image.new('RGB', (1200, 800), '#608090')
    widget = TemplateManagerDialog(folder, source)
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()
    source.close()


def choose(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)
    _APP.processEvents()


def test_landscape_portrait_square_restore_without_template_writes(dialog):
    path = dialog.template_paths[dialog.current_template_name]
    saved = path.read_bytes()
    payload = copy.deepcopy(dialog.current_payload)
    original_size = dialog._preview_crop_size
    choose(dialog.preview_ratio_combo, 1.5)
    choose(dialog.preview_orientation_combo, 'portrait')
    assert dialog._preview_crop_size[0] / dialog._preview_crop_size[1] == pytest.approx(2/3, abs=.002)
    assert dialog.preview_ratio_combo.currentText() == '2:3'
    assert dialog.preview_label.canvas._crop_pixel_context.ratio == pytest.approx(2/3)
    assert not dialog.crop_edit_mode_check.isEnabled()
    dialog._on_tmpl_canvas_crop_box_changed((.1, .1, .9, .9))
    assert dialog.current_payload == payload
    choose(dialog.preview_orientation_combo, 'landscape')
    assert dialog._preview_crop_size == (1200, 800)
    choose(dialog.preview_ratio_combo, 1.)
    assert dialog._preview_crop_size[0] == dialog._preview_crop_size[1]
    choose(dialog.preview_orientation_combo, 'template')
    choose(dialog.preview_ratio_combo, None)
    assert dialog._preview_crop_size == original_size
    assert dialog.crop_edit_mode_check.isEnabled()
    assert dialog.current_payload == payload
    assert path.read_bytes() == saved


def test_overlay_layout_and_grid_follow_portrait_without_rotating_source(dialog, tmp_path):
    original_pixels = dialog.placeholder.tobytes()
    dialog.overlay_panel.add('text')
    dialog.overlay_panel.edit('text', '模板横竖版预览')
    path = dialog.template_paths[dialog.current_template_name]
    saved = path.read_bytes()
    choose(dialog.preview_orientation_combo, 'portrait')
    choose(dialog.preview_ratio_combo, 1.5)
    scene = dialog.overlay_session.scene
    assert scene is not None
    assert scene.size[0] / scene.size[1] == pytest.approx(2/3, abs=.002)
    assert dialog.placeholder.tobytes() == original_pixels
    dialog.overlay_edit_check.setChecked(False)
    dialog.show_focus_box_check.setChecked(False)
    dialog.show_bird_box_check.setChecked(False)
    canvas = dialog.preview_label.canvas
    no_grid = canvas.render_source_pixmap_with_overlays().toImage()
    choose(dialog.preview_grid_combo, 'thirds')
    with_grid = canvas.render_source_pixmap_with_overlays().toImage()
    box = dialog.preview_overlay_state.crop_effect_box
    left, top, right, bottom = box
    x = round((left + (right-left)/3) * with_grid.width())
    y = round((top + (bottom-top)/2) * with_grid.height())
    assert any(with_grid.pixelColor(x+dx, y) != no_grid.pixelColor(x+dx, y) for dx in (-1, 0, 1))
    assert with_grid.pixelColor(10, y) == no_grid.pixelColor(10, y)
    assert canvas.save_source_pixmap_with_overlays(str(tmp_path / 'portrait-grid.png'), 'PNG')
    assert path.read_bytes() == saved
    dialog.resize(1500, 850)
    dialog.show()
    _APP.processEvents()
    assert dialog.grab().save(str(tmp_path / 'template-portrait-ui.png'))


def test_custom_crop_restored_after_preview_override(dialog):
    dialog.current_payload.update(ratio='free', crop_box=[.1, .2, .9, .8], center_mode='custom')
    dialog._refresh_preview()
    original = dialog._preview_crop_size
    choose(dialog.preview_ratio_combo, 1.)
    assert dialog._preview_crop_size[0] == dialog._preview_crop_size[1]
    choose(dialog.preview_ratio_combo, None)
    assert dialog._preview_crop_size == original
    assert dialog.current_payload['crop_box'] == [.1, .2, .9, .8]
