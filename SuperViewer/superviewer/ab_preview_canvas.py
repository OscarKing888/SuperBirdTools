"""Viewport interactions for image A/B comparison (adapted from main 82b6d32)."""
from app_common.preview_canvas import PreviewCanvas
from .qt_compat import pyqtSignal


class ViewerPreviewCanvas(PreviewCanvas):
    """Expose viewport changes for optional A/B linking without changing loading policy."""

    viewport_interacted = pyqtSignal()
    viewport_content_changed = pyqtSignal()

    def fit_to_window(self) -> None:
        pixmap = self._source_pixmap
        if pixmap is None or pixmap.isNull():
            return
        content = self.contentsRect()
        if content.width() <= 0 or content.height() <= 0:
            return
        scale = min(content.width() / pixmap.width(), content.height() / pixmap.height())
        self.set_display_scale_percent(scale * 100, preserve_view=False)

    def viewport_state(self):
        center = self._view_center_ratio()
        return (self._zoom, center) if center is not None else None

    def apply_viewport_state(self, state) -> None:
        if state is None or self._source_pixmap is None:
            return
        zoom, center = state
        self._zoom = max(self._min_zoom, min(self._max_zoom, zoom))
        self._apply_view_center_ratio(center)
        self._clamp_offset()
        self._update_cursor()
        self.update()
        self._emit_display_scale_percent_changed()

    def set_source_pixmap(self, pixmap, **kwargs) -> None:
        super().set_source_pixmap(pixmap, **kwargs)
        self.viewport_content_changed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.viewport_content_changed.emit()

    def set_display_scale_percent(self, value, *, preserve_view=True) -> bool:
        changed = super().set_display_scale_percent(value, preserve_view=preserve_view)
        if changed:
            self.viewport_interacted.emit()
        return changed

    def wheelEvent(self, event) -> None:
        super().wheelEvent(event)
        if event.isAccepted():
            self.viewport_interacted.emit()

    def mouseMoveEvent(self, event) -> None:
        dragging = self._dragging
        super().mouseMoveEvent(event)
        if dragging:
            self.viewport_interacted.emit()
