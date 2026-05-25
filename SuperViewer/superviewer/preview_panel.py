# -*- coding: utf-8 -*-
"""预览区：内嵌 PreviewCanvas，提供 set_image 与构图线。"""

from __future__ import annotations

import time as _time
import os
from pathlib import Path

from app_common import thumb_stream
from app_common.file_browser._browser_core import _load_thumbnail_image, _read_thumb_from_disk_cache
from app_common.log import get_logger
from app_common.perf_probe import perf_log
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
    QImage,
    QImageReader,
    QLabel,
    QPixmap,
    QThread,
    QTimer,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)


_log = get_logger("superviewer.preview_panel")
_QUICK_PREVIEW_SIZE = 512
_QUICK_PREVIEW_FALLBACK_SIZE = 128
_FULL_PREVIEW_DELAY_MS = 80


def _qimage_from_rgb_result(result) -> QImage | None:
    if not result:
        return None
    try:
        data, w, h = result
        w = int(w)
        h = int(h)
        if w <= 0 or h <= 0:
            return None
        fmt_container = getattr(QImage, "Format", QImage)
        fmt = getattr(fmt_container, "Format_RGB888", None)
        if fmt is None:
            fmt = getattr(QImage, "Format_RGB888")
        qimg = QImage(bytes(data), w, h, w * 3, fmt).copy()
        return qimg if not qimg.isNull() else None
    except Exception:
        return None


def _quick_preview_target_size(canvas: QWidget) -> int:
    return _QUICK_PREVIEW_SIZE


def _load_quick_preview_pixmap(path: str, target_size: int) -> QPixmap | None:
    qimg = None
    try:
        mtime = float(os.path.getmtime(path))
    except Exception:
        mtime = 0.0
    for cached_size in (512, 256, 128):
        if cached_size > int(target_size):
            continue
        qimg = _read_thumb_from_disk_cache(path, mtime, cached_size)
        if qimg is not None and not qimg.isNull():
            break
    if qimg is None or qimg.isNull():
        qimg = _load_thumbnail_image(path, _QUICK_PREVIEW_FALLBACK_SIZE)
    if qimg is None or qimg.isNull():
        qimg = _qimage_from_rgb_result(thumb_stream.load_thumbnail_rgb(path, _QUICK_PREVIEW_FALLBACK_SIZE))
    if qimg is None or qimg.isNull():
        return None
    pix = QPixmap.fromImage(qimg)
    return pix if not pix.isNull() else None


def _load_full_preview_qimage(path: str) -> QImage | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        reader = QImageReader(path)
        try:
            reader.setAutoTransform(True)
        except Exception:
            pass
        qimg = reader.read()
        if qimg is not None and not qimg.isNull():
            return qimg.copy()
    except Exception:
        pass
    return None


class _FullPreviewLoader(QThread):
    loaded = pyqtSignal(int, str, object, float)

    def __init__(self, token: int, path: str, parent=None) -> None:
        super().__init__(parent)
        self._token = int(token)
        self._path = os.path.normpath(path) if path else ""

    def run(self) -> None:
        started = _time.perf_counter()
        qimg = None
        if not self.isInterruptionRequested():
            qimg = _load_full_preview_qimage(self._path)
        self.loaded.emit(
            self._token,
            self._path,
            qimg,
            (_time.perf_counter() - started) * 1000.0,
        )


class PreviewPanel(QWidget):
    """预览区：内嵌 app_common.preview_canvas.PreviewCanvas，提供 set_image 等接口。"""

    display_scale_percent_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setAcceptDrops(False)
        self._current_path = None
        self._preview_request_token = 0
        self._full_preview_loaded = False
        self._full_preview_loader: _FullPreviewLoader | None = None
        self._retired_full_preview_loaders: list[_FullPreviewLoader] = []
        self._full_preview_timer = QTimer(self)
        self._full_preview_timer.setSingleShot(True)
        self._full_preview_timer.timeout.connect(self._start_full_preview_loader)
        self._preview_resolution: tuple[int, int] | None = None
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
        self._preview_status_label = QLabel("当前预览分辨率: - | 当前缩放: -")
        self._preview_status_label.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(self._preview_status_label)

    def set_image(self, path: str, *, load_full: bool = True):
        t0 = _time.perf_counter()
        norm_path = os.path.normpath(path) if path else path
        if norm_path and self._is_current_path(norm_path) and self._has_canvas_pixmap():
            if load_full and not self._full_preview_loaded and not self._full_preview_timer.isActive():
                loader = self._full_preview_loader
                if loader is None or not loader.isRunning():
                    self._full_preview_timer.start(_FULL_PREVIEW_DELAY_MS)
            perf_log(
                _log,
                "[PERF][image_switch][preview_panel.set_image] path=%r same_path=1 full_loaded=%s total_ms=%.1f",
                path,
                self._full_preview_loaded,
                (_time.perf_counter() - t0) * 1000.0,
            )
            return
        self._preview_request_token += 1
        token = self._preview_request_token
        self._current_path = norm_path
        self._full_preview_loaded = False
        self._cancel_pending_full_preview()
        load_t0 = _time.perf_counter()
        target_size = _quick_preview_target_size(self._canvas)
        pix = _load_quick_preview_pixmap(path, target_size)
        load_ms = (_time.perf_counter() - load_t0) * 1000.0
        canvas_ms = 0.0
        status_ms = 0.0
        if pix is not None and not pix.isNull():
            canvas_t0 = _time.perf_counter()
            self._set_canvas_pixmap(pix)
            canvas_ms = (_time.perf_counter() - canvas_t0) * 1000.0
            status_t0 = _time.perf_counter()
            self._set_preview_status_text(pix.width(), pix.height())
            status_ms = (_time.perf_counter() - status_t0) * 1000.0
        else:
            canvas_t0 = _time.perf_counter()
            self._canvas.set_source_pixmap(None)
            self._canvas.setText(f"无法预览\n{Path(path).name}")
            canvas_ms = (_time.perf_counter() - canvas_t0) * 1000.0
            status_t0 = _time.perf_counter()
            self._set_preview_status_text(None, None)
            status_ms = (_time.perf_counter() - status_t0) * 1000.0
        if load_full and path:
            self._full_preview_timer.start(_FULL_PREVIEW_DELAY_MS)
        perf_log(
            _log,
            "[PERF][image_switch][preview_panel.set_image] path=%r token=%s quick_ok=%s quick_size=%s target=%s load_ms=%.1f canvas_ms=%.1f status_ms=%.1f total_ms=%.1f",
            path,
            token,
            bool(pix is not None and not pix.isNull()),
            (pix.width(), pix.height()) if pix is not None and not pix.isNull() else None,
            target_size,
            load_ms,
            canvas_ms,
            status_ms,
            (_time.perf_counter() - t0) * 1000.0,
        )

    def clear_image(self):
        self._preview_request_token += 1
        self._current_path = None
        self._full_preview_loaded = False
        self._cancel_pending_full_preview()
        self._canvas.set_source_pixmap(None)
        self._set_preview_status_text(None, None)

    def _set_canvas_pixmap(self, pix: QPixmap) -> None:
        if self._keep_view_on_switch:
            self._canvas.set_source_pixmap(
                pix,
                preserve_view=True,
                preserve_scale=True,
            )
        else:
            self._canvas.set_source_pixmap(pix, reset_view=True)

    def _has_canvas_pixmap(self) -> bool:
        pixmap = getattr(self._canvas, "_source_pixmap", None)
        return bool(pixmap is not None and not pixmap.isNull())

    def _is_current_path(self, path: str) -> bool:
        if not path or not self._current_path:
            return False
        try:
            return os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(str(self._current_path)))
        except Exception:
            return str(path) == str(self._current_path)

    def _cancel_pending_full_preview(self) -> None:
        if self._full_preview_timer.isActive():
            self._full_preview_timer.stop()
        loader = self._full_preview_loader
        if loader is not None and loader.isRunning():
            loader.requestInterruption()

    def _start_full_preview_loader(self) -> None:
        path = os.path.normpath(str(self._current_path or ""))
        if not path or not os.path.isfile(path):
            return
        token = self._preview_request_token
        loader = self._full_preview_loader
        if loader is not None and loader.isRunning():
            loader.requestInterruption()
            self._retired_full_preview_loaders.append(loader)
        loader = _FullPreviewLoader(token, path, self)
        loader.loaded.connect(self._on_full_preview_loaded)
        loader.finished.connect(lambda l=loader: self._cleanup_full_preview_loader(l))
        self._full_preview_loader = loader
        loader.start()

    def _cleanup_full_preview_loader(self, loader: _FullPreviewLoader) -> None:
        if self._full_preview_loader is loader:
            self._full_preview_loader = None
        self._retired_full_preview_loaders = [
            item for item in self._retired_full_preview_loaders if item is not loader
        ]
        try:
            loader.deleteLater()
        except Exception:
            pass

    def _on_full_preview_loaded(self, token: int, path: str, qimg, load_ms: float) -> None:
        if int(token) != int(self._preview_request_token):
            return
        if not path or not self._current_path:
            return
        if not self._is_current_path(path):
            return
        if qimg is None or qimg.isNull():
            perf_log(
                _log,
                "[preview.full] path=%r token=%s ok=False load_ms=%.1f",
                path,
                token,
                load_ms,
            )
            return
        apply_t0 = _time.perf_counter()
        pix = QPixmap.fromImage(qimg)
        if pix.isNull():
            return
        self._set_canvas_pixmap(pix)
        self._set_preview_status_text(pix.width(), pix.height())
        self._full_preview_loaded = True
        perf_log(
            _log,
            "[preview.full] path=%r token=%s ok=True size=%s load_ms=%.1f apply_ms=%.1f",
            path,
            token,
            (pix.width(), pix.height()),
            load_ms,
            (_time.perf_counter() - apply_t0) * 1000.0,
        )

    def _ensure_full_preview_loaded_sync(self) -> None:
        if self._full_preview_loaded:
            return
        path = os.path.normpath(str(self._current_path or ""))
        if not path or not os.path.isfile(path):
            return
        pix = _load_preview_pixmap_for_canvas(path)
        if pix is None or pix.isNull():
            return
        self._cancel_pending_full_preview()
        self._set_canvas_pixmap(pix)
        self._set_preview_status_text(pix.width(), pix.height())
        self._full_preview_loaded = True

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
        self._ensure_full_preview_loaded_sync()
        return self._canvas.render_source_pixmap_with_overlays()

    def save_source_pixmap_with_overlays(
        self,
        path: str,
        fmt: str | None = None,
        quality: int = -1,
    ) -> bool:
        self._ensure_full_preview_loaded_sync()
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
        self._preview_status_label.setText(f"当前预览分辨率: {resolution_text} | 当前缩放: {scale_text}")

    def _on_canvas_display_scale_percent_changed(self, scale_percent: object) -> None:
        self._refresh_preview_status_text()
        self.display_scale_percent_changed.emit(scale_percent)

    def current_path(self):
        return self._current_path

    def shutdown(self) -> None:
        self._preview_request_token += 1
        self._cancel_pending_full_preview()
        workers = [self._full_preview_loader] + list(self._retired_full_preview_loaders)
        for worker in workers:
            if worker is None:
                continue
            try:
                worker.requestInterruption()
                worker.wait(2000)
            except Exception:
                pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def source_pixmap_for_path(self, path: str) -> QPixmap | None:
        """返回当前预览已经加载的同路径源图，供右侧信息面板复用，避免重复解码。"""
        if not path or not self._current_path:
            return None
        try:
            requested = os.path.normcase(os.path.normpath(path))
            current = os.path.normcase(os.path.normpath(str(self._current_path)))
        except Exception:
            requested = str(path)
            current = str(self._current_path)
        if requested != current:
            return None
        pixmap = getattr(self._canvas, "_source_pixmap", None)
        if pixmap is None or pixmap.isNull():
            return None
        return pixmap

    def _apply_overlay_options(self) -> None:
        options = PreviewOverlayOptions(show_focus_box=False)
        if hasattr(options, "composition_grid_mode"):
            options.composition_grid_mode = self._composition_grid_mode
        if hasattr(options, "composition_grid_line_width"):
            options.composition_grid_line_width = self._composition_grid_line_width
        self._canvas.apply_overlay_options(options)
        self._canvas.update()
