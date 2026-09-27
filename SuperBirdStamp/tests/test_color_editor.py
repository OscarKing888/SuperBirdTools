from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QColorDialog, QDialog

from birdstamp import config
from birdstamp.gui import color_editor
from birdstamp.gui.color_editor import ColorEditor, AdvancedColorDialog, PaletteStore
from birdstamp.gui.editor_crop_padding_widget import CropPaddingEditorWidget
from birdstamp.gui.editor_template_dialog import TemplateManagerDialog
from birdstamp.gui import editor_template

_APP = QApplication.instance() or QApplication([])


def test_palette_survives_new_dialog_and_naming_does_not_clear_swatches(tmp_path):
    store = PaletteStore(tmp_path / 'palette.json')
    dialog = AdvancedColorDialog('#FFFFFF', store=store)
    QColorDialog.setCustomColor(0, QColor('#123456'))
    dialog.palette_names.setEditText('鸟羽配色')
    dialog.save_palette()
    assert store.read()['鸟羽配色'][0] == '#123456'
    dialog.close()
    reloaded = AdvancedColorDialog('#FFFFFF', store=PaletteStore(store.path))
    assert QColorDialog.customColor(0).name() == '#123456'
    reloaded.close()


def test_swatch_and_icon_open_same_dialog_picker_commits_once(monkeypatch):
    widget = ColorEditor('#FFFFFF', allow_none=True, allow_alpha=True)
    calls, emitted = [], []
    widget.colorChanged.connect(emitted.append)
    class Dialog:
        def __init__(self, value, parent, **kwargs):
            calls.append(value)
            self.picker = self
        def exec(self): return QDialog.DialogCode.Accepted
        def currentColor(self): return QColor('#123456')
        def deleteLater(self): pass
    monkeypatch.setattr(color_editor, 'AdvancedColorDialog', Dialog)
    QTest.mouseClick(widget.swatch, Qt.MouseButton.LeftButton)
    QTest.mouseClick(widget.palette_button, Qt.MouseButton.LeftButton)
    assert calls == ['#FFFFFF', '#123456']
    assert emitted == ['#123456']
    monkeypatch.setattr(color_editor.editor_utils, 'start_screen_color_picker',
                        lambda **kwargs: kwargs['on_picked']('#ABCDEF'))
    widget.picker_button.click()
    assert widget.value() == '#ABCDEF'
    widget.set_value('none')
    assert widget.value() == 'none'
    assert len(emitted) == 2
    widget.edit.setText('invalid')
    assert widget.value() == 'none'
    widget.set_value('#12345680')
    assert widget.value() == '#12345680'
    widget.close()


def test_all_template_colors_and_effect_settings_save_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    monkeypatch.setattr(TemplateManagerDialog, '_refresh_preview', lambda self: None)
    dialog = TemplateManagerDialog(tmp_path / 'templates', Image.new('RGB', (300,200)))
    assert isinstance(dialog.field_color_editor, ColorEditor)
    assert isinstance(dialog.banner_color_editor, ColorEditor)
    assert isinstance(dialog._gradient_editor._top_color, ColorEditor)
    assert isinstance(dialog._gradient_editor._bot_color, ColorEditor)
    dialog.field_effect_widgets['shadow_enabled'].setChecked(True)
    dialog.field_effect_widgets['shadow_color'].set_value('#127856', emit=True)
    dialog.field_effect_widgets['stroke_enabled'].setChecked(True)
    dialog.field_effect_widgets['stroke_width'].setValue(4.5)
    dialog.banner_color_editor.set_value('none', emit=True)
    path = dialog.template_paths[dialog.current_template_name]
    loaded = editor_template.load_template_payload(path)
    field = loaded['fields'][dialog.field_list.currentRow()]
    assert field['shadow_enabled'] and field['stroke_enabled']
    assert field['shadow_color'] == '#127856'
    assert field['stroke_width'] == 4.5
    assert loaded['banner_color'] == 'none'
    padding = CropPaddingEditorWidget()
    padding.fill_editor.set_value('#246810', emit=True)
    assert padding.get_values()['crop_padding_fill'] == '#246810'
    padding.close()
    dialog.close()


def test_typing_hex_does_not_expand_midway_or_move_cursor():
    widget = ColorEditor()
    widget.edit.clear()
    QTest.keyClicks(widget.edit, '#123456')
    assert widget.edit.text() == '#123456'
    assert widget.value() == '#123456'
    widget.edit.setCursorPosition(2)
    QTest.keyClick(widget.edit, Qt.Key.Key_Delete)
    QTest.keyClicks(widget.edit, 'a')
    assert widget.edit.cursorPosition() == 3
    assert widget.value() == '#1A3456'
    widget.close()
