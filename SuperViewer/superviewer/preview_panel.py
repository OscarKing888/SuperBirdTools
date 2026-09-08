# -*- coding: utf-8 -*-
"""预览区：内嵌 PreviewCanvas，提供 set_image 与构图线。"""

from __future__ import annotations

import io as _io
import time as _time
import os
from pathlib import Path

from PIL import Image, ImageOps

from app_common import thumb_stream
from app_common.file_browser._browser_core import _load_thumbnail_image, _read_thumb_from_disk_cache
from app_common.image_formats import HEIF_EXTENSIONS, PHOTOSHOP_EXTENSIONS, RAW_EXTENSIONS
from app_common.log import get_logger
from app_common.perf_probe import perf_log
from app_common.psd_composite import read_psd_composite_size
from app_common.preview_canvas import (
    PreviewCanvas,
    PreviewOverlayOptions,
    format_preview_scale_percent,
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
)
from app_common.superviewer_user_options import get_keep_view_on_switch

from .focus_preview_loader import (
    _get_orientation_from_file,
    _load_preview_pixmap_for_canvas,
    _load_raw_full_as_pixmap,
)
from .qt_compat import (
    _KeepAspectRatio,
    _SmoothTransformation,
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
_DIRECT_ORIGINAL_PREVIEW_MAX_PIXELS = 40 * 1024 * 1024  # 保留原有约 40MP 阈值。
_EXPORT_PREVIEW_DRAIN_TIMEOUT_MS = 30_000
_HEIF_PIL_OPENER_REGISTERED = False


def _qimage_rgb888_format():
    fmt_container = getattr(QImage, "Format", QImage)
    fmt = getattr(fmt_container, "Format_RGB888", None)
    if fmt is None:
        fmt = getattr(QImage, "Format_RGB888")
    return fmt


def _register_heif_pil_opener() -> bool:
    global _HEIF_PIL_OPENER_REGISTERED
    if _HEIF_PIL_OPENER_REGISTERED:
        return True
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return False
    try:
        register_heif_opener()
        _HEIF_PIL_OPENER_REGISTERED = True
        return True
    except Exception:
        return False


def _qimage_from_rgb_result(result) -> QImage | None:
    if not result:
        return None
    try:
        data, w, h = result
        w = int(w)
        h = int(h)
        if w <= 0 or h <= 0:
            return None
        qimg = QImage(bytes(data), w, h, w * 3, _qimage_rgb888_format()).copy()
        return qimg if not qimg.isNull() else None
    except Exception:
        return None


def _quick_preview_target_size(canvas: QWidget, requested_size: int | None = None) -> int:
    try:
        parsed = int(requested_size or 0)
    except Exception:
        parsed = 0
    return max(_QUICK_PREVIEW_FALLBACK_SIZE, parsed or _QUICK_PREVIEW_SIZE)


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
    if (qimg is None or qimg.isNull()) and Path(path).suffix.lower() in HEIF_EXTENSIONS:
        # Pillow's HEIF thumbnail path still decodes the full HEVC image.
        # A cache miss must leave that work to the owned full-preview worker,
        # including when the caller only wants a held-key quick frame.
        return None
    if qimg is None or qimg.isNull():
        qimg = _load_thumbnail_image(path, _QUICK_PREVIEW_FALLBACK_SIZE)
    if qimg is None or qimg.isNull():
        qimg = _qimage_from_rgb_result(thumb_stream.load_thumbnail_rgb(path, _QUICK_PREVIEW_FALLBACK_SIZE))
    if qimg is None or qimg.isNull():
        return None
    pix = QPixmap.fromImage(qimg)
    return pix if not pix.isNull() else None


def _preview_source_pixel_count(path: str) -> int:
    """只读图片头获取像素数，不触发原图像素解码。"""
    if not path or not os.path.isfile(path):
        return 0
    ext = Path(path).suffix.lower()
    if ext in RAW_EXTENSIONS:
        # RAW 即使尺寸较小也不能在选图热路径同步 demosaic。
        return 0
    if ext in PHOTOSHOP_EXTENSIONS:
        psd_size = read_psd_composite_size(path)
        if psd_size is not None:
            return max(0, int(psd_size[0])) * max(0, int(psd_size[1]))
    try:
        reader = QImageReader(path)
        try:
            reader.setAutoTransform(True)
        except Exception:
            pass
        pixels = _qsize_pixel_count(reader.size())
        if pixels > 0:
            return pixels
    except Exception:
        pass
    if ext in HEIF_EXTENSIONS:
        _register_heif_pil_opener()
    try:
        with Image.open(path) as img:
            width, height = img.size
            return max(0, int(width)) * max(0, int(height))
    except Exception:
        return 0


def _should_load_original_immediately(path: str) -> bool:
    pixels = _preview_source_pixel_count(path)
    return 0 < pixels <= _DIRECT_ORIGINAL_PREVIEW_MAX_PIXELS


def _qimage_pixel_count(qimg: QImage | None) -> int:
    if qimg is None or qimg.isNull():
        return 0
    try:
        return max(0, int(qimg.width())) * max(0, int(qimg.height()))
    except Exception:
        return 0


def _qsize_pixel_count(size) -> int:
    try:
        if size is None or not size.isValid():
            return 0
        return max(0, int(size.width())) * max(0, int(size.height()))
    except Exception:
        return 0


def _apply_orientation_to_pil_image(img: Image.Image, orientation: int) -> Image.Image:
    try:
        orientation = int(orientation or 1)
    except Exception:
        orientation = 1
    method = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(orientation)
    return img.transpose(method) if method is not None else img


def _qimage_from_pil_image(img: Image.Image) -> QImage | None:
    try:
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (45, 45, 45))
            try:
                alpha = img.split()[-1]
                bg.paste(img.convert("RGB"), mask=alpha)
            except Exception:
                bg.paste(img.convert("RGB"))
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if w <= 0 or h <= 0:
            return None
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, int(w), int(h), int(w) * 3, _qimage_rgb888_format()).copy()
        return qimg if not qimg.isNull() else None
    except Exception:
        return None


def _load_raw_embedded_preview_qimage(path: str) -> QImage | None:
    if not path or not os.path.isfile(path) or Path(path).suffix.lower() not in RAW_EXTENSIONS:
        return None
    preview_data = thumb_stream.get_raw_preview_jpeg(path)
    if not preview_data:
        return None
    try:
        with Image.open(_io.BytesIO(preview_data)) as img:
            img = _apply_orientation_to_pil_image(img, _get_orientation_from_file(path))
            return _qimage_from_pil_image(img)
    except Exception:
        return None


def _load_full_preview_qimage_raw(path: str) -> QImage | None:
    """加载 RAW 的显示级预览，不承担覆盖导出的原分辨率契约。"""
    embedded = _load_raw_embedded_preview_qimage(path)
    if embedded is not None and not embedded.isNull():
        return embedded
    try:
        import rawpy
    except Exception:
        return None
    try:
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(
                half_size=True,
                use_camera_wb=True,
                no_auto_bright=False,
                output_bps=8,
            )
        img = Image.fromarray(rgb).convert("RGB")
        img = _apply_orientation_to_pil_image(img, _get_orientation_from_file(path))
        return _qimage_from_pil_image(img)
    except Exception:
        return None


def _load_full_preview_qimage_pil(path: str) -> QImage | None:
    if not path or not os.path.isfile(path):
        return None
    if Path(path).suffix.lower() in HEIF_EXTENSIONS:
        _register_heif_pil_opener()
    try:
        with Image.open(path) as img:
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            return _qimage_from_pil_image(img)
    except Exception:
        if Path(path).suffix.lower() in PHOTOSHOP_EXTENSIONS:
            return _qimage_from_rgb_result(
                thumb_stream.load_psd_composite_rgb(path, None)
            )
        return None


def _load_full_preview_qimage(path: str) -> QImage | None:
    if not path or not os.path.isfile(path):
        return None
    ext = Path(path).suffix.lower()
    qt_qimg = None
    expected_pixels = 0

    if ext in RAW_EXTENSIONS:
        # RAW 的常规显示只允许内嵌预览或 half-size 解码；覆盖导出走独立
        # 的全分辨率路径，避免切图时做完整传感器 demosaic。
        return _load_full_preview_qimage_raw(path)

    if ext in HEIF_EXTENSIONS:
        pil_qimg = _load_full_preview_qimage_pil(path)
        if pil_qimg is not None and not pil_qimg.isNull():
            return pil_qimg

    try:
        reader = QImageReader(path)
        try:
            reader.setAutoTransform(True)
        except Exception:
            pass
        try:
            expected_pixels = _qsize_pixel_count(reader.size())
        except Exception:
            expected_pixels = 0
        qimg = reader.read()
        if qimg is not None and not qimg.isNull():
            qt_qimg = qimg.copy()
    except Exception:
        pass

    qt_pixels = _qimage_pixel_count(qt_qimg)
    should_try_pil = qt_pixels <= 0 or (
        expected_pixels > 0 and qt_pixels < int(expected_pixels * 0.9)
    )
    if should_try_pil:
        pil_qimg = _load_full_preview_qimage_pil(path)
        if (
            pil_qimg is not None
            and not pil_qimg.isNull()
            and _qimage_pixel_count(pil_qimg) >= qt_pixels
        ):
            return pil_qimg
    return qt_qimg if qt_pixels > 0 else None


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
        if self.isInterruptionRequested():
            # 不把可能很大的过期 QImage 排入 GUI 事件队列。
            qimg = None
            return
        self.loaded.emit(
            self._token,
            self._path,
            qimg,
            (_time.perf_counter() - started) * 1000.0,
        )


class PreviewPanel(QWidget):
    """预览区：内嵌 app_common.preview_canvas.PreviewCanvas，提供 set_image 等接口。"""

    display_scale_percent_changed = pyqtSignal(object)
    full_preview_ready = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setAcceptDrops(False)
        self._current_path = None
        self._preview_request_token = 0
        self._full_preview_loaded = False
        self._canvas_source_full_resolution = False
        self._fast_preview_only = False
        self._full_preview_loader: _FullPreviewLoader | None = None
        self._pending_full_preview_request: tuple[int, str] | None = None
        self._shutdown_requested = False
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

    def set_image(self, path: str, *, load_full: bool = True, quick_size: int | None = None):
        if self._shutdown_requested:
            return
        t0 = _time.perf_counter()
        norm_path = os.path.normpath(path) if path else path
        same_path = bool(norm_path and self._is_current_path(norm_path))
        can_reuse = bool(same_path and self._has_canvas_pixmap())
        if can_reuse:
            if not load_full:
                # 同路径从普通预览切回键盘快速模式时，也必须让已经排队的
                # full result 失效；保留当前 canvas，不重新读取缩略图。
                self._preview_request_token += 1
                self._fast_preview_only = True
                self._cancel_pending_full_preview()
                return
            if load_full:
                self._fast_preview_only = False
            if load_full and not self._full_preview_loaded and not self._full_preview_timer.isActive():
                loader = self._full_preview_loader
                loader_is_current = bool(
                    loader is not None
                    and loader.isRunning()
                    and int(getattr(loader, "_token", -1)) == int(self._preview_request_token)
                    and self._is_current_path(getattr(loader, "_path", ""))
                )
                if not loader_is_current:
                    if not self._try_set_direct_original_preview(norm_path):
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
        self._canvas_source_full_resolution = False
        self._fast_preview_only = not bool(load_full)
        self._cancel_pending_full_preview()
        load_t0 = _time.perf_counter()
        target_size = _quick_preview_target_size(self._canvas, quick_size)
        pix = None
        direct_original = False
        if load_full:
            direct_original = self._try_set_direct_original_preview(norm_path)
        if not direct_original:
            pix = _load_quick_preview_pixmap(path, target_size)
        load_ms = (_time.perf_counter() - load_t0) * 1000.0
        canvas_ms = 0.0
        status_ms = 0.0
        if direct_original:
            # _try_set_direct_original_preview 已更新 canvas 与状态栏。
            canvas_ms = 0.0
            status_ms = 0.0
        elif pix is not None and not pix.isNull():
            canvas_t0 = _time.perf_counter()
            self._set_canvas_pixmap(pix, log_performance=load_full)
            canvas_ms = (_time.perf_counter() - canvas_t0) * 1000.0
            status_t0 = _time.perf_counter()
            self._set_preview_status_text(pix.width(), pix.height())
            status_ms = (_time.perf_counter() - status_t0) * 1000.0
        else:
            canvas_t0 = _time.perf_counter()
            self._canvas.set_source_pixmap(None, log_performance=load_full)
            loading_heif = bool(load_full and path and Path(path).suffix.lower() in HEIF_EXTENSIONS)
            message = "正在加载预览" if loading_heif else "无法预览"
            self._canvas.setText(f"{message}\n{Path(path).name if path else ''}")
            canvas_ms = (_time.perf_counter() - canvas_t0) * 1000.0
            status_t0 = _time.perf_counter()
            self._set_preview_status_text(None, None)
            status_ms = (_time.perf_counter() - status_t0) * 1000.0
        if load_full and path and not direct_original:
            self._full_preview_timer.start(_FULL_PREVIEW_DELAY_MS)
        perf_log(
            _log,
            "[PERF][image_switch][preview_panel.set_image] path=%r token=%s direct_original=%s quick_ok=%s quick_size=%s target=%s load_ms=%.1f canvas_ms=%.1f status_ms=%.1f total_ms=%.1f",
            path,
            token,
            direct_original,
            bool(pix is not None and not pix.isNull()),
            (pix.width(), pix.height()) if pix is not None and not pix.isNull() else None,
            target_size,
            load_ms,
            canvas_ms,
            status_ms,
            (_time.perf_counter() - t0) * 1000.0,
        )

    def set_quick_pixmap(self, path: str, pixmap: QPixmap, *, quick_size: int | None = None) -> None:
        """直接显示文件列表内存中的当前缩略图层级，避免落盘再读。"""
        if self._shutdown_requested:
            return
        self._preview_request_token += 1
        self._current_path = os.path.normpath(path) if path else path
        self._full_preview_loaded = False
        self._canvas_source_full_resolution = False
        self._fast_preview_only = True
        self._cancel_pending_full_preview()

        target_size = _quick_preview_target_size(self._canvas, quick_size)
        output = None
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            if pixmap.width() <= target_size and pixmap.height() <= target_size:
                output = pixmap
            else:
                output = pixmap.scaled(
                    target_size,
                    target_size,
                    _KeepAspectRatio,
                    _SmoothTransformation,
                )
        if output is not None and not output.isNull():
            self._set_canvas_pixmap(output, log_performance=False)
            self._set_preview_status_text(output.width(), output.height())
        else:
            self._canvas.set_source_pixmap(None, log_performance=False)
            self._canvas.setText(f"无法预览\n{Path(path).name if path else ''}")
            self._set_preview_status_text(None, None)

    def clear_image(self):
        self._preview_request_token += 1
        self._current_path = None
        self._full_preview_loaded = False
        self._canvas_source_full_resolution = False
        self._fast_preview_only = False
        self._cancel_pending_full_preview()
        self._canvas.set_source_pixmap(None)
        self._set_preview_status_text(None, None)

    def _set_canvas_pixmap(self, pix: QPixmap, *, log_performance: bool = True) -> None:
        if self._keep_view_on_switch:
            self._canvas.set_source_pixmap(
                pix,
                preserve_view=True,
                preserve_scale=True,
                log_performance=log_performance,
            )
        else:
            self._canvas.set_source_pixmap(
                pix,
                reset_view=True,
                log_performance=log_performance,
            )

    def _has_canvas_pixmap(self) -> bool:
        pixmap = getattr(self._canvas, "_source_pixmap", None)
        return bool(pixmap is not None and not pixmap.isNull())

    def _try_set_direct_original_preview(self, path: str) -> bool:
        if not path or self._full_preview_loader is not None:
            # 旧 worker 尚未由 finished 回调清理时继续维持 single-flight。
            return False
        if not _should_load_original_immediately(path):
            return False
        load_t0 = _time.perf_counter()
        qimg = _load_full_preview_qimage(path)
        load_ms = (_time.perf_counter() - load_t0) * 1000.0
        if qimg is None or qimg.isNull():
            return False
        pix = QPixmap.fromImage(qimg)
        if pix.isNull():
            return False
        apply_t0 = _time.perf_counter()
        self._set_canvas_pixmap(pix)
        self._set_preview_status_text(pix.width(), pix.height())
        self._full_preview_loaded = True
        self._canvas_source_full_resolution = True
        self._fast_preview_only = False
        perf_log(
            _log,
            "[preview.direct_original] path=%r size=%s load_ms=%.1f apply_ms=%.1f",
            path,
            (pix.width(), pix.height()),
            load_ms,
            (_time.perf_counter() - apply_t0) * 1000.0,
        )
        return True

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
        self._pending_full_preview_request = None
        loader = self._full_preview_loader
        if loader is not None and loader.isRunning():
            loader.requestInterruption()

    def _start_full_preview_loader(self) -> None:
        if self._shutdown_requested:
            return
        path = os.path.normpath(str(self._current_path or ""))
        if not path or not os.path.isfile(path):
            return
        token = self._preview_request_token
        loader = self._full_preview_loader
        if loader is not None:
            if loader.isRunning():
                loader.requestInterruption()
            # 底层解码通常不能在函数中途取消；这里只保存最后一次请求，绝不
            # 并行启动第二个全图 decoder。即使线程已结束，也要等待其 queued
            # finished 回调清理当前指针，避免旧回调与新线程交错。
            self._pending_full_preview_request = (int(token), path)
            return
        self._pending_full_preview_request = None
        self._launch_full_preview_loader(token, path)

    def _launch_full_preview_loader(self, token: int, path: str) -> None:
        if self._shutdown_requested:
            return
        if self._full_preview_loader is not None:
            self._pending_full_preview_request = (int(token), path)
            return
        if int(token) != int(self._preview_request_token):
            return
        if not path or not self._is_current_path(path) or not os.path.isfile(path):
            return
        loader = _FullPreviewLoader(token, path, self)
        loader.loaded.connect(self._on_full_preview_loaded)
        loader.finished.connect(lambda l=loader: self._cleanup_full_preview_loader(l))
        self._full_preview_loader = loader
        loader.start()

    def _cleanup_full_preview_loader(self, loader: _FullPreviewLoader) -> None:
        was_current = self._full_preview_loader is loader
        if was_current:
            self._full_preview_loader = None
        try:
            loader.deleteLater()
        except Exception:
            pass
        if not was_current:
            return
        if self._shutdown_requested:
            self._pending_full_preview_request = None
            return
        pending = self._pending_full_preview_request
        self._pending_full_preview_request = None
        if pending is not None:
            token, path = pending
            self._launch_full_preview_loader(token, path)

    def _on_full_preview_loaded(self, token: int, path: str, qimg, load_ms: float) -> None:
        if self._shutdown_requested or int(token) != int(self._preview_request_token):
            return
        if not path or not self._current_path:
            return
        if not self._is_current_path(path):
            return
        if qimg is None or qimg.isNull():
            if not self._has_canvas_pixmap():
                self._canvas.setText(f"无法预览\n{Path(path).name}")
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
        self._canvas_source_full_resolution = Path(path).suffix.lower() not in RAW_EXTENSIONS
        self._fast_preview_only = False
        self.full_preview_ready.emit(path)
        perf_log(
            _log,
            "[preview.full] path=%r token=%s ok=True size=%s load_ms=%.1f apply_ms=%.1f",
            path,
            token,
            (pix.width(), pix.height()),
            load_ms,
            (_time.perf_counter() - apply_t0) * 1000.0,
        )

    def _ensure_full_preview_loaded_sync(self) -> bool:
        if self._canvas_source_full_resolution:
            return True
        path = os.path.normpath(str(self._current_path or ""))
        if not path or not os.path.isfile(path):
            return False
        # 使已经排入 GUI 队列、但尚未处理的显示级结果立即失效，避免它在
        # 导出完成后把 full-resolution canvas 覆盖回 RAW half-size。
        self._preview_request_token += 1
        self._cancel_pending_full_preview()
        loader = self._full_preview_loader
        if loader is not None and loader.isRunning():
            try:
                loader.requestInterruption()
                drained = bool(loader.wait(_EXPORT_PREVIEW_DRAIN_TIMEOUT_MS))
            except Exception:
                drained = False
            if not drained:
                _log.warning("[preview.export] decoder drain timed out path=%r", path)
                return False
            if self._full_preview_loader is loader:
                self._full_preview_loader = None
            try:
                loader.deleteLater()
            except Exception:
                pass

        ext = Path(path).suffix.lower()
        if ext in RAW_EXTENSIONS:
            # 显示级 RAW 可以是内嵌图/half-size；覆盖导出必须显式完整解码。
            pix = _load_raw_full_as_pixmap(path)
        else:
            pix = _load_preview_pixmap_for_canvas(path)
            if (pix is None or pix.isNull()) and ext in PHOTOSHOP_EXTENSIONS:
                qimg = _load_full_preview_qimage(path)
                pix = QPixmap.fromImage(qimg) if qimg is not None and not qimg.isNull() else None
        if pix is None or pix.isNull():
            return False
        self._set_canvas_pixmap(pix)
        self._set_preview_status_text(pix.width(), pix.height())
        self._full_preview_loaded = True
        self._canvas_source_full_resolution = True
        self._fast_preview_only = False
        return True

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
        if not self._ensure_full_preview_loaded_sync():
            return None
        return self._canvas.render_source_pixmap_with_overlays()

    def save_source_pixmap_with_overlays(
        self,
        path: str,
        fmt: str | None = None,
        quality: int = -1,
    ) -> bool:
        if not self._ensure_full_preview_loaded_sync():
            return False
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

    def request_shutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._preview_request_token += 1
        self._cancel_pending_full_preview()

    def shutdown(self, *, wait_timeout_ms: int | None = None) -> bool:
        self.request_shutdown()
        worker = self._full_preview_loader
        if worker is None:
            return True
        try:
            worker.requestInterruption()
            if wait_timeout_ms is None:
                wait_result = worker.wait()
            else:
                wait_result = worker.wait(max(0, int(wait_timeout_ms)))
        except Exception:
            wait_result = False
        finished = bool(wait_result)
        try:
            finished = finished or not worker.isRunning()
        except Exception:
            pass
        if not finished:
            return False
        if self._full_preview_loader is worker:
            self._full_preview_loader = None
        try:
            worker.deleteLater()
        except Exception:
            pass
        return True

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.request_shutdown()
        if not self.shutdown(wait_timeout_ms=25):
            event.ignore()
            QTimer.singleShot(25, self.close)
            return
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
