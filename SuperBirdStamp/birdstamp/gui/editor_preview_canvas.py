# -*- coding: utf-8 -*-
"""editor_preview_canvas.py – BirdStamp editor's PreviewCanvas subclass.

Extends ``app_common.preview_canvas.PreviewCanvas`` with two editor-specific
overlays:

* **Bird detection box** – semi-transparent blue fill + border.
* **Crop-effect shade** – darkened path outside the intended crop rectangle.

When crop-edit mode is on, draws a 9-grid (8 handles: corners + edge midpoints)
and allows dragging to adjust the crop box; aspect ratio can be locked by
template ratio or free.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from app_common.preview_canvas import PreviewCanvas, PreviewOverlayOptions, PreviewOverlayState
from birdstamp.gui.editor_utils import DEFAULT_CROP_EFFECT_ALPHA as _DEFAULT_CROP_EFFECT_ALPHA

NormalizedBox = tuple[float, float, float, float]

# Handle names for 9-grid: 4 corners + 4 edges
_CROP_HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
_HANDLE_HIT_RADIUS = 8
_MIN_CROP_SIZE = 0.02


@dataclass(slots=True)
class EditorPreviewOverlayState(PreviewOverlayState):
    """Editor-specific overlay payloads.

    Extends the base preview overlay state (focus box) with editor overlays.
    """

    bird_box: "NormalizedBox | None" = None
    crop_effect_box: "NormalizedBox | None" = None


@dataclass(slots=True)
class EditorPreviewOverlayOptions(PreviewOverlayOptions):
    """Editor-specific preview overlay options."""

    show_bird_box: bool = False
    show_crop_effect: bool = False
    crop_effect_alpha: int = _DEFAULT_CROP_EFFECT_ALPHA


class EditorPreviewCanvas(PreviewCanvas):
    """PreviewCanvas specialised for the BirdStamp photo editor.

    Adds bird-detection-box and crop-effect-shade overlays on top of the
    base class capabilities.  When crop-edit mode is on, draws 9-grid handles
    and allows drag-to-adjust with optional ratio lock.
    """

    crop_box_changed = pyqtSignal(tuple)  # (l, t, r, b) normalized

    def __init__(
        self,
        parent: "QWidget | None" = None,
        *,
        placeholder_text: str = "暂无预览",
    ) -> None:
        super().__init__(parent, placeholder_text=placeholder_text)
        self._bird_box: "NormalizedBox | None" = None
        self._show_bird_box: bool = False
        self._crop_effect_box: "NormalizedBox | None" = None
        self._show_crop_effect: bool = False
        self._crop_effect_alpha: int = _DEFAULT_CROP_EFFECT_ALPHA
        self._crop_edit_mode: bool = False
        self._crop_ratio: float | None = None  # None = use image aspect when constraining
        self._ratio_free: bool = False
        self._dragging_handle: str | None = None
        self._drag_start_box: "NormalizedBox | None" = None
        self._drag_start_pos: "QPointF | None" = None
        self._last_pos: "QPointF | None" = None
        self._has_pan: bool = False

    # ------------------------------------------------------------------
    # Public API – crop edit (9-grid)
    # ------------------------------------------------------------------

    def set_crop_edit_mode(self, enabled: bool) -> None:
        if self._crop_edit_mode == enabled:
            return
        self._crop_edit_mode = enabled
        self._dragging_handle = None
        self._drag_start_box = None
        self._drag_start_pos = None
        self._last_pos = None
        self._has_pan = False
        self.update()

    def set_crop_ratio_constraint(self, ratio: float | None, free: bool) -> None:
        self._crop_ratio = ratio
        self._ratio_free = free
        self.update()

    def crop_edit_mode(self) -> bool:
        return self._crop_edit_mode

    def has_pan(self) -> bool:
        """Return whether current crop box has been panned (dragged by center handle)."""
        return bool(self._has_pan)

    # ------------------------------------------------------------------
    # Public API – bird box
    # ------------------------------------------------------------------

    def set_bird_box(self, bird_box: "NormalizedBox | None") -> None:
        if self._set_bird_box_no_update(bird_box):
            self.update()

    def set_show_bird_box(self, enabled: bool) -> None:
        if self._set_show_bird_box_no_update(enabled):
            self.update()

    # ------------------------------------------------------------------
    # Public API – crop-effect shade
    # ------------------------------------------------------------------

    def set_crop_effect_box(self, crop_effect_box: "NormalizedBox | None") -> None:
        if self._set_crop_effect_box_no_update(crop_effect_box):
            self.update()

    def set_show_crop_effect(self, enabled: bool) -> None:
        if self._set_show_crop_effect_no_update(enabled):
            self.update()

    def set_crop_effect_alpha(self, alpha: int) -> None:
        if self._set_crop_effect_alpha_no_update(alpha):
            self.update()

    # ------------------------------------------------------------------
    # Extension hooks
    # ------------------------------------------------------------------

    def _apply_overlay_state_data(self, state: "PreviewOverlayState") -> bool:
        changed = super()._apply_overlay_state_data(state)
        if not isinstance(state, EditorPreviewOverlayState):
            return changed
        if self._set_bird_box_no_update(state.bird_box):
            changed = True
        if self._set_crop_effect_box_no_update(state.crop_effect_box):
            changed = True
        return changed

    def _apply_overlay_options_data(self, options: "PreviewOverlayOptions") -> bool:
        changed = super()._apply_overlay_options_data(options)
        if not isinstance(options, EditorPreviewOverlayOptions):
            return changed
        if self._set_show_bird_box_no_update(options.show_bird_box):
            changed = True
        if self._set_show_crop_effect_no_update(options.show_crop_effect):
            changed = True
        if self._set_crop_effect_alpha_no_update(options.crop_effect_alpha):
            changed = True
        return changed

    def _on_source_cleared(self) -> None:
        self._bird_box = None
        self._crop_effect_box = None
        self._dragging_handle = None
        self._drag_start_box = None
        self._drag_start_pos = None
        self._last_pos = None
        self._has_pan = False

    def _paint_overlays(self, painter, draw_rect, content_rect) -> None:  # type: ignore[override]
        if self._show_bird_box and self._bird_box:
            self._paint_bird_overlay(painter, draw_rect, content_rect)
        if self._show_crop_effect and self._crop_effect_box:
            self._paint_crop_shade(painter, draw_rect, content_rect)
        if self._crop_edit_mode and self._crop_effect_box:
            self._paint_crop_handles(painter, draw_rect, content_rect)

    def _composition_grid_target_rect(self, draw_rect: QRectF, content_rect) -> QRectF:  # type: ignore[override]
        """构图线优先限制在当前裁切范围内，避免覆盖到裁切外区域。"""
        box = self._crop_effect_box
        if box is None:
            return draw_rect
        crop_rect = QRectF(
            draw_rect.left() + box[0] * draw_rect.width(),
            draw_rect.top() + box[1] * draw_rect.height(),
            max(0.0, (box[2] - box[0]) * draw_rect.width()),
            max(0.0, (box[3] - box[1]) * draw_rect.height()),
        ).intersected(draw_rect)
        if crop_rect.width() < 1.0 or crop_rect.height() < 1.0:
            return draw_rect
        return crop_rect

    # ------------------------------------------------------------------
    # Private overlay painters
    # ------------------------------------------------------------------

    def _set_bird_box_no_update(self, bird_box: "NormalizedBox | None") -> bool:
        if self._bird_box == bird_box:
            return False
        self._bird_box = bird_box
        return True

    def _set_show_bird_box_no_update(self, enabled: bool) -> bool:
        parsed = bool(enabled)
        if self._show_bird_box == parsed:
            return False
        self._show_bird_box = parsed
        return True

    def _set_crop_effect_box_no_update(self, crop_effect_box: "NormalizedBox | None") -> bool:
        if self._crop_effect_box == crop_effect_box:
            return False
        self._crop_effect_box = crop_effect_box
        return True

    def _set_show_crop_effect_no_update(self, enabled: bool) -> bool:
        parsed = bool(enabled)
        if self._show_crop_effect == parsed:
            return False
        self._show_crop_effect = parsed
        return True

    def _set_crop_effect_alpha_no_update(self, alpha: int) -> bool:
        parsed = max(0, min(255, int(alpha)))
        if parsed == self._crop_effect_alpha:
            return False
        self._crop_effect_alpha = parsed
        return True

    def _norm_to_widget(self, draw_rect: QRectF, nx: float, ny: float) -> tuple[float, float]:
        x = draw_rect.left() + nx * draw_rect.width()
        y = draw_rect.top() + ny * draw_rect.height()
        return (x, y)

    def _widget_to_norm(self, draw_rect: QRectF, x: float, y: float) -> tuple[float, float]:
        if draw_rect.width() <= 0 or draw_rect.height() <= 0:
            return (0.5, 0.5)
        nx = (x - draw_rect.left()) / draw_rect.width()
        ny = (y - draw_rect.top()) / draw_rect.height()
        return (nx, ny)

    def _handle_position(self, box: NormalizedBox, handle: str) -> tuple[float, float]:
        l, t, r, b = box[0], box[1], box[2], box[3]
        cx = (l + r) * 0.5
        cy = (t + b) * 0.5
        if handle == "nw":
            return (l, t)
        if handle == "n":
            return (cx, t)
        if handle == "ne":
            return (r, t)
        if handle == "e":
            return (r, cy)
        if handle == "se":
            return (r, b)
        if handle == "s":
            return (cx, b)
        if handle == "sw":
            return (l, b)
        if handle == "w":
            return (l, cy)
        return (cx, cy)

    def _hit_handle(self, draw_rect: QRectF, box: NormalizedBox, wx: float, wy: float) -> str | None:
        for h in _CROP_HANDLES:
            nx, ny = self._handle_position(box, h)
            hx, hy = self._norm_to_widget(draw_rect, nx, ny)
            if (wx - hx) ** 2 + (wy - hy) ** 2 <= _HANDLE_HIT_RADIUS ** 2:
                return h
        return None

    def _is_inside_crop_box(self, draw_rect: QRectF, box: NormalizedBox, wx: float, wy: float) -> bool:
        """True if (wx, wy) is inside the crop rect (widget coords) and not on a handle."""
        if self._hit_handle(draw_rect, box, wx, wy) is not None:
            return False
        nx, ny = self._widget_to_norm(draw_rect, wx, wy)
        l, t, r, b = box[0], box[1], box[2], box[3]
        return l <= nx <= r and t <= ny <= b

    def _clamp_box(self, l: float, t: float, r: float, b: float) -> NormalizedBox:
        l, r = min(l, r), max(l, r)
        t, b = min(t, b), max(t, b)
        w = r - l
        h = b - t
        if w < _MIN_CROP_SIZE:
            r = l + _MIN_CROP_SIZE
        if h < _MIN_CROP_SIZE:
            b = t + _MIN_CROP_SIZE
        return (max(0.0, min(1.0, l)), max(0.0, min(1.0, t)), max(0.0, min(1.0, r)), max(0.0, min(1.0, b)))

    def _constrain_box_to_ratio_from_fixed_corner(
        self,
        fixed_l: float,
        fixed_t: float,
        fixed_r: float,
        fixed_b: float,
        moving_nx: float,
        moving_ny: float,
        handle: str,
        ratio: float,
    ) -> NormalizedBox:
        """Given fixed corner (opposite to handle) and moving point, return box with aspect ratio."""
        if handle in ("nw", "n", "w"):
            l = moving_nx
            t = moving_ny
            r, b = fixed_r, fixed_b
        elif handle in ("ne", "e"):
            r = moving_nx
            t = moving_ny
            l, b = fixed_l, fixed_b
        elif handle in ("se", "s"):
            r = moving_nx
            b = moving_ny
            l, t = fixed_l, fixed_t
        else:
            l = moving_nx
            b = moving_ny
            r, t = fixed_r, fixed_t
        w = r - l
        h = b - t
        if w <= 0 or h <= 0:
            w = max(_MIN_CROP_SIZE, w)
            h = max(_MIN_CROP_SIZE, h)
        if ratio <= 0:
            return self._clamp_box(l, t, r, b)
        if w / h > ratio:
            h = w / ratio
            if handle in ("nw", "n", "w"):
                t = b - h
            else:
                b = t + h
        else:
            w = h * ratio
            if handle in ("nw", "ne", "n"):
                l = r - w
            else:
                r = l + w
        return self._clamp_box(l, t, r, b)

    def _box_after_drag(
        self,
        start_box: NormalizedBox,
        handle: str,
        new_nx: float,
        new_ny: float,
        image_aspect: float,
    ) -> NormalizedBox:
        l, t, r, b = start_box[0], start_box[1], start_box[2], start_box[3]
        if self._ratio_free:
            if handle in ("nw", "n", "ne"):
                t = new_ny
            if handle in ("ne", "e", "se"):
                r = new_nx
            if handle in ("se", "s", "sw"):
                b = new_ny
            if handle in ("sw", "w", "nw"):
                l = new_nx
            return self._clamp_box(l, t, r, b)
        # Target ratio R is for crop in pixels: (r-l)*W / ((b-t)*H) = R => (r-l)/(b-t) = R/image_aspect.
        target_pixel_ratio = self._crop_ratio if self._crop_ratio is not None and self._crop_ratio > 0 else image_aspect
        ratio_norm = target_pixel_ratio / image_aspect if image_aspect > 0 else target_pixel_ratio
        if handle == "nw":
            return self._constrain_box_to_ratio_from_fixed_corner(r, b, r, b, new_nx, new_ny, "nw", ratio_norm)
        if handle == "n":
            return self._constrain_box_to_ratio_from_fixed_corner(l, b, r, b, (l + r) * 0.5, new_ny, "n", ratio_norm)
        if handle == "ne":
            return self._constrain_box_to_ratio_from_fixed_corner(l, b, l, b, new_nx, new_ny, "ne", ratio_norm)
        if handle == "e":
            return self._constrain_box_to_ratio_from_fixed_corner(l, t, l, b, new_nx, (t + b) * 0.5, "e", ratio_norm)
        if handle == "se":
            return self._constrain_box_to_ratio_from_fixed_corner(l, t, l, t, new_nx, new_ny, "se", ratio_norm)
        if handle == "s":
            return self._constrain_box_to_ratio_from_fixed_corner(l, t, r, t, (l + r) * 0.5, new_ny, "s", ratio_norm)
        if handle == "sw":
            return self._constrain_box_to_ratio_from_fixed_corner(r, t, r, t, new_nx, new_ny, "sw", ratio_norm)
        if handle == "w":
            return self._constrain_box_to_ratio_from_fixed_corner(r, t, r, b, new_nx, (t + b) * 0.5, "w", ratio_norm)
        return start_box

    _CROP_DRAG_CENTER = "center"

    def _box_after_pan(
        self,
        start_box: NormalizedBox,
        dnx: float,
        dny: float,
    ) -> NormalizedBox:
        """Shift crop box by (dnx, dny) in normalized space, clamped to [0,1] preserving size."""
        l = start_box[0] + dnx
        t = start_box[1] + dny
        r = start_box[2] + dnx
        b = start_box[3] + dny
        w = r - l
        h = b - t
        if l < 0.0:
            l = 0.0
            r = l + w
        if r > 1.0:
            r = 1.0
            l = r - w
        if t < 0.0:
            t = 0.0
            b = t + h
        if b > 1.0:
            b = 1.0
            t = b - h
        return self._clamp_box(l, t, r, b)

    def _paint_crop_handles(self, painter, draw_rect: QRectF, content_rect) -> None:
        box = self._crop_effect_box
        if box is None:
            return
        l, t, r, b = box[0], box[1], box[2], box[3]
        path = QPainterPath()
        path.addRect(QRectF(
            draw_rect.left() + l * draw_rect.width(),
            draw_rect.top() + t * draw_rect.height(),
            (r - l) * draw_rect.width(),
            (b - t) * draw_rect.height(),
        ))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawPath(path)
        for h in _CROP_HANDLES:
            nx, ny = self._handle_position(box, h)
            hx, hy = self._norm_to_widget(draw_rect, nx, ny)
            hr = _HANDLE_HIT_RADIUS * 0.6
            painter.setBrush(QColor(255, 255, 255))
            painter.setPen(QPen(QColor(0, 0, 0), 1))
            painter.drawEllipse(QPointF(hx, hy), hr, hr)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._crop_edit_mode
            and self._crop_effect_box is not None
        ):
            draw_rect = self._display_rect()
            if draw_rect is not None and draw_rect.width() > 0 and draw_rect.height() > 0:
                pos = event.position()
                hit = self._hit_handle(draw_rect, self._crop_effect_box, pos.x(), pos.y())
                if hit is not None:
                    self._dragging_handle = hit
                    self._drag_start_box = self._crop_effect_box
                    self._last_pos = QPointF(pos)
                    event.accept()
                    return
                if self._is_inside_crop_box(draw_rect, self._crop_effect_box, pos.x(), pos.y()):
                    self._dragging_handle = self._CROP_DRAG_CENTER
                    self._drag_start_box = self._crop_effect_box
                    self._drag_start_pos = QPointF(pos)
                    self._last_pos = QPointF(pos)
                    self._has_pan = True
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging_handle is not None and self._drag_start_box is not None:
            draw_rect = self._display_rect()
            if draw_rect is not None and draw_rect.width() > 0 and draw_rect.height() > 0:
                pos = event.position()
                if self._dragging_handle == self._CROP_DRAG_CENTER and self._drag_start_pos is not None:
                    dnx = (pos.x() - self._drag_start_pos.x()) / draw_rect.width()
                    dny = (pos.y() - self._drag_start_pos.y()) / draw_rect.height()
                    new_box = self._box_after_pan(self._drag_start_box, dnx, dny)
                else:
                    nx, ny = self._widget_to_norm(draw_rect, pos.x(), pos.y())
                    image_aspect = draw_rect.width() / float(draw_rect.height())
                    new_box = self._box_after_drag(
                        self._drag_start_box,
                        self._dragging_handle,
                        nx,
                        ny,
                        image_aspect,
                    )
                self._set_crop_effect_box_no_update(new_box)
                self.crop_box_changed.emit(new_box)
                self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._dragging_handle is not None:
            if self._crop_effect_box is not None:
                self.crop_box_changed.emit(self._crop_effect_box)
            self._dragging_handle = None
            self._drag_start_box = None
            self._drag_start_pos = None
            self._last_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _paint_bird_overlay(self, painter, draw_rect: "QRectF", content_rect) -> None:
        bb = self._bird_box
        if bb is None:
            return
        bl = draw_rect.left() + bb[0] * draw_rect.width()
        bt = draw_rect.top() + bb[1] * draw_rect.height()
        br = draw_rect.left() + bb[2] * draw_rect.width()
        bbot = draw_rect.top() + bb[3] * draw_rect.height()
        bird_rect = QRectF(
            min(bl, br), min(bt, bbot),
            abs(br - bl), abs(bbot - bt),
        ).intersected(QRectF(content_rect))
        if bird_rect.width() < 1.0 or bird_rect.height() < 1.0:
            return

        fill = QColor("#A9DBFF")
        fill.setAlpha(96)
        painter.fillRect(bird_rect, fill)

        pen = QPen(QColor("#8BCBFF"))
        pen.setWidth(1)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawRect(bird_rect)

    def _paint_crop_shade(self, painter, draw_rect: "QRectF", content_rect) -> None:
        cb = self._crop_effect_box
        if cb is None:
            return
        cl = draw_rect.left() + cb[0] * draw_rect.width()
        ct = draw_rect.top() + cb[1] * draw_rect.height()
        cr = draw_rect.left() + cb[2] * draw_rect.width()
        cbot = draw_rect.top() + cb[3] * draw_rect.height()
        crop_rect = QRectF(
            min(cl, cr), min(ct, cbot),
            abs(cr - cl), abs(cbot - ct),
        )
        visible_rect = draw_rect.intersected(QRectF(content_rect))
        crop_rect = crop_rect.intersected(visible_rect)
        if visible_rect.width() < 1.0 or visible_rect.height() < 1.0:
            return
        if crop_rect.width() < 1.0 or crop_rect.height() < 1.0:
            return

        shade_path = QPainterPath()
        shade_path.addRect(visible_rect)
        keep_path = QPainterPath()
        keep_path.addRect(crop_rect)
        painter.fillPath(shade_path.subtracted(keep_path), QColor(0, 0, 0, self._crop_effect_alpha))


# ---------------------------------------------------------------------------
# Backward-compatible alias: code that imports PreviewCanvas from this module
# continues to work without changes.
# ---------------------------------------------------------------------------
PreviewCanvas = EditorPreviewCanvas  # type: ignore[misc]
