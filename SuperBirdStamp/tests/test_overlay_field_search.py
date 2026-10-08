"""新增/已有元数据图层共用搜索条，搜索不提交，选择支持撤销。"""
from copy import deepcopy
import pytest
from PyQt6.QtCore import Qt, QCoreApplication, QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox
from birdstamp import config
from birdstamp.gui.overlay_panel import OverlayPanel
from birdstamp.gui.filterable_combo import FilterableComboBox
from app_common.filterable_combo import FilterableComboBox as SharedFilterableComboBox
from birdstamp.gui.editor_template_dialog import _FilterableComboBox

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    widget = OverlayPanel()
    widget.set_document({'fields': []}, 'template:search')
    widget.add('text', metadata=True)
    widget.show()
    _APP.processEvents()
    yield widget
    widget.metadata.hidePopup()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('query', ['拼音', 'AUTO PINYIN'])
def test_filter_and_keyboard_choice_commit_once_and_undo(panel, query):
    combo = panel.metadata
    before = deepcopy(panel.doc)
    changes = []
    panel.changed.connect(changes.append)
    combo.showPopup()
    search, results = combo._filter_popup_filter, combo._filter_popup_list
    assert search.isVisible() and '搜索字段' in search.placeholderText()
    search.setText(query)
    assert results.count() == 1
    assert 'bird_species_pinyin' in results.item(0).text()
    assert panel.doc == before and not changes
    QTest.keyClick(search, Qt.Key.Key_Down)
    QTest.keyClick(results, Qt.Key.Key_Return)
    _APP.processEvents()
    assert panel.selected()['text_source'] == {'type': 'auto', 'key': 'bird_species_pinyin'}
    assert len(changes) == 1
    panel.undo()
    assert panel.doc == before
    panel.redo()
    assert panel.selected()['text_source']['key'] == 'bird_species_pinyin'


def test_empty_clear_escape_and_reopen_do_not_modify_existing_layer(panel):
    combo = panel.metadata
    panel.edit('text_source', {'type': 'auto', 'key': 'iso'})
    before = deepcopy(panel.doc)
    combo.showPopup()
    search = combo._filter_popup_filter
    search.setText('不存在的元数据字段')
    assert combo._filter_popup_list.item(0).text() == '无匹配结果'
    QTest.keyClick(search, Qt.Key.Key_Return)
    assert panel.doc == before
    search.clear()
    assert combo._filter_popup_list.count() == combo.count()
    QTest.keyClick(search, Qt.Key.Key_Escape)
    assert panel.doc == before and combo._filter_popup is None
    # 旧 popup 的延迟销毁不能清空新 popup 的引用。
    combo.showPopup()
    new_popup = combo._filter_popup
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert combo._filter_popup is new_popup
    assert combo._filter_popup_filter.text() == ''
    assert combo.currentData() == ('auto', 'iso')


def test_custom_placeholder_still_editable_without_inserting_empty_payload(panel):
    combo = panel.metadata
    assert isinstance(combo, FilterableComboBox)
    assert _FilterableComboBox is FilterableComboBox
    assert FilterableComboBox is SharedFilterableComboBox
    assert isinstance(panel.font, SharedFilterableComboBox)
    assert combo.insertPolicy() == QComboBox.InsertPolicy.NoInsert
    count = combo.count()
    combo.setEditText('custom_bird_field')
    combo.lineEdit().editingFinished.emit()
    assert panel.selected()['text_source'] == {'type': 'auto', 'key': 'custom_bird_field'}
    assert combo.count() == count
    combo.showPopup()
    combo._filter_popup_filter.setText('bird_species_pinyin')
    QTest.keyClick(combo._filter_popup_filter, Qt.Key.Key_Return)
    assert panel.selected()['text_source'] == {'type': 'auto', 'key': 'bird_species_pinyin'}


def test_mouse_can_choose_first_field_after_custom_placeholder(panel):
    combo = panel.metadata
    panel.edit('text_source', {'type': 'auto', 'key': 'custom_bird_field'})
    combo.showPopup()
    combo._filter_popup_filter.setText('bird_species_cn')
    results = combo._filter_popup_list
    _APP.processEvents()
    item = results.item(0)
    QTest.mouseClick(results.viewport(), Qt.MouseButton.LeftButton, pos=results.visualItemRect(item).center())
    assert panel.selected()['text_source'] == {'type': 'auto', 'key': 'bird_species_cn'}


def test_template_manager_popup_receives_native_keyboard_without_editing_field(tmp_path, monkeypatch):
    import time
    from PIL import Image
    from PyQt6.QtWidgets import QStyle, QStyleOptionComboBox
    from birdstamp.gui.editor_template import default_template_payload, save_template_payload
    from birdstamp.gui.editor_template_dialog import TemplateManagerDialog

    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    monkeypatch.setattr(TemplateManagerDialog, '_load_preview_source', lambda self: None)
    folder = tmp_path / 'templates'
    folder.mkdir()
    save_template_payload(folder / 'test.json', default_template_payload())
    dialog = TemplateManagerDialog(folder, Image.new('RGB', (800, 450)))
    try:
        dialog.show()
        dialog.activateWindow()
        deadline = time.monotonic() + 2
        while _APP.focusWindow() is not dialog.windowHandle() and time.monotonic() < deadline:
            _APP.processEvents()
            QTest.qWait(5)
        assert _APP.focusWindow() is dialog.windowHandle()
        panel = dialog.overlay_panel
        panel.add('text', metadata=True)
        panel.focus_content()
        _APP.processEvents()
        combo = panel.metadata
        before = deepcopy(panel.doc)
        text_before = combo.currentText()
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        arrow = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, combo)
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=arrow.center())
        search = combo._filter_popup_filter
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if _APP.focusWindow() is not None and _APP.focusWindow().focusObject() is search:
                break
            _APP.processEvents()
            QTest.qWait(5)
        assert search is not None and _APP.focusWindow().focusObject() is search
        for key in (Qt.Key.Key_I, Qt.Key.Key_S, Qt.Key.Key_O):
            QTest.keyClick(_APP.focusWindow(), key)
        assert search.text() == 'iso'
        assert combo.currentText() == text_before
        assert panel.doc == before
        assert combo._filter_popup_list.count() > 0
        QTest.keyClick(_APP.focusWindow(), Qt.Key.Key_Escape)
        assert combo._filter_popup is None
    finally:
        dialog.overlay_panel.metadata.hidePopup()
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
