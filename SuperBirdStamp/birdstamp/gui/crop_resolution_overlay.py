"""UI-only crop size labels and transient resize guides."""
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from birdstamp.crop_resolution import CropPixelContext, choose_snap_target, resolution_targets, tier_size
from . import editor_options


class CropResolutionOverlayMixin:
    def _init_crop_resolution(self):
        self._crop_pixel_context = None
        self._crop_resolution_guide = None
        self._crop_resolution_snapped = False
        self._crop_snap_options = editor_options.load_crop_resolution_snap_options(editor_options.CROP_RESOLUTION_SNAP)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_crop_pixel_context(self, context: CropPixelContext | None):
        context = context if context is not None and context.valid else None
        if context == self._crop_pixel_context:
            return
        if (self._crop_pixel_context is not None
                and (context is None or context.source_key != self._crop_pixel_context.source_key)):
            self._finish_crop_resolution_drag(commit=False)
        self._crop_pixel_context = context
        self._clear_crop_resolution()

    def _clear_crop_resolution(self):
        self._crop_resolution_guide = None
        self._crop_resolution_snapped = False
        self.update()

    def _finish_crop_resolution_drag(self, *, commit=True):
        active = self._dragging_handle is not None
        box = self._crop_effect_box
        self._dragging_handle = None
        self._drag_start_box = None
        self._drag_start_pos = None
        self._clear_crop_resolution()
        if active:
            self._drag_probe.end()
            self.crop_drag_finished.emit()
            if commit and box is not None:
                self.crop_box_changed.emit(box)

    def _resolution_ratio(self, box):
        context = self._crop_pixel_context
        return context.ratio if context.ratio is not None else context.pixel_ratio(box)

    def _update_crop_resolution_guide(self, box, draw_rect):
        context = self._crop_pixel_context
        if (not self._crop_edit_mode or context is None
                or self._dragging_handle in (None, self._CROP_DRAG_CENTER)):
            self._crop_resolution_guide = None
            self._crop_resolution_snapped = False
            return box
        start = self._drag_start_box
        handle = self._dragging_handle
        targets = resolution_targets(context, start, handle, self._resolution_ratio(start),
                                     self._crop_snap_options["tiers"])
        previous = self._crop_resolution_guide if self._crop_resolution_snapped else None
        guide, snapped = choose_snap_target(
            box, targets, handle, (draw_rect.width(), draw_rect.height()), previous=previous,
            enter_distance=self._crop_snap_options["enter_distance"],
            leave_distance=self._crop_snap_options["leave_distance"],
        )
        self._crop_resolution_guide, self._crop_resolution_snapped = guide, snapped
        return guide.box if snapped else box

    def _crop_resize_box(self, draw_rect, pos):
        nx, ny = self._widget_to_norm(draw_rect, pos.x(), pos.y())
        box = self._box_after_drag(self._drag_start_box, self._dragging_handle, nx, ny,
                                   draw_rect.width() / draw_rect.height())
        return self._update_crop_resolution_guide(box, draw_rect)

    def _begin_crop_resolution_drag(self, draw_rect):
        self._crop_snap_options = editor_options.load_crop_resolution_snap_options(self._crop_snap_options)
        self._crop_resolution_guide = None
        self._crop_resolution_snapped = False
        if self._crop_pixel_context is not None:
            self._update_crop_resolution_guide(self._crop_effect_box, draw_rect)
            self._crop_resolution_snapped = False
        self.update()

    def focusOutEvent(self, event):
        self._finish_crop_resolution_drag()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._finish_crop_resolution_drag()
        super().hideEvent(event)

    def _paint_crop_resolution_ui(self):
        context, box = self._crop_pixel_context, self._crop_effect_box
        if context is None or box is None or not (self._crop_edit_mode or self._show_crop_effect):
            return
        draw_rect = self._display_rect()
        if draw_rect is None:
            return
        painter = QPainter(self)
        try:
            painter.setClipRect(self.contentsRect())
            crop_rect = self._crop_resolution_rect(box, draw_rect)
            w, h = context.crop_size(box)
            label = f"{w} × {h} px"
            ratio = context.ratio if context.ratio is not None else w / h
            for tier_label, edge in self._crop_snap_options["tiers"]:
                if (w, h) == tier_size(edge, ratio):
                    label += f" · {tier_label}"
                    break
            guide = self._crop_resolution_guide
            self._draw_crop_resolution_label(painter, crop_rect, label, QColor("white"))
            if self._dragging_handle not in (None, self._CROP_DRAG_CENTER) and guide:
                guide_rect = self._crop_resolution_rect(guide.box, draw_rect)
                color = QColor("#7FFFF0" if self._crop_resolution_snapped else "#45D6E8")
                painter.setPen(QPen(color, 2 if self._crop_resolution_snapped else 1, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(guide_rect)
                w, h = guide.size
                self._draw_crop_resolution_label(painter, guide_rect, f"{guide.label} · {w} × {h} px", color, bottom=True)
        finally:
            painter.end()

    @staticmethod
    def _crop_resolution_rect(box, rect):
        l, t, r, b = box
        return QRectF(rect.left() + l * rect.width(), rect.top() + t * rect.height(),
                      (r - l) * rect.width(), (b - t) * rect.height())

    def _draw_crop_resolution_label(self, painter, crop_rect, text, color, *, bottom=False):
        viewport = QRectF(self.contentsRect()).adjusted(4, 4, -4, -4)
        if not crop_rect.intersects(viewport):
            return
        metrics = painter.fontMetrics()
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(viewport.width() - 12)))
        width, height = metrics.horizontalAdvance(text) + 12, metrics.height() + 6
        x = max(viewport.left(), min(crop_rect.left() + 14, viewport.right() - width))
        y = crop_rect.bottom() + 12 if bottom else crop_rect.top() - height - 12
        if y < viewport.top() or y + height > viewport.bottom():
            y = crop_rect.bottom() - height - 14 if bottom else crop_rect.top() + 14
        y = max(viewport.top(), min(y, viewport.bottom() - height))
        label_rect = QRectF(x, y, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 185))
        painter.drawRoundedRect(label_rect, 3, 3)
        painter.setPen(color)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, text)
