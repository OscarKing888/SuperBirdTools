import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402

from birdstamp.gui.edit_modes import (  # noqa: E402
    EDIT_MODE_CROP_ADJUST,
    EDIT_MODE_NONE,
    EDIT_MODE_REFERENCE_REGION,
    CropAdjustEditMode,
    EditMode,
    EditModeController,
    ReferenceRegionEditMode,
    normalized_rect_from_points,
    rect_has_area,
)


def test_normalized_rect_orders_and_clamps_points() -> None:
    box = normalized_rect_from_points((0.8, 0.9), (0.2, 0.1))
    assert box == (0.2, 0.1, 0.8, 0.9)
    clamped = normalized_rect_from_points((-0.5, 1.5), (0.5, 0.5))
    assert clamped == (0.0, 0.5, 0.5, 1.0)


def test_rect_has_area() -> None:
    assert rect_has_area((0.1, 0.1, 0.5, 0.5)) is True
    assert rect_has_area((0.1, 0.1, 0.105, 0.105)) is False


class _DummyMode(EditMode):
    mode_id = "dummy"
    label = "dummy"

    def __init__(self) -> None:
        self.activated = 0
        self.deactivated = 0

    def activate(self, canvas) -> None:
        self.activated += 1

    def deactivate(self, canvas) -> None:
        self.deactivated += 1


class _FakeCanvas:
    def update(self) -> None:
        pass


def test_controller_switches_single_active_mode() -> None:
    canvas = _FakeCanvas()
    controller = EditModeController(canvas)
    dummy = _DummyMode()
    ref = ReferenceRegionEditMode()
    controller.register(dummy)
    controller.register(ref)

    assert controller.active_id() == EDIT_MODE_NONE
    assert controller.active_mode() is None

    assert controller.set_active("dummy") is True
    assert controller.active_id() == "dummy"
    assert dummy.activated == 1

    # 切到另一个模式应停用前一个（FSM 单激活保证）。
    assert controller.set_active(EDIT_MODE_REFERENCE_REGION) is True
    assert controller.active_id() == EDIT_MODE_REFERENCE_REGION
    assert dummy.deactivated == 1

    # 未知模式回落到 none。
    assert controller.set_active("bogus") is True
    assert controller.active_id() == EDIT_MODE_NONE

    # 重复设置同一模式不触发变化。
    assert controller.set_active(EDIT_MODE_NONE) is False


def test_controller_inactive_does_not_consume_events() -> None:
    controller = EditModeController(_FakeCanvas())
    controller.register(ReferenceRegionEditMode())
    # 无激活模式时事件不消费（回落到画布默认行为）。
    assert controller.handle_press(object()) is False
    assert controller.handle_move(object()) is False
    assert controller.handle_release(object()) is False


class _CropCanvas:
    def __init__(self) -> None:
        self.crop_edit_calls: list[bool] = []
        self.committed: list[object] = []
        self.append_flags: list[bool] = []
        self.updated = 0

    def set_crop_edit_mode(self, enabled: bool) -> None:
        self.crop_edit_calls.append(bool(enabled))

    def commit_reference_region(self, box, *, append: bool = False) -> None:
        self.committed.append(box)
        self.append_flags.append(bool(append))

    def update(self) -> None:
        self.updated += 1


class _FakeMouseEvent:
    def __init__(self, button) -> None:
        self._button = button
        self.accepted = False

    def button(self):
        return self._button

    def accept(self) -> None:
        self.accepted = True


def test_crop_adjust_mode_toggles_canvas_crop_edit_flag() -> None:
    canvas = _CropCanvas()
    controller = EditModeController(canvas)
    controller.register(CropAdjustEditMode())

    assert controller.set_active(EDIT_MODE_CROP_ADJUST) is True
    assert canvas.crop_edit_calls == [True]

    # 切回选择模式应停用裁剪标志。
    assert controller.set_active(EDIT_MODE_NONE) is True
    assert canvas.crop_edit_calls == [True, False]


def test_crop_adjust_mode_does_not_consume_mouse_events() -> None:
    canvas = _CropCanvas()
    mode = CropAdjustEditMode()
    event = _FakeMouseEvent(Qt.MouseButton.LeftButton)
    # 不消费事件，让既有 9 宫格逻辑照常运行。
    assert mode.on_mouse_press(canvas, event) is False
    assert mode.on_mouse_move(canvas, event) is False
    assert mode.on_mouse_release(canvas, event) is False


def test_reference_region_right_click_clears_selection() -> None:
    canvas = _CropCanvas()
    mode = ReferenceRegionEditMode()
    event = _FakeMouseEvent(Qt.MouseButton.RightButton)

    consumed = mode.on_mouse_press(canvas, event)

    assert consumed is True
    assert event.accepted is True
    assert canvas.committed == [None]
    assert canvas.append_flags == [False]
    assert canvas.updated == 1


def test_three_modes_register_and_switch_exclusively() -> None:
    canvas = _CropCanvas()
    controller = EditModeController(canvas)
    controller.register(ReferenceRegionEditMode())
    controller.register(CropAdjustEditMode())

    ids = set(controller.available_mode_ids())
    assert {EDIT_MODE_REFERENCE_REGION, EDIT_MODE_CROP_ADJUST} <= ids

    assert controller.set_active(EDIT_MODE_REFERENCE_REGION) is True
    assert controller.is_active(EDIT_MODE_REFERENCE_REGION)
    assert controller.set_active(EDIT_MODE_CROP_ADJUST) is True
    assert controller.is_active(EDIT_MODE_CROP_ADJUST)
    assert controller.is_active(EDIT_MODE_REFERENCE_REGION) is False
