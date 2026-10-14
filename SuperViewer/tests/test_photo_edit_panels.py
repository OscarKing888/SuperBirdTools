"""Real widgets: gesture history, complete parameter forms and ordered steps."""
import time

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common.photo_edit.catalog import ADJUSTMENT_TITLES,normalize_camera,normalize_adjustment
from app_common.photo_edit.qt_controls import ScalarEditor,CurveEditor,ColorWheel
from SuperViewer.superviewer.photo_edit_forms import CameraRawForm,AdjustmentForm
from SuperViewer.superviewer.photo_edit_controller import PhotoEditController
from SuperViewer.superviewer.image_info_tab_camera_raw import ImageInfoTabPanel_CameraRaw
from SuperViewer.superviewer.image_info_tab_color_adjust import ImageInfoTabPanel_ColorAdjust

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate,timeout=6):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return bool(predicate())


@pytest.fixture
def panels(tmp_path):
    source = tmp_path/"编辑中文照片.png"
    Image.new("RGB",(48,32),"#426896").save(source)
    controller = PhotoEditController(directory=tmp_path/"state")
    camera = ImageInfoTabPanel_CameraRaw(controller)
    colors = ImageInfoTabPanel_ColorAdjust(controller)
    camera.resize(360,650)
    colors.resize(360,650)
    camera.on_photo_selected(str(source))
    colors.on_photo_selected(str(source))
    assert wait_for(lambda:controller.session() is not None)
    yield controller,camera,colors
    controller.request_shutdown()
    assert wait_for(controller.is_shutdown_done)
    camera.deleteLater()
    colors.deleteLater()
    controller.deleteLater()
    _APP.processEvents()


def test_camera_groups_and_shared_history_across_panels(panels):
    controller,camera,colors = panels
    assert len(camera.form.groups)==7
    exposure = camera.form.findChildren(ScalarEditor)[0]
    exposure.slider.sliderPressed.emit()
    exposure.slider.setValue(25)
    exposure.slider.setValue(50)
    exposure.slider.sliderReleased.emit()
    assert controller.session().state.current.recipe.camera["exposure"]==.5
    assert colors.undo_button.isEnabled()
    colors.undo_button.click()
    assert controller.session().state.current.recipe.camera["exposure"]==0
    assert not controller.session().history.can_undo
    colors.redo_button.click()
    assert exposure.value()==.5


def test_steps_repeat_reorder_disable_remove_and_undo(panels):
    controller,camera,colors = panels
    colors.kind.setCurrentIndex(colors.kind.findData("exposure"))
    colors.add_step()
    colors.kind.setCurrentIndex(colors.kind.findData("invert"))
    colors.add_step()
    assert [s.kind for s in controller.session().state.current.recipe.steps]==["exposure","invert"]
    colors.move_step(-1)
    assert [s.kind for s in controller.session().state.current.recipe.steps]==["invert","exposure"]
    colors.steps.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert not controller.session().state.current.recipe.steps[0].enabled
    colors.remove_step()
    assert len(controller.session().state.current.recipe.steps)==1
    camera.undo_button.click()
    assert len(controller.session().state.current.recipe.steps)==2


def test_all_adjustment_forms_keep_valid_defaults_and_accept_edits():
    for kind in ADJUSTMENT_TITLES:
        form = AdjustmentForm(kind)
        assert normalize_adjustment(kind,form.values)==form.values
        changes = []
        form.changed.connect(lambda value,commit:changes.append((value,commit)))
        scalars = form.findChildren(ScalarEditor)
        if scalars:
            widget = scalars[0]
            widget.spin.setValue(widget.value()+widget.spin.singleStep())
            assert changes,kind
            normalize_adjustment(kind,changes[-1][0])
        form.deleteLater()
    _APP.processEvents()


def test_camera_curves_color_wheel_keyboard_and_collapse_preserve_parameters(panels):
    controller,camera,_ = panels
    curve = camera.form.findChild(CurveEditor)
    curve.selected = 1
    QTest.keyClick(curve,Qt.Key.Key_Down)
    assert controller.session().state.current.recipe.camera["pointCurve"][1][1]==254
    wheel = camera.form.findChild(ColorWheel)
    QTest.keyClick(wheel,Qt.Key.Key_Up)
    assert controller.session().state.current.recipe.camera["gradeShadows"]["sat"]==1
    signature = controller.session().state.current.recipe.signature()
    group = camera.form.groups["色彩分级"]
    group.set_expanded(True)
    group.set_expanded(False)
    assert controller.session().state.current.recipe.signature()==signature
    normalize_camera(controller.session().state.current.recipe.camera)


def test_same_input_histogram_is_real_and_fixed_actions_fit_narrow_panel(panels):
    pytest.importorskip("app_common.photo_edit._native")
    controller,camera,_ = panels
    camera.resize(340,580)
    camera.show()
    assert wait_for(lambda:camera.histogram.histogram is not None)
    assert camera.histogram.histogram["samples"]==48*32
    assert camera.export_button.isVisible()
    assert camera.export_button.mapTo(camera,camera.export_button.rect().bottomRight()).y()<camera.height()
    camera.hide()
