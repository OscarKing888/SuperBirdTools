"""百分比参数同步、滑动范围和一次手势撤销的真实 Qt 回归。"""
from copy import deepcopy

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest

from test_template_text_scale import _APP
from birdstamp import config
from birdstamp.gui.overlay_panel import OverlayPanel
from birdstamp.gui.percent_editor import PercentEditor
from birdstamp.overlays.model import new_item


@pytest.fixture
def panel(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'get_user_data_dir',lambda:tmp_path/'user')
    panel=OverlayPanel()
    panel.set_document(dict(overlays=[new_item('text'),new_item('image'),new_item('background')]),'photo:percent',following=True)
    try:
        yield panel
    finally:
        panel.close(); panel.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('key,kind,value,expected',[
    ('x_offset_pct','text',-35.5,-35.5),('y_offset_pct','text',42.25,42.25),
    ('x','text',62.5,.625),('y','text',-25,-.25),('scale','text',175,1.75),
    ('width','image',55.5,.555),('height','background',38.75,.3875),
    ('opacity','image',66.75,66.75),('shadow_opacity','text',35.5,35.5),
    ('stroke_opacity','text',42.25,42.25),
    ('banner_gradient_top_opacity_pct','background',28.25,28.25),
    ('banner_gradient_bottom_opacity_pct','background',65.75,65.75),
    ('banner_gradient_height_pct','background',45.5,45.5),
])
def test_all_percent_parameters_support_slider_numeric_and_undo(panel,key,kind,value,expected):
    item=next(i for i in panel.doc['overlays'] if i['type']==kind)
    if key.endswith('_offset_pct'): item['layout_mode']='auto'
    if key in ('stroke_opacity', 'shadow_opacity'): item[key.split('_')[0] + '_enabled'] = True
    panel.select(item['id'])
    before=deepcopy(panel.doc)
    editor=panel.widgets[key]
    assert isinstance(editor,PercentEditor)
    editor.slider.setValue(round(value*100))
    assert panel.selected()[key]==pytest.approx(expected)
    assert editor.spin.value()==pytest.approx(value)
    assert not panel.following
    panel.undo()
    assert panel.doc==before and panel.following
    editor.spin.setValue(value)
    assert panel.selected()[key]==pytest.approx(expected)
    assert editor.slider.value()==round(value*100)
    panel.edit('locked',True)
    assert not editor.slider.isEnabled() and not editor.spin.isEnabled()


@pytest.mark.parametrize('kind', ['text', 'badge'])
def test_effect_controls_follow_toggles_selection_lock_and_undo(panel, kind):
    panel.add(kind)
    selected_id = panel.selected_id
    for prefix in ('stroke', 'shadow'):
        toggle = panel.widgets[prefix + '_enabled']
        toggle.setChecked(False)
        controls = [(key, widget) for key, widget in panel.widgets.items()
                    if key.startswith(prefix + '_') and not key.endswith('_enabled')]
        assert toggle.isEnabled()
        assert all(not widget.isEnabled() for _, widget in controls)
        color = panel.widgets[prefix + '_color']
        assert not color.palette_button.isEnabled() and not color.picker_button.isEnabled()
        toggle.click()
        assert all(widget.isEnabled() for _, widget in controls)
        panel.widgets[prefix + '_opacity'].spin.setValue(37.25)
        toggle.click()
        assert all(not widget.isEnabled() for _, widget in controls)
        panel.undo()
        assert toggle.isChecked() and all(widget.isEnabled() for _, widget in controls)
        assert panel.selected()[prefix + '_opacity'] == 37.25
        panel.redo()
        assert not toggle.isChecked()
        panel.select(panel.doc['overlays'][0]['id'])
        panel.select(selected_id)
        assert all(not widget.isEnabled() for _, widget in controls)
        toggle.click()
    panel.edit('locked', True)
    assert all(not widget.isEnabled() for key, widget in panel.widgets.items()
               if key.startswith(('stroke_', 'shadow_')))
    panel.undo()
    assert panel.widgets['stroke_opacity'].isEnabled()
    assert panel.widgets['shadow_opacity'].isEnabled()


def test_large_values_expand_slider_without_changing_saved_value(panel):
    item=panel.doc['overlays'][0]
    item.update(x=-25.45678,scale=50)
    before=deepcopy(panel.doc)
    panel.select(item['id'])
    assert panel.doc==before
    x=panel.widgets['x']; scale=panel.widgets['scale']
    assert x.value()==pytest.approx(-2545.68)
    assert x.slider.minimum()<=x.slider.value()==-254568
    assert scale.slider.maximum()>=scale.slider.value()==500000
    scale.spin.setValue(7500)
    assert panel.selected()['scale']==75 and scale.slider.value()==750000
    # 切到另一层会恢复常用区间，避免大范围挤占正常参数的调整精度。
    panel.select(panel.doc['overlays'][1]['id'])
    assert scale.slider.maximum()==30000


def test_continuous_drag_is_one_undo_and_contexts_stay_separate(panel):
    panel.select(panel.doc['overlays'][0]['id'])
    editor=panel.widgets['opacity']
    original=deepcopy(panel.doc)
    editor.slider.setSliderDown(True)
    editor.slider.setSliderPosition(8000)
    editor.slider.setSliderPosition(5000)
    editor.slider.setSliderPosition(2575)
    assert panel.selected()['opacity']==25.75
    editor.slider.setSliderDown(False)
    panel.undo()
    assert panel.doc==original and panel.following
    assert not panel.undo_button.isEnabled()
    panel.redo()
    assert panel.selected()['opacity']==25.75
    panel.set_document(original,'photo:other',following=True)
    panel.select(original['overlays'][0]['id'])
    editor.slider.setValue(4000)
    panel.undo()
    assert panel.doc==original and panel.following


def test_slider_keyboard_steps_and_numeric_fraction(panel):
    panel.select(panel.doc['overlays'][0]['id'])
    editor=panel.widgets['opacity']
    editor.spin.setValue(50.25)
    QTest.keyClick(editor.slider,Qt.Key.Key_Right)
    assert panel.selected()['opacity']==51.25
    QTest.keyClick(editor.slider,Qt.Key.Key_Left)
    assert panel.selected()['opacity']==50.25
