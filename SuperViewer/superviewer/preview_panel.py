# -*- coding: utf-8 -*-
"""预览区：内嵌 PreviewCanvas，提供 set_image 与构图线。"""

from __future__ import annotations

from pathlib import Path

from app_common.preview_canvas import (
    PreviewCanvas,
    PreviewOverlayOptions,
    format_preview_scale_percent,
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
)
from app_common.superviewer_user_options import get_keep_view_on_switch

from .focus_preview_loader import (
    _load_preview_pixmap_for_canvas,
)
from .qt_compat import (
    QLabel,
    QPixmap,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)


class PreviewPanel(QWidget):
    """预览区：内嵌 app_common.preview_canvas.PreviewCanvas，提供 set_image 等接口。"""

    display_scale_percent_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setAcceptDrops(False)
        self._current_path = None
        self._preview_resolution: tuple[int, int] | None = None
        self._photo_exposure: tuple[str, str, str] = ("", "", "")
        self._keep_view_on_switch = bool(get_keep_view_on_switch())
        self._composition_grid_mode = normalize_preview_composition_grid_mode("none")
        self._composition_grid_line_width = normalize_preview_composition_grid_line_width(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._canvas = PreviewCanvas(self, placeholder_text="未选择图片")
        if hasattr(self._canvas, "set_keep_view_on_switch"):
            self._canvas.set_keep_view_on_switch(self._keep_view_on_switch)
        if hasattr(self._canvas, "display_scale_percent_changed"):
            self._canvas.display_scale_percent_changed.connect(self._on_canvas_display_scale_percent_changed)
        layout.addWidget(self._canvas, stretch=1)
        self._preview_status_label = QLabel("当前预览分辨率: - | 当前缩放: - | 快门: - | 光圈: - | ISO: -")
        self._preview_status_label.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(self._preview_status_label)

    def set_image(self, path: str):
        self._current_path = path
        self._photo_exposure = ("", "", "")
        pix = _load_preview_pixmap_for_canvas(path)
        if pix is not None and not pix.isNull():
            if self._keep_view_on_switch:
                self._canvas.set_source_pixmap(
                    pix,
                    preserve_view=True,
                    preserve_scale=True,
                )
            else:
                self._canvas.set_source_pixmap(pix, reset_view=True)
            self._set_preview_status_text(pix.width(), pix.height())
        else:
            self._canvas.set_source_pixmap(None)
            self._canvas.setText(f"无法预览\n{Path(path).name}")
            self._set_preview_status_text(None, None)

    def clear_image(self):
        self._current_path = None
        self._photo_exposure = ("", "", "")
        self._canvas.set_source_pixmap(None)
        self._set_preview_status_text(None, None)

    def set_photo_exposure(self, shutter: str = "", aperture: str = "", iso: str = "") -> None:
        self._photo_exposure = (str(shutter or ""), str(aperture or ""), str(iso or ""))
        self._refresh_preview_status_text()

    def set_keep_view_on_switch(self, enabled: bool) -> None:
        self._keep_view_on_switch = bool(enabled)
        if hasattr(self._canvas, "set_keep_view_on_switch"):
            self._canvas.set_keep_view_on_switch(self._keep_view_on_switch)

    @property
    def canvas(self) -> PreviewCanvas:
        return self._canvas

    def current_display_scale_percent(self) -> float | None:
        return self._canvas.current_display_scale_percent()

    def set_display_scale_percent(self, scale_percent: float | int, *, preserve_view: bool = True) -> bool:
        return self._canvas.set_display_scale_percent(scale_percent, preserve_view=preserve_view)

    def render_source_pixmap_with_overlays(self) -> QPixmap | None:
        return self._canvas.render_source_pixmap_with_overlays()

    def save_source_pixmap_with_overlays(
        self,
        path: str,
        fmt: str | None = None,
        quality: int = -1,
    ) -> bool:
        return self._canvas.save_source_pixmap_with_overlays(path, fmt=fmt, quality=quality)

    def set_composition_grid_mode(self, mode: str | None) -> None:
        self._composition_grid_mode = normalize_preview_composition_grid_mode(mode)
        self._apply_overlay_options()

    def set_composition_grid_line_width(self, width: int | str | None) -> None:
        self._composition_grid_line_width = normalize_preview_composition_grid_line_width(width)
        self._apply_overlay_options()

    def composition_grid_mode(self) -> str:
        return self._composition_grid_mode

    def get_preview_image_size(self):
        pix = getattr(self._canvas, "_source_pixmap", None)
        if pix is None or pix.isNull():
            return None
        return (int(pix.width()), int(pix.height()))

    def _set_preview_status_text(self, width: int | None, height: int | None) -> None:
        if width is None or height is None:
            self._preview_resolution = None
        else:
            self._preview_resolution = (int(width), int(height))
        self._refresh_preview_status_text()

    def _refresh_preview_status_text(self) -> None:
        if self._preview_resolution is None:
            resolution_text = "-"
        else:
            resolution_text = f"{self._preview_resolution[0]}x{self._preview_resolution[1]}"
        scale_text = format_preview_scale_percent(self.current_display_scale_percent())
        shutter, aperture, iso = self._photo_exposure
        self._preview_status_label.setText(
            f"当前预览分辨率: {resolution_text} | 当前缩放: {scale_text} | "
            f"快门: {shutter or '-'} | 光圈: {aperture or '-'} | ISO: {iso or '-'}"
        )

    def _on_canvas_display_scale_percent_changed(self, scale_percent: object) -> None:
        self._refresh_preview_status_text()
        self.display_scale_percent_changed.emit(scale_percent)

    def current_path(self):
        return self._current_path

    def _apply_overlay_options(self) -> None:
        options = PreviewOverlayOptions(show_focus_box=False)
        if hasattr(options, "composition_grid_mode"):
            options.composition_grid_mode = self._composition_grid_mode
        if hasattr(options, "composition_grid_line_width"):
            options.composition_grid_line_width = self._composition_grid_line_width
        self._canvas.apply_overlay_options(options)
        self._canvas.update()
