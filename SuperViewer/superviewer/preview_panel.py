# -*- coding: utf-8 -*-
"""预览区：内嵌 PreviewCanvas，拖放、点击选图、set_image、焦点框与构图线。"""

from __future__ import annotations

import os
from pathlib import Path

from app_common.preview_canvas import (
    PreviewCanvas,
    PreviewOverlayOptions,
    PreviewOverlayState,
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
)
from app_common.superviewer_user_options import get_keep_view_on_switch

from .focus_preview_loader import (
    IMAGE_EXTENSIONS,
    RAW_EXTENSIONS,
    _load_preview_pixmap_for_canvas,
)
from .qt_compat import (
    QDragEnterEvent,
    QDropEvent,
    QFileDialog,
    QLabel,
    QPixmap,
    QVBoxLayout,
    QWidget,
    _LeftButton,
)


class PreviewPanel(QWidget):
    """预览区：内嵌 app_common.preview_canvas.PreviewCanvas，提供拖放、点击选图及 set_image 等接口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setAcceptDrops(True)
        self._current_path = None
        self._show_focus_enabled = True
        self._keep_view_on_switch = bool(get_keep_view_on_switch())
        self._composition_grid_mode = normalize_preview_composition_grid_mode("none")
        self._composition_grid_line_width = normalize_preview_composition_grid_line_width(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._canvas = PreviewCanvas(self, placeholder_text="将图片拖入或点击选择")
        if hasattr(self._canvas, "set_keep_view_on_switch"):
            self._canvas.set_keep_view_on_switch(self._keep_view_on_switch)
        layout.addWidget(self._canvas, stretch=1)
        self._preview_status_label = QLabel("当前预览分辨率: -")
        self._preview_status_label.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(self._preview_status_label)

    def set_image(self, path: str):
        self._current_path = path
        self.set_focus_box(None)
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
        self._canvas.set_source_pixmap(None)
        self._set_preview_status_text(None, None)

    def set_keep_view_on_switch(self, enabled: bool) -> None:
        self._keep_view_on_switch = bool(enabled)
        if hasattr(self._canvas, "set_keep_view_on_switch"):
            self._canvas.set_keep_view_on_switch(self._keep_view_on_switch)

    def set_focus_box(self, focus_box):
        self._canvas.apply_overlay_state(PreviewOverlayState(focus_box=focus_box))

    def set_show_focus_enabled(self, enabled: bool):
        self._show_focus_enabled = bool(enabled)
        self._apply_overlay_options()

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
            self._preview_status_label.setText("当前预览分辨率: -")
            return
        self._preview_status_label.setText(f"当前预览分辨率: {int(width)}x{int(height)}")

    def current_path(self):
        return self._current_path

    def _apply_overlay_options(self) -> None:
        options = PreviewOverlayOptions(show_focus_box=self._show_focus_enabled)
        if hasattr(options, "composition_grid_mode"):
            options.composition_grid_mode = self._composition_grid_mode
        if hasattr(options, "composition_grid_line_width"):
            options.composition_grid_line_width = self._composition_grid_line_width
        self._canvas.apply_overlay_options(options)
        self._canvas.update()

    def mousePressEvent(self, event):
        if event.button() == _LeftButton:
            std_exts = " ".join(
                f"*{e}" for e in (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".heic", ".heif", ".hif")
            )
            raw_exts = " ".join(f"*{e}" for e in RAW_EXTENSIONS)
            path, _ = QFileDialog.getOpenFileName(
                self,
                "选择图片",
                os.path.expanduser("~"),
                f"图片 ({std_exts});;RAW ({raw_exts});;全部 (*.*)",
            )
            if path:
                self.set_image(path)
                if self.parent() and hasattr(self.parent(), "on_image_loaded"):
                    self.parent().on_image_loaded(path)
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                path = urls[0].toLocalFile()
                if path and Path(path).suffix.lower() in IMAGE_EXTENSIONS:
                    event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                path = urls[0].toLocalFile()
                if path and os.path.isfile(path):
                    self.set_image(path)
                    if self.parent() and hasattr(self.parent(), "on_image_loaded"):
                        self.parent().on_image_loaded(path)
        event.acceptProposedAction()
