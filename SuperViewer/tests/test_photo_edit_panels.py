"""Real widgets: gesture history, complete parameter forms and ordered steps."""
import time

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPalette
from PyQt6.QtTest import QTest, QSignalSpy
from PyQt6.QtWidgets import QApplication, QComboBox, QScrollArea, QFileDialog

from app_common.photo_edit.catalog import ADJUSTMENT_TITLES,normalize_camera,normalize_adjustment
from app_common.photo_edit.qt_controls import ScalarEditor,CurveEditor,ColorWheel
from app_common.photo_edit.model import AdjustmentStep
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


def group_for(colors, kind):
    return next(group for group in colors.groups if group.kind == kind)


def test_flat_groups_start_expanded_without_editing_and_enable_in_display_order(panels):
    controller, _, colors = panels
    session = controller.session()
    assert [group.kind for group in colors.groups] == list(ADJUSTMENT_TITLES)
    assert all(group.is_expanded() for group in colors.groups)
    assert all(not group.enabled.isChecked() and not group.form.isEnabled() for group in colors.groups)
    assert not session.dirty and not session.history.can_undo
    assert not session.state.current.recipe.steps
    groups = list(colors.groups)
    # Starting at the bottom must not make that effect run before earlier groups.
    for group in reversed(groups):
        group.enabled.click()
        assert colors.groups == groups
    assert [step.kind for step in session.state.current.recipe.steps] == list(ADJUSTMENT_TITLES)
    assert all(group.form.isEnabled() for group in groups)
    colors.undo_button.click()
    assert not groups[0].enabled.isChecked()
    assert colors.groups == groups
    colors.redo_button.click()
    assert groups[0].enabled.isChecked()


def test_steps_repeat_reorder_disable_remove_and_undo(panels):
    controller,camera,colors = panels
    exposure = group_for(colors, "exposure")
    invert = group_for(colors, "invert")
    exposure.enabled.click()
    exposure.form.findChildren(ScalarEditor)[0].spin.setValue(1.25)
    invert.enabled.click()
    assert [s.kind for s in controller.session().state.current.recipe.steps]==["exposure","invert"]
    invert.move_up.trigger()
    assert [s.kind for s in controller.session().state.current.recipe.steps]==["invert","exposure"]
    assert [group.step_id for group in colors.groups if group.step_id] == [s.id for s in controller.session().state.current.recipe.steps]
    invert.enabled.click()
    assert not controller.session().state.current.recipe.steps[0].enabled
    exposure.duplicate.trigger()
    steps = controller.session().state.current.recipe.steps
    assert [s.kind for s in steps] == ["invert", "exposure", "exposure"]
    assert steps[1].id != steps[2].id and steps[1].params == steps[2].params
    assert len(colors.groups) == 17
    duplicate = next(group for group in colors.groups if group.step_id == steps[2].id)
    duplicate.form.findChildren(ScalarEditor)[0].spin.setValue(2.5)
    assert exposure.form.values["exposure"] == 1.25
    duplicate.remove.trigger()
    assert len(controller.session().state.current.recipe.steps)==2
    camera.undo_button.click()
    assert len(controller.session().state.current.recipe.steps)==3
    assert controller.session().state.current.recipe.steps[2].params["exposure"] == 2.5


def test_flat_groups_keep_gesture_history_folds_channels_and_focus(panels):
    controller, _, colors = panels
    exposure = group_for(colors, "exposure")
    exposure.enabled.click()
    curves = group_for(colors, "curves")
    curves.enabled.click()
    channel = curves.form.findChild(QComboBox)
    channel.setCurrentIndex(2)
    curves.set_expanded(False)
    scalar = exposure.form.findChildren(ScalarEditor)[0]
    colors.show()
    scroll = colors.findChild(QScrollArea)
    scroll.ensureWidgetVisible(scalar)
    scalar.spin.setFocus()
    assert wait_for(scalar.spin.hasFocus)
    scroll_before = scroll.verticalScrollBar().value()
    scalar.slider.sliderPressed.emit()
    scalar.slider.setValue(25)
    scalar.slider.setValue(50)
    scalar.slider.sliderReleased.emit()
    assert exposure.form.values["exposure"] == .5
    assert scalar.spin.hasFocus()
    assert scroll.verticalScrollBar().value() == scroll_before
    colors.undo_button.click()
    assert exposure.form.values["exposure"] == 0
    colors.redo_button.click()
    assert exposure.form.values["exposure"] == .5
    assert not curves.is_expanded() and channel.currentIndex() == 2
    signature = controller.session().state.current.recipe.signature()
    serial = controller.session().serial
    changes = QSignalSpy(controller.state_changed)
    edits = QSignalSpy(colors.editing_started)
    for _ in range(3):
        colors.collapse_all.click()
        assert all(not group.is_expanded() for group in colors.groups)
        QTest.keyClick(exposure.header_button, Qt.Key.Key_Space)
        assert exposure.is_expanded()
        colors.expand_all.click()
    assert all(group.is_expanded() for group in colors.groups)
    assert not changes and not edits
    assert controller.session().serial == serial
    assert controller.session().state.current.recipe.signature() == signature
    assert channel.currentIndex() == 2
    colors.hide()


def test_loaded_recipe_keeps_custom_order_duplicate_values_and_disabled_state(panels):
    controller, _, colors = panels
    recipe = controller.session().state.current.recipe.validated()
    recipe.steps = [AdjustmentStep("invert"), AdjustmentStep("exposure", {"exposure": 2}),
                    AdjustmentStep("exposure", {"exposure": -1}, enabled=False)]
    controller.edit(recipe)
    before = controller.session().state.current.recipe.to_dict()
    colors._update_state()
    groups = [group for group in colors.groups if group.step_id]
    assert [group.step_id for group in groups] == [step.id for step in recipe.steps]
    assert [group.position.text() for group in groups] == ["第 1 步", "第 2 步", "第 3 步"]
    assert groups[1].form.values["exposure"] == 2
    assert groups[2].form.values["exposure"] == -1
    assert not groups[2].enabled.isChecked()
    assert controller.session().state.current.recipe.to_dict() == before
    colors.reset_button.click()
    assert len(colors.groups) == 16 and not controller.session().state.current.recipe.steps
    assert all(not group.enabled.isChecked() for group in colors.groups)
    colors.undo_button.click()
    assert controller.session().state.current.recipe.to_dict() == before


def test_flat_groups_restore_each_photo_without_leaking_parameters(panels, tmp_path):
    controller, _, colors = panels
    original = colors.current_photo_path()
    exposure = group_for(colors, "exposure")
    exposure.enabled.click()
    exposure.form.findChildren(ScalarEditor)[0].spin.setValue(1.25)
    exposure.enabled.click()
    exposure.set_expanded(False)
    original_recipe = controller.session().state.current.recipe.to_dict()
    other = tmp_path / "另一张照片.png"
    Image.new("RGB", (48, 32), "#112233").save(other)
    colors.on_photo_selected(str(other))
    assert wait_for(lambda: controller.session() is not None)
    assert not controller.session().dirty
    assert not controller.session().state.current.recipe.steps
    assert exposure.form.values["exposure"] == 0
    assert not exposure.enabled.isChecked() and not exposure.is_expanded()
    group_for(colors, "invert").enabled.click()
    colors.on_photo_selected(original)
    assert wait_for(lambda: controller.session() is not None)
    assert controller.session().state.current.recipe.to_dict() == original_recipe
    assert exposure.form.values["exposure"] == 1.25
    assert not exposure.enabled.isChecked() and not exposure.form.isEnabled()
    assert not group_for(colors, "invert").enabled.isChecked()
    exposure.enabled.click()
    assert exposure.form.values["exposure"] == 1.25


def test_theme_change_keeps_flat_forms_and_edit_state(panels):
    from SuperViewer.superviewer.ui_theme import build_palette
    controller, _, colors = panels
    groups = list(colors.groups)
    group_for(colors, "exposure").enabled.click()
    group_for(colors, "exposure").set_expanded(False)
    signature = controller.session().state.current.recipe.signature()
    changes = QSignalSpy(controller.state_changed)
    for theme in ("dark", "light", "dark"):
        palette = build_palette(theme)
        colors.setPalette(palette)
        _APP.processEvents()
        assert colors.groups == groups
        for group in groups:
            assert group.palette().color(QPalette.ColorRole.Base) == palette.color(QPalette.ColorRole.Base)
            assert group.header_button.palette().color(QPalette.ColorRole.WindowText) == palette.color(QPalette.ColorRole.WindowText)
        assert not group_for(colors, "exposure").is_expanded()
    assert not changes
    assert controller.session().state.current.recipe.signature() == signature


def test_all_groups_fit_large_font_and_narrow_scroll_area(panels):
    _, _, colors = panels
    font = QFont(colors.font())
    font.setPointSize(16)
    colors.setFont(font)
    # Explicit widget fonts model the inherited large font at app construction,
    # including styled sections whose fonts Qt resolves at polish time.
    for group in colors.groups:
        group.setFont(font)
    colors.resize(360, 640)
    colors.show()
    scroll = colors.findChild(QScrollArea)
    assert wait_for(lambda: scroll.verticalScrollBar().maximum() > 0)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert all(group.width() <= scroll.viewport().width() for group in colors.groups)
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    _APP.processEvents()
    last = colors.groups[-1]
    assert last.mapTo(scroll.viewport(), last.rect().bottomRight()).y() <= scroll.viewport().height()
    for button in (colors.undo_button, colors.redo_button, colors.reset_button, colors.export_button):
        assert colors.rect().contains(button.mapTo(colors, button.rect().bottomRight()))
    colors.hide()


def test_lut_import_stays_attached_to_requested_duplicate(panels, monkeypatch):
    controller, _, colors = panels
    first = group_for(colors, "colorLookup")
    first.enabled.click()
    first.duplicate.trigger()
    second = [group for group in colors.groups if group.kind == "colorLookup"][1]
    pending = {}
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: ("test.cube", ""))
    monkeypatch.setattr(controller, "run_operation", lambda name, prepare, *, apply: pending.update(apply=apply))
    second.form.lut_requested.emit()
    second.move_up.trigger()
    reference = dict(path="lut/test.cube", sha256="a" * 64)
    pending["apply"](controller.session(), reference)
    by_id = {step.id: step for step in controller.session().state.current.recipe.steps}
    assert by_id[second.step_id].params["asset"] == reference
    assert by_id[first.step_id].params["asset"] is None
    # A late import after removal must not attach to the other LUT group.
    second.remove.trigger()
    signature = controller.session().state.current.recipe.signature()
    pending["apply"](controller.session(), reference)
    assert controller.session().state.current.recipe.signature() == signature


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
