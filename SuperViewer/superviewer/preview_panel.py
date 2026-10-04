# -*- coding: utf-8 -*-
"""预览区：内嵌 PreviewCanvas，提供 set_image 与构图线。"""

from __future__ import annotations

import time as _time
import io as _io
import os
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY, rawpy_camera_crop_box, map_camera_focus_box
from app_common import thumb_stream
from app_common.image_formats import HEIF_EXTENSIONS, PHOTOSHOP_EXTENSIONS, RAW_EXTENSIONS
from app_common.log import get_logger
from app_common.perf_probe import perf_log
from app_common.psd_composite import read_psd_composite_size
from app_common.preview_canvas import (
    FocusCenteredPreviewCanvas as _FocusCenteredPreviewCanvas,
    PreviewOverlayOptions,
    PreviewOverlayState,
    format_preview_scale_percent,
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
)
from app_common.superviewer_user_options import get_keep_view_on_switch
from .bird_body_overlay import BirdBodyOverlayMixin, map_bird_overlay

from .focus_preview_loader import _load_preview_pixmap_for_canvas
from .qt_compat import (
    _KeepAspectRatio,
    _SmoothTransformation,
    QImage,
    QImageReader,
    QLabel,
    QPixmap,
    QSize,
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
_HEIF_PIL_OPENER_REGISTERED = False
_PREVIEW_NOTE_KEY = "superviewer_preview_note"
_DENOISED_PATH_KEY = "superviewer_denoised_path"


class ViewerPreviewCanvas(BirdBodyOverlayMixin, _FocusCenteredPreviewCanvas):
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


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


_SYNC_FULL_PREVIEW_MAX_MP = max(0.0, _env_float("SuperViewer_SYNC_FULL_PREVIEW_MAX_MP", 40.0))
_SYNC_FULL_PREVIEW_MAX_PIXELS = int(_SYNC_FULL_PREVIEW_MAX_MP * 1_000_000)
# HEVC 解码单位像素成本远高于 JPEG（M2 Max 实测 21 MP HIF 约 380 ms，约 18 ms/MP），
# 因此 HEIF 使用独立阈值：默认只让约 4 MP 以下的小 HIF 在 GUI 线程同步完整显示，
# 更大的 HIF 走“精确档位缓存 → 正在加载 → worker 完整解码”。设为 40 即恢复旧行为。
_SYNC_FULL_PREVIEW_HEIF_MAX_MP = max(0.0, _env_float("SuperViewer_SYNC_FULL_PREVIEW_HEIF_MAX_MP", 4.0))
_SYNC_FULL_PREVIEW_HEIF_MAX_PIXELS = int(_SYNC_FULL_PREVIEW_HEIF_MAX_MP * 1_000_000)


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
    return _normalize_preview_target_size(requested_size or _QUICK_PREVIEW_SIZE)


def _can_reuse_current_preview(
    *,
    same_path: bool,
    has_pixmap: bool,
    load_full: bool,
    fast_preview_only: bool,
) -> bool:
    """A fast-only frame must re-enter normal loading policy on key release."""
    return bool(same_path and has_pixmap and not (load_full and fast_preview_only))


def _normalize_preview_target_size(target_size: int | None) -> int:
    try:
        parsed = int(target_size or 0)
    except Exception:
        parsed = 0
    return max(_QUICK_PREVIEW_FALLBACK_SIZE, parsed or _QUICK_PREVIEW_SIZE)


def _scale_preview_qimage(qimg: QImage | None, target_size: int) -> QImage | None:
    if qimg is None or qimg.isNull():
        return None
    target_size = _normalize_preview_target_size(target_size)
    try:
        width = int(qimg.width())
        height = int(qimg.height())
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    if width <= target_size and height <= target_size:
        return qimg.copy()
    scaled = qimg.scaled(
        target_size,
        target_size,
        _KeepAspectRatio,
        _SmoothTransformation,
    )
    return scaled.copy() if scaled is not None and not scaled.isNull() else None


def _load_scaled_preview_qimage(path: str, target_size: int) -> QImage | None:
    if not path or not os.path.isfile(path):
        return None
    target_size = _normalize_preview_target_size(target_size)
    try:
        reader = QImageReader(path)
        try:
            reader.setAutoTransform(True)
        except Exception:
            pass
        size = reader.size()
        if size is not None and size.isValid():
            scaled_size = size.scaled(QSize(target_size, target_size), _KeepAspectRatio)
            if scaled_size.isValid():
                reader.setScaledSize(scaled_size)
        qimg = reader.read()
        return qimg.copy() if qimg is not None and not qimg.isNull() else None
    except Exception:
        return None


def _load_quick_preview_pixmap(path: str, target_size: int) -> QPixmap | None:
    target_size = _normalize_preview_target_size(target_size)
    qimg = _qimage_from_rgb_result(thumb_stream.load_thumbnail_rgb(path, target_size))
    if qimg is None or qimg.isNull():
        qimg = _load_scaled_preview_qimage(path, target_size)
    qimg = _scale_preview_qimage(qimg, target_size)
    if qimg is None or qimg.isNull():
        return None
    pix = QPixmap.fromImage(qimg)
    return pix if not pix.isNull() else None


def _expected_image_pixel_count(path: str) -> int:
    if not path or not os.path.isfile(path):
        return 0
    ext = Path(path).suffix.lower()
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
        size = reader.size()
        pixels = _qsize_pixel_count(size)
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


def _sync_full_preview_max_pixels(path: str) -> int:
    if Path(path).suffix.lower() in HEIF_EXTENSIONS:
        return _SYNC_FULL_PREVIEW_HEIF_MAX_PIXELS
    return _SYNC_FULL_PREVIEW_MAX_PIXELS


def _should_load_full_preview_sync(path: str) -> bool:
    if not path or Path(path).suffix.lower() in RAW_EXTENSIONS:
        return False
    max_pixels = _sync_full_preview_max_pixels(path)
    if max_pixels <= 0:
        return False
    pixels = _expected_image_pixel_count(path)
    return 0 < pixels <= max_pixels


def _defers_uncached_preview_to_worker(path: str) -> bool:
    """这些格式的任何预览都需要完整解码或读取 RAW，GUI 线程只用已有缓存，其余交给 worker。

    - HEIF：Pillow 缩略图仍会完整解码 HEVC。
    - RAW：内嵌 JPEG 提取与解码（旧实现最多 3 次 ExifTool 进程）不能阻塞 GUI 点击。
    """
    ext = Path(path).suffix.lower() if path else ""
    return ext in HEIF_EXTENSIONS or ext in RAW_EXTENSIONS


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


def _exif_transpose_in_place(img: Image.Image) -> None:
    """按 EXIF 方向就地旋转；方向为 1 时不复制整幅像素（exif_transpose 默认总会 copy）。"""
    try:
        ImageOps.exif_transpose(img, in_place=True)
    except Exception:
        pass


def _qimage_for_pixmap_upload(qimg: QImage | None) -> QImage | None:
    """在 worker 线程把 RGB888 转成 RGB32，GUI 线程 QPixmap.fromImage 就无需再转换格式。"""
    if qimg is None or qimg.isNull():
        return qimg
    try:
        rgb888 = _qimage_rgb888_format()
        if qimg.format() == rgb888:
            fmt_container = getattr(QImage, "Format", QImage)
            rgb32 = getattr(fmt_container, "Format_RGB32", None) or getattr(QImage, "Format_RGB32")
            converted = qimg.convertToFormat(rgb32)
            if not converted.isNull():
                return converted
    except Exception:
        pass
    return qimg


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
    try:
        preview_data = thumb_stream.get_raw_preview_jpeg(path)
    except Exception:
        preview_data = None
    if preview_data:
        try:
            with Image.open(_io.BytesIO(preview_data)) as img:
                if max(img.size) < thumb_stream.RAW_INPROCESS_PREVIEW_MIN_LONG_EDGE:
                    return None
                # 与 BirdStamp 一致：使用内嵌 JPEG 自身的 EXIF 方向。
                _exif_transpose_in_place(img)
                embedded_qimg = _qimage_from_pil_image(img)
                if embedded_qimg is not None and not embedded_qimg.isNull():
                    return embedded_qimg
        except Exception:
            pass
    return None


def _load_full_preview_qimage_raw(path: str) -> QImage | None:
    if not path or not os.path.isfile(path) or Path(path).suffix.lower() not in RAW_EXTENSIONS:
        return None
    embedded = _load_raw_embedded_preview_qimage(path)
    if embedded is not None and not embedded.isNull():
        return embedded
    return _load_sensor_raw_qimage(path)


def _load_sensor_raw_qimage(path: str) -> QImage | None:
    """完整 RAW 解码仅由后台预览 worker（或显式导出）调用。"""
    try:
        import rawpy

        # LibRaw 的 Windows 窄字符路径不支持中文；二进制流交给 rawpy。
        source = (open(path, "rb") if sys.platform.startswith("win") and not path.isascii()
                  else nullcontext(path))
        with source as raw_source, rawpy.imread(raw_source) as raw:
            pixels = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
            crop = rawpy_camera_crop_box(getattr(raw, "sizes", None))
        # LibRaw 已处理传感器方向，不能再次应用源文件的 Orientation。
        image = _qimage_from_pil_image(Image.fromarray(pixels))
        if image is not None and crop is not None:
            image.setText(RAW_FOCUS_CROP_KEY, json.dumps(crop))
        return image
    except Exception:
        _log.exception("[preview.raw] full RAW decode failed path=%r", path)
        return None


def _load_full_preview_qimage_pil(path: str) -> QImage | None:
    if not path or not os.path.isfile(path):
        return None
    if Path(path).suffix.lower() in HEIF_EXTENSIONS:
        _register_heif_pil_opener()
    try:
        with Image.open(path) as img:
            _exif_transpose_in_place(img)
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
        # 与 BirdStamp 共用内嵌预览选择规则，不让 Qt/Pillow 另选低清缩略图。
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
            # read() returns an image that owns its pixels; a deep copy here only
            # duplicated a full-size buffer (~87 ms for 33 MP) on the GUI thread.
            qt_qimg = qimg
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


def _load_denoised_preview_qimage(path: str, *, cancelled=None) -> QImage | None:
    """成片查找、XML 读取及解码均位于完整预览 worker，绝不在快切路径运行。"""
    from app_common.superviewer_user_options import get_runtime_user_options
    from image_denoise.preview import find_denoised_preview
    from image_denoise.types import DenoiseOptions, DenoiseCancelled

    settings = get_runtime_user_options()
    options = DenoiseOptions(**{key: settings[f"denoise_{key}"] for key in
                                ("output_mode", "subdir", "output_directory", "format",
                                 "strength", "device", "workers")})
    note = "未找到降噪成片，当前显示原图；可通过右键菜单执行降噪"
    try:
        result = find_denoised_preview(path, options, cancelled=cancelled)
        if cancelled is not None and cancelled():
            return None
        if result is not None:
            image = _load_full_preview_qimage(result.path)
            if image is not None and not image.isNull():
                crop = result.camera_crop
                if result.legacy and Path(path).suffix.lower() in RAW_EXTENSIONS:
                    # 旧成片未记录几何时仅在后台读取 LibRaw 尺寸，不进行二次显影。
                    import rawpy
                    with open(path, "rb") as stream, rawpy.imread(stream) as raw:
                        crop = rawpy_camera_crop_box(raw.sizes)
                if crop is not None:
                    image.setText(RAW_FOCUS_CROP_KEY, json.dumps(crop))
                image.setText(_PREVIEW_NOTE_KEY, "显示降噪成片")
                image.setText(_DENOISED_PATH_KEY, result.path)
                return image
            note = "降噪成片解码失败，当前显示原图"
    except DenoiseCancelled:
        return None
    except Exception:
        _log.exception("[preview.denoised] source=%r lookup/decode failed", path)
        note = "降噪成片读取失败，当前显示原图"
    if cancelled is not None and cancelled():
        return None
    image = _load_full_preview_qimage(path)
    if image is not None and not image.isNull():
        image.setText(_PREVIEW_NOTE_KEY, note)
    return image


class _FullPreviewLoader(QThread):
    loaded = pyqtSignal(int, str, object, float)

    def __init__(self, token: int, path: str, parent=None, *, show_raw: bool = False,
                 source_mode: str | None = None) -> None:
        super().__init__(parent)
        self._token = int(token)
        self._source_mode = source_mode or ("raw" if show_raw else "default")
        self._show_raw = self._source_mode == "raw"
        self._path = os.path.normpath(path) if path else ""

    def run(self) -> None:
        started = _time.perf_counter()
        qimg = None
        if not self.isInterruptionRequested():
            if self._source_mode == "denoised":
                qimg = _load_denoised_preview_qimage(self._path, cancelled=self.isInterruptionRequested)
            elif self._show_raw and Path(self._path).suffix.lower() in RAW_EXTENSIONS:
                qimg = _load_sensor_raw_qimage(self._path)
            else:
                qimg = _load_full_preview_qimage(self._path)
        if self.isInterruptionRequested():
            # Dropping the decoded QImage in this worker avoids queueing a
            # potentially hundreds-of-megabytes stale object to the GUI loop.
            qimg = None
            return
        qimg = _qimage_for_pixmap_upload(qimg)
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
    source_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setAcceptDrops(False)
        self._current_path = None
        self._source_identity_path = ""
        self._focus_cache_path = ""
        self._show_raw = False
        self._preview_source_mode = "default"
        self._preview_note = ""
        self._denoised_display_path = ""
        self._source_focus_box = None
        self._source_bird_box = None
        self._raw_focus_crop_box = None
        self._last_quick_size = None
        self._fit_next_image = True
        self._first_image_fit_token = None
        self._preview_request_token = 0
        self._full_preview_loaded = False
        self._fast_preview_only = False
        self._navigation_playback_active = False
        self._full_preview_loader: _FullPreviewLoader | None = None
        self._pending_full_preview_request: tuple[int, str] | None = None
        self._shutdown_requested = False
        self._quick_preview_provider: Callable[[str, int], QPixmap | None] | None = None
        self._full_preview_timer = QTimer(self)
        self._full_preview_timer.setSingleShot(True)
        self._full_preview_timer.timeout.connect(self._start_full_preview_loader)
        self._preview_resolution: tuple[int, int] | None = None
        self._fast_preview_perf_started_at = 0.0
        self._fast_preview_perf_frames = 0
        self._fast_preview_perf_total_ms = 0.0
        self._fast_preview_perf_max_ms = 0.0
        self._keep_view_on_switch = bool(get_keep_view_on_switch())
        self._show_focus_enabled = True
        self._composition_grid_mode = normalize_preview_composition_grid_mode("none")
        self._composition_grid_line_width = normalize_preview_composition_grid_line_width(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._canvas = ViewerPreviewCanvas(self, placeholder_text="未选择图片")
        self._canvas.viewport_interacted.connect(self._cancel_first_image_fit)
        if hasattr(self._canvas, "set_keep_view_on_switch"):
            self._canvas.set_keep_view_on_switch(self._keep_view_on_switch)
        if hasattr(self._canvas, "display_scale_percent_changed"):
            self._canvas.display_scale_percent_changed.connect(self._on_canvas_display_scale_percent_changed)
        layout.addWidget(self._canvas, stretch=1)
        self._preview_status_label = QLabel("当前预览分辨率: - | 当前缩放: -")
        self._preview_status_label.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(self._preview_status_label)

    def show_raw(self) -> bool:
        return self._show_raw

    def set_show_raw(self, enabled: bool) -> None:
        self.set_preview_source_mode("raw" if enabled else "default")

    def preview_source_mode(self) -> str:
        return self._preview_source_mode

    def source_identity_path(self) -> str:
        return self._source_identity_path or self._current_path or ""

    def set_source_identity(self, path: str) -> None:
        """缓存 JPEG 作为显示路径时，工具栏仍依据真正源图判断 RAW 模式。"""
        self._source_identity_path = os.path.normpath(path) if path else ""
        self.source_changed.emit()

    def set_navigation_playback_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._navigation_playback_active:
            return
        self._navigation_playback_active = active
        if active:
            # 首个重复帧缓存未就绪时可能保留已提交画面，仍必须冻结其完整任务。
            self._preview_request_token += 1
            self._fast_preview_only = True
            self._cancel_pending_full_preview()

    def set_preview_source_mode(self, mode: str) -> None:
        if mode not in {"default", "raw", "denoised"}:
            raise ValueError(f"未知预览来源：{mode}")
        if self._shutdown_requested or mode == self._preview_source_mode:
            return
        old_mode = self._preview_source_mode
        self._preview_source_mode = mode
        self._show_raw = mode == "raw"
        path = self.source_identity_path()
        from app_common.video import is_video
        if path and not is_video(path) and (Path(path).suffix.lower() in RAW_EXTENSIONS
                                           or "denoised" in (old_mode, mode)):
            # 模式属于请求身份；强制绕过同路径复用，并让迟到结果失效。
            if self._navigation_playback_active or self._fast_preview_only:
                # 长按切模式时连缓存磁盘查询都不做，松键才读取最终来源。
                self._preview_request_token += 1
                self._cancel_pending_full_preview()
                self.source_changed.emit()
                return
            focus_box = self._source_focus_box
            bird_box = self._source_bird_box
            self._current_path = None
            self.set_image(path, load_full=True, quick_size=self._last_quick_size)
            self.set_focus_box(focus_box)
            self.set_bird_box(bird_box)
        self.source_changed.emit()

    def refresh_denoised_preview(self, source: str) -> None:
        """新成片发布后升级仍停留在该源图的视口；长按时留待最终选择。"""
        if (self._shutdown_requested or self._preview_source_mode != "denoised"
                or self._navigation_playback_active or self._fast_preview_only
                or not self._is_current_path(source)):
            return
        focus_box, bird_box = self._source_focus_box, self._source_bird_box
        self._current_path = None
        self.set_image(source, quick_size=self._last_quick_size)
        self.set_focus_box(focus_box)
        self.set_bird_box(bird_box)

    def set_image(self, path: str, *, load_full: bool = True, quick_size: int | None = None):
        if self._shutdown_requested:
            return
        if self._navigation_playback_active:
            load_full = False
        self._last_quick_size = quick_size
        t0 = _time.perf_counter()
        norm_path = os.path.normpath(path) if path else path
        if _can_reuse_current_preview(
            same_path=bool(norm_path and self._is_current_path(norm_path)),
            has_pixmap=self._has_canvas_pixmap(),
            load_full=load_full,
            fast_preview_only=self._fast_preview_only,
        ):
            if load_full and not self._full_preview_loaded and not self._full_preview_timer.isActive():
                loader = self._full_preview_loader
                if loader is None or not loader.isRunning():
                    self._full_preview_timer.start(_FULL_PREVIEW_DELAY_MS)
            total_ms = (_time.perf_counter() - t0) * 1000.0
            if load_full:
                perf_log(
                    _log,
                    "[PERF][image_switch][preview_panel.set_image] path=%r same_path=1 full_loaded=%s total_ms=%.1f",
                    path,
                    self._full_preview_loaded,
                    total_ms,
                )
            else:
                self._record_fast_preview_timing(total_ms)
            return
        self._preview_request_token += 1
        token = self._preview_request_token
        self._current_path = norm_path
        self._source_identity_path = norm_path or ""
        self._focus_cache_path = norm_path
        self.source_changed.emit()
        self._full_preview_loaded = False
        self._fast_preview_only = not bool(load_full)
        self._cancel_pending_full_preview()
        self._raw_focus_crop_box = None
        self._preview_note = ""
        self._denoised_display_path = ""
        self.set_focus_box(None)
        self.set_bird_box(None)
        load_t0 = _time.perf_counter()
        target_size = _quick_preview_target_size(self._canvas, quick_size)
        pix = None
        direct_full = False
        active_loader = self._full_preview_loader
        full_decode_busy = active_loader is not None and active_loader.isRunning()
        # RAW never decodes in the GUI thread: the embedded camera preview is
        # extracted and decoded by the owned full worker (two-stage display).
        if (load_full and path and self._preview_source_mode != "denoised"
                and not full_decode_busy and _should_load_full_preview_sync(path)):
            qimg = _load_full_preview_qimage(path)
            if qimg is not None and not qimg.isNull():
                pix = QPixmap.fromImage(qimg)
                direct_full = bool(pix is not None and not pix.isNull())
        if pix is None or pix.isNull():
            pix = self._cached_quick_preview_pixmap(path, target_size)
        # HEIF thumbnailing through Pillow still decodes the full HEVC image, and
        # RAW previews need embedded-JPEG extraction. Uncached files must not do
        # that work in the GUI thread merely to produce a placeholder frame; the
        # owned full worker handles them.
        defer_uncached_preview = _defers_uncached_preview_to_worker(path)
        if (pix is None or pix.isNull()) and not defer_uncached_preview:
            pix = _load_quick_preview_pixmap(path, target_size)
        load_ms = (_time.perf_counter() - load_t0) * 1000.0
        canvas_ms = 0.0
        status_ms = 0.0
        if pix is not None and not pix.isNull():
            canvas_t0 = _time.perf_counter()
            self._set_canvas_pixmap(pix, log_performance=load_full)
            canvas_ms = (_time.perf_counter() - canvas_t0) * 1000.0
            status_t0 = _time.perf_counter()
            self._set_preview_status_text(pix.width(), pix.height())
            status_ms = (_time.perf_counter() - status_t0) * 1000.0
            if direct_full:
                self._full_preview_loaded = True
                self._fast_preview_only = False
                self._first_image_fit_token = None
        else:
            canvas_t0 = _time.perf_counter()
            if load_full:
                self._canvas.set_source_pixmap(None)
            else:
                self._canvas.set_source_pixmap(None, log_performance=False)
            message = "正在加载预览" if defer_uncached_preview and load_full else "无法预览"
            self._canvas.setText(f"{message}\n{Path(path).name}")
            canvas_ms = (_time.perf_counter() - canvas_t0) * 1000.0
            status_t0 = _time.perf_counter()
            self._set_preview_status_text(None, None)
            status_ms = (_time.perf_counter() - status_t0) * 1000.0
        if load_full and path and not self._full_preview_loaded:
            self._full_preview_timer.start(_FULL_PREVIEW_DELAY_MS)
        total_ms = (_time.perf_counter() - t0) * 1000.0
        if load_full:
            perf_log(
                _log,
                "[PERF][image_switch][preview_panel.set_image] path=%r token=%s quick_ok=%s direct_full=%s full_loaded=%s quick_size=%s target=%s load_ms=%.1f canvas_ms=%.1f status_ms=%.1f total_ms=%.1f",
                path,
                token,
                bool(pix is not None and not pix.isNull()),
                direct_full,
                self._full_preview_loaded,
                (pix.width(), pix.height()) if pix is not None and not pix.isNull() else None,
                target_size,
                load_ms,
                canvas_ms,
                status_ms,
                total_ms,
            )
        else:
            self._record_fast_preview_timing(total_ms)

    def set_quick_preview_provider(self, provider: Callable[[str, int], QPixmap | None] | None) -> None:
        self._quick_preview_provider = provider

    def _cached_quick_preview_pixmap(self, path: str, size: int) -> QPixmap | None:
        if self._quick_preview_provider is None or not path:
            return None
        try:
            pixmap = self._quick_preview_provider(path, size)
        except Exception:
            _log.exception("[preview.quick] cached preview lookup failed path=%r", path)
            return None
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return None
        if pixmap.width() > size or pixmap.height() > size:
            pixmap = pixmap.scaled(size, size, _KeepAspectRatio, _SmoothTransformation)
        return pixmap

    def set_quick_pixmap(self, path: str, pixmap: QPixmap, *, quick_size: int | None = None) -> None:
        """Display an already-decoded selected-tier frame without disk round-tripping."""
        if self._shutdown_requested:
            return
        self._last_quick_size = quick_size
        started_at = _time.perf_counter()
        self._preview_request_token += 1
        self._current_path = os.path.normpath(path) if path else path
        self._source_identity_path = self._current_path or ""
        self._focus_cache_path = self._current_path
        self.source_changed.emit()
        self._full_preview_loaded = False
        self._fast_preview_only = True
        self._cancel_pending_full_preview()
        self._raw_focus_crop_box = None
        self._preview_note = ""
        self._denoised_display_path = ""
        self.set_focus_box(None)
        self.set_bird_box(None)

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
            self._canvas.setText(f"无法预览\n{Path(path).name}")
            self._set_preview_status_text(None, None)
        self._record_fast_preview_timing((_time.perf_counter() - started_at) * 1000.0)

    def _record_fast_preview_timing(self, total_ms: float) -> None:
        now = _time.perf_counter()
        if self._fast_preview_perf_started_at <= 0.0:
            self._fast_preview_perf_started_at = now
        self._fast_preview_perf_frames += 1
        self._fast_preview_perf_total_ms += max(0.0, float(total_ms))
        self._fast_preview_perf_max_ms = max(self._fast_preview_perf_max_ms, max(0.0, float(total_ms)))
        elapsed_s = now - self._fast_preview_perf_started_at
        if elapsed_s < 1.0:
            return
        frames = self._fast_preview_perf_frames
        perf_log(
            _log,
            "[preview.fast.summary] frames=%s elapsed_ms=%.1f effective_fps=%.1f avg_ms=%.1f max_ms=%.1f",
            frames,
            elapsed_s * 1000.0,
            (frames / elapsed_s) if elapsed_s > 0.0 else 0.0,
            (self._fast_preview_perf_total_ms / frames) if frames else 0.0,
            self._fast_preview_perf_max_ms,
        )
        self._fast_preview_perf_started_at = now
        self._fast_preview_perf_frames = 0
        self._fast_preview_perf_total_ms = 0.0
        self._fast_preview_perf_max_ms = 0.0

    def clear_image(self):
        self._preview_request_token += 1
        self._fit_next_image = True
        self._first_image_fit_token = None
        self._current_path = None
        self._source_identity_path = ""
        self._focus_cache_path = ""
        self.source_changed.emit()
        self._full_preview_loaded = False
        self._fast_preview_only = False
        self._cancel_pending_full_preview()
        self._raw_focus_crop_box = None
        self._preview_note = ""
        self._denoised_display_path = ""
        self.set_focus_box(None)
        self.set_bird_box(None)
        self._canvas.set_source_pixmap(None)
        self._set_preview_status_text(None, None)

    def _cancel_first_image_fit(self) -> None:
        # 首张小图出现后，用户的缩放/平移优先于后台清晰图到达时的自动适应。
        self._first_image_fit_token = None

    def _set_canvas_pixmap(self, pix: QPixmap, *, log_performance: bool = True) -> None:
        fit_first = (self._fit_next_image
                     or self._first_image_fit_token == self._preview_request_token)
        if fit_first:
            self._canvas.set_source_pixmap(pix, reset_view=True, log_performance=log_performance)
            # 焦点居中会保护旧 zoom；显式适应确保切目录后不会沿用上一目录倍率。
            self._canvas.fit_to_window()
            self._fit_next_image = False
            self._first_image_fit_token = self._preview_request_token
        elif self._canvas._auto_focus_center:
            # 保持相对于适应窗口的放大程度，缩略图换成完整图时不跳变。
            self._canvas.set_source_pixmap(pix, log_performance=log_performance)
        elif self._keep_view_on_switch:
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
        if self._shutdown_requested or self._navigation_playback_active:
            return
        path = os.path.normpath(str(self._current_path or ""))
        if not path or not os.path.isfile(path):
            return
        token = self._preview_request_token
        loader = self._full_preview_loader
        if loader is not None:
            loader.requestInterruption()
            # Decoders generally cannot be interrupted mid-call.  Coalesce all
            # newer selections to one latest request instead of starting more
            # full-size decodes in parallel. Retain ownership even after the
            # native thread exits, until its queued finished slot is handled.
            self._pending_full_preview_request = (int(token), path)
            return
        self._pending_full_preview_request = None
        self._launch_full_preview_loader(token, path)

    def _launch_full_preview_loader(self, token: int, path: str) -> None:
        if self._shutdown_requested or self._navigation_playback_active:
            return
        if int(token) != int(self._preview_request_token):
            return
        if not path or not self._is_current_path(path) or not os.path.isfile(path):
            return
        loader = _FullPreviewLoader(token, path, self, source_mode=self._preview_source_mode)
        loader.loaded.connect(self._on_full_preview_loaded)
        loader.finished.connect(self._on_full_preview_loader_finished)
        self._full_preview_loader = loader
        loader.start()

    def _on_full_preview_loader_finished(self) -> None:
        # 使用 QObject 接收槽，避免关闭时 lambda 捕获 worker 形成循环引用。
        loader = self.sender()
        if isinstance(loader, _FullPreviewLoader):
            self._cleanup_full_preview_loader(loader)

    def _cleanup_full_preview_loader(self, loader: _FullPreviewLoader) -> None:
        is_current = self._full_preview_loader is loader
        try:
            loader.deleteLater()
        except Exception:
            pass
        if not is_current:
            return
        self._full_preview_loader = None
        if self._shutdown_requested:
            self._pending_full_preview_request = None
            return
        pending = self._pending_full_preview_request
        self._pending_full_preview_request = None
        if pending is None:
            return
        token, path = pending
        self._launch_full_preview_loader(token, path)

    def _on_full_preview_loaded(self, token: int, path: str, qimg, load_ms: float) -> None:
        if (self._shutdown_requested or self._navigation_playback_active
                or int(token) != int(self._preview_request_token)):
            return
        if not path or not self._current_path:
            return
        if not self._is_current_path(path):
            return
        if qimg is None or qimg.isNull():
            if self._preview_source_mode == "denoised":
                self._preview_status_label.setText("降噪成片与原图均无法预览，可切换预览来源重试")
            if self._show_raw and Path(path).suffix.lower() in RAW_EXTENSIONS:
                self._preview_status_label.setText("RAW 解码失败，可切回‘默认预览’重试内嵌预览")
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
        try:
            self._raw_focus_crop_box = json.loads(qimg.text(RAW_FOCUS_CROP_KEY) or "null")
        except (ValueError, TypeError):
            self._raw_focus_crop_box = None
        self._preview_note = qimg.text(_PREVIEW_NOTE_KEY)
        self._denoised_display_path = qimg.text(_DENOISED_PATH_KEY)
        self.set_focus_box(self._source_focus_box)
        self.set_bird_box(self._source_bird_box)
        self._set_canvas_pixmap(pix)
        self._set_preview_status_text(pix.width(), pix.height())
        self._full_preview_loaded = True
        self._fast_preview_only = False
        self._first_image_fit_token = None
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
        self._fast_preview_only = False

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
        path = self._current_path or ""
        if (self._preview_source_mode == "denoised" or
                (Path(path).suffix.lower() in RAW_EXTENSIONS and (self._show_raw or not self._full_preview_loaded))):
            # 来源切换只控制视口。叠加导出固定使用默认来源且不改变当前请求/画布。
            image = _load_full_preview_qimage(path)
            if image is None or image.isNull():
                return None
            original = self._canvas._source_pixmap
            original_focus = self._canvas._focus_box
            original_bird = self._canvas._bird_box
            try:
                export_crop = json.loads(image.text(RAW_FOCUS_CROP_KEY) or "null")
                self._canvas._focus_box = map_camera_focus_box(self._source_focus_box, export_crop)
                self._canvas._bird_box = map_bird_overlay(self._source_bird_box, export_crop, map_camera_focus_box)
                self._canvas._source_pixmap = QPixmap.fromImage(image)
                return self._canvas.render_source_pixmap_with_overlays()
            finally:
                self._canvas._source_pixmap = original
                self._canvas._focus_box = original_focus
                self._canvas._bird_box = original_bird
        self._ensure_full_preview_loaded_sync()
        return self._canvas.render_source_pixmap_with_overlays()

    def save_source_pixmap_with_overlays(
        self,
        path: str,
        fmt: str | None = None,
        quality: int = -1,
    ) -> bool:
        rendered = self.render_source_pixmap_with_overlays()
        if rendered is None or rendered.isNull():
            return False
        return rendered.save(path, quality=quality) if fmt is None else rendered.save(path, fmt, quality)

    def set_focus_box(self, focus_box) -> None:
        """更新对焦点框（归一化坐标），传 None 表示清除。"""
        self._source_focus_box = focus_box
        self._canvas.apply_overlay_state(PreviewOverlayState(
            focus_box=map_camera_focus_box(focus_box, self._raw_focus_crop_box)))

    def set_auto_focus_center(self, enabled: bool) -> None:
        """自动以焦点（缺失时为图像中心）为缩放和切图基准。"""
        self._canvas.set_auto_focus_center(enabled)

    def set_bird_box(self, bird_box) -> None:
        """One camera-frame box, or several (flock, main bird first)."""
        self._source_bird_box = bird_box
        self._canvas.set_bird_box(map_bird_overlay(bird_box, self._raw_focus_crop_box, map_camera_focus_box))

    def set_show_bird_box(self, enabled: bool) -> None:
        self._canvas.set_show_bird_box(enabled)

    def set_show_focus_enabled(self, enabled: bool) -> None:
        """开关「显示对焦点」叠加层。"""
        self._show_focus_enabled = bool(enabled)
        self._apply_overlay_options()

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
        note = f" | {self._preview_note}" if self._preview_note else ""
        self._preview_status_label.setText(f"当前预览分辨率: {resolution_text} | 当前缩放: {scale_text}{note}")

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
            # There is at most one decoder now.  Waiting for its owned work to
            # finish prevents "QThread destroyed while running" on application
            # shutdown; no new pending request can start once shutdown begins.
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
        self.shutdown()
        super().closeEvent(event)

    def source_pixmap_for_path(self, path: str) -> QPixmap | None:
        """返回当前预览已经加载的同路径源图，供右侧信息面板复用，避免重复解码。"""
        if not self._full_preview_loaded or self._fast_preview_only:
            return None
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
        options = PreviewOverlayOptions(show_focus_box=self._show_focus_enabled)
        if hasattr(options, "composition_grid_mode"):
            options.composition_grid_mode = self._composition_grid_mode
        if hasattr(options, "composition_grid_line_width"):
            options.composition_grid_line_width = self._composition_grid_line_width
        self._canvas.apply_overlay_options(options)
        self._canvas.update()
