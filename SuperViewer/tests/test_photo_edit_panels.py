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


def test_auto_adjust_keyboard_busy_state_result_and_shared_undo(panels, monkeypatch):
    import threading
    from app_common.photo_edit.auto_adjust import AutoAdjustmentResult
    from app_common.photo_edit.catalog import adjustment_defaults
    controller, camera, colors = panels
    started, release = threading.Event(), threading.Event()
    def analyze(snapshot, mode, **kwargs):
        assert mode == "all"
        started.set()
        assert release.wait(4)
        recipe = snapshot.current.recipe.validated()
        values = {
            "brightnessContrast": dict(brightness=14, contrast=6),
            "levels": dict(master=dict(in_black=.13, in_white=.88, gamma=1.06, out_black=0, out_white=1)),
            "curves": dict(master=[dict(input=0, output=0), dict(input=.5, output=.57), dict(input=1, output=1)]),
            "exposure": dict(exposure=.25, gamma=1.04),
            "vibrance": dict(vibrance=18),
            "colorBalance": dict(midtones=[-8, 0, 9]),
        }
        for kind, changes in values.items():
            params = adjustment_defaults(kind)
            params.update(changes)
            recipe.steps.append(AdjustmentStep(kind, params, id="auto-basic-" + kind))
        return AutoAdjustmentResult(recipe.validated(), mode, True, tuple(step.id for step in recipe.steps))
    monkeypatch.setattr("app_common.photo_edit.auto_adjust.prepare_auto_adjustment", analyze)
    colors.show()
    assert colors.auto_button.text() == "一键自动调节"
    assert not hasattr(colors, "auto_mode")
    colors.set_groups_expanded(False)
    group_for(colors, "levels").form.channel_selector.setCurrentIndex(2)
    signals = QSignalSpy(colors.editing_started)
    assert not controller.session().dirty
    try:
        colors.auto_button.setFocus()
        QTest.keyClick(colors.auto_button, Qt.Key.Key_Space)
        assert wait_for(started.is_set)
        assert not colors.auto_button.isEnabled()
    finally:
        release.set()
    assert wait_for(lambda: not controller._operations)
    assert colors.auto_button.isEnabled() and len(signals) == 1
    levels = group_for(colors, "levels")
    assert "自动" in levels.title() and levels.enabled.isChecked()
    for step in controller.session().state.current.recipe.steps:
        group = group_for(colors, step.kind)
        assert group.is_expanded() and group.form.isEnabled()
        assert group.form.values == step.params
    assert levels.form.channel_selector.currentData() == "master"
    assert group_for(colors, "colorBalance").form.channel_selector.currentData() == "midtones"
    def displayed(kind, label):
        return next(editor.value() for editor in group_for(colors, kind).form.findChildren(ScalarEditor)
                    if editor.accessibleName() == label)
    assert displayed("brightnessContrast", "亮度") == 14
    assert displayed("levels", "输入黑场") == .13
    assert displayed("exposure", "曝光") == .25
    assert displayed("vibrance", "自然饱和度") == 18
    assert displayed("colorBalance", "青 ↔ 红") == -8
    assert "继续微调" in colors.status.text()
    recipe = controller.session().state.current.recipe.to_dict()
    for _ in range(3):
        colors.set_groups_expanded(False)
        colors.set_groups_expanded(True)
    assert controller.session().state.current.recipe.to_dict() == recipe
    camera.undo_button.click()
    assert controller.session().state.current.recipe.is_identity()
    colors.redo_button.click()
    assert controller.session().state.current.recipe.to_dict() == recipe
    editor = next(editor for editor in group_for(colors, "brightnessContrast").form.findChildren(ScalarEditor)
                  if editor.accessibleName() == "亮度")
    editor.spin.setValue(17)
    assert controller.session().state.current.recipe.steps[0].params["brightness"] == 17
    controller.begin_file_operation()
    assert not colors.auto_button.isEnabled()
    controller.end_file_operation()
    assert colors.auto_button.isEnabled()


def test_auto_adjust_empty_photo_and_step_limit_disable_action(panels):
    controller, _, colors = panels
    recipe = controller.session().state.current.recipe.validated()
    recipe.steps = [AdjustmentStep("invert") for _ in range(64)]
    controller.edit(recipe)
    assert not colors.auto_button.isEnabled() and "64" in colors.auto_button.toolTip()
    colors.on_photo_selected("")
    assert not colors.auto_button.isEnabled()


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


def add_action(group, kind):
    return next(action for action in group.add_menu.actions() if action.data() == kind)


def test_group_add_menu_works_collapsed_inserts_after_anchor_and_undoes_once(panels):
    controller, _, colors = panels
    exposure = group_for(colors, "exposure")
    exposure.enabled.click()
    exposure.form.findChildren(ScalarEditor)[0].spin.setValue(1.25)
    invert = group_for(colors, "invert")
    invert.enabled.click()
    exposure.set_expanded(False)
    colors.show()
    scroll = colors.findChild(QScrollArea)
    scroll.ensureWidgetVisible(exposure.header_button)
    assert wait_for(exposure.add_button.isVisible)
    assert not exposure.form.isVisible()
    assert [action.data() for action in exposure.add_menu.actions()] == list(ADJUSTMENT_TITLES)
    before = controller.session().state.current.recipe.to_dict()
    serial = controller.session().serial
    menu = exposure.add_menu
    menu.popup(exposure.add_button.mapToGlobal(exposure.add_button.rect().bottomLeft()))
    assert wait_for(menu.isVisible)
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert controller.session().serial == serial
    assert controller.session().state.current.recipe.to_dict() == before
    menu.popup(exposure.add_button.mapToGlobal(exposure.add_button.rect().bottomLeft()))
    assert wait_for(menu.isVisible)
    menu.setActiveAction(add_action(exposure, "invert"))
    QTest.keyClick(menu, Qt.Key.Key_Return)
    steps = controller.session().state.current.recipe.steps
    assert [step.kind for step in steps] == ["exposure", "invert", "invert"]
    assert steps[0].params["exposure"] == 1.25
    assert steps[2].id == invert.step_id
    assert steps[1].id != steps[2].id and steps[1].enabled
    added = next(group for group in colors.groups if group.step_id == steps[1].id)
    assert added.is_expanded() and added.add_button.isEnabled()
    assert not exposure.is_expanded()
    assert wait_for(lambda: 0 <= added.header_button.mapTo(scroll.viewport(), added.header_button.rect().center()).y() < scroll.viewport().height())
    colors.undo_button.click()
    assert controller.session().state.current.recipe.to_dict() == before
    colors.redo_button.click()
    assert len(controller.session().state.current.recipe.steps) == 3
    colors.hide()


def test_add_after_unused_group_preserves_disabled_anchor_and_supports_new_group_menu(panels):
    controller, _, colors = panels
    anchor = group_for(colors, "selectiveColor")
    anchor.set_expanded(False)
    add_action(anchor, "exposure").trigger()
    steps = controller.session().state.current.recipe.steps
    assert [step.kind for step in steps] == ["selectiveColor", "exposure"]
    assert not steps[0].enabled and steps[1].enabled
    assert not anchor.enabled.isChecked() and not anchor.is_expanded()
    added = next(group for group in colors.groups if group.step_id == steps[1].id)
    assert added.is_expanded()
    add_action(added, "exposure").trigger()
    steps = controller.session().state.current.recipe.steps
    assert [step.kind for step in steps] == ["selectiveColor", "exposure", "exposure"]
    assert len({step.id for step in steps}) == 3
    colors.undo_button.click()
    assert len(controller.session().state.current.recipe.steps) == 2
    colors.undo_button.click()
    assert not controller.session().state.current.recipe.steps
    assert not controller.session().history.can_undo


def test_group_add_menu_respects_recipe_limit_including_unused_anchor(panels):
    controller, _, colors = panels
    recipe = controller.session().state.current.recipe.validated()
    recipe.steps = [AdjustmentStep("invert") for _ in range(63)]
    controller.edit(recipe)
    anchor = group_for(colors, "invert")
    unused = group_for(colors, "exposure")
    assert anchor.add_button.isEnabled()
    assert not unused.add_button.isEnabled()  # Anchor + new step need two positions.
    before = controller.session().state.current.recipe.signature()
    colors.add_step_after(unused, "invert")
    assert controller.session().state.current.recipe.signature() == before
    add_action(anchor, "exposure").trigger()
    assert len(controller.session().state.current.recipe.steps) == 64
    assert all(not group.add_button.isEnabled() for group in colors.groups)
    before = controller.session().state.current.recipe.signature()
    colors.add_step_after(anchor, "invert")
    assert controller.session().state.current.recipe.signature() == before
    colors.undo_button.click()
    assert len(controller.session().state.current.recipe.steps) == 63
    assert anchor.add_button.isEnabled()


def test_undone_add_does_not_reveal_stale_group(panels):
    controller, _, colors = panels
    anchor = group_for(colors, "brightnessContrast")
    anchor.enabled.click()
    colors.show()
    assert wait_for(colors.isVisible)
    add_action(anchor, "brightnessContrast").trigger()
    assert colors._reveal_timer.isActive()
    controller.undo()
    anchor.header_button.setFocus()
    assert wait_for(lambda: not colors._reveal_timer.isActive())
    assert anchor.header_button.hasFocus()
    assert len(controller.session().state.current.recipe.steps) == 1
    colors.hide()


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
