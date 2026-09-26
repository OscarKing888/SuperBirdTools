# -*- coding: utf-8 -*-
"""edit_modes.py – 预览画布交互编辑模式（EditMode）有限状态机。

把预览画布上的鼠标交互抽象为可切换的 :class:`EditMode`，由
:class:`EditModeController` 作为有限状态机（FSM）保证任意时刻仅有一个激活模式，
并优先处理鼠标事件；未被任何编辑模式消费的事件再回落到画布原有行为
（裁切 9 宫格 / 视图平移），从而在不改动现有交互的前提下扩展新工具。

当前内置：

* ``ReferenceRegionEditMode`` – 去抖动"特征参考区"框选：在预览图上拖拽绘制矩形。

坐标与画布解耦：模式只通过画布暴露的少量协议方法
（``display_rect`` / ``widget_to_norm`` / ``commit_reference_region`` 等）交互，
便于后续新增其它编辑模式。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPen

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示
    from PyQt6.QtGui import QMouseEvent, QPainter

NormalizedBox = tuple[float, float, float, float]

EDIT_MODE_NONE = "none"
EDIT_MODE_REFERENCE_REGION = "reference_region"
EDIT_MODE_CROP_ADJUST = "crop_adjust"


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def normalized_rect_from_points(
    start: tuple[float, float],
    end: tuple[float, float],
) -> NormalizedBox:
    """由两点（归一化坐标）构造有序、限幅到 [0,1] 的矩形框。"""
    left = _clamp_unit(min(start[0], end[0]))
    top = _clamp_unit(min(start[1], end[1]))
    right = _clamp_unit(max(start[0], end[0]))
    bottom = _clamp_unit(max(start[1], end[1]))
    return (left, top, right, bottom)


def rect_has_area(box: NormalizedBox, min_size: float = 0.01) -> bool:
    return (box[2] - box[0]) >= min_size and (box[3] - box[1]) >= min_size


class EditMode(ABC):
    """预览画布交互编辑模式抽象接口。

    所有回调返回 ``True`` 表示已消费该事件（不再回落到画布默认行为）。
    """

    mode_id: str = EDIT_MODE_NONE
    label: str = "无"

    def activate(self, canvas) -> None:  # noqa: D401 - 钩子
        """模式被激活时调用。"""

    def deactivate(self, canvas) -> None:
        """模式被取消时调用，应清理临时状态。"""

    def on_mouse_press(self, canvas, event: "QMouseEvent") -> bool:
        return False

    def on_mouse_move(self, canvas, event: "QMouseEvent") -> bool:
        return False

    def on_mouse_release(self, canvas, event: "QMouseEvent") -> bool:
        return False

    def paint(self, canvas, painter: "QPainter", draw_rect: QRectF, content_rect) -> None:
        """在画布 overlay 之上绘制模式相关的临时图形。"""


class EditModeController:
    """编辑模式 FSM：注册、切换并分发鼠标事件给当前激活模式。"""

    def __init__(self, canvas) -> None:
        self._canvas = canvas
        self._modes: dict[str, EditMode] = {}
        self._active_id: str = EDIT_MODE_NONE

    def register(self, mode: EditMode) -> None:
        self._modes[mode.mode_id] = mode

    def available_mode_ids(self) -> tuple[str, ...]:
        return tuple(self._modes.keys())

    def active_id(self) -> str:
        return self._active_id

    def active_mode(self) -> EditMode | None:
        return self._modes.get(self._active_id)

    def is_active(self, mode_id: str) -> bool:
        return self._active_id == mode_id

    def set_active(self, mode_id: str | None) -> bool:
        """切换激活模式；返回是否发生变化。"""
        normalized = str(mode_id or EDIT_MODE_NONE)
        if normalized not in self._modes:
            normalized = EDIT_MODE_NONE
        if normalized == self._active_id:
            return False
        previous = self._modes.get(self._active_id)
        if previous is not None:
            previous.deactivate(self._canvas)
        self._active_id = normalized
        current = self._modes.get(normalized)
        if current is not None:
            current.activate(self._canvas)
        return True

    def clear(self) -> bool:
        return self.set_active(EDIT_MODE_NONE)

    # -- 事件分发 ---------------------------------------------------------
    def handle_press(self, event: "QMouseEvent") -> bool:
        mode = self.active_mode()
        return bool(mode.on_mouse_press(self._canvas, event)) if mode is not None else False

    def handle_move(self, event: "QMouseEvent") -> bool:
        mode = self.active_mode()
        return bool(mode.on_mouse_move(self._canvas, event)) if mode is not None else False

    def handle_release(self, event: "QMouseEvent") -> bool:
        mode = self.active_mode()
        return bool(mode.on_mouse_release(self._canvas, event)) if mode is not None else False

    def paint(self, painter: "QPainter", draw_rect: QRectF, content_rect) -> None:
        mode = self.active_mode()
        if mode is not None:
            mode.paint(self._canvas, painter, draw_rect, content_rect)


class ReferenceRegionEditMode(EditMode):
    """参考区框选和八手柄缩放；拖动仅更新显示，松手后提交持久状态。"""

    mode_id = EDIT_MODE_REFERENCE_REGION
    label = "去抖动参考区"
    handles = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
    _cursors = {
        "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
        "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
        "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
        "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
    }

    def __init__(self) -> None:
        self._reset()

    def deactivate(self, canvas) -> None:
        self.cancel(canvas)

    def cancel(self, canvas) -> None:
        self._reset()
        if hasattr(canvas, "unsetCursor"):
            canvas.unsetCursor()
        canvas.update()

    def _reset(self) -> None:
        self._drawing = False
        self._start_norm = None
        self._current_norm = None
        self._append = False
        self._resize_index = None
        self._resize_handle = None
        self._resize_start = None
        self._resize_box = None

    @staticmethod
    def handle_positions(box: NormalizedBox):
        l, t, r, b = box
        cx, cy = (l + r) / 2, (t + b) / 2
        return ((l, t), (cx, t), (r, t), (r, cy), (r, b), (cx, b), (l, b), (l, cy))

    @staticmethod
    def resize_box(box: NormalizedBox, handle: str, point: tuple[float, float]) -> NormalizedBox:
        l, t, r, b = box
        x, y = map(_clamp_unit, point)
        # 固定对侧边/角，禁止翻转；参考区不受裁切比例约束。
        min_w, min_h = min(.01, r - l), min(.01, b - t)
        if "w" in handle:
            l = min(x, r - min_w)
        if "e" in handle:
            r = max(x, l + min_w)
        if "n" in handle:
            t = min(y, b - min_h)
        if "s" in handle:
            b = max(y, t + min_h)
        return (l, t, r, b)

    def preview_regions(self, regions):
        if self._resize_index is None or self._resize_box is None:
            return regions
        return tuple(self._resize_box if i == self._resize_index else box
                     for i, box in enumerate(regions))

    def _hit_handle(self, canvas, event):
        rect = canvas.display_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        pos = event.position()
        # 后添加的区域在上层；同一区域选择最近手柄，避免小框的手柄命中歧义。
        for index in reversed(range(len(canvas.reference_regions()))):
            distances = []
            for handle, (nx, ny) in zip(self.handles, self.handle_positions(canvas.reference_regions()[index])):
                distance = (pos.x() - rect.left() - nx * rect.width()) ** 2 + (pos.y() - rect.top() - ny * rect.height()) ** 2
                distances.append((distance, handle))
            distance, handle = min(distances)
            if distance <= 8 ** 2:
                return index, handle
        return None

    def _point_norm(self, canvas, event) -> tuple[float, float] | None:
        draw_rect = canvas.display_rect()
        if draw_rect is None or draw_rect.width() <= 0 or draw_rect.height() <= 0:
            return None
        pos = event.position()
        nx, ny = canvas.widget_to_norm(draw_rect, pos.x(), pos.y())
        return (_clamp_unit(nx), _clamp_unit(ny))

    def on_mouse_press(self, canvas, event) -> bool:
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel(canvas)
            canvas.commit_reference_region(None, append=False)
            event.accept()
            return True
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        point = self._point_norm(canvas, event)
        if point is None:
            return False
        hit = self._hit_handle(canvas, event)
        self._reset()
        if hit is not None:
            self._resize_index, self._resize_handle = hit
            self._resize_start = canvas.reference_regions()[self._resize_index]
            self._resize_box = self._resize_start
            canvas.setCursor(self._cursors[self._resize_handle])
        else:
            self._drawing = True
            self._start_norm = self._current_norm = point
            self._append = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        canvas.update()
        event.accept()
        return True

    def on_mouse_move(self, canvas, event) -> bool:
        point = self._point_norm(canvas, event)
        if self._resize_index is not None:
            if point is not None:
                self._resize_box = self.resize_box(self._resize_start, self._resize_handle, point)
        elif self._drawing:
            if point is not None:
                self._current_norm = point
        else:
            hit = self._hit_handle(canvas, event)
            canvas.setCursor(self._cursors[hit[1]] if hit else Qt.CursorShape.CrossCursor)
            return False
        canvas.update()
        event.accept()
        return True

    def on_mouse_release(self, canvas, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._resize_index is not None:
            point = self._point_norm(canvas, event)
            box = self.resize_box(self._resize_start, self._resize_handle, point) if point else self._resize_box
            index = self._resize_index
            self._reset()
            canvas.replace_reference_region(index, box)
        elif self._drawing:
            start = self._start_norm
            end = self._point_norm(canvas, event) or self._current_norm or start
            append = self._append
            self._reset()
            if start is not None and end is not None:
                box = normalized_rect_from_points(start, end)
                if rect_has_area(box):
                    canvas.commit_reference_region(box, append=append)
        else:
            return False
        canvas.update()
        event.accept()
        return True

    def paint(self, canvas, painter, draw_rect, content_rect) -> None:
        painter.save()
        painter.setClipRect(QRectF(content_rect))
        pen = QPen(QColor("#FFD166"))
        pen.setWidth(2)
        if self._drawing and self._start_norm is not None and self._current_norm is not None:
            box = normalized_rect_from_points(self._start_norm, self._current_norm)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            painter.drawRect(QRectF(draw_rect.left() + box[0] * draw_rect.width(),
                                    draw_rect.top() + box[1] * draw_rect.height(),
                                    (box[2] - box[0]) * draw_rect.width(),
                                    (box[3] - box[1]) * draw_rect.height()))
        else:
            painter.setPen(QPen(QColor("#6B4700"), 1))
            painter.setBrush(QColor("#FFD166"))
            for box in self.preview_regions(canvas.reference_regions()):
                for nx, ny in self.handle_positions(box):
                    x, y = draw_rect.left() + nx * draw_rect.width(), draw_rect.top() + ny * draw_rect.height()
                    painter.drawRect(QRectF(x - 4, y - 4, 8, 8))
        painter.restore()


class CropAdjustEditMode(EditMode):
    """调整裁剪框模式：复用画布既有的 9 宫格裁切交互。

    本模式不消费鼠标事件，只在激活/停用时切换画布的 ``_crop_edit_mode`` 标志，
    让现有（受保护的）9 宫格手柄拖拽逻辑照常运行，从而保证行为零改动。
    """

    mode_id = EDIT_MODE_CROP_ADJUST
    label = "调整裁剪框"

    def activate(self, canvas) -> None:
        setter = getattr(canvas, "set_crop_edit_mode", None)
        if callable(setter):
            setter(True)

    def deactivate(self, canvas) -> None:
        setter = getattr(canvas, "set_crop_edit_mode", None)
        if callable(setter):
            setter(False)


__all__ = [
    "EDIT_MODE_CROP_ADJUST",
    "EDIT_MODE_NONE",
    "EDIT_MODE_REFERENCE_REGION",
    "CropAdjustEditMode",
    "EditMode",
    "EditModeController",
    "ReferenceRegionEditMode",
    "normalized_rect_from_points",
    "rect_has_area",
]
