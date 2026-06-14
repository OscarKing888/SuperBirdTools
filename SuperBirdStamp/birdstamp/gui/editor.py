from __future__ import annotations

import json
import hashlib
import math
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageColor, ImageDraw, ImageOps
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QProgressBar,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app_common.about_dialog import load_about_info, load_about_images, show_about_dialog
from app_common.app_info_bar import AppInfoBar
from app_common.file_utils import is_apple_double_metadata_file
from app_common.log import get_logger
from app_common.perf_probe import elapsed_ms, perf_counter
from app_common.superviewer_user_options import (
    KEY_PERF_PROBES_ENABLED,
    apply_runtime_user_options,
    get_runtime_user_options,
    get_user_options_path,
    reload_runtime_user_options,
    save_user_options,
)
from app_common.send_to_app import (
    SingleInstanceReceiver,
    ensure_file_open_aware_application,
    install_file_open_handler,
    normalize_file_paths,
)

import birdstamp
from birdstamp.config import get_app_resource_dir, get_config_path, resolve_bundled_path
from birdstamp.constants import SEND_TO_APP_ID, SUPPORTED_EXTENSIONS
from birdstamp import perf as birdstamp_perf
from app_common.exif_io import (
    extract_many,
    extract_many_with_xmp_priority,
    extract_pillow_metadata,
    extract_metadata_with_xmp_priority,
    read_batch_metadata,
)
from birdstamp.meta.normalize import format_settings_line, normalize_metadata
from birdstamp.render.typography import list_available_font_paths, load_font

from birdstamp.gui import editor_core
from birdstamp.gui import editor_options
from birdstamp.gui import editor_template
from birdstamp.gui import editor_utils
from birdstamp.gui import template_context as _template_context
from birdstamp.gui.editor_crop_padding_widget import _CropPaddingEditorWidget
from birdstamp.gui.editor_template_dialog import (
    _GradientBarWidget,  # noqa: F401  (re-exported for compat)
    _GradientEditorWidget,  # noqa: F401
    TemplateManagerDialog,
)
from app_common.preview_canvas import (
    PREVIEW_COMPOSITION_GRID_LINE_WIDTHS,
    PREVIEW_COMPOSITION_GRID_MODES,
    PreviewWithStatusBar,
    configure_preview_scale_preset_combo,
    sync_preview_scale_preset_combo,
)
from birdstamp.gui.edit_modes import (
    EDIT_MODE_CROP_ADJUST,
    EDIT_MODE_NONE,
    EDIT_MODE_REFERENCE_REGION,
)
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas, EditorPreviewOverlayState
from birdstamp.gui.editor_photo_metadata_loader import EditorPhotoListMetadataLoader
from birdstamp.gui.editor_photo_list import (
    PHOTO_COL_APERTURE,
    PHOTO_COL_CAPTURE_TIME,
    PHOTO_COL_ISO,
    PHOTO_COL_NAME,
    PHOTO_COL_RATING,
    PHOTO_COL_RATIO,
    PHOTO_COL_ROW,
    PHOTO_COL_SEQ,
    PHOTO_COL_SHUTTER,
    PHOTO_COL_TITLE,
    PHOTO_LIST_DISPLAY_ROW_ROLE,
    PHOTO_LIST_PHOTO_INFO_ROLE,
    PHOTO_LIST_PATH_ROLE,
    PHOTO_LIST_SEQUENCE_ROLE,
    PHOTO_LIST_SORT_ROLE,
    PhotoListItem,
    PhotoListWidget,
)
from birdstamp.gui.editor_collapsible import CollapsibleSection
from birdstamp.gui.editor_gif_panel import GifExportPanel
from birdstamp.gui.editor_video_panel import VideoExportPanel, VideoExportRequest, VideoExportWorker
from birdstamp.gui.editor_workspace import _BirdStampWorkspaceMixin
from birdstamp.gui.editor_crop_calculator import _BirdStampCropMixin
from birdstamp.gui.editor_renderer import _BirdStampRendererMixin
from birdstamp.gui.editor_exporter import _BirdStampExporterMixin
from birdstamp.video_export import (
    DEFAULT_EXPORT_STAGE_ID,
    EXPORT_STAGE_GIF_ID,
    EXPORT_STAGE_ID_KEY,
    PIPELINE_STAGE_ENABLED_KEY,
    PIPELINE_STAGE_ORDER_KEY,
    STAGE_FOCUS_OVERLAY_ENABLED_KEY,
    STAGE_FOCUS_OVERLAY_ID,
    STAGE_RESIZE_LIMIT_ENABLED_KEY,
    STAGE_RESIZE_LIMIT_ID,
    STAGE_TEMPLATE_CROP_ENABLED_KEY,
    STAGE_TEMPLATE_CROP_ID,
    STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
    STAGE_TEMPLATE_OVERLAY_ID,
    VideoExportOptions,
    VideoFrameJob,
    build_default_image_proc_pipeline,
    build_image_proc_export_stages,
    crop_plan_precompute_required,
    ffmpeg_install_script_path,
    find_ffmpeg_executable,
    normalize_export_stage_id,
    normalize_pipeline_stage_order,
    prepare_uniform_auto_crop_plans,
    preferred_ffmpeg_binary_path,
)
from app_common.report_db import (
    ReportDB,
    find_superpicky_report_db_paths,
    resolve_existing_report_db_path,
)

# Re-export / aliases for refactored symbols (used below)
ALIGN_OPTIONS_VERTICAL = editor_utils.ALIGN_OPTIONS_VERTICAL
ALIGN_OPTIONS_HORIZONTAL = editor_utils.ALIGN_OPTIONS_HORIZONTAL
STYLE_OPTIONS = editor_options.STYLE_OPTIONS
RATIO_OPTIONS = editor_options.RATIO_OPTIONS
MAX_LONG_EDGE_OPTIONS = editor_options.MAX_LONG_EDGE_OPTIONS
OUTPUT_FORMAT_OPTIONS = editor_options.OUTPUT_FORMAT_OPTIONS
COLOR_PRESETS = editor_options.COLOR_PRESETS
DEFAULT_FIELD_TAG = editor_options.DEFAULT_FIELD_TAG
TAG_OPTIONS = editor_options.TAG_OPTIONS
SAMPLE_RAW_METADATA = editor_options.SAMPLE_RAW_METADATA
_DEFAULT_CROP_EFFECT_ALPHA = editor_utils.DEFAULT_CROP_EFFECT_ALPHA
_DEFAULT_CROP_PADDING_PX = editor_core.DEFAULT_CROP_PADDING_PX
_CENTER_MODE_IMAGE = editor_core.CENTER_MODE_IMAGE
_CENTER_MODE_FOCUS = editor_core.CENTER_MODE_FOCUS
_CENTER_MODE_BIRD = editor_core.CENTER_MODE_BIRD
_CENTER_MODE_CUSTOM = editor_core.CENTER_MODE_CUSTOM
_CENTER_MODE_OPTIONS = editor_core.CENTER_MODE_OPTIONS
_DEFAULT_TEMPLATE_BANNER_COLOR = editor_utils.DEFAULT_TEMPLATE_BANNER_COLOR
_TEMPLATE_BANNER_COLOR_NONE = editor_utils.TEMPLATE_BANNER_COLOR_NONE
_TEMPLATE_BANNER_COLOR_CUSTOM = editor_utils.TEMPLATE_BANNER_COLOR_CUSTOM
_TEMPLATE_BANNER_TOP_PADDING_PX = editor_utils.TEMPLATE_BANNER_TOP_PADDING_PX
_PREVIEW_GRID_MODE_ITEMS = editor_utils.PREVIEW_GRID_MODE_ITEMS
_PREVIEW_GRID_MODE_COMBO_WIDTH = editor_utils.PREVIEW_GRID_MODE_COMBO_WIDTH
_PREVIEW_GRID_LINE_WIDTH_COMBO_WIDTH = editor_utils.PREVIEW_GRID_LINE_WIDTH_COMBO_WIDTH
_PREVIEW_SCALE_COMBO_WIDTH = 96
_GIF_AUTO_FPS_METADATA_TAGS = [
    "-ExifIFD:DateTimeOriginal",
    "-EXIF:DateTimeOriginal",
    "-XMP-exif:DateTimeOriginal",
    "-DateTimeOriginal",
    "-Composite:SubSecDateTimeOriginal",
    "-SubSecDateTimeOriginal",
    "-ExifIFD:CreateDate",
    "-EXIF:CreateDate",
    "-XMP-xmp:CreateDate",
    "-CreateDate",
    "-Composite:SubSecCreateDate",
    "-SubSecCreateDate",
    "-ExifIFD:SubSecTimeOriginal",
    "-EXIF:SubSecTimeOriginal",
    "-XMP-exif:SubSecTimeOriginal",
    "-SubSecTimeOriginal",
    "-ExifIFD:SubSecTimeDigitized",
    "-EXIF:SubSecTimeDigitized",
    "-SubSecTimeDigitized",
    "-ExifIFD:SubSecTime",
    "-EXIF:SubSecTime",
    "-SubSecTime",
]
_build_color_preview_swatch = editor_utils.build_color_preview_swatch
_set_color_preview_swatch = editor_utils.set_color_preview_swatch
_configure_form_layout = editor_utils.configure_form_layout
_normalize_template_banner_color = editor_utils.normalize_template_banner_color
_template_banner_fill_color = editor_utils.template_banner_fill_color
_template_font_choices = editor_utils.template_font_choices
_template_font_path_from_type = editor_utils.template_font_path_from_type
_font_family_label_from_path = editor_utils.font_family_label_from_path
_start_screen_color_picker = editor_utils.start_screen_color_picker
_build_placeholder_image = editor_utils.build_placeholder_image
_build_metadata_context = editor_utils.build_metadata_context
_BANNER_BACKGROUND_STYLE_SOLID = editor_template.BANNER_BACKGROUND_STYLE_SOLID
_BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM = editor_template.BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM
_BANNER_BACKGROUND_STYLE_OPTIONS = editor_template.BANNER_BACKGROUND_STYLE_OPTIONS
_normalize_banner_background_style = editor_template.normalize_banner_background_style
_BANNER_GRADIENT_HEIGHT_PCT_DEFAULT = editor_template.BANNER_GRADIENT_HEIGHT_PCT_DEFAULT
_BANNER_GRADIENT_HEIGHT_PCT_MIN = editor_template.BANNER_GRADIENT_HEIGHT_PCT_MIN
_BANNER_GRADIENT_HEIGHT_PCT_MAX = editor_template.BANNER_GRADIENT_HEIGHT_PCT_MAX
_BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT = editor_template.BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT
_BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MIN = editor_template.BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MIN
_BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MAX = editor_template.BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MAX
_BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT = editor_template.BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT
_BANNER_GRADIENT_TOP_COLOR_DEFAULT = editor_template.BANNER_GRADIENT_TOP_COLOR_DEFAULT
_BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT = editor_template.BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT
_DEFAULT_TEMPLATE_CENTER_MODE = editor_template.DEFAULT_TEMPLATE_CENTER_MODE
_DEFAULT_TEMPLATE_MAX_LONG_EDGE = editor_template.DEFAULT_TEMPLATE_MAX_LONG_EDGE
_path_key = editor_utils.path_key


def _is_complete_list_metadata(metadata: dict[str, Any] | None) -> bool:
    """列表 metadata 是否已完成后台加载（非仅 SourceFile 占位）。"""
    if not isinstance(metadata, dict) or not metadata:
        return False
    if len(metadata) <= 1:
        return False
    return True


def _metadata_digest_for_cache(raw_metadata: dict[str, Any]) -> str:
    try:
        payload = json.dumps(raw_metadata, sort_keys=True, default=str)
    except Exception:
        payload = str(sorted(raw_metadata.items()))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

_sanitize_template_name = editor_utils.sanitize_template_name
_template_directory = editor_template.template_directory
_ensure_template_repository = editor_template.ensure_template_repository
_list_template_names = editor_template.list_template_names
_load_template_payload = editor_template.load_template_payload
_save_template_payload = editor_template.save_template_payload
_default_template_payload = editor_template.default_template_payload
_normalize_template_payload = editor_template.normalize_template_payload
render_template_overlay = editor_template.render_template_overlay
_render_template_overlay_in_crop_region = editor_template.render_template_overlay_in_crop_region
_extract_focus_box = editor_core.extract_focus_box
_extract_focus_point = editor_core.get_focus_point
_transform_focus_box_after_crop = editor_core.transform_focus_box_after_crop
_normalized_box_to_pixel_box = editor_core.normalized_box_to_pixel_box
_transform_source_box_after_crop_padding = editor_core.transform_source_box_after_crop_padding
_resize_fit = editor_core.resize_fit
_pad_image = editor_core.pad_image
_parse_ratio_value = editor_core.parse_ratio_value
_parse_bool_value = editor_core.parse_bool_value
_parse_padding_value = editor_core.parse_padding_value
_expand_unit_box_to_unclamped_pixels = editor_core.expand_unit_box_to_unclamped_pixels
_normalize_center_mode = editor_core.normalize_center_mode
_normalize_unit_box = editor_core.normalize_unit_box
_box_center = editor_core.box_center
_solve_axis_crop_start = editor_core.solve_axis_crop_start
_compute_ratio_crop_box = editor_core.compute_ratio_crop_box
_crop_box_has_effect = editor_core.crop_box_has_effect
_constrain_box_to_ratio = editor_core.constrain_box_to_ratio
_is_ratio_free = editor_core.is_ratio_free
_is_ratio_no_crop = editor_core.is_ratio_no_crop
_crop_image_by_normalized_box = editor_core.crop_image_by_normalized_box
_detect_primary_bird_box = editor_core.detect_primary_bird_box
_load_sidecar_xmp_metadata = editor_core.load_sidecar_xmp_metadata
_load_bird_detector = editor_core.preload_bird_detector
_crop_to_ratio_with_anchor = editor_core.crop_to_ratio_with_anchor
_clean_text = editor_core.clean_text
_normalize_lookup = editor_core.normalize_lookup
_safe_color = editor_utils.safe_color
_DEFAULT_TEMPLATE_FONT_TYPE = editor_utils.DEFAULT_TEMPLATE_FONT_TYPE
_normalize_template_font_type = editor_utils.normalize_template_font_type
_normalize_template_field = editor_template.normalize_template_field
_deep_copy_payload = editor_template.deep_copy_payload


class _ReportDBListWidget(QListWidget):
    """支持拖放 report.db 文件的列表控件。"""

    def __init__(self, owner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData() and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData() and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        mime = event.mimeData()
        if not mime or not mime.hasUrls():
            event.ignore()
            return
        paths: list[Path] = []
        for url in mime.urls():
            try:
                if url.isLocalFile():
                    paths.append(Path(url.toLocalFile()))
            except Exception:
                continue
        if paths and hasattr(self._owner, "_add_report_db_paths"):
            try:
                self._owner._add_report_db_paths(paths)
            except Exception:
                pass
        event.acceptProposedAction()


def _app_icon_paths() -> tuple[Path, Path]:
    """返回 (窗口用图标路径, AppInfoBar 用 PNG 路径)。窗口优先 .ico/.icns，否则 .png。"""
    icon_dir = resolve_bundled_path("icons")
    if not icon_dir.is_dir():
        icon_dir = Path(__file__).resolve().parents[2] / "icons"
    png_path = icon_dir / "app_icon.png"
    ico_path = icon_dir / "app_icon.ico"
    icns_path = icon_dir / "app_icon.icns"
    if sys.platform == "win32" and ico_path.exists():
        window_icon = ico_path
    elif sys.platform == "darwin" and icns_path.exists():
        window_icon = icns_path
    else:
        window_icon = png_path
    return (window_icon, png_path if png_path.exists() else window_icon)


def _make_edit_mode_icon(kind: str, *, size: int = 18, color: "QColor | None" = None) -> QIcon:
    """程序化绘制编辑模式工具按钮图标（随主题色），避免引入图标资源文件。

    kind: "selection" 箭头指针 / "reference" 虚线参考框+中心点 / "crop" 四角裁切框。
    """
    pen_color = color if isinstance(color, QColor) else QColor("#3C3C3C")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(pen_color)
        pen.setWidthF(1.6)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        if kind == "selection":
            # 鼠标箭头指针
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pen_color)
            path = QPainterPath()
            path.moveTo(size * 0.26, size * 0.16)
            path.lineTo(size * 0.26, size * 0.80)
            path.lineTo(size * 0.42, size * 0.64)
            path.lineTo(size * 0.54, size * 0.90)
            path.lineTo(size * 0.66, size * 0.84)
            path.lineTo(size * 0.54, size * 0.58)
            path.lineTo(size * 0.74, size * 0.56)
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "reference":
            # 虚线方框 + 中心点（特征参考区）
            dashed = QPen(pen_color)
            dashed.setWidthF(1.4)
            dashed.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dashed)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            margin = size * 0.18
            painter.drawRect(QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pen_color)
            r = size * 0.09
            painter.drawEllipse(QPointF(size * 0.5, size * 0.5), r, r)
        else:  # crop
            # 四角裁切框（两个 L 形角标）
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            a = size * 0.20
            b = size * 0.80
            arm = size * 0.22
            # 左上角
            painter.drawLine(QPointF(a, a), QPointF(a + arm, a))
            painter.drawLine(QPointF(a, a), QPointF(a, a + arm))
            # 右下角
            painter.drawLine(QPointF(b, b), QPointF(b - arm, b))
            painter.drawLine(QPointF(b, b), QPointF(b, b - arm))
            # 中间虚线提示裁切范围
            thin = QPen(pen_color)
            thin.setWidthF(1.0)
            thin.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(thin)
            painter.drawRect(QRectF(a, a, b - a, b - a))
    finally:
        painter.end()
    return QIcon(pixmap)


def _get_bird_detector_error_message() -> str:
    return editor_core.get_bird_detector_error_message()


_pil_to_qpixmap = editor_utils.pil_to_qpixmap
_log = get_logger("editor")
_PHOTO_LIST_META_PROGRESS_HIDE_DELAY_MS = 600
_RECEIVE_PROGRESS_HIDE_DELAY_MS = 1200
_RECEIVED_PHOTO_IMPORT_BATCH_MIN = 16
_RECEIVED_PHOTO_IMPORT_BATCH_MAX = 64
_RECEIVED_PHOTO_IMPORT_BATCH_BUDGET_S = 0.012
_ABOUT_CFG_FILENAME = "about.cfg"
_BIRDSTAMP_DEFAULT_APP_NAME = "极速鸟框 - 鸟类照片智能裁切与模板叠加工具"
_BIRDSTAMP_DEFAULT_PRODUCT_NAME = "极速鸟框"
_BIRDSTAMP_DEFAULT_SUBTITLE = "鸟类照片智能裁切与模板叠加"
_PIPELINE_STAGE_ENABLED_KEYS = {
    STAGE_TEMPLATE_CROP_ID: STAGE_TEMPLATE_CROP_ENABLED_KEY,
    STAGE_RESIZE_LIMIT_ID: STAGE_RESIZE_LIMIT_ENABLED_KEY,
    STAGE_TEMPLATE_OVERLAY_ID: STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
    STAGE_FOCUS_OVERLAY_ID: STAGE_FOCUS_OVERLAY_ENABLED_KEY,
}
_CENTER_MODE_RADIO_ITEMS = (
    ("鸟体", _CENTER_MODE_BIRD),
    ("焦点", _CENTER_MODE_FOCUS),
    ("图像中心", _CENTER_MODE_IMAGE),
    ("自定义", _CENTER_MODE_CUSTOM),
)


class _PhotoInputDiscoveryWorker(QThread):
    paths_ready = pyqtSignal(object)
    progress_updated = pyqtSignal(int)
    finished_discovery = pyqtSignal(int)

    def __init__(self, inputs: Iterable[Path], *, batch_size: int = 256, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._inputs = [Path(path) for path in inputs]
        self._batch_size = max(1, int(batch_size))
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True
        self.requestInterruption()

    def _should_stop(self) -> bool:
        return self._stop_requested or self.isInterruptionRequested()

    def _iter_supported_directory(self, directory: Path) -> Iterable[Path]:
        stack = [directory]
        while stack and not self._should_stop():
            current = stack.pop()
            try:
                with os.scandir(current) as iterator:
                    entries = list(iterator)
            except OSError:
                continue
            entries.sort(key=lambda entry: entry.name.casefold())
            for entry in entries:
                if self._should_stop():
                    return
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        path = Path(entry.path)
                        if (
                            not is_apple_double_metadata_file(path)
                            and path.suffix.lower() in SUPPORTED_EXTENSIONS
                        ):
                            yield path
                except OSError:
                    continue

    def _iter_supported_inputs(self) -> Iterable[Path]:
        for raw_path in self._inputs:
            if self._should_stop():
                return
            try:
                path = raw_path.resolve(strict=False)
            except OSError:
                path = raw_path
            if (
                path.is_file()
                and not is_apple_double_metadata_file(path)
                and path.suffix.lower() in SUPPORTED_EXTENSIONS
            ):
                yield path
            elif path.is_dir():
                yield from self._iter_supported_directory(path)

    def run(self) -> None:  # type: ignore[override]
        batch: list[Path] = []
        seen: set[str] = set()
        found_count = 0
        try:
            for path in self._iter_supported_inputs():
                if self._should_stop():
                    break
                key = _path_key(path)
                if key in seen:
                    continue
                seen.add(key)
                batch.append(path)
                found_count += 1
                if len(batch) >= self._batch_size:
                    self.paths_ready.emit(batch)
                    self.progress_updated.emit(found_count)
                    batch = []
            if batch and not self._should_stop():
                self.paths_ready.emit(batch)
        finally:
            self.progress_updated.emit(found_count)
            self.finished_discovery.emit(found_count)


def _sanitize_about_display_text(value: Any) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if not text:
        return ""
    cleaned: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in "\t\n\r":
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return "".join(cleaned).strip()


def _bundled_about_cfg_path() -> Path:
    return resolve_bundled_path(_ABOUT_CFG_FILENAME)


def _user_about_cfg_path() -> Path:
    return get_config_path().parent / _ABOUT_CFG_FILENAME


def _load_about_override_info(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    about = raw.get("about") if isinstance(raw, dict) else None
    if not isinstance(about, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in about.items():
        key_text = _sanitize_about_display_text(key)
        value_text = _sanitize_about_display_text(value)
        if not key_text or not value_text:
            continue
        value_text = value_text.replace("{app_name}", _BIRDSTAMP_DEFAULT_APP_NAME)
        value_text = value_text.replace("{version}", birdstamp.__version__)
        result[key_text] = value_text
    return result


def _load_birdstamp_about_info() -> dict[str, str]:
    info = load_about_info(
        app_name=_BIRDSTAMP_DEFAULT_APP_NAME,
        version=birdstamp.__version__,
    )
    for cfg_path in (_bundled_about_cfg_path(), _user_about_cfg_path()):
        info.update(_load_about_override_info(cfg_path))
    return info


def _birdstamp_product_name(about_info: dict[str, Any] | None = None) -> str:
    raw_name = ""
    if isinstance(about_info, dict):
        raw_name = _sanitize_about_display_text(about_info.get("app_name", ""))
    if not raw_name:
        raw_name = _BIRDSTAMP_DEFAULT_APP_NAME
    short_name = raw_name.split(" - ", 1)[0].strip()
    return short_name or _BIRDSTAMP_DEFAULT_PRODUCT_NAME


def _birdstamp_app_subtitle(about_info: dict[str, Any] | None = None) -> str:
    raw_name = ""
    if isinstance(about_info, dict):
        raw_name = _sanitize_about_display_text(about_info.get("app_name", ""))
    if " - " in raw_name:
        subtitle = raw_name.split(" - ", 1)[1].strip()
        if subtitle:
            return subtitle
    return _BIRDSTAMP_DEFAULT_SUBTITLE


def _build_birdstamp_main_window_title(about_info: dict[str, Any] | None = None) -> str:
    if not isinstance(about_info, dict):
        return _BIRDSTAMP_DEFAULT_PRODUCT_NAME
    app_name = _sanitize_about_display_text(about_info.get("app_name", "")) or _BIRDSTAMP_DEFAULT_APP_NAME
    version = _sanitize_about_display_text(about_info.get("version", "")) or ""
    author = _sanitize_about_display_text(about_info.get("作者", "")) or ""
    parts: list[str] = [app_name]
    if version:
        parts.append(version)
    if author:
        parts.append(author)
    return " - ".join(parts)


def _load_birdstamp_about_images() -> list[dict]:
    user_path = _user_about_cfg_path()
    if user_path.is_file():
        user_images = load_about_images(override_path=str(user_path))
        if user_images:
            return user_images
    bundled_path = _bundled_about_cfg_path()
    if bundled_path.is_file():
        return load_about_images(
            override_path=str(bundled_path),
            base_dir=str(get_app_resource_dir()),
        )
    return load_about_images()


# PreviewCanvas and PhotoListWidget now live in editor_preview_canvas.py / editor_photo_list.py

class BirdStampEditorWindow(
    QMainWindow,
    _BirdStampCropMixin,
    _BirdStampRendererMixin,
    _BirdStampExporterMixin,
    _BirdStampWorkspaceMixin,
):
    def __init__(
        self,
        startup_file: Path | None = None,
        startup_files: list[Path] | None = None,
    ) -> None:
        super().__init__()
        self._about_info = _load_birdstamp_about_info()
        self.setWindowTitle(_build_birdstamp_main_window_title(self._about_info))
        self.resize(1420, 920)
        self.setMinimumSize(1120, 720)

        self.template_dir = _template_directory()
        _ensure_template_repository(self.template_dir)

        self.template_paths: dict[str, Path] = {}
        self.current_template_payload: dict[str, Any] = _default_template_payload(name="default")

        # ReportDB 相关：多库列表 + 行缓存（stem → row）
        self._report_db_entries: list[Path] = []
        self._report_db_cache: dict[str, dict[str, Any]] = {}

        self.preview_pixmap: QPixmap | None = None
        self.preview_overlay_state = EditorPreviewOverlayState()
        self._original_mode_pixmap: QPixmap | None = None
        self._original_mode_signature: str | None = None
        self._bird_box_cache: dict[str, tuple[float, float, float, float] | None] = {}
        self._source_image_cache: dict[str, Image.Image] = {}
        self._preview_image_cache: dict[str, Image.Image] = {}
        self._perf_decode_counts: dict[str, int] = {}
        self._crop_drag_active = False
        self.photo_render_overrides: dict[str, dict[str, Any]] = {}
        self._photo_export_dirty_keys: set[str] = set()
        self._last_global_export_settings: dict[str, Any] = {}
        self._pipeline_stage_enabled: dict[str, bool] = {
            stage_id: True
            for stage_id in normalize_pipeline_stage_order(None)
        }
        self._crop_padding_state: dict[str, Any] = {
            "top": _DEFAULT_CROP_PADDING_PX,
            "bottom": _DEFAULT_CROP_PADDING_PX,
            "left": _DEFAULT_CROP_PADDING_PX,
            "right": _DEFAULT_CROP_PADDING_PX,
            "fill": "#FFFFFF",
        }
        self._bird_detect_error_reported = False
        self._bird_detector_preload_started = False
        self._bird_detector_preload_thread: threading.Thread | None = None
        self._bird_detect_worker = None
        self.last_rendered: Image.Image | None = None
        self.current_path: Path | None = None
        self.current_photo_info: _template_context.PhotoInfo | None = None
        self.current_source_image: Image.Image | None = None
        self.current_source_full_size: tuple[int, int] | None = None
        self.current_raw_metadata: dict[str, Any] = {}
        self.current_metadata_context: dict[str, str] = {}
        self._metadata_context_cache: dict[str, dict[str, str]] = {}
        self.raw_metadata_cache: dict[str, dict[str, Any]] = {}
        self.photo_list_metadata_cache: dict[str, dict[str, Any]] = {}
        self._photo_item_map: dict[str, QTreeWidgetItem] = {}
        self._photo_list_metadata_pending_keys: set[str] = set()
        self._photo_list_metadata_loader: EditorPhotoListMetadataLoader | None = None
        self._pending_photo_list_metadata_loaders: list[EditorPhotoListMetadataLoader] = []
        self._photo_list_metadata_loading = False
        self._photo_list_header_fast_mode = False
        self._next_photo_sequence_number: int = 0
        self._received_photo_import_pending_paths: deque[Path] = deque()
        self._received_photo_import_total: int = 0
        self._received_photo_import_processed: int = 0
        self._received_photo_import_added: int = 0
        self._received_photo_import_auto_report_db_count: int = 0
        self._received_photo_import_last_added_item: QTreeWidgetItem | None = None
        self._received_photo_import_added_paths: list[Path] = []
        self._received_photo_import_completion_callbacks: list[Callable[[], None]] = []
        self._received_photo_import_progress_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._received_photo_import_existing_keys: set[str] = set()
        self._received_photo_import_default_settings: dict[str, Any] | None = None
        self._received_photo_import_select_last_added: bool = False
        self._photo_input_discovery_workers: list[_PhotoInputDiscoveryWorker] = []
        self._photo_input_discovery_import_options: dict[int, dict[str, Any]] = {}
        self._receive_progress_reset_token: int = 0
        self._pending_preview_fit_reset: bool = False
        self._pending_startup_workspace_restore: bool = False
        self._pending_workspace_current_item: QTreeWidgetItem | None = None
        self._pending_workspace_selected_paths: list[Path] = []
        self._pending_workspace_select_metadata_key: str | None = None
        self._photo_list_display_batch_depth: int = 0
        self._photo_list_display_batch_sorting: bool = False
        self._video_export_worker: VideoExportWorker | None = None
        self._video_export_started_at: float | None = None
        self._pending_video_export_dirty_keys: set[str] = set()
        self._image_export_progress_token: int = 0
        self._image_export_active_worker_count: int = 0
        self._image_export_last_output_dir: Path | None = self._load_image_export_last_output_dir()
        self._batch_export_last_output_dir: Path | None = self._load_batch_export_last_output_dir()
        self._video_export_last_output_dir: Path | None = self._load_video_export_last_output_dir()
        self._workspace_path: Path | None = None
        # 占位图路径标记：非 None 时表示当前预览的是默认占位图而非用户照片
        self.placeholder_path: Path | None = None

        self.placeholder = _build_placeholder_image(1400, 900)

        self._preview_debounce_timer = QTimer(self)
        self._preview_debounce_timer.setSingleShot(True)
        self._preview_debounce_timer.setInterval(250)
        self._preview_debounce_timer.timeout.connect(self.render_preview)
        self._received_photo_import_timer = QTimer(self)
        self._received_photo_import_timer.setSingleShot(False)
        self._received_photo_import_timer.setInterval(0)
        self._received_photo_import_timer.timeout.connect(self._process_received_photo_import_batch)
        self._workspace_restore_photo_timer = QTimer(self)
        self._workspace_restore_photo_timer.setInterval(0)
        self._workspace_restore_photo_timer.timeout.connect(self._process_workspace_restore_photo_batch)
        self._workspace_restore_pending_entries: deque[dict[str, Any]] = deque()
        self._workspace_restore_context: dict[str, Any] | None = None
        self._init_workspace_autosave()

        self._setup_ui()
        self._apply_image_export_preferences_from_state()
        self._last_global_export_settings = self._current_global_export_settings()
        self._setup_shortcuts()
        self._setup_menu_bar()
        self._apply_system_adaptive_style()
        self._reload_template_combo(preferred="default")
        self._set_status("就绪。请添加照片并选择模板。")
        self._show_instant_placeholder_preview()

        # 冷启动或「发送到本应用」传入的文件列表：加入照片列表
        files_to_add: list[Path] = []
        if startup_files:
            files_to_add = list(startup_files)
        elif startup_file:
            files_to_add = [startup_file]
        if files_to_add:
            self._add_photo_paths(files_to_add)
        else:
            self._pending_startup_workspace_restore = True

        self._start_bird_detector_preload()

        # 初始化 report.db 行解析器（无缓存时返回 None）
        self._update_report_db_row_resolver()

    def _run_deferred_startup_tasks(self) -> None:
        """出窗后恢复上次工作区或加载完整占位预览，避免阻塞构造函数。"""
        if getattr(self, "_pending_startup_workspace_restore", False):
            self._pending_startup_workspace_restore = False
            self._restore_startup_workspace()
            return
        QTimer.singleShot(0, self._show_placeholder_preview)

    def _begin_photo_list_item_display_batch(self) -> None:
        depth = int(getattr(self, "_photo_list_display_batch_depth", 0))
        if depth == 0:
            self._photo_list_display_batch_sorting = bool(self.photo_list.isSortingEnabled())
            self.photo_list.setSortingEnabled(False)
            self.photo_list.setUpdatesEnabled(False)
        self._photo_list_display_batch_depth = depth + 1

    def _end_photo_list_item_display_batch(self, *, resort: bool = False) -> None:
        depth = int(getattr(self, "_photo_list_display_batch_depth", 0))
        if depth <= 0:
            return
        depth -= 1
        self._photo_list_display_batch_depth = depth
        if depth != 0:
            return
        self.photo_list.setUpdatesEnabled(True)
        if resort:
            self.photo_list.resort()
        if getattr(self, "_photo_list_display_batch_sorting", False):
            self.photo_list.setSortingEnabled(True)

    def _schedule_workspace_photo_selection(
        self,
        current_item: QTreeWidgetItem | None,
        selected_paths: Iterable[Path],
        *,
        wait_metadata: bool,
    ) -> None:
        self._pending_workspace_current_item = current_item
        self._pending_workspace_selected_paths = list(selected_paths)
        self._pending_workspace_select_metadata_key = None
        if wait_metadata and current_item is not None:
            raw = current_item.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
            if isinstance(raw, str):
                key = _path_key(Path(raw))
                if key in self._photo_list_metadata_pending_keys:
                    self._pending_workspace_select_metadata_key = key
                    return
        QTimer.singleShot(0, self._apply_pending_workspace_photo_selection)

    def _apply_pending_workspace_photo_selection(self) -> None:
        current_item = self._pending_workspace_current_item
        selected_paths = list(self._pending_workspace_selected_paths)
        self._pending_workspace_current_item = None
        self._pending_workspace_selected_paths = []
        self._pending_workspace_select_metadata_key = None
        if current_item is None:
            return
        self.photo_list.setCurrentItem(current_item)
        for idx in range(self.photo_list.topLevelItemCount()):
            item = self.photo_list.topLevelItem(idx)
            if item is not None:
                item.setSelected(False)
        current_item.setSelected(True)
        for path in selected_paths:
            item = self._find_photo_item_by_path(path)
            if item is not None:
                item.setSelected(True)

    def _maybe_apply_pending_workspace_photo_selection(self) -> None:
        pending_key = self._pending_workspace_select_metadata_key
        if not pending_key:
            return
        if pending_key in self._photo_list_metadata_pending_keys:
            return
        self._pending_workspace_select_metadata_key = None
        QTimer.singleShot(0, self._apply_pending_workspace_photo_selection)

    # ------------------------------------------------------------------
    # ReportDB 列表与缓存
    # ------------------------------------------------------------------

    def _update_report_db_row_resolver(self) -> None:
        """根据当前缓存更新模板上下文中的 report.db 行解析函数。"""

        cache = self._report_db_cache

        if not cache:
            _template_context.set_report_db_row_resolver(None)
            return

        def _resolver(path: Path) -> dict[str, Any] | None:
            for key in _template_context.report_db_lookup_keys_for_path(path):
                row = cache.get(key)
                if isinstance(row, dict):
                    return row
            return None

        _template_context.set_report_db_row_resolver(_resolver)

    def _rebuild_report_db_cache(self) -> None:
        """根据当前 report.db 列表重建行缓存，并更新 provider 解析器。"""
        cache: dict[str, dict[str, Any]] = {}
        for db_path in self._report_db_entries:
            try:
                p = db_path
            except Exception:
                continue
            try:
                db = ReportDB.open_db_path_if_exists(str(p))
            except Exception:
                continue
            if not db:
                continue
            try:
                for row in db.get_all_photos():
                    row_data = dict(row)
                    try:
                        lookup_keys = _template_context.report_db_lookup_keys_for_value(row_data.get("filename"))
                    except Exception:
                        continue
                    if not lookup_keys:
                        continue
                    for key in lookup_keys:
                        if key in cache:
                            continue
                        cache[key] = row_data
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        self._report_db_cache = cache
        self._update_report_db_row_resolver()

    def _add_report_db_paths(self, paths: Iterable[Path]) -> None:
        """将一个或多个 report.db 文件路径加入列表并重建缓存。"""
        added = 0
        existing: set[Path] = set(self._report_db_entries)
        for incoming in paths:
            try:
                p = incoming if isinstance(incoming, Path) else Path(str(incoming))
            except Exception:
                continue
            try:
                p = p.resolve(strict=False)
            except Exception:
                pass
            if not p.is_file():
                continue
            name_lower = p.name.lower()
            if not (name_lower.endswith(".db") or name_lower == "report.db"):
                continue
            if p in existing:
                continue
            existing.add(p)
            self._report_db_entries.append(p)
            label = f"{p.parent.name} ({p.name})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.report_db_list.addItem(item)
            added += 1
        if added:
            self._rebuild_report_db_cache()
            self._schedule_workspace_autosave()

    def _auto_add_report_db_paths_for_photos(self, paths: Iterable[Path]) -> int:
        """根据照片所在目录自动发现 report.db，并加入当前列表。"""
        existing_count = len(self._report_db_entries)
        candidates: list[Path] = []
        seen_dirs: set[Path] = set()
        for incoming in paths:
            try:
                photo_path = incoming if isinstance(incoming, Path) else Path(str(incoming))
                photo_path = photo_path.resolve(strict=False)
                parent = photo_path.parent
            except Exception:
                continue
            if parent in seen_dirs:
                continue
            seen_dirs.add(parent)
            db_path = resolve_existing_report_db_path(str(parent))
            if not db_path:
                continue
            try:
                candidates.append(Path(db_path))
            except Exception:
                continue
        if candidates:
            self._add_report_db_paths(candidates)
        return max(0, len(self._report_db_entries) - existing_count)

    def _auto_add_report_db_paths_for_received_files(self, paths: Iterable[Path]) -> int:
        """根据 received 首个外部文件所在目录，向上最多 3 层补充发现 `.superpicky/report.db`。"""
        existing_count = len(self._report_db_entries)
        first_directory: Path | None = None
        for incoming in paths:
            try:
                candidate = incoming if isinstance(incoming, Path) else Path(str(incoming))
                candidate = candidate.resolve(strict=False)
            except Exception:
                continue
            first_directory = candidate if candidate.is_dir() else candidate.parent
            break

        if first_directory is None:
            return 0

        candidates = [Path(db_path) for db_path in find_superpicky_report_db_paths(str(first_directory), max_levels=3)]
        if candidates:
            self._add_report_db_paths(candidates)
        return max(0, len(self._report_db_entries) - existing_count)

    def _remove_selected_report_dbs(self) -> None:
        """从列表中移除选中的 report.db，并更新缓存。"""
        items = self.report_db_list.selectedItems()
        if not items:
            return
        paths_to_remove: set[Path] = set()
        for item in items:
            raw = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(raw, str):
                try:
                    paths_to_remove.add(Path(raw))
                except Exception:
                    pass
            row = self.report_db_list.row(item)
            if row >= 0:
                self.report_db_list.takeItem(row)
        if not paths_to_remove:
            return
        self._report_db_entries = [p for p in self._report_db_entries if p not in paths_to_remove]
        self._rebuild_report_db_cache()
        self._schedule_workspace_autosave()

    def _clear_report_dbs_state(self, *, status_message: str | None = None) -> None:
        """清空所有 report.db 记录与缓存。"""
        self.report_db_list.clear()
        self._report_db_entries.clear()
        self._report_db_cache.clear()
        self._update_report_db_row_resolver()
        self._schedule_workspace_autosave()
        if status_message is not None:
            self._set_status(status_message)

    def _clear_report_dbs(self) -> None:
        self._clear_report_dbs_state(status_message="已清空 report.db 列表。")

    def _setup_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        left_scroll = QScrollArea()
        left_scroll.setObjectName("EditorLeftScrollArea")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        left_panel = QWidget()
        left_panel.setObjectName("EditorLeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(10)
        self._left_panel_layout = left_layout

        _window_icon_path, _info_bar_icon_path = _app_icon_paths()
        _app_icon = QIcon(str(_window_icon_path))
        if not _app_icon.isNull():
            self.setWindowIcon(_app_icon)
        self._info_bar = AppInfoBar(
            self,
            title=_birdstamp_product_name(self._about_info),
            subtitle=_birdstamp_app_subtitle(self._about_info),
            icon_path=str(_info_bar_icon_path) if _info_bar_icon_path.exists() else None,
            on_about_clicked=self._show_about_dialog,
        )
        left_layout.addWidget(self._info_bar)

        self._setup_ui_photos_list(left_layout)
        self._setup_ui_template_output_actions(left_layout)
        left_scroll.setWidget(left_panel)

        right_panel = self._setup_ui_preview_panel()

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([600, 920])
        splitter.setChildrenCollapsible(False)

        self.setStatusBar(self.statusBar())

    def _setup_ui_photos_list(self, left_layout: QVBoxLayout) -> None:
        """构建左侧「Report 数据库」+「照片列表」分组 UI。"""
        # ── Report 数据库列表 ────────────────────────────────────────────────
        db_content = QWidget()
        db_layout = QVBoxLayout(db_content)
        db_layout.setContentsMargins(0, 0, 0, 0)
        db_layout.setSpacing(6)

        self.report_db_list = _ReportDBListWidget(self)
        self.report_db_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        row_h = self.report_db_list.fontMetrics().height() + 4
        self.report_db_list.setMaximumHeight(2 * row_h + 6)
        db_layout.addWidget(self.report_db_list, stretch=1)

        db_btn_row = QHBoxLayout()
        db_remove_btn = QPushButton("删除所选")
        db_remove_btn.clicked.connect(self._remove_selected_report_dbs)
        db_btn_row.addWidget(db_remove_btn)

        db_clear_btn = QPushButton("清空")
        db_clear_btn.clicked.connect(self._clear_report_dbs)
        db_btn_row.addWidget(db_clear_btn)
        db_btn_row.addStretch(1)
        db_layout.addLayout(db_btn_row)

        db_hint = QLabel("支持拖入 report.db 文件")
        db_hint.setStyleSheet("color: #7A7A7A; font-size: 11px;")
        db_layout.addWidget(db_hint)

        db_section = CollapsibleSection("Report 数据库", expanded=True)
        db_section.set_content_widget(db_content)
        left_layout.addWidget(db_section)

        # ── 照片列表 ────────────────────────────────────────────────────────
        photos_content = QWidget()
        photos_layout = QVBoxLayout(photos_content)
        photos_layout.setContentsMargins(0, 0, 0, 0)
        photos_layout.setSpacing(6)

        photo_manage_row = QHBoxLayout()
        add_files_btn = QPushButton("添加照片")
        add_files_btn.clicked.connect(self._pick_files)
        photo_manage_row.addWidget(add_files_btn)

        add_dir_btn = QPushButton("添加目录")
        add_dir_btn.clicked.connect(self._pick_directory)
        photo_manage_row.addWidget(add_dir_btn)

        remove_btn = QPushButton("删除所选")
        remove_btn.clicked.connect(self._remove_selected_photos)
        photo_manage_row.addWidget(remove_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_photos)
        photo_manage_row.addWidget(clear_btn)
        photos_layout.addLayout(photo_manage_row)

        self.photo_list_progress = QProgressBar()
        self.photo_list_progress.setMinimum(0)
        self.photo_list_progress.setMaximum(1)
        self.photo_list_progress.setValue(0)
        self.photo_list_progress.setFixedHeight(18)
        self.photo_list_progress.setTextVisible(True)
        self.photo_list_progress.setFormat("照片信息 0/0")
        self.photo_list_progress.hide()
        photos_layout.addWidget(self.photo_list_progress)

        self.receive_progress = QProgressBar()
        self.receive_progress.setMinimum(0)
        self.receive_progress.setMaximum(1)
        self.receive_progress.setValue(0)
        self.receive_progress.setFixedHeight(18)
        self.receive_progress.setTextVisible(True)
        self.receive_progress.setFormat("热接收 0/0")
        self.receive_progress.hide()
        photos_layout.addWidget(self.receive_progress)

        self.photo_list = PhotoListWidget()
        self.photo_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.photo_list.pathsDropped.connect(self._add_photo_paths)
        self.photo_list.currentItemChanged.connect(self._on_photo_selected)
        self.photo_list.itemSelectionChanged.connect(self._on_workspace_state_changed)
        self.photo_list.header().sortIndicatorChanged.connect(self._on_workspace_state_changed)
        self.photo_list.setMinimumHeight(240)
        self.photo_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        photos_layout.addWidget(self.photo_list, stretch=1)

        hint = QLabel("支持拖入单张照片或整个目录")
        hint.setStyleSheet("color: #7A7A7A;")
        photos_layout.addWidget(hint)

        photos_section = CollapsibleSection("照片列表", expanded=True)
        photos_section.set_content_widget(photos_content)
        photos_section.toggled.connect(self._on_photos_section_toggled)
        self._photos_section = photos_section
        left_layout.addWidget(photos_section)

    # ------------------------------------------------------------------
    # ReportDB 列表与缓存
    # ------------------------------------------------------------------

    def _update_report_db_row_resolver(self) -> None:
        """根据当前缓存更新模板上下文中的 report.db 行解析函数。"""

        cache = self._report_db_cache

        if not cache:
            _template_context.set_report_db_row_resolver(None)
            return

        def _resolver(path: Path) -> dict[str, Any] | None:
            for key in _template_context.report_db_lookup_keys_for_path(path):
                row = cache.get(key)
                if isinstance(row, dict):
                    return row
            return None

        _template_context.set_report_db_row_resolver(_resolver)

    def _setup_ui_template_output_actions(self, left_layout: QVBoxLayout) -> None:
        """构建左侧「处理管线」「导出」分组 UI。"""
        self.template_combo = QComboBox()
        self.template_combo.currentTextChanged.connect(self._on_template_changed)

        self.manage_template_btn = QPushButton("模板管理")
        self.manage_template_btn.clicked.connect(self._open_template_manager)

        self.ratio_combo = QComboBox()
        for label, ratio in RATIO_OPTIONS:
            self.ratio_combo.addItem(label, ratio)
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio_changed)

        self.center_mode_widget = QWidget()
        center_mode_layout = QHBoxLayout(self.center_mode_widget)
        center_mode_layout.setContentsMargins(0, 0, 0, 0)
        center_mode_layout.setSpacing(10)
        self.center_mode_button_group = QButtonGroup(self)
        self.center_mode_button_group.setExclusive(True)
        self.center_mode_buttons: dict[str, QRadioButton] = {}
        for label, mode in _CENTER_MODE_RADIO_ITEMS:
            radio = QRadioButton(label)
            radio.setProperty("center_mode", mode)
            radio.toggled.connect(
                lambda checked, _mode=mode: self._on_crop_settings_changed()
                if checked else None
            )
            self.center_mode_button_group.addButton(radio)
            self.center_mode_buttons[mode] = radio
            center_mode_layout.addWidget(radio)
        center_mode_layout.addStretch(1)
        self._set_center_mode_value(_DEFAULT_TEMPLATE_CENTER_MODE, emit_changed=False)

        self.crop_padding_editor = _CropPaddingEditorWidget()
        self.crop_padding_editor.changed.connect(self._on_crop_padding_editor_changed)

        self.reset_override_btn = QPushButton("重置为模板值")
        self.reset_override_btn.setToolTip(
            "<b>重置为模板值</b><br>"
            "将「裁切比例」「裁切中心」以及当前模板<br>"
            "记录的裁剪框默认值恢复为<br>"
            "当前所选模板中存储的默认值。<br>"
            "<i>适合撤销手动调整、快速回到模板初始状态。</i>"
        )
        self.reset_override_btn.clicked.connect(self._reset_template_overrides)
        self.apply_all_btn = QPushButton("全部应用")
        self.apply_all_btn.setToolTip(
            "<b>全部应用</b><br>"
            "将当前「模板裁切」中的所有设置<br>"
            "批量覆盖到已加载的每张照片，<br>"
            "包括裁切比例、中心模式以及<br>"
            "当前照片上调整过的裁剪框。<br>"
            "<i>仅影响本次会话的照片列表，不修改模板文件。</i>"
        )
        self.apply_all_btn.clicked.connect(self._apply_current_settings_to_all_photos)

        # ── 导出设置 ───────────────────────────────────────────────────────
        export_content = QWidget()
        export_root = QVBoxLayout(export_content)
        export_root.setContentsMargins(0, 0, 0, 0)
        export_root.setSpacing(8)

        self.draw_banner_check = QCheckBox("Banner 底")
        self.draw_banner_check.setChecked(True)
        self.draw_banner_check.toggled.connect(self._on_output_settings_changed)
        self.draw_text_check = QCheckBox("文本")
        self.draw_text_check.setChecked(True)
        self.draw_text_check.toggled.connect(self._on_output_settings_changed)
        self.draw_focus_check = QCheckBox("焦点")
        self.draw_focus_check.setChecked(False)
        self.draw_focus_check.toggled.connect(self._on_output_settings_changed)

        self.uniform_auto_crop_check = QCheckBox("批量统一自动裁切尺寸")
        self.uniform_auto_crop_check.setToolTip(
            "导出多张图片/GIF/视频前预计算自动裁切，并按同一比例组取最大裁切视野。"
        )
        self.uniform_auto_crop_check.setChecked(False)
        self.uniform_auto_crop_check.toggled.connect(self._on_output_settings_changed)
        self.uniform_auto_crop_check.toggled.connect(self._save_image_export_preferences)
        self.auto_crop_stabilization_slider = QSlider(Qt.Orientation.Horizontal)
        self.auto_crop_stabilization_slider.setRange(0, 100)
        self.auto_crop_stabilization_slider.setSingleStep(5)
        self.auto_crop_stabilization_slider.setPageStep(10)
        self.auto_crop_stabilization_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.auto_crop_stabilization_slider.setTickInterval(25)
        self.auto_crop_stabilization_slider.setToolTip(
            "0 为关闭；数值越高，批量自动裁切中心越接近稳定中位点，GIF/视频抖动越少。"
        )
        self.auto_crop_stabilization_slider.setValue(0)
        self.auto_crop_stabilization_slider.setEnabled(False)
        self.auto_crop_stabilization_value_label = QLabel("0%")
        self.auto_crop_stabilization_value_label.setMinimumWidth(36)
        self.auto_crop_stabilization_value_label.setEnabled(False)
        self.auto_crop_stabilization_slider.valueChanged.connect(
            lambda value: self.auto_crop_stabilization_value_label.setText(f"{int(value)}%")
        )
        self.auto_crop_stabilization_slider.valueChanged.connect(lambda _value: self._on_output_settings_changed())
        self.auto_crop_stabilization_slider.valueChanged.connect(lambda _value: self._save_image_export_preferences())
        self.uniform_auto_crop_check.toggled.connect(self.auto_crop_stabilization_slider.setEnabled)
        self.uniform_auto_crop_check.toggled.connect(self.auto_crop_stabilization_value_label.setEnabled)

        self.max_edge_combo = QComboBox()
        seen_edges: set[int] = set()
        for value in MAX_LONG_EDGE_OPTIONS:
            try:
                edge = int(value)
            except Exception:
                continue
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            self.max_edge_combo.addItem("不限制" if edge <= 0 else str(edge), edge)
        if self.max_edge_combo.count() == 0:
            self.max_edge_combo.addItem("不限制", 0)
        self.max_edge_combo.setCurrentIndex(0)
        self.max_edge_combo.currentIndexChanged.connect(self._on_output_settings_changed)

        pipeline_group = QGroupBox("处理管线")
        pipeline_layout = QVBoxLayout(pipeline_group)
        pipeline_layout.setContentsMargins(8, 8, 8, 8)
        pipeline_layout.setSpacing(8)

        pipeline_order_row = QHBoxLayout()
        pipeline_order_row.setContentsMargins(0, 0, 0, 0)
        pipeline_order_row.setSpacing(6)
        self.pipeline_stage_list = QListWidget()
        self.pipeline_stage_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pipeline_stage_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.pipeline_stage_list.setFixedHeight(104)
        self.pipeline_stage_list.currentRowChanged.connect(lambda _row: self._refresh_pipeline_stage_move_buttons())
        self.pipeline_stage_list.itemChanged.connect(self._on_pipeline_stage_item_changed)
        pipeline_order_row.addWidget(self.pipeline_stage_list, 1)
        pipeline_btn_col = QVBoxLayout()
        pipeline_btn_col.setContentsMargins(0, 0, 0, 0)
        pipeline_btn_col.setSpacing(6)
        self.pipeline_stage_up_btn = QPushButton("上移")
        self.pipeline_stage_up_btn.clicked.connect(lambda: self._move_pipeline_stage(-1))
        self.pipeline_stage_down_btn = QPushButton("下移")
        self.pipeline_stage_down_btn.clicked.connect(lambda: self._move_pipeline_stage(1))
        pipeline_btn_col.addWidget(self.pipeline_stage_up_btn)
        pipeline_btn_col.addWidget(self.pipeline_stage_down_btn)
        pipeline_btn_col.addStretch()
        pipeline_order_row.addLayout(pipeline_btn_col)
        pipeline_layout.addLayout(pipeline_order_row)

        self.pipeline_stage_options_layout = QVBoxLayout()
        self.pipeline_stage_options_layout.setContentsMargins(0, 0, 0, 0)
        self.pipeline_stage_options_layout.setSpacing(6)
        pipeline_layout.addLayout(self.pipeline_stage_options_layout)

        self._setup_pipeline_stage_option_groups()
        self._set_pipeline_stage_order(normalize_pipeline_stage_order(None), save=False, mark_dirty=False)
        export_root.addWidget(pipeline_group)

        export_stage_group = QGroupBox("导出 Stage")
        export_stage_form = QFormLayout(export_stage_group)
        _configure_form_layout(export_stage_form)
        export_stage_widget = QWidget()
        export_stage_layout = QHBoxLayout(export_stage_widget)
        export_stage_layout.setContentsMargins(0, 0, 0, 0)
        export_stage_layout.setSpacing(10)
        self.export_stage_button_group = QButtonGroup(self)
        self.export_stage_button_group.setExclusive(True)
        self.export_stage_buttons: dict[str, QRadioButton] = {}
        for stage in build_image_proc_export_stages():
            radio = QRadioButton(stage.label)
            radio.setProperty("stage_id", stage.stage_id)
            radio.toggled.connect(
                lambda checked, _stage_id=stage.stage_id: self._on_export_stage_changed()
                if checked else None
            )
            self.export_stage_button_group.addButton(radio)
            self.export_stage_buttons[stage.stage_id] = radio
            export_stage_layout.addWidget(radio)
        export_stage_layout.addStretch(1)
        export_stage_form.addRow("输出", export_stage_widget)
        self._set_selected_export_stage_id(DEFAULT_EXPORT_STAGE_ID, save=False)
        export_root.addWidget(export_stage_group)

        image_export_group = QGroupBox("图片导出")
        self.image_export_group = image_export_group
        image_export_layout = QVBoxLayout(image_export_group)
        image_export_layout.setContentsMargins(8, 8, 8, 8)
        image_export_layout.setSpacing(6)

        image_export_form = QFormLayout()
        image_export_form.setContentsMargins(0, 0, 0, 0)
        _configure_form_layout(image_export_form)

        output_format_widget = QWidget()
        output_format_layout = QHBoxLayout(output_format_widget)
        output_format_layout.setContentsMargins(0, 0, 0, 0)
        output_format_layout.setSpacing(10)
        self.output_format_button_group = QButtonGroup(self)
        self.output_format_button_group.setExclusive(True)
        self.output_format_buttons: dict[str, QRadioButton] = {}
        for suffix, label in OUTPUT_FORMAT_OPTIONS:
            if suffix == "gif":
                continue
            radio = QRadioButton(label)
            radio.setProperty("output_suffix", suffix)
            radio.toggled.connect(
                lambda checked, _suffix=suffix: self._on_image_export_format_changed()
                if checked else None
            )
            self.output_format_button_group.addButton(radio)
            self.output_format_buttons[str(suffix).strip().lower()] = radio
            output_format_layout.addWidget(radio)
        if not self.output_format_buttons:
            for suffix, label in (("png", "PNG"), ("jpg", "JPG")):
                radio = QRadioButton(label)
                radio.setProperty("output_suffix", suffix)
                radio.toggled.connect(
                    lambda checked, _suffix=suffix: self._on_image_export_format_changed()
                    if checked else None
                )
                self.output_format_button_group.addButton(radio)
                self.output_format_buttons[suffix] = radio
                output_format_layout.addWidget(radio)
        output_format_layout.addStretch(1)
        image_export_form.addRow("输出格式", output_format_widget)
        self._set_selected_output_suffix("png", save=False)

        export_btn_row = QHBoxLayout()
        export_btn_row.setContentsMargins(0, 0, 0, 0)
        export_btn_row.setSpacing(6)
        self.export_current_btn = QPushButton("导出当前")
        self.export_current_btn.clicked.connect(self.export_current)
        export_btn_row.addWidget(self.export_current_btn)
        self.export_batch_btn = QPushButton("批量导出")
        self.export_batch_btn.clicked.connect(self.export_all)
        export_btn_row.addWidget(self.export_batch_btn)
        image_export_form.addRow("", export_btn_row)
        image_export_layout.addLayout(image_export_form)

        self.gif_export_panel = GifExportPanel()
        self.gif_export_panel.optionsChanged.connect(self._on_image_export_preferences_changed)
        self.gif_export_panel.autoFpsRequested.connect(self._on_gif_auto_fps_requested)
        image_export_layout.addWidget(self.gif_export_panel)

        self.image_export_progress = QProgressBar()
        self.image_export_progress.setMinimum(0)
        self.image_export_progress.setMaximum(1)
        self.image_export_progress.setValue(0)
        self.image_export_progress.setFixedHeight(18)
        self.image_export_progress.setTextVisible(True)
        self.image_export_progress.setFormat("图片导出 0/0")
        self.image_export_progress.hide()
        image_export_layout.addWidget(self.image_export_progress)
        export_root.addWidget(image_export_group)

        self.video_export_panel = VideoExportPanel()
        self.video_export_panel.exportRequested.connect(self._start_video_export)
        self.video_export_panel.cancelRequested.connect(self._cancel_video_export)
        self.video_export_panel.autoFpsRequested.connect(self._on_video_auto_fps_requested)
        ffmpeg_path = find_ffmpeg_executable()
        if ffmpeg_path is not None:
            self.video_export_panel.set_status_text(f"ffmpeg: {ffmpeg_path}")
        else:
            install_script = ffmpeg_install_script_path()
            if install_script is not None:
                self.video_export_panel.set_status_text(
                    f"未找到 ffmpeg，可运行: {install_script}，目标: {preferred_ffmpeg_binary_path()}"
                )
            else:
                self.video_export_panel.set_status_text(f"未找到 ffmpeg，目标: {preferred_ffmpeg_binary_path()}")
        export_root.addWidget(self.video_export_panel)

        export_section = CollapsibleSection("导出", expanded=True)
        export_section.set_content_widget(export_content)
        left_layout.addWidget(export_section)
        left_layout.addStretch(1)
        self._on_photos_section_toggled(self._photos_section.is_expanded())

    def _on_photos_section_toggled(self, expanded: bool) -> None:
        layout = getattr(self, "_left_panel_layout", None)
        photos_section = getattr(self, "_photos_section", None)
        if layout is None or photos_section is None:
            return
        layout.setStretchFactor(photos_section, 1 if expanded else 0)

    def _center_mode_button_value(self) -> str:
        buttons = getattr(self, "center_mode_buttons", None)
        if isinstance(buttons, dict):
            for mode, button in buttons.items():
                try:
                    if button.isChecked():
                        return _normalize_center_mode(mode)
                except Exception:
                    continue
        combo = getattr(self, "center_mode_combo", None)
        if combo is not None:
            return _normalize_center_mode(combo.currentData())
        return _normalize_center_mode(_DEFAULT_TEMPLATE_CENTER_MODE)

    def _set_center_mode_value(self, value: Any, *, emit_changed: bool) -> None:
        mode = _normalize_center_mode(value)
        if emit_changed and mode != _CENTER_MODE_CUSTOM:
            self._clear_derived_crop_overrides_for_auto_center_mode()
        buttons = getattr(self, "center_mode_buttons", None)
        if isinstance(buttons, dict) and buttons:
            target = buttons.get(mode) or buttons.get(_DEFAULT_TEMPLATE_CENTER_MODE)
            if target is not None:
                changed = not target.isChecked()
                previous_states: list[tuple[QRadioButton, bool]] = []
                for button in buttons.values():
                    try:
                        previous_states.append((button, bool(button.blockSignals(True))))
                    except Exception:
                        continue
                try:
                    target.setChecked(True)
                finally:
                    for button, old_state in reversed(previous_states):
                        button.blockSignals(old_state)
                if emit_changed and changed:
                    self._on_crop_settings_changed()
                return
        combo = getattr(self, "center_mode_combo", None)
        if combo is not None:
            idx = combo.findData(mode)
            if idx >= 0:
                old_blocked = bool(combo.blockSignals(not emit_changed))
                try:
                    combo.setCurrentIndex(idx)
                finally:
                    if not emit_changed:
                        combo.blockSignals(old_blocked)

    def _clear_derived_crop_overrides_for_auto_center_mode(self) -> None:
        """切换到图像/焦点/鸟体中心时，丢弃手动裁切框与自定义中心派生状态。"""
        if self._current_edit_mode_id() == EDIT_MODE_CROP_ADJUST:
            return
        self._crop_box_override = None
        self._custom_center = None
        if self.current_path is None or self._is_placeholder_active():
            return
        key = _path_key(self.current_path)
        overrides = self.photo_render_overrides.get(key)
        if not isinstance(overrides, dict):
            return
        updated = dict(overrides)
        updated["crop_box"] = None
        updated["custom_center_x"] = None
        updated["custom_center_y"] = None
        self.photo_render_overrides[key] = updated
        self._set_photo_crop_box_for_path(self.current_path, None)

    def _setup_pipeline_stage_option_groups(self) -> None:
        descriptors = {
            descriptor.stage_id: descriptor
            for descriptor in build_default_image_proc_pipeline().ui_descriptors()
        }
        self._pipeline_stage_labels = {
            stage_id: descriptors.get(stage_id).label if descriptors.get(stage_id) is not None else stage_id
            for stage_id in normalize_pipeline_stage_order(None)
        }

        self._pipeline_stage_option_groups: dict[str, QGroupBox] = {}

        template_group = QGroupBox()
        template_form = QFormLayout(template_group)
        _configure_form_layout(template_form)
        template_row_widget = QWidget()
        template_row_layout = QHBoxLayout(template_row_widget)
        template_row_layout.setContentsMargins(0, 0, 0, 0)
        template_row_layout.setSpacing(6)
        template_row_layout.addWidget(self.template_combo, 1)
        template_row_layout.addWidget(self.manage_template_btn)

        template_form.addRow("模板", template_row_widget)

        override_btn_widget = QWidget()
        override_btn_layout = QHBoxLayout(override_btn_widget)
        override_btn_layout.setContentsMargins(0, 0, 0, 0)
        override_btn_layout.setSpacing(6)
        override_btn_layout.addWidget(self.reset_override_btn)
        override_btn_layout.addWidget(self.apply_all_btn)
        override_btn_layout.addStretch()
        template_form.addRow("重载", override_btn_widget)
        
        template_hint = QLabel("这些参数仍会作为当前模板重载参与预览、批量应用和导出。")
        template_hint.setWordWrap(True)
        template_form.addRow("说明", template_hint)
        template_form.addRow("裁切比例", self.ratio_combo)
        template_form.addRow("裁切中心", self.center_mode_widget)
        template_form.addRow("留边", self.crop_padding_editor)

        auto_crop_row_widget = QWidget()
        auto_crop_row_layout = QHBoxLayout(auto_crop_row_widget)
        auto_crop_row_layout.setContentsMargins(0, 0, 0, 0)
        auto_crop_row_layout.setSpacing(10)
        auto_crop_row_layout.addWidget(self.uniform_auto_crop_check)
        auto_crop_row_layout.addWidget(QLabel("防抖"))
        auto_crop_row_layout.addWidget(self.auto_crop_stabilization_slider, 1)
        auto_crop_row_layout.addWidget(self.auto_crop_stabilization_value_label)
        auto_crop_row_layout.addStretch()
        template_form.addRow("批量预计算", auto_crop_row_widget)
        self._pipeline_stage_option_groups["template_crop"] = template_group

        resize_group = QGroupBox()
        resize_form = QFormLayout(resize_group)
        _configure_form_layout(resize_form)
        resize_form.addRow("最大长边", self.max_edge_combo)
        self._pipeline_stage_option_groups["resize_limit"] = resize_group

        overlay_group = QGroupBox()
        overlay_form = QFormLayout(overlay_group)
        _configure_form_layout(overlay_form)
        overlay_row_widget = QWidget()
        overlay_row_layout = QHBoxLayout(overlay_row_widget)
        overlay_row_layout.setContentsMargins(0, 0, 0, 0)
        overlay_row_layout.setSpacing(10)
        overlay_row_layout.addWidget(self.draw_banner_check)
        overlay_row_layout.addWidget(self.draw_text_check)
        overlay_row_layout.addStretch()
        overlay_form.addRow("叠加信息", overlay_row_widget)
        self._pipeline_stage_option_groups["template_overlay"] = overlay_group

        focus_group = QGroupBox()
        focus_form = QFormLayout(focus_group)
        _configure_form_layout(focus_form)
        focus_form.addRow("焦点框", self.draw_focus_check)
        self._pipeline_stage_option_groups["focus_overlay"] = focus_group

    def _pipeline_stage_label(self, stage_id: str) -> str:
        labels = getattr(self, "_pipeline_stage_labels", {}) or {}
        return str(labels.get(stage_id) or stage_id)

    def _default_pipeline_stage_enabled_map(self) -> dict[str, bool]:
        return {
            stage_id: True
            for stage_id in normalize_pipeline_stage_order(None)
        }

    def _is_required_pipeline_stage(self, stage_id: str) -> bool:
        return str(stage_id or "").strip() == STAGE_TEMPLATE_CROP_ID

    def _current_pipeline_stage_order(self) -> tuple[str, ...]:
        stage_list = getattr(self, "pipeline_stage_list", None)
        if stage_list is None:
            return normalize_pipeline_stage_order(getattr(self, "_pipeline_stage_order", None))
        values: list[str] = []
        for index in range(stage_list.count()):
            item = stage_list.item(index)
            if item is None:
                continue
            stage_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if stage_id:
                values.append(stage_id)
        return normalize_pipeline_stage_order(values)

    def _current_pipeline_stage_enabled_map(self) -> dict[str, bool]:
        enabled = self._default_pipeline_stage_enabled_map()
        stored = getattr(self, "_pipeline_stage_enabled", None)
        if isinstance(stored, dict):
            for stage_id in enabled:
                if stage_id in stored:
                    enabled[stage_id] = bool(stored[stage_id])

        stage_list = getattr(self, "pipeline_stage_list", None)
        if stage_list is not None:
            for index in range(stage_list.count()):
                item = stage_list.item(index)
                if item is None:
                    continue
                stage_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
                if stage_id in enabled:
                    enabled[stage_id] = item.checkState() == Qt.CheckState.Checked
        enabled[STAGE_TEMPLATE_CROP_ID] = True
        return enabled

    def _is_pipeline_stage_enabled(self, stage_id: str) -> bool:
        stage_id = str(stage_id or "").strip()
        if self._is_required_pipeline_stage(stage_id):
            return True
        if stage_id not in _PIPELINE_STAGE_ENABLED_KEYS:
            return True
        return bool(self._current_pipeline_stage_enabled_map().get(stage_id, True))

    def _set_pipeline_stage_enabled_map(
        self,
        raw: Any,
        *,
        save: bool,
        mark_dirty: bool,
    ) -> None:
        enabled = self._default_pipeline_stage_enabled_map()
        source: Any = raw
        if isinstance(raw, dict) and isinstance(raw.get(PIPELINE_STAGE_ENABLED_KEY), dict):
            source = raw.get(PIPELINE_STAGE_ENABLED_KEY)
        if isinstance(source, dict):
            for stage_id, enabled_key in _PIPELINE_STAGE_ENABLED_KEYS.items():
                if stage_id in source:
                    enabled[stage_id] = _parse_bool_value(source.get(stage_id), True)
                elif enabled_key in source:
                    enabled[stage_id] = _parse_bool_value(source.get(enabled_key), True)
        enabled[STAGE_TEMPLATE_CROP_ID] = True

        self._pipeline_stage_enabled = enabled
        stage_list = getattr(self, "pipeline_stage_list", None)
        if stage_list is not None:
            old_blocked = bool(stage_list.blockSignals(True))
            try:
                for index in range(stage_list.count()):
                    item = stage_list.item(index)
                    if item is None:
                        continue
                    stage_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
                    if stage_id in enabled:
                        item.setCheckState(
                            Qt.CheckState.Checked if enabled[stage_id] else Qt.CheckState.Unchecked
                        )
            finally:
                stage_list.blockSignals(old_blocked)
        self._sync_pipeline_stage_option_group_order()
        if save:
            self._save_image_export_preferences()
        if mark_dirty:
            self._on_output_settings_changed()

    def _set_pipeline_stage_order(
        self,
        order: Any,
        *,
        save: bool,
        mark_dirty: bool,
    ) -> None:
        normalized = normalize_pipeline_stage_order(order)
        enabled = self._current_pipeline_stage_enabled_map()
        self._pipeline_stage_order = normalized
        stage_list = getattr(self, "pipeline_stage_list", None)
        if stage_list is not None:
            current_stage = None
            current_item = stage_list.currentItem()
            if current_item is not None:
                current_stage = str(current_item.data(Qt.ItemDataRole.UserRole) or "").strip()
            stage_list.blockSignals(True)
            try:
                stage_list.clear()
                for stage_id in normalized:
                    item = QListWidgetItem(self._pipeline_stage_label(stage_id))
                    item.setData(Qt.ItemDataRole.UserRole, stage_id)
                    flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    if self._is_required_pipeline_stage(stage_id):
                        flags &= ~Qt.ItemFlag.ItemIsUserCheckable
                        item.setToolTip("模板裁切是必选 Stage，始终执行。")
                    item.setFlags(flags)
                    item.setCheckState(
                        Qt.CheckState.Checked if enabled.get(stage_id, True) else Qt.CheckState.Unchecked
                    )
                    stage_list.addItem(item)
                selected_index = normalized.index(current_stage) if current_stage in normalized else 0
                if stage_list.count() > 0:
                    stage_list.setCurrentRow(selected_index)
            finally:
                stage_list.blockSignals(False)
            self._pipeline_stage_enabled = self._current_pipeline_stage_enabled_map()
            self._refresh_pipeline_stage_move_buttons()
        self._sync_pipeline_stage_option_group_order()
        if save:
            self._save_image_export_preferences()
        if mark_dirty:
            self._on_output_settings_changed()

    def _sync_pipeline_stage_option_group_order(self) -> None:
        layout = getattr(self, "pipeline_stage_options_layout", None)
        groups = getattr(self, "_pipeline_stage_option_groups", {}) or {}
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.hide()
        visible_index = 0
        for stage_id in self._current_pipeline_stage_order():
            group = groups.get(stage_id)
            if group is None:
                continue
            if not self._is_pipeline_stage_enabled(stage_id):
                group.hide()
                continue
            visible_index += 1
            group.setTitle(f"{visible_index}. {self._pipeline_stage_label(stage_id)}")
            group.show()
            layout.addWidget(group)

    def _refresh_pipeline_stage_move_buttons(self) -> None:
        stage_list = getattr(self, "pipeline_stage_list", None)
        up_btn = getattr(self, "pipeline_stage_up_btn", None)
        down_btn = getattr(self, "pipeline_stage_down_btn", None)
        row = stage_list.currentRow() if stage_list is not None else -1
        count = stage_list.count() if stage_list is not None else 0
        if up_btn is not None:
            up_btn.setEnabled(row > 1)
        if down_btn is not None:
            down_btn.setEnabled(row > 0 and row < count - 1)

    def _on_pipeline_stage_item_changed(self, item: QListWidgetItem) -> None:
        stage_id = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if stage_id not in _PIPELINE_STAGE_ENABLED_KEYS:
            return
        if self._is_required_pipeline_stage(stage_id):
            stage_list = getattr(self, "pipeline_stage_list", None)
            old_blocked = bool(stage_list.blockSignals(True)) if stage_list is not None else False
            try:
                item.setCheckState(Qt.CheckState.Checked)
            finally:
                if stage_list is not None:
                    stage_list.blockSignals(old_blocked)
            self._pipeline_stage_enabled = self._current_pipeline_stage_enabled_map()
            self._sync_pipeline_stage_option_group_order()
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        current = self._current_pipeline_stage_enabled_map()
        current[stage_id] = enabled
        self._pipeline_stage_enabled = current
        self._sync_pipeline_stage_option_group_order()
        self._save_image_export_preferences()
        self._on_output_settings_changed()

    def _move_pipeline_stage(self, direction: int) -> None:
        stage_list = getattr(self, "pipeline_stage_list", None)
        if stage_list is None:
            return
        current_row = stage_list.currentRow()
        if current_row <= 0:
            return
        target_row = current_row + int(direction)
        if target_row <= 0 or target_row >= stage_list.count():
            return
        order = list(self._current_pipeline_stage_order())
        order[current_row], order[target_row] = order[target_row], order[current_row]
        self._set_pipeline_stage_order(order, save=True, mark_dirty=True)
        stage_list.setCurrentRow(target_row)

    def _selected_export_stage_id(self) -> str:
        buttons = getattr(self, "export_stage_buttons", None)
        if isinstance(buttons, dict):
            for stage_id, button in buttons.items():
                try:
                    if button.isChecked():
                        return normalize_export_stage_id(stage_id)
                except Exception:
                    continue
        combo = getattr(self, "export_stage_combo", None)
        if combo is None:
            return DEFAULT_EXPORT_STAGE_ID
        return normalize_export_stage_id(combo.currentData())

    def _set_selected_export_stage_id(self, stage_id: Any, *, save: bool) -> None:
        normalized = normalize_export_stage_id(stage_id)
        buttons = getattr(self, "export_stage_buttons", None)
        if isinstance(buttons, dict) and buttons:
            target = buttons.get(normalized) or buttons.get(DEFAULT_EXPORT_STAGE_ID)
            if target is not None:
                changed = not target.isChecked()
                previous_states: list[tuple[QRadioButton, bool]] = []
                for button in buttons.values():
                    try:
                        previous_states.append((button, bool(button.blockSignals(True))))
                    except Exception:
                        continue
                try:
                    target.setChecked(True)
                finally:
                    for button, old_state in reversed(previous_states):
                        button.blockSignals(old_state)
                if changed:
                    self._refresh_image_export_action_states()
        combo = getattr(self, "export_stage_combo", None)
        if combo is not None:
            index = combo.findData(normalized)
            if index < 0:
                index = combo.findData(DEFAULT_EXPORT_STAGE_ID)
            if index >= 0:
                combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(index)
                finally:
                    combo.blockSignals(False)
        if save:
            self._save_image_export_preferences()
        self._refresh_image_export_action_states()

    def _on_export_stage_changed(self, *_args: Any) -> None:
        self._refresh_image_export_action_states()
        self._save_image_export_preferences()
        self._schedule_workspace_autosave()

    def _normalize_output_suffix(self, value: Any) -> str:
        suffix = str(value or "").strip().lower()
        if suffix == "jpeg":
            suffix = "jpg"
        buttons = getattr(self, "output_format_buttons", None)
        if isinstance(buttons, dict) and buttons:
            if suffix in buttons:
                return suffix
            return "png" if "png" in buttons else next(iter(buttons))
        supported = [item_suffix for item_suffix, _label in OUTPUT_FORMAT_OPTIONS if item_suffix in {"jpg", "jpeg", "png"}]
        if suffix in supported:
            return suffix
        return supported[0] if supported else "png"

    def _set_selected_output_suffix(self, suffix: Any, *, save: bool) -> None:
        normalized = self._normalize_output_suffix(suffix)
        buttons = getattr(self, "output_format_buttons", None)
        if isinstance(buttons, dict) and buttons:
            target = buttons.get(normalized)
            if target is not None:
                previous_states: list[tuple[QRadioButton, bool]] = []
                for button in buttons.values():
                    try:
                        previous_states.append((button, bool(button.blockSignals(True))))
                    except Exception:
                        continue
                try:
                    target.setChecked(True)
                finally:
                    for button, old_state in reversed(previous_states):
                        button.blockSignals(old_state)
        combo = getattr(self, "output_format_combo", None)
        if combo is not None:
            index = combo.findData(normalized)
            if index >= 0:
                combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(index)
                finally:
                    combo.blockSignals(False)
        if save:
            self._save_image_export_preferences()
        self._refresh_image_export_action_states()

    def _setup_ui_preview_panel(self) -> QWidget:
        """构建右侧「预览区」UI，返回该面板 QWidget。"""
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(8)

        self.current_file_label = QLabel("当前照片: 未选择")
        right_layout.addWidget(self.current_file_label)

        preview_toolbar = QHBoxLayout()
        preview_toolbar.setContentsMargins(0, 0, 0, 0)
        preview_toolbar.setSpacing(8)

        # 编辑模式切换：三个互斥图标按钮（选择 / 去抖动参考区 / 调整裁剪框）。
        self._setup_edit_mode_buttons(preview_toolbar)

        mode_separator = QFrame()
        mode_separator.setFrameShape(QFrame.Shape.VLine)
        mode_separator.setFrameShadow(QFrame.Shadow.Sunken)
        preview_toolbar.addWidget(mode_separator)

        self.show_crop_effect_check = QCheckBox("显示裁切效果")
        self.show_crop_effect_check.setChecked(True)
        self.show_crop_effect_check.toggled.connect(self._on_preview_toolbar_toggled)
        preview_toolbar.addWidget(self.show_crop_effect_check)

        self.dejitter_reference_clear_btn = QPushButton("清除参考区")
        self.dejitter_reference_clear_btn.setToolTip("清除已框选的去抖动特征参考区（也可在参考区模式下右键清除）。")
        self.dejitter_reference_clear_btn.clicked.connect(self._on_dejitter_reference_clear)
        # preview_toolbar.addWidget(self.dejitter_reference_clear_btn)

        self.crop_effect_alpha_label = QLabel("Alpha")
        preview_toolbar.addWidget(self.crop_effect_alpha_label)

        self.crop_effect_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.crop_effect_alpha_slider.setRange(0, 255)
        self.crop_effect_alpha_slider.setSingleStep(1)
        self.crop_effect_alpha_slider.setPageStep(16)
        self.crop_effect_alpha_slider.setValue(_DEFAULT_CROP_EFFECT_ALPHA)
        self.crop_effect_alpha_slider.setFixedWidth(120)
        self.crop_effect_alpha_slider.valueChanged.connect(self._on_crop_effect_alpha_changed)
        preview_toolbar.addWidget(self.crop_effect_alpha_slider)

        self.crop_effect_alpha_value_label = QLabel(str(_DEFAULT_CROP_EFFECT_ALPHA))
        self.crop_effect_alpha_value_label.setMinimumWidth(28)
        preview_toolbar.addWidget(self.crop_effect_alpha_value_label)

        self.show_focus_box_check = QCheckBox("显示对焦点")
        self.show_focus_box_check.setChecked(True)
        self.show_focus_box_check.toggled.connect(self._on_preview_toolbar_toggled)
        preview_toolbar.addWidget(self.show_focus_box_check)

        self.show_bird_box_check = QCheckBox("显示鸟体框")
        self.show_bird_box_check.setChecked(True)
        self.show_bird_box_check.toggled.connect(self._on_preview_toolbar_toggled)
        preview_toolbar.addWidget(self.show_bird_box_check)

        self.preview_grid_combo = QComboBox()
        self.preview_grid_combo.setFixedWidth(_PREVIEW_GRID_MODE_COMBO_WIDTH)
        valid_grid_modes = set(PREVIEW_COMPOSITION_GRID_MODES)
        for mode, label in _PREVIEW_GRID_MODE_ITEMS:
            if mode in valid_grid_modes:
                self.preview_grid_combo.addItem(label, mode)
        current_grid_index = self.preview_grid_combo.findData("none")
        if current_grid_index < 0 and self.preview_grid_combo.count() > 0:
            current_grid_index = 0
        if current_grid_index >= 0:
            self.preview_grid_combo.setCurrentIndex(current_grid_index)
        self.preview_grid_combo.setToolTip("设置预览图构图辅助线；BirdStamp 中会优先绘制在当前裁切范围内。")
        self.preview_grid_combo.currentIndexChanged.connect(self._on_preview_grid_mode_changed)
        preview_toolbar.addWidget(self.preview_grid_combo)

        self.preview_grid_line_width_combo = QComboBox()
        self.preview_grid_line_width_combo.setFixedWidth(_PREVIEW_GRID_LINE_WIDTH_COMBO_WIDTH)
        for line_width in PREVIEW_COMPOSITION_GRID_LINE_WIDTHS:
            self.preview_grid_line_width_combo.addItem(f"{line_width} px", line_width)
        current_width_index = self.preview_grid_line_width_combo.findData(1)
        if current_width_index < 0 and self.preview_grid_line_width_combo.count() > 0:
            current_width_index = 0
        if current_width_index >= 0:
            self.preview_grid_line_width_combo.setCurrentIndex(current_width_index)
        self.preview_grid_line_width_combo.setToolTip("设置构图辅助线线宽。")
        self.preview_grid_line_width_combo.currentIndexChanged.connect(self._on_preview_grid_line_width_changed)
        preview_toolbar.addWidget(self.preview_grid_line_width_combo)

        self.preview_scale_combo = QComboBox()
        configure_preview_scale_preset_combo(
            self.preview_scale_combo,
            tooltip="设置预览缩放比例，表示当前显示像素相对原图像素的百分比。",
            fixed_width=_PREVIEW_SCALE_COMBO_WIDTH,
        )
        self.preview_scale_combo.activated.connect(self._on_preview_scale_preset_activated)
        preview_toolbar.addWidget(self.preview_scale_combo)

        preview_toolbar.addStretch(1)
        right_layout.addLayout(preview_toolbar)

        self.preview_label = PreviewWithStatusBar(canvas=EditorPreviewCanvas())
        self.preview_label.setObjectName("PreviewLabel")
        self._crop_box_override: tuple[float, float, float, float] | None = None
        self._custom_center: tuple[float, float] | None = None
        self._dejitter_reference_regions: tuple[tuple[float, float, float, float], ...] = ()
        self._dejitter_reference_source: str | None = None
        self._preview_outer_pad: tuple[int, int, int, int] = (0, 0, 0, 0)
        canvas = self.preview_label.canvas
        if hasattr(canvas, "crop_box_changed"):
            canvas.crop_box_changed.connect(self._on_canvas_crop_box_changed)
        if hasattr(canvas, "crop_drag_started"):
            canvas.crop_drag_started.connect(self._on_canvas_crop_drag_started)
        if hasattr(canvas, "crop_drag_finished"):
            canvas.crop_drag_finished.connect(self._on_canvas_crop_drag_finished)
        if hasattr(canvas, "reference_region_changed"):
            canvas.reference_region_changed.connect(self._on_canvas_reference_region_changed)
        self._update_dejitter_reference_clear_enabled()
        if hasattr(self.preview_label, "display_scale_percent_changed"):
            self.preview_label.display_scale_percent_changed.connect(self._sync_preview_scale_combo)
            self.preview_label.display_scale_percent_changed.connect(self._on_workspace_state_changed)
        self._sync_preview_scale_combo(self.preview_label.current_display_scale_percent())
        right_layout.addWidget(self.preview_label, stretch=1)

        return right_panel

    def _setup_shortcuts(self) -> None:
        self.action_add_files = QAction("添加照片...", self)
        self.action_add_files.setShortcut(QKeySequence.StandardKey.Open)
        self.action_add_files.triggered.connect(self._pick_files)
        self.addAction(self.action_add_files)

        self.action_add_directory = QAction("添加目录...", self)
        self.action_add_directory.triggered.connect(self._pick_directory)
        self.addAction(self.action_add_directory)

        self.action_load_workspace = QAction("加载工作区...", self)
        self.action_load_workspace.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.action_load_workspace.triggered.connect(self.load_workspace)
        self.addAction(self.action_load_workspace)

        self.action_save_workspace = QAction("保存工作区", self)
        self.action_save_workspace.setShortcut(QKeySequence.StandardKey.Save)
        self.action_save_workspace.triggered.connect(self.save_workspace)
        self.addAction(self.action_save_workspace)

        self.action_save_workspace_as = QAction("工作区另存为...", self)
        self.action_save_workspace_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.action_save_workspace_as.triggered.connect(self.save_workspace_as)
        self.addAction(self.action_save_workspace_as)

        self.action_preview = QAction("刷新预览", self)
        self.action_preview.setShortcut(QKeySequence("Ctrl+R"))
        self.action_preview.triggered.connect(self.render_preview)
        self.addAction(self.action_preview)

        self.action_export_current = QAction("导出当前", self)
        self.action_export_current.setShortcut(QKeySequence("Ctrl+E"))
        self.action_export_current.triggered.connect(self.export_current)
        self.addAction(self.action_export_current)

        self.action_export_all = QAction("批量导出", self)
        self.action_export_all.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.action_export_all.triggered.connect(self.export_all)
        self.addAction(self.action_export_all)

    def _setup_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.clear()

        file_menu = menu_bar.addMenu("文件")
        file_menu.addAction(self.action_add_files)
        file_menu.addAction(self.action_add_directory)
        file_menu.addSeparator()
        file_menu.addAction(self.action_load_workspace)
        file_menu.addAction(self.action_save_workspace)
        file_menu.addAction(self.action_save_workspace_as)

        settings_menu = menu_bar.addMenu("设置")
        perf_probe_act = QAction("性能探针日志", self)
        perf_probe_act.setCheckable(True)
        perf_probe_act.setChecked(bool(get_runtime_user_options().get(KEY_PERF_PROBES_ENABLED, 0)))
        perf_probe_act.setToolTip(
            "开启后在日志中记录照片选择、图像加载/预览渲染、编辑模式鼠标拖拽等关键路径耗时。"
        )
        perf_probe_act.triggered.connect(self._set_perf_probes_enabled)
        self._perf_probe_action = perf_probe_act
        settings_menu.addAction(perf_probe_act)

    def _sync_perf_probe_action(self) -> None:
        action = getattr(self, "_perf_probe_action", None)
        if action is None:
            return
        try:
            action.blockSignals(True)
            action.setChecked(bool(get_runtime_user_options().get(KEY_PERF_PROBES_ENABLED, 0)))
        finally:
            action.blockSignals(False)

    def _set_perf_probes_enabled(self, checked: bool) -> None:
        options = get_runtime_user_options()
        options[KEY_PERF_PROBES_ENABLED] = int(bool(checked))
        try:
            normalized = save_user_options(options)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"无法写入性能探针选项：\n{exc}")
            self._sync_perf_probe_action()
            return
        apply_runtime_user_options(normalized)
        self._sync_perf_probe_action()
        enabled = bool(normalized.get(KEY_PERF_PROBES_ENABLED, 0))
        _log.info(
            "[PERF_PROBE] enabled=%s config=%r",
            enabled,
            get_user_options_path(),
        )
        if enabled:
            self._set_status(
                f"性能探针已开启；日志前缀 [PERF_PROBE]，配置文件：{get_user_options_path()}"
            )
        else:
            self._set_status("性能探针已关闭。")

    def _apply_system_adaptive_style(self) -> None:
        palette = self.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        base_color = palette.color(QPalette.ColorRole.Base)
        text_color = palette.color(QPalette.ColorRole.Text)
        button_color = palette.color(QPalette.ColorRole.Button)
        button_text = palette.color(QPalette.ColorRole.ButtonText)
        disabled_text = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)

        dark_mode = window_color.lightness() < 128
        border_color = window_color.lighter(132) if dark_mode else window_color.darker(130)
        hover_color = button_color.lighter(115) if dark_mode else button_color.darker(105)
        preview_bg = window_color.lighter(108) if dark_mode else window_color.darker(103)

        self.setStyleSheet(
            f"""
            QWidget {{
                font-size: 13px;
            }}
            QGroupBox {{
                border: 1px solid {border_color.name()};
                border-radius: 10px;
                margin-top: 10px;
                background: {base_color.name()};
            }}
            QGroupBox::title {{
                left: 10px;
                padding: 0 4px;
                font-weight: 600;
            }}
            QScrollArea#EditorLeftScrollArea {{
                background: transparent;
                border: none;
            }}
            QWidget#EditorLeftPanel {{
                background: transparent;
            }}
            QToolButton#CollapsibleHeaderButton {{
                text-align: left;
                font-weight: 600;
                border: 1px solid {border_color.name()};
                border-radius: 10px;
                background: {base_color.name()};
                color: {text_color.name()};
                padding: 7px 10px;
            }}
            QToolButton#CollapsibleHeaderButton:hover {{
                background: {hover_color.name()};
            }}
            QFrame#CollapsibleContentFrame {{
                border: 1px solid {border_color.name()};
                border-top: none;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                background: {base_color.name()};
                padding: 8px;
            }}
            QLineEdit {{
                border: 1px solid {border_color.name()};
                border-radius: 7px;
                background: {base_color.name()};
                color: {text_color.name()};
                min-height: 28px;
            }}
            QLineEdit {{
                padding: 5px 8px;
            }}
            QComboBox QAbstractItemView {{
                background: {base_color.name()};
                color: {text_color.name()};
                border: 1px solid {border_color.name()};
                selection-background-color: {hover_color.name()};
                selection-color: {text_color.name()};
                outline: none;
            }}
            QListWidget, QTreeWidget {{
                border: 1px solid {border_color.name()};
                border-radius: 7px;
                background: {base_color.name()};
                color: {text_color.name()};
            }}
            QPushButton {{
                border: 1px solid {border_color.name()};
                border-radius: 7px;
                background: {button_color.name()};
                color: {button_text.name()};
                padding: 6px 10px;
            }}
            QPushButton:hover {{
                background: {hover_color.name()};
            }}
            QPushButton:disabled {{
                color: {disabled_text.name()};
            }}
            QLabel#PreviewLabel {{
                border: 1px solid {border_color.name()};
                border-radius: 10px;
                background: {preview_bg.name()};
                color: {text_color.name()};
            }}
            QLabel#PreviewInfoLabel {{
                color: {text_color.name()};
                padding: 2px 4px;
            }}
            """
        )

    def _set_status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _editor_export_state_path(self) -> Path:
        return get_config_path().parent / "editor_export_state.json"

    def _legacy_video_export_state_path(self) -> Path:
        return get_config_path().parent / "editor_video_export_state.json"

    def _load_editor_export_state_raw(self) -> dict[str, Any]:
        state_path = self._editor_export_state_path()
        try:
            text = state_path.read_text(encoding="utf-8")
            raw = json.loads(text)
        except Exception:
            raw = None
        if isinstance(raw, dict):
            return raw

        legacy_path = self._legacy_video_export_state_path()
        try:
            legacy_text = legacy_path.read_text(encoding="utf-8")
            legacy_raw = json.loads(legacy_text)
        except Exception:
            return {}
        if not isinstance(legacy_raw, dict):
            return {}
        last_output_dir = str(legacy_raw.get("last_output_dir") or "").strip()
        if not last_output_dir:
            return {}
        return {
            "last_video_output_dir": last_output_dir,
        }

    def _load_editor_export_state_value(self, key: str, default: Any = None) -> Any:
        raw = self._load_editor_export_state_raw()
        if not isinstance(raw, dict):
            return default
        return raw.get(key, default)

    def _load_remembered_output_dir(self, key: str) -> Path | None:
        dir_text = str(self._load_editor_export_state_value(key, "") or "").strip()
        if not dir_text:
            return None
        try:
            path = Path(dir_text).expanduser().resolve(strict=False)
        except Exception:
            return None
        return path if path.is_dir() else None

    def _save_editor_export_state_value(self, key: str, value: Any) -> None:
        state_path = self._editor_export_state_path()
        payload = self._load_editor_export_state_raw()
        payload[key] = value
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            _log.warning("save export state failed: key=%s err=%s", key, exc)

    def _save_remembered_output_dir(self, key: str, directory: Path) -> None:
        try:
            target_dir = directory.expanduser().resolve(strict=False)
        except Exception:
            target_dir = Path(directory)
        self._save_editor_export_state_value(key, str(target_dir))
        self._schedule_workspace_autosave()

    def _load_video_export_last_output_dir(self) -> Path | None:
        return self._load_remembered_output_dir("last_video_output_dir")

    def _save_video_export_last_output_dir(self, directory: Path) -> None:
        self._save_remembered_output_dir("last_video_output_dir", directory)

    def _load_image_export_last_output_dir(self) -> Path | None:
        return self._load_remembered_output_dir("last_image_output_dir")

    def _save_image_export_last_output_dir(self, directory: Path) -> None:
        self._save_remembered_output_dir("last_image_output_dir", directory)

    def _load_batch_export_last_output_dir(self) -> Path | None:
        return self._load_remembered_output_dir("last_batch_output_dir")

    def _save_batch_export_last_output_dir(self, directory: Path) -> None:
        self._save_remembered_output_dir("last_batch_output_dir", directory)

    def _apply_image_export_preferences_from_state(self) -> None:
        output_format = str(self._load_editor_export_state_value("image_output_format", "") or "").strip().lower()
        selected_export_stage = self._load_editor_export_state_value(EXPORT_STAGE_ID_KEY, None)
        if selected_export_stage is None and output_format == "gif":
            selected_export_stage = EXPORT_STAGE_GIF_ID
        self._set_selected_export_stage_id(selected_export_stage or DEFAULT_EXPORT_STAGE_ID, save=False)

        pipeline_stage_enabled = self._load_editor_export_state_value(PIPELINE_STAGE_ENABLED_KEY, None)
        if pipeline_stage_enabled is None:
            pipeline_stage_enabled = {
                key: self._load_editor_export_state_value(key, None)
                for key in _PIPELINE_STAGE_ENABLED_KEYS.values()
            }
        self._set_pipeline_stage_enabled_map(pipeline_stage_enabled, save=False, mark_dirty=False)

        pipeline_stage_order = self._load_editor_export_state_value(PIPELINE_STAGE_ORDER_KEY, None)
        self._set_pipeline_stage_order(pipeline_stage_order, save=False, mark_dirty=False)

        if output_format:
            self._set_selected_output_suffix(output_format, save=False)

        uniform_auto_crop = self._load_editor_export_state_value("uniform_auto_crop", None)
        if uniform_auto_crop is not None and hasattr(self, "uniform_auto_crop_check"):
            enabled = (
                uniform_auto_crop
                if isinstance(uniform_auto_crop, bool)
                else str(uniform_auto_crop).strip().lower() not in {"0", "false", "no", "off", ""}
            )
            self.uniform_auto_crop_check.blockSignals(True)
            try:
                self.uniform_auto_crop_check.setChecked(bool(enabled))
            finally:
                self.uniform_auto_crop_check.blockSignals(False)
            if hasattr(self, "auto_crop_stabilization_slider"):
                enabled = bool(self.uniform_auto_crop_check.isChecked())
                self.auto_crop_stabilization_slider.setEnabled(enabled)
                self.auto_crop_stabilization_value_label.setEnabled(enabled)

        auto_crop_stabilization = self._load_editor_export_state_value("auto_crop_stabilization", None)
        if auto_crop_stabilization is not None and hasattr(self, "auto_crop_stabilization_slider"):
            try:
                stabilization_value = int(round(float(auto_crop_stabilization)))
            except Exception:
                stabilization_value = 0
            self.auto_crop_stabilization_slider.blockSignals(True)
            try:
                value = max(0, min(100, stabilization_value))
                enabled = bool(self.uniform_auto_crop_check.isChecked())
                self.auto_crop_stabilization_slider.setValue(value)
                self.auto_crop_stabilization_slider.setEnabled(enabled)
                self.auto_crop_stabilization_value_label.setText(f"{value}%")
                self.auto_crop_stabilization_value_label.setEnabled(enabled)
            finally:
                self.auto_crop_stabilization_slider.blockSignals(False)

        gif_fps = self._load_editor_export_state_value("gif_fps", None)
        gif_loop = self._load_editor_export_state_value("gif_loop", None)
        gif_keep_frames = self._load_editor_export_state_value("gif_keep_frame_images", None)
        raw_scales = self._load_editor_export_state_value("gif_scale_factors", None)
        scale_factors: list[float] | None = None
        if isinstance(raw_scales, list):
            scale_factors = []
            for item in raw_scales:
                try:
                    scale = float(item)
                except Exception:
                    continue
                if scale > 0:
                    scale_factors.append(scale)
        try:
            gif_fps_value = float(gif_fps) if gif_fps is not None else None
        except Exception:
            gif_fps_value = None
        try:
            gif_loop_value = int(gif_loop) if gif_loop is not None else None
        except Exception:
            gif_loop_value = None
        keep_frame_images_value: bool | None
        if gif_keep_frames is None:
            keep_frame_images_value = None
        elif isinstance(gif_keep_frames, bool):
            keep_frame_images_value = gif_keep_frames
        else:
            keep_frame_images_value = str(gif_keep_frames).strip().lower() not in {"0", "false", "no", "off", ""}
        self.gif_export_panel.set_state(
            fps=gif_fps_value,
            loop=gif_loop_value,
            keep_frame_images=keep_frame_images_value,
            scale_factors=scale_factors,
        )
        self._refresh_image_export_action_states()

    def _save_image_export_preferences(self) -> None:
        gif_request = self.gif_export_panel.current_request()
        self._save_editor_export_state_value("image_output_format", self._selected_output_suffix())
        self._save_editor_export_state_value(EXPORT_STAGE_ID_KEY, self._selected_export_stage_id())
        self._save_editor_export_state_value(PIPELINE_STAGE_ORDER_KEY, list(self._current_pipeline_stage_order()))
        stage_enabled = self._current_pipeline_stage_enabled_map()
        self._save_editor_export_state_value(PIPELINE_STAGE_ENABLED_KEY, dict(stage_enabled))
        for stage_id, enabled_key in _PIPELINE_STAGE_ENABLED_KEYS.items():
            self._save_editor_export_state_value(enabled_key, bool(stage_enabled.get(stage_id, True)))
        self._save_editor_export_state_value("gif_fps", gif_request.fps)
        self._save_editor_export_state_value("gif_loop", gif_request.loop)
        self._save_editor_export_state_value("gif_keep_frame_images", gif_request.keep_frame_images)
        self._save_editor_export_state_value("gif_scale_factors", list(gif_request.scale_factors))
        if hasattr(self, "uniform_auto_crop_check"):
            self._save_editor_export_state_value("uniform_auto_crop", bool(self.uniform_auto_crop_check.isChecked()))
        if hasattr(self, "auto_crop_stabilization_slider"):
            self._save_editor_export_state_value(
                "auto_crop_stabilization",
                int(self.auto_crop_stabilization_slider.value()),
            )

    def _on_image_export_format_changed(self, *_args: Any) -> None:
        self._refresh_image_export_action_states()
        self._save_image_export_preferences()
        self._schedule_workspace_autosave()

    def _on_image_export_preferences_changed(self) -> None:
        self._save_image_export_preferences()
        self._refresh_image_export_action_states()
        self._schedule_workspace_autosave()

    @staticmethod
    def _metadata_lookup_value(raw_metadata: dict[str, Any], candidates: Iterable[str]) -> Any:
        if not isinstance(raw_metadata, dict):
            return None
        lookup: dict[str, Any] = {}
        for key, value in raw_metadata.items():
            text = str(key or "").strip()
            if not text:
                continue
            lookup.setdefault(text.lower(), value)
            lookup.setdefault(text.rsplit(":", 1)[-1].lower(), value)
        for candidate in candidates:
            text = str(candidate or "").strip().lower()
            if not text:
                continue
            for key in (text, text.rsplit(":", 1)[-1]):
                value = lookup.get(key)
                if value not in (None, "", " "):
                    return value
        return None

    def _parse_gif_auto_fps_datetime_value(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (list, tuple)):
            for item in value:
                parsed = self._parse_gif_auto_fps_datetime_value(item)
                if parsed is not None:
                    return parsed
            return None
        if isinstance(value, dict):
            for item in value.values():
                parsed = self._parse_gif_auto_fps_datetime_value(item)
                if parsed is not None:
                    return parsed
            return None

        text = _clean_text(value) or str(value).strip()
        if not text:
            return None
        normalized = text.replace("T", " ").strip()
        for pattern in (
            "%Y:%m:%d %H:%M:%S.%f%z",
            "%Y:%m:%d %H:%M:%S.%f",
            "%Y:%m:%d %H:%M:%S%z",
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.strptime(normalized, pattern)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    def _parse_gif_auto_fps_subsecond(self, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            for item in value:
                parsed = self._parse_gif_auto_fps_subsecond(item)
                if parsed is not None:
                    return parsed
            return None
        if isinstance(value, dict):
            for item in value.values():
                parsed = self._parse_gif_auto_fps_subsecond(item)
                if parsed is not None:
                    return parsed
            return None
        text = _clean_text(value) or str(value).strip()
        if not text:
            return None
        if "." in text:
            text = text.split(".", 1)[1]
        digits = re.sub(r"\D+", "", text)
        if not digits:
            return None
        return int(digits[:6].ljust(6, "0"))

    def _extract_gif_auto_fps_capture_datetime(self, raw_metadata: dict[str, Any]) -> datetime | None:
        composite_value = self._metadata_lookup_value(
            raw_metadata,
            (
                "Composite:SubSecDateTimeOriginal",
                "SubSecDateTimeOriginal",
                "Composite:SubSecCreateDate",
                "SubSecCreateDate",
            ),
        )
        dt = self._parse_gif_auto_fps_datetime_value(composite_value)
        if dt is not None:
            return dt

        base_value = self._metadata_lookup_value(
            raw_metadata,
            (
                "EXIF:DateTimeOriginal",
                "ExifIFD:DateTimeOriginal",
                "XMP-exif:DateTimeOriginal",
                "DateTimeOriginal",
                "EXIF:CreateDate",
                "ExifIFD:CreateDate",
                "XMP-xmp:CreateDate",
                "CreateDate",
            ),
        )
        dt = self._parse_gif_auto_fps_datetime_value(base_value)
        if dt is None:
            return None
        subsecond = self._parse_gif_auto_fps_subsecond(
            self._metadata_lookup_value(
                raw_metadata,
                (
                    "EXIF:SubSecTimeOriginal",
                    "ExifIFD:SubSecTimeOriginal",
                    "XMP-exif:SubSecTimeOriginal",
                    "SubSecTimeOriginal",
                    "EXIF:SubSecTimeDigitized",
                    "ExifIFD:SubSecTimeDigitized",
                    "SubSecTimeDigitized",
                    "EXIF:SubSecTime",
                    "ExifIFD:SubSecTime",
                    "SubSecTime",
                ),
            )
        )
        return dt.replace(microsecond=subsecond) if subsecond is not None else dt

    def _load_gif_auto_fps_metadata(self, paths: list[Path]) -> dict[str, dict[str, Any]]:
        metadata_by_key: dict[str, dict[str, Any]] = {
            _path_key(path): dict(self._photo_list_display_metadata_for_path(path))
            for path in paths
        }
        try:
            raw_batch = read_batch_metadata(
                [str(path.resolve(strict=False)) for path in paths],
                tags=_GIF_AUTO_FPS_METADATA_TAGS,
            )
        except Exception as exc:
            _log.warning("[_load_gif_auto_fps_metadata] metadata read failed: %s", exc)
            raw_batch = {}

        batch_by_key: dict[str, dict[str, Any]] = {}
        for raw_path, raw_metadata in (raw_batch or {}).items():
            if not isinstance(raw_metadata, dict):
                continue
            try:
                key = _path_key(Path(raw_path))
            except Exception:
                continue
            batch_by_key[key] = dict(raw_metadata)

        for path in paths:
            key = _path_key(path)
            merged = dict(metadata_by_key.get(key) or {"SourceFile": str(path)})
            merged.update(batch_by_key.get(key) or {})
            metadata_by_key[key] = merged
            if key in self.raw_metadata_cache:
                self.raw_metadata_cache[key] = dict(merged)
            else:
                self.photo_list_metadata_cache[key] = dict(merged)
        return metadata_by_key

    def _calculate_auto_fps_from_photo_capture_times(self) -> tuple[int, float, int] | None:
        paths = self._list_photo_paths()
        if len(paths) < 2:
            self._show_error("无法计算 FPS", "当前照片列表至少需要 2 张照片。")
            return None

        metadata_by_key = self._load_gif_auto_fps_metadata(paths)
        timestamps: list[float] = []
        for path in paths:
            dt = self._extract_gif_auto_fps_capture_datetime(metadata_by_key.get(_path_key(path), {}))
            if dt is None:
                continue
            try:
                timestamps.append(float(dt.timestamp()))
            except Exception:
                continue
        timestamps.sort()
        intervals = [
            right - left
            for left, right in zip(timestamps, timestamps[1:])
            if right > left
        ]
        if not intervals:
            self._show_error(
                "无法计算 FPS",
                "当前照片缺少可区分的拍摄时间。连拍序列通常需要 EXIF 亚秒时间才能自动计算 FPS。",
            )
            return None
        intervals.sort()
        mid = len(intervals) // 2
        median_interval = intervals[mid] if len(intervals) % 2 else (intervals[mid - 1] + intervals[mid]) * 0.5
        if median_interval <= 0:
            self._show_error("无法计算 FPS", "拍摄时间间隔无效。")
            return None

        fps = 1.0 / median_interval
        fps_value = max(1, min(240, int(round(fps))))
        return (fps_value, fps, len(timestamps))

    def _on_gif_auto_fps_requested(self) -> None:
        result = self._calculate_auto_fps_from_photo_capture_times()
        if result is None:
            return
        fps_value, fps, timestamp_count = result
        self.gif_export_panel.set_state(fps=fps_value)
        self._save_image_export_preferences()
        self._schedule_workspace_autosave()
        self._set_status(
            f"已根据 {timestamp_count} 张照片的拍摄时间计算 GIF FPS：{fps_value}（原始 {fps:.2f}）。"
        )

    def _on_video_auto_fps_requested(self) -> None:
        result = self._calculate_auto_fps_from_photo_capture_times()
        if result is None:
            return
        fps_value, fps, timestamp_count = result
        self.video_export_panel.set_fps(fps_value)
        self._schedule_workspace_autosave()
        self._set_status(
            f"已根据 {timestamp_count} 张照片的拍摄时间计算视频 FPS：{fps_value}（原始 {fps:.2f}）。"
        )

    def _confirm_video_output_overwrite(self, output_path: Path) -> bool:
        if not output_path.exists():
            return True
        answer = QMessageBox.question(
            self,
            "输出文件已存在",
            f"目标文件已存在，是否覆盖？\n\n{output_path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _show_about_dialog(self) -> None:
        about_info = _load_birdstamp_about_info()
        self._about_info = about_info
        self.setWindowTitle(_build_birdstamp_main_window_title(about_info))
        about_images = _load_birdstamp_about_images()
        show_about_dialog(self, about_info, logo_path=None, banner_path=None, images=about_images)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_preview_label()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        if event.type() in {QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange}:
            self._apply_system_adaptive_style()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        active_worker = self._video_export_worker
        if active_worker is not None and active_worker.isRunning():
            QMessageBox.information(self, "视频导出进行中", "请先中断当前视频导出，或等待导出完成后再关闭窗口。")
            event.ignore()
            return
        self._stop_photo_input_discovery_workers(wait=True)
        self._stop_photo_list_metadata_loader(wait=True, reset_progress=True)
        self._autosave_workspace_now()
        super().closeEvent(event)

    def _on_preview_toolbar_toggled(self, _checked: bool) -> None:
        self._refresh_preview_label(preserve_view=True)
        self._schedule_workspace_autosave()

    def _on_preview_grid_mode_changed(self, _index: int) -> None:
        self._apply_preview_overlay_options_from_ui()
        self._schedule_workspace_autosave()

    def _on_preview_grid_line_width_changed(self, _index: int) -> None:
        self._apply_preview_overlay_options_from_ui()
        self._schedule_workspace_autosave()

    def _on_workspace_state_changed(self, *_args: Any) -> None:
        self._schedule_workspace_autosave()

    def _on_preview_scale_preset_activated(self, index: int) -> None:
        percent = self.preview_scale_combo.itemData(index)
        try:
            parsed = float(percent)
        except Exception:
            return
        self.preview_label.set_display_scale_percent(parsed, preserve_view=True)
        self._sync_preview_scale_combo(self.preview_label.current_display_scale_percent())

    def _sync_preview_scale_combo(self, scale_percent: object) -> None:
        sync_preview_scale_preset_combo(self.preview_scale_combo, scale_percent)

    def _on_canvas_crop_drag_started(self) -> None:
        self._crop_drag_active = True

    def _on_canvas_crop_drag_finished(self) -> None:
        self._crop_drag_active = False

    def _on_canvas_crop_box_changed(self, box: tuple[float, float, float, float]) -> None:
        """9 宫格裁切框变更。

        - 若当前裁切框尚未平移（仅缩放），则根据图像尺寸反算 top/bottom/left/right padding。
        - 若已经发生过平移，则将裁切中心改为自定义，并记录 custom_center_x/y。
        - 拖拽进行中走轻量路径，release 后一次性提交完整 settings。
        """
        start = perf_counter()
        try:
            canvas = self.preview_label.canvas
            has_pan = False
            if hasattr(canvas, "has_pan"):
                try:
                    has_pan = bool(canvas.has_pan())  # type: ignore[call-arg]
                except Exception:
                    has_pan = False

            if self.current_source_image is not None:
                if not has_pan:
                    self._update_crop_padding_from_box(box, self.current_source_image.size)
                else:
                    self._set_custom_center_from_box(box)

            self._crop_box_override = box
            self._set_photo_crop_box_for_path(self.current_path, box)

            if self._crop_drag_active:
                self._preview_debounce_timer.start()
                return

            self._on_crop_settings_changed()
        finally:
            drag_probe = getattr(self.preview_label.canvas, "_drag_probe", None)
            if drag_probe is not None:
                drag_probe.add_callback(elapsed_ms(start))

    def _setup_edit_mode_buttons(self, toolbar) -> None:
        """创建三个互斥的编辑模式图标按钮（选择 / 去抖动参考区 / 调整裁剪框）。"""
        try:
            icon_color = self.palette().color(self.palette().ColorRole.WindowText)
        except Exception:
            icon_color = None

        self.edit_mode_group = QButtonGroup(self)
        self.edit_mode_group.setExclusive(True)

        specs = (
            (
                EDIT_MODE_NONE,
                "selection",
                "选择模式",
                "选择模式：移动/缩放预览（默认）。",
            ),
            (
                EDIT_MODE_REFERENCE_REGION,
                "reference",
                "去抖动参考区",
                "去抖动参考区：拖拽框选特征参考区，导出去抖动以此为锚点；"
                "按住 Shift 追加，右键清除。",
            ),
            (
                EDIT_MODE_CROP_ADJUST,
                "crop",
                "调整裁剪框",
                "调整裁剪框：拖动 9 宫格手柄调整裁剪范围，比例由「裁切比例」锁定。",
            ),
        )

        self._edit_mode_buttons: dict[str, QToolButton] = {}
        for mode_id, icon_kind, text, tip in specs:
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setIcon(_make_edit_mode_icon(icon_kind, color=icon_color))
            btn.setToolTip(tip)
            btn.setAccessibleName(text)
            self.edit_mode_group.addButton(btn)
            self._edit_mode_buttons[mode_id] = btn
            toolbar.addWidget(btn)

        self._edit_mode_buttons[EDIT_MODE_NONE].setChecked(True)
        self.edit_mode_group.buttonClicked.connect(self._on_edit_mode_changed)

    def _current_edit_mode_id(self) -> str:
        buttons = getattr(self, "_edit_mode_buttons", None)
        if not buttons:
            return EDIT_MODE_NONE
        for mode_id, btn in buttons.items():
            if btn.isChecked():
                return mode_id
        return EDIT_MODE_NONE

    def _set_edit_mode_button_checked(self, mode_id: str) -> None:
        """以静默方式设置当前编辑模式按钮选中（不触发回调）。"""
        buttons = getattr(self, "_edit_mode_buttons", None)
        if not buttons:
            return
        target = buttons.get(mode_id) or buttons.get(EDIT_MODE_NONE)
        if target is None:
            return
        group = getattr(self, "edit_mode_group", None)
        if group is not None:
            group.blockSignals(True)
        try:
            target.setChecked(True)
        finally:
            if group is not None:
                group.blockSignals(False)

    def _on_edit_mode_changed(self, *args) -> None:
        """编辑模式按钮切换：刷新预览叠加并自动保存工作区。"""
        self._refresh_preview_label(preserve_view=True)
        self._schedule_workspace_autosave()

    def _on_dejitter_reference_clear(self) -> None:
        if not self._dejitter_reference_regions:
            return
        self._dejitter_reference_regions = ()
        self._dejitter_reference_source = None
        self._update_dejitter_reference_clear_enabled()
        self._apply_preview_overlay_options_from_ui()
        self._schedule_workspace_autosave()

    def _on_canvas_reference_region_changed(
        self, regions: tuple[tuple[float, float, float, float], ...]
    ) -> None:
        """参考区由画布交互提交（预览/含外填充归一化）→ 转为源图归一化存储。"""
        source_regions: list[tuple[float, float, float, float]] = []
        for box in regions or ():
            if isinstance(box, (list, tuple)) and len(box) == 4:
                source_regions.append(
                    self._reference_region_preview_to_source(tuple(float(v) for v in box))
                )
        self._dejitter_reference_regions = tuple(source_regions)
        if source_regions and self.current_path is not None:
            self._dejitter_reference_source = str(self.current_path)
        elif not source_regions:
            self._dejitter_reference_source = None
        self._update_dejitter_reference_clear_enabled()
        self._apply_preview_overlay_options_from_ui()
        self._schedule_workspace_autosave()

    def _update_dejitter_reference_clear_enabled(self) -> None:
        btn = getattr(self, "dejitter_reference_clear_btn", None)
        if btn is not None:
            btn.setEnabled(bool(getattr(self, "_dejitter_reference_regions", ())))

    def _get_crop_padding_state(self) -> dict[str, Any]:
        state = self._crop_padding_state if isinstance(self._crop_padding_state, dict) else {}
        return {
            "top": _parse_padding_value(state.get("top", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            "bottom": _parse_padding_value(state.get("bottom", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            "left": _parse_padding_value(state.get("left", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            "right": _parse_padding_value(state.get("right", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            "fill": _safe_color(str(state.get("fill", "#FFFFFF")), "#FFFFFF"),
        }

    def _set_crop_padding_state(
        self,
        *,
        top: Any | None = None,
        bottom: Any | None = None,
        left: Any | None = None,
        right: Any | None = None,
        fill: Any | None = None,
    ) -> None:
        state = self._get_crop_padding_state()
        if top is not None:
            state["top"] = _parse_padding_value(top, _DEFAULT_CROP_PADDING_PX)
        if bottom is not None:
            state["bottom"] = _parse_padding_value(bottom, _DEFAULT_CROP_PADDING_PX)
        if left is not None:
            state["left"] = _parse_padding_value(left, _DEFAULT_CROP_PADDING_PX)
        if right is not None:
            state["right"] = _parse_padding_value(right, _DEFAULT_CROP_PADDING_PX)
        if fill is not None:
            state["fill"] = _safe_color(str(fill), "#FFFFFF")
        self._crop_padding_state = state
        self._sync_crop_padding_editor_from_state()

    def _sync_crop_padding_editor_from_state(self) -> None:
        editor = getattr(self, "crop_padding_editor", None)
        if editor is None or not hasattr(editor, "set_values"):
            return
        state = self._get_crop_padding_state()
        try:
            editor.set_values(
                top=state["top"],
                bottom=state["bottom"],
                left=state["left"],
                right=state["right"],
                fill=state["fill"],
            )
        except Exception:
            return

    def _on_crop_padding_editor_changed(self) -> None:
        editor = getattr(self, "crop_padding_editor", None)
        if editor is None or not hasattr(editor, "get_values"):
            return
        try:
            values = editor.get_values()
        except Exception:
            return
        self._set_crop_padding_state(
            top=values.get("crop_padding_top"),
            bottom=values.get("crop_padding_bottom"),
            left=values.get("crop_padding_left"),
            right=values.get("crop_padding_right"),
            fill=values.get("crop_padding_fill"),
        )
        self._on_crop_settings_changed()

    def _update_crop_padding_from_box(
        self,
        box: tuple[float, float, float, float],
        image_size: tuple[int, int],
    ) -> None:
        """按照当前裁切框在整张图中的位置，更新四向 padding 数值（像素）。"""
        try:
            width, height = image_size
        except Exception:
            return
        if width <= 0 or height <= 0:
            return
        l, t, r, b = box
        top_px = int(round(t * height))
        bottom_px = int(round((1.0 - b) * height))
        left_px = int(round(l * width))
        right_px = int(round((1.0 - r) * width))
        fill_color = self._get_crop_padding_state()["fill"]
        self._set_crop_padding_state(
            top=top_px,
            bottom=bottom_px,
            left=left_px,
            right=right_px,
            fill=fill_color,
        )

    def _set_custom_center_from_box(self, box: tuple[float, float, float, float]) -> None:
        """从裁切框中心推导自定义裁切中心，并切换到 CENTER_MODE_CUSTOM。"""
        cx = float((box[0] + box[2]) * 0.5)
        cy = float((box[1] + box[3]) * 0.5)
        self._custom_center = (cx, cy)
        # 切换中心模式到自定义
        self._set_center_mode_value(_CENTER_MODE_CUSTOM, emit_changed=False)

    def _on_preview_scale_mode_toggled(self, _checked: bool) -> None:
        self._refresh_preview_label(preserve_view=True)

    def _on_crop_effect_alpha_changed(self, value: int) -> None:
        alpha = max(0, min(255, int(value)))
        self.crop_effect_alpha_value_label.setText(str(alpha))
        self._apply_preview_overlay_options_from_ui()
        self._schedule_workspace_autosave()

    def _on_ratio_changed(self, *_args: Any) -> None:
        """裁切比例变更：使当前裁切框与新区比例一致（按中心约束），再走 settings 流程。"""
        new_ratio = self._selected_ratio()
        if (
            not _is_ratio_no_crop(new_ratio)
            and not _is_ratio_free(new_ratio)
            and self._crop_box_override is not None
            and self.current_source_image is not None
        ):
            w, h = self.current_source_image.size
            if w > 0 and h > 0:
                self._crop_box_override = _constrain_box_to_ratio(
                    self._crop_box_override,
                    new_ratio,
                    w,
                    h,
                )
        self._on_crop_settings_changed(*_args)

    def _on_crop_settings_changed(self, *_args: Any) -> None:
        """裁切相关选项变更：标记需要全图视图，然后走普通 settings 流程。"""
        self._pending_preview_fit_reset = True
        self._on_output_settings_changed(*_args)

    def _is_placeholder_active(self) -> bool:
        """当前预览是否为占位默认图（不是用户加载的真实照片）。"""
        return self.placeholder_path is not None and self.current_path == self.placeholder_path

    def _current_global_export_settings(self) -> dict[str, Any]:
        stage_enabled = self._current_pipeline_stage_enabled_map()
        return {
            "draw_banner": bool(self.draw_banner_check.isChecked()),
            "draw_text": bool(self.draw_text_check.isChecked()),
            "draw_focus": bool(self.draw_focus_check.isChecked()),
            PIPELINE_STAGE_ORDER_KEY: list(self._current_pipeline_stage_order()),
            PIPELINE_STAGE_ENABLED_KEY: dict(stage_enabled),
            STAGE_TEMPLATE_CROP_ENABLED_KEY: bool(stage_enabled.get(STAGE_TEMPLATE_CROP_ID, True)),
            STAGE_RESIZE_LIMIT_ENABLED_KEY: bool(stage_enabled.get(STAGE_RESIZE_LIMIT_ID, True)),
            STAGE_TEMPLATE_OVERLAY_ENABLED_KEY: bool(stage_enabled.get(STAGE_TEMPLATE_OVERLAY_ID, True)),
            STAGE_FOCUS_OVERLAY_ENABLED_KEY: bool(stage_enabled.get(STAGE_FOCUS_OVERLAY_ID, True)),
            "max_long_edge": self._selected_max_long_edge(),
            "uniform_auto_crop": bool(self.uniform_auto_crop_check.isChecked()),
            "auto_crop_stabilization": int(self.auto_crop_stabilization_slider.value()),
        }

    def _refresh_global_export_settings_snapshot(self) -> bool:
        current = self._current_global_export_settings()
        previous = dict(getattr(self, "_last_global_export_settings", {}) or {})
        self._last_global_export_settings = current
        return current != previous

    def _mark_photo_export_dirty(self, path: Path | None) -> None:
        if path is None:
            return
        self._photo_export_dirty_keys.add(_path_key(path))

    def _mark_photo_exports_dirty(self, paths: Iterable[Path]) -> None:
        for path in paths:
            self._mark_photo_export_dirty(path)

    def _mark_all_photo_exports_dirty(self) -> None:
        self._mark_photo_exports_dirty(self._list_photo_paths())

    def _clear_photo_export_dirty(self, paths: Iterable[Path]) -> None:
        for path in paths:
            try:
                self._photo_export_dirty_keys.discard(_path_key(path))
            except Exception:
                continue

    def _dirty_photo_path_keys(self, paths: Iterable[Path] | None = None) -> set[str]:
        if paths is None:
            return set(self._photo_export_dirty_keys)
        return {_path_key(path) for path in paths if _path_key(path) in self._photo_export_dirty_keys}

    def _on_output_settings_changed(self, *_args: Any) -> None:
        global_changed = self._refresh_global_export_settings_snapshot()
        if global_changed:
            self._mark_all_photo_exports_dirty()
        if self.current_path is not None and not self._is_placeholder_active():
            key = _path_key(self.current_path)
            snapshot = self._photo_override_settings_from_snapshot(self._build_current_render_settings())
            previous_snapshot = self.photo_render_overrides.get(key)
            self._set_photo_crop_box_for_path(self.current_path, snapshot.get("crop_box"))
            self.photo_render_overrides[key] = snapshot
            if not global_changed and previous_snapshot != snapshot:
                self._mark_photo_export_dirty(self.current_path)
            self._update_photo_list_item_display(self.current_path, settings=snapshot)
            self._invalidate_original_mode_cache()
        self._preview_debounce_timer.start()
        self._schedule_workspace_autosave()

    def _start_bird_detector_preload(self) -> None:
        if self._bird_detector_preload_started:
            return
        self._bird_detector_preload_started = True

        def _worker() -> None:
            _load_bird_detector()

        thread = threading.Thread(
            target=_worker,
            name="birdstamp-bird-detector-preload",
            daemon=True,
        )
        self._bird_detector_preload_thread = thread
        thread.start()











    def _reload_template_combo(self, preferred: str | None) -> None:
        _ensure_template_repository(self.template_dir)
        names = _list_template_names(self.template_dir)
        self.template_paths = {name: self.template_dir / f"{name}.json" for name in names}

        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItems(names)
        self.template_combo.blockSignals(False)

        if not names:
            self.current_template_payload = _default_template_payload(name="default")
            return

        selected = preferred if preferred in self.template_paths else names[0]
        self.template_combo.setCurrentText(selected)
        self._load_selected_template(selected)
        self._apply_template_ratio_to_main_output()
        self._apply_template_output_settings_to_main_output()
        self._apply_template_crop_padding_to_main_output()

    def _load_selected_template(self, name: str) -> None:
        path = self.template_paths.get(name)
        if not path:
            return
        try:
            self.current_template_payload = _load_template_payload(path)
        except Exception as exc:
            self._show_error("模板错误", str(exc))
            self.current_template_payload = _default_template_payload(name="default")

    def _apply_template_ratio_to_main_output(self) -> None:
        ratio = _parse_ratio_value(self.current_template_payload.get("ratio"))
        idx = self._ratio_combo_index_for_value(ratio)
        if idx < 0:
            return
        self.ratio_combo.blockSignals(True)
        try:
            self.ratio_combo.setCurrentIndex(idx)
        finally:
            self.ratio_combo.blockSignals(False)

    def _reset_template_overrides(self) -> None:
        """将模板裁切 Stage 的所有选项恢复为当前模板中存储的值。"""
        self._apply_template_ratio_to_main_output()
        self._apply_template_output_settings_to_main_output()
        self._apply_template_crop_padding_to_main_output()
        self._invalidate_original_mode_cache()
        self._pending_preview_fit_reset = True
        if self.current_path:
            self._on_output_settings_changed()
        else:
            self.render_preview()
            self._schedule_workspace_autosave()

    def _apply_template_crop_padding_to_main_output(self) -> None:
        p = self.current_template_payload
        self._set_crop_padding_state(
            top=_parse_padding_value(p.get("crop_padding_top", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            bottom=_parse_padding_value(p.get("crop_padding_bottom", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            left=_parse_padding_value(p.get("crop_padding_left", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            right=_parse_padding_value(p.get("crop_padding_right", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            fill=_safe_color(str(p.get("crop_padding_fill", "#FFFFFF")), "#FFFFFF"),
        )
        cb = p.get("crop_box")
        if cb is not None and isinstance(cb, (list, tuple)) and len(cb) == 4:
            try:
                self._crop_box_override = (float(cb[0]), float(cb[1]), float(cb[2]), float(cb[3]))
            except (TypeError, ValueError):
                self._crop_box_override = None
        else:
            self._crop_box_override = None

    def _apply_template_output_settings_to_main_output(self) -> None:
        """将模板中的裁切中心 / 最大长边应用到主界面控件。"""
        p = self.current_template_payload

        center = _normalize_center_mode(p.get("center_mode", _DEFAULT_TEMPLATE_CENTER_MODE))
        self._set_center_mode_value(center, emit_changed=False)

        try:
            max_edge = max(0, int(p.get("max_long_edge") or _DEFAULT_TEMPLATE_MAX_LONG_EDGE))
        except Exception:
            max_edge = _DEFAULT_TEMPLATE_MAX_LONG_EDGE
        max_edge_idx = self._ensure_max_edge_option(max_edge)
        if max_edge_idx >= 0:
            self.max_edge_combo.blockSignals(True)
            try:
                self.max_edge_combo.setCurrentIndex(max_edge_idx)
            finally:
                self.max_edge_combo.blockSignals(False)

    def _on_template_changed(self, name: str) -> None:
        if not name:
            return
        self._load_selected_template(name)
        self._apply_template_ratio_to_main_output()
        self._apply_template_output_settings_to_main_output()
        self._apply_template_crop_padding_to_main_output()
        self._invalidate_original_mode_cache()
        if self.current_path:
            self._on_output_settings_changed()
        else:
            self.render_preview()
            self._schedule_workspace_autosave()

    def _open_template_manager(self) -> None:
        from app_common.log import get_logger
        from app_common.stat import stat_begin, stat_end, stat_report, stat_reset

        log = get_logger("template_manager")
        log.info("opening template manager dialog")
        stat_reset()
        stat_begin("template_manager_open")
        dialog = TemplateManagerDialog(template_dir=self.template_dir, placeholder=self.placeholder, parent=self)
        stat_end("template_manager_open")
        for line in stat_report(return_lines=True) or []:
            log.info(line)
        dialog.showMaximized()
        dialog.exec()
        preferred = dialog.current_template_name
        self._reload_template_combo(preferred=preferred)
        if self.current_path:
            settings = self._render_settings_for_path(self.current_path, prefer_current_ui=False)
            self._apply_render_settings_to_ui(settings)
            self.render_preview()
        self._schedule_workspace_autosave()

    def _pick_files(self) -> None:
        ext_pattern = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "添加照片",
            "",
            f"Images ({ext_pattern});;All Files (*.*)",
        )
        if not file_paths:
            return
        self._add_photo_paths([Path(item) for item in file_paths])

    def _pick_directory(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择目录", "")
        if not folder:
            return
        self._add_photo_paths([Path(folder)])

    def _format_ratio_display(self, ratio: Any) -> str:
        parsed = _parse_ratio_value(ratio)
        idx = self._ratio_combo_index_for_value(parsed)
        if idx >= 0:
            label = str(self.ratio_combo.itemText(idx) or "").strip()
            if label:
                return label
        if _is_ratio_no_crop(parsed):
            return "不裁切"
        if _is_ratio_free(parsed):
            return "自由"
        if parsed is None:
            return "原比例"
        text = f"{parsed:.4f}".rstrip("0").rstrip(".")
        return text or "原比例"

    def _ratio_sort_key(self, ratio: Any) -> tuple[int, float | str]:
        parsed = _parse_ratio_value(ratio)
        if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
            return (0, float(parsed))
        return (1, self._format_ratio_display(parsed))

    def _parse_display_capture_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (list, tuple)):
            for item in value:
                parsed = self._parse_display_capture_datetime(item)
                if parsed is not None:
                    return parsed
            return None
        if isinstance(value, dict):
            for item in value.values():
                parsed = self._parse_display_capture_datetime(item)
                if parsed is not None:
                    return parsed
            return None

        text = _clean_text(value) or str(value).strip()
        if not text:
            return None
        normalized = text.replace("T", " ").strip()
        if "." in normalized:
            normalized = normalized.split(".", 1)[0]
        for pattern in (
            "%Y:%m:%d %H:%M:%S%z",
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return datetime.strptime(normalized, pattern)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    def _photo_info_for_display(
        self,
        path: Path,
        *,
        raw_metadata: dict[str, Any] | None = None,
    ) -> _template_context.PhotoInfo:
        item = self._find_photo_item_by_path(path)
        existing = item.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE) if item is not None else None
        metadata = raw_metadata if isinstance(raw_metadata, dict) else self._load_raw_metadata(path)
        photo_info = _template_context.ensure_editor_photo_info(
            existing if isinstance(existing, _template_context.PhotoInfo) else path,
            raw_metadata=metadata,
        )
        if item is not None:
            item.setData(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE, photo_info)
        return photo_info

    def _photo_crop_box_for_path(self, path: Path | None) -> tuple[float, float, float, float] | None:
        if path is None:
            return _template_context.photo_crop_box(self.current_photo_info)
        key = _path_key(path)
        if self.current_path is not None and key == _path_key(self.current_path):
            current_crop_box = _template_context.photo_crop_box(self.current_photo_info)
            if current_crop_box is not None:
                return current_crop_box
        item = self._find_photo_item_by_path(path)
        if item is None:
            return None
        photo_info = item.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE)
        if not isinstance(photo_info, _template_context.PhotoInfo):
            return None
        return _template_context.photo_crop_box(photo_info)

    def _set_photo_crop_box_for_path(self, path: Path | None, crop_box: Any) -> None:
        if path is None:
            if isinstance(self.current_photo_info, _template_context.PhotoInfo):
                self.current_photo_info = _template_context.ensure_editor_photo_info(
                    self.current_photo_info,
                    crop_box=crop_box,
                )
            return

        updated_info: _template_context.PhotoInfo | None = None
        item = self._find_photo_item_by_path(path)
        if item is not None:
            photo_info = item.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE)
            updated_info = _template_context.ensure_editor_photo_info(
                photo_info if isinstance(photo_info, _template_context.PhotoInfo) else path,
                crop_box=crop_box,
            )
            item.setData(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE, updated_info)

        if self.current_path is not None and _path_key(path) == _path_key(self.current_path):
            base_info = updated_info if updated_info is not None else (
                self.current_photo_info if isinstance(self.current_photo_info, _template_context.PhotoInfo) else path
            )
            self.current_photo_info = _template_context.ensure_editor_photo_info(
                base_info,
                raw_metadata=self.current_raw_metadata if isinstance(self.current_raw_metadata, dict) else None,
                crop_box=crop_box,
            )

    def _provider_text_candidates(
        self,
        photo_info: _template_context.PhotoInfo,
        candidates: Iterable[str],
    ) -> str:
        for source_key in candidates:
            provider = _template_context.build_template_context_provider(
                _template_context.TEMPLATE_SOURCE_AUTO,
                source_key,
            )
            text = str(provider.get_text_content(photo_info) or "").strip()
            if text and text.upper() != _template_context.MISSING_TEMPLATE_TEXT:
                return text
        return ""

    def _display_filename_from_photo_info(self, photo_info: _template_context.PhotoInfo) -> str:
        return self._provider_text_candidates(
            photo_info,
            ["{filename}"],
        )

    def _extract_display_capture_time_from_metadata(
        self,
        photo_info: _template_context.PhotoInfo,
    ) -> tuple[str, tuple[int, float]]:
        capture_text = self._provider_text_candidates(
            photo_info,
            [
                "capture_text",
                "{capture_text}",
                "capture_date",
                "{capture_date}",
                "EXIF:DateTimeOriginal",
                "ExifIFD:DateTimeOriginal",
                "XMP-exif:DateTimeOriginal",
                "DateTimeOriginal",
                "EXIF:CreateDate",
                "XMP-xmp:CreateDate",
                "CreateDate",
                "DateTimeCreated",
                "DateCreated",
                "MediaCreateDate",
            ],
        )
        capture_dt = self._parse_display_capture_datetime(capture_text)
        if capture_dt is not None:
            try:
                sort_value = float(capture_dt.timestamp())
            except Exception:
                sort_value = 0.0
            return capture_dt.strftime("%Y-%m-%d %H:%M:%S"), (0, sort_value)
        return "-", (1, 0.0)

    def _extract_display_title_from_metadata(self, photo_info: _template_context.PhotoInfo) -> str:
        return self._provider_text_candidates(
            photo_info,
            [
                "bird_species_cn",
                "{bird_common}",
                "{bird}",
                "bird",
                "XMP:Title",
                "XMP-dc:Title",
                "IPTC:ObjectName",
                "IPTC:Headline",
                "EXIF:ImageDescription",
                "EXIF:XPTitle",
                "Image:Title",
                "Title",
                "ImageDescription",
            ],
        )

    def _extract_display_rating_from_metadata(self, photo_info: _template_context.PhotoInfo) -> int | None:
        def _value_to_rating(value: Any) -> int | None:
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                for item in value:
                    parsed = _value_to_rating(item)
                    if parsed is not None:
                        return parsed
                return None
            if isinstance(value, dict):
                for item in value.values():
                    parsed = _value_to_rating(item)
                    if parsed is not None:
                        return parsed
                return None

            text = _clean_text(value)
            if text:
                full_star_count = text.count("★")
                if full_star_count > 0:
                    return max(0, min(5, full_star_count))
            else:
                text = str(value).strip()

            number_match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
            if not number_match:
                return None
            try:
                raw_score = float(number_match.group(0))
            except Exception:
                return None
            if raw_score < 0:
                return None
            if raw_score > 5:
                raw_score = raw_score / 20.0
            score = int(round(raw_score))
            return max(0, min(5, score))

        rating_text = self._provider_text_candidates(
            photo_info,
            [
                "rating",
                "XMP:Rating",
                "XMP-xmp:Rating",
                "EXIF:Rating",
                "Composite:Rating",
                "Rating",
            ],
        )
        parsed = _value_to_rating(rating_text)
        if parsed is not None:
            return parsed

        for key, value in (_template_context.ensure_photo_info(photo_info).raw_metadata or {}).items():
            key_text = str(key or "").strip().lower()
            if "rating" not in key_text:
                continue
            parsed = _value_to_rating(value)
            if parsed is not None:
                return parsed
        return None

    def _format_rating_display(self, rating: int | None) -> str:
        if rating is None:
            return "-"
        stars = max(0, min(5, int(rating)))
        if stars <= 0:
            return "-"
        return "★" * stars

    def _extract_display_camera_settings_from_metadata(
        self,
        photo_info: _template_context.PhotoInfo,
    ) -> tuple[str, tuple[int, float], str, tuple[int, int], str, tuple[int, float]]:
        raw_metadata = _template_context.ensure_photo_info(photo_info).raw_metadata or {}
        return self._camera_list_display_from_raw_metadata(
            raw_metadata if isinstance(raw_metadata, dict) else {}
        )

    def _camera_list_display_from_raw_metadata(
        self,
        raw_metadata: dict[str, Any],
    ) -> tuple[str, tuple[int, float], str, tuple[int, int], str, tuple[int, float]]:
        lookup = _normalize_lookup(raw_metadata) if isinstance(raw_metadata, dict) else {}

        def _first_value(*keys: str) -> Any:
            for key in keys:
                value = lookup.get(str(key or "").strip().lower())
                if value not in (None, "", " "):
                    return value
            return None

        def _parse_positive_float(value: Any) -> float | None:
            text = _clean_text(value) or str(value or "").strip()
            if not text:
                return None
            normalized = text.lower().replace("seconds", "").replace("second", "").replace("sec", "").strip()
            if normalized.startswith("f/"):
                normalized = normalized[2:].strip()
            if normalized.endswith("s"):
                normalized = normalized[:-1].strip()
            if "(" in normalized and ")" in normalized:
                normalized = normalized.split("(", 1)[0].strip()
            if not normalized:
                return None
            if "/" in normalized:
                left, _, right = normalized.partition("/")
                try:
                    numerator = float(left.strip())
                    denominator = float(right.strip())
                except Exception:
                    return None
                if denominator == 0:
                    return None
                parsed = numerator / denominator
            else:
                try:
                    parsed = float(normalized)
                except Exception:
                    return None
            return parsed if parsed > 0 else None

        def _parse_optional_int(value: Any) -> int | None:
            text = _clean_text(value) or str(value or "").strip()
            if not text:
                return None
            normalized = text.strip()
            if normalized.upper().startswith("ISO"):
                normalized = normalized[3:].strip()
            try:
                parsed = int(float(normalized))
            except Exception:
                return None
            return parsed if parsed >= 0 else None

        shutter_raw = _first_value(
            "ExifIFD:ExposureTime",
            "EXIF:ExposureTime",
            "XMP-exif:ExposureTime",
            "Composite:ShutterSpeed",
            "ExposureTime",
            "ShutterSpeed",
        )
        iso_raw = _first_value(
            "ExifIFD:ISO",
            "EXIF:ISO",
            "XMP-exif:PhotographicSensitivity",
            "XMP-exif:ISOSpeedRatings",
            "ISO",
            "PhotographicSensitivity",
            "ISOSpeedRatings",
        )
        aperture_raw = _first_value(
            "ExifIFD:FNumber",
            "EXIF:FNumber",
            "XMP-exif:FNumber",
            "Composite:Aperture",
            "FNumber",
            "Aperture",
            "ApertureValue",
        )

        shutter_seconds = _parse_positive_float(shutter_raw)
        iso_value = _parse_optional_int(iso_raw)
        aperture_value = _parse_positive_float(aperture_raw)

        if shutter_seconds is None:
            shutter_text = _clean_text(shutter_raw) or "-"
        elif shutter_seconds < 1:
            denominator = round(1.0 / shutter_seconds) if shutter_seconds > 0 else 0
            shutter_text = f"1/{denominator}s" if denominator > 0 else "-"
        else:
            shutter_text = f"{shutter_seconds:g}s"

        iso_text = str(iso_value) if iso_value is not None else (_clean_text(iso_raw) or "-")
        aperture_text = f"f/{aperture_value:g}" if aperture_value is not None else (_clean_text(aperture_raw) or "-")

        return (
            shutter_text,
            (0, shutter_seconds) if shutter_seconds is not None else (1, 0.0),
            iso_text,
            (0, iso_value) if iso_value is not None else (1, 0),
            aperture_text,
            (0, aperture_value) if aperture_value is not None else (1, 0.0),
        )

    def _fast_display_capture_time_from_raw(
        self,
        raw_metadata: dict[str, Any],
    ) -> tuple[str, tuple[int, float]]:
        lookup = _normalize_lookup(raw_metadata)
        for key in (
            "exififd:datetimeoriginal",
            "exif:datetimeoriginal",
            "xmp-exif:datetimeoriginal",
            "datetimeoriginal",
            "exif:createdate",
            "xmp-xmp:createdate",
            "createdate",
            "datetimecreated",
            "datecreated",
            "mediacreatedate",
            "capture_text",
            "capture_date",
        ):
            capture_text = _clean_text(lookup.get(key))
            if not capture_text:
                continue
            capture_dt = self._parse_display_capture_datetime(capture_text)
            if capture_dt is not None:
                try:
                    sort_value = float(capture_dt.timestamp())
                except Exception:
                    sort_value = 0.0
                return capture_dt.strftime("%Y-%m-%d %H:%M:%S"), (0, sort_value)
        return "-", (1, 0.0)

    def _fast_display_title_from_raw(self, raw_metadata: dict[str, Any]) -> str:
        lookup = _normalize_lookup(raw_metadata)
        for key in (
            "bird_species_cn",
            "xmp:title",
            "xmp-dc:title",
            "iptc:objectname",
            "iptc:headline",
            "exif:imagedescription",
            "exif:xptitle",
            "image:title",
            "title",
            "imagedescription",
        ):
            text = _clean_text(lookup.get(key))
            if text:
                return text
        return ""

    def _fast_display_rating_from_raw(self, raw_metadata: dict[str, Any]) -> int | None:
        lookup = _normalize_lookup(raw_metadata)
        for key in (
            "rating",
            "xmp:rating",
            "xmp-xmp:rating",
            "exif:rating",
            "composite:rating",
        ):
            parsed = self._parse_display_rating_value(lookup.get(key))
            if parsed is not None:
                return parsed
        for key, value in raw_metadata.items():
            if "rating" not in str(key or "").strip().lower():
                continue
            parsed = self._parse_display_rating_value(value)
            if parsed is not None:
                return parsed
        return None

    def _parse_display_rating_value(self, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            for item in value:
                parsed = self._parse_display_rating_value(item)
                if parsed is not None:
                    return parsed
            return None
        if isinstance(value, dict):
            for item in value.values():
                parsed = self._parse_display_rating_value(item)
                if parsed is not None:
                    return parsed
            return None

        text = _clean_text(value)
        if text:
            full_star_count = text.count("★")
            if full_star_count > 0:
                return max(0, min(5, full_star_count))
        else:
            text = str(value).strip()

        number_match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        if not number_match:
            return None
        try:
            raw_score = float(number_match.group(0))
        except Exception:
            return None
        if raw_score < 0:
            return None
        if raw_score > 5:
            raw_score = raw_score / 20.0
        score = int(round(raw_score))
        return max(0, min(5, score))

    def _remember_photo_item(self, path: Path, item: QTreeWidgetItem) -> None:
        self._photo_item_map[_path_key(path)] = item

    def _forget_photo_item(self, path: Path | str) -> None:
        try:
            key = _path_key(path if isinstance(path, Path) else Path(str(path)))
        except Exception:
            return
        self._photo_item_map.pop(key, None)
        self.photo_list_metadata_cache.pop(key, None)
        self._photo_list_metadata_pending_keys.discard(key)

    def _photo_list_display_metadata_for_path(self, path: Path) -> dict[str, Any]:
        key = _path_key(path)
        cached = self.raw_metadata_cache.get(key)
        if isinstance(cached, dict) and cached:
            return cached
        cached = self.photo_list_metadata_cache.get(key)
        if isinstance(cached, dict) and cached:
            return cached
        return {"SourceFile": str(path)}

    def _set_photo_list_progress(self, current: int, total: int) -> None:
        total_value = max(0, int(total))
        current_value = max(0, min(int(current), max(1, total_value)))
        self.photo_list_progress.setMaximum(max(1, total_value))
        self.photo_list_progress.setValue(current_value)
        self.photo_list_progress.setFormat(f"照片信息 {current_value}/{total_value}")
        if total_value > 0:
            self.photo_list_progress.show()

    def _reset_photo_list_progress(self) -> None:
        self.photo_list_progress.setMaximum(1)
        self.photo_list_progress.setValue(0)
        self.photo_list_progress.setFormat("照片信息 0/0")
        self.photo_list_progress.hide()

    def _set_receive_progress(self, phase_text: str, current: int, total: int) -> None:
        total_value = max(0, int(total))
        current_value = max(0, min(int(current), max(1, total_value)))
        self.receive_progress.setMaximum(max(1, total_value))
        self.receive_progress.setValue(current_value)
        self.receive_progress.setFormat(f"{phase_text} {current_value}/{total_value}")
        if total_value > 0:
            self.receive_progress.show()
        self._receive_progress_reset_token += 1

    def _reset_receive_progress(self) -> None:
        self.receive_progress.setMaximum(1)
        self.receive_progress.setValue(0)
        self.receive_progress.setFormat("热接收 0/0")
        self.receive_progress.hide()

    def _schedule_receive_progress_reset(self) -> None:
        self._receive_progress_reset_token += 1
        token = self._receive_progress_reset_token
        QTimer.singleShot(
            _RECEIVE_PROGRESS_HIDE_DELAY_MS,
            lambda expected_token=token: self._reset_receive_progress()
            if (
                expected_token == self._receive_progress_reset_token
                and not self._received_photo_import_pending_paths
                and not self._received_photo_import_timer.isActive()
            )
            else None,
        )

    def _emit_received_photo_import_progress(
        self,
        phase: str,
        current: int,
        total: int,
        *,
        message: str = "",
    ) -> None:
        payload = {
            "phase": str(phase or "").strip(),
            "current": max(0, int(current)),
            "total": max(0, int(total)),
            "message": str(message or "").strip(),
        }
        self._apply_receive_transfer_progress(dict(payload))
        for callback in list(self._received_photo_import_progress_callbacks):
            try:
                callback(dict(payload))
            except Exception:
                pass

    def _set_photo_list_header_fast_mode(self, enabled: bool) -> None:
        if enabled == self._photo_list_header_fast_mode:
            return
        header = self.photo_list.header()
        try:
            if enabled:
                header.setSectionResizeMode(PHOTO_COL_CAPTURE_TIME, QHeaderView.ResizeMode.Interactive)
                header.setSectionResizeMode(PHOTO_COL_TITLE, QHeaderView.ResizeMode.Interactive)
                header.setSectionResizeMode(PHOTO_COL_RATIO, QHeaderView.ResizeMode.Interactive)
                header.setSectionResizeMode(PHOTO_COL_RATING, QHeaderView.ResizeMode.Interactive)
                header.setSectionResizeMode(PHOTO_COL_SHUTTER, QHeaderView.ResizeMode.Interactive)
                header.setSectionResizeMode(PHOTO_COL_ISO, QHeaderView.ResizeMode.Interactive)
                header.setSectionResizeMode(PHOTO_COL_APERTURE, QHeaderView.ResizeMode.Interactive)
                self._photo_list_header_fast_mode = True
                return
            header.setSectionResizeMode(PHOTO_COL_SEQ, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(PHOTO_COL_NAME, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(PHOTO_COL_CAPTURE_TIME, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(PHOTO_COL_TITLE, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(PHOTO_COL_RATIO, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(PHOTO_COL_RATING, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(PHOTO_COL_SHUTTER, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(PHOTO_COL_ISO, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(PHOTO_COL_APERTURE, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(PHOTO_COL_ROW, QHeaderView.ResizeMode.Fixed)
            self._photo_list_header_fast_mode = False
        except Exception:
            pass

    def _detach_photo_list_metadata_loader(
        self,
        loader: EditorPhotoListMetadataLoader,
        *,
        wait: bool,
    ) -> None:
        loader.stop()
        try:
            loader.metadata_batch_ready.disconnect(self._on_photo_list_metadata_batch_ready)
        except Exception:
            pass
        try:
            loader.progress_updated.disconnect(self._on_photo_list_metadata_progress)
        except Exception:
            pass
        try:
            loader.finished.disconnect(self._on_photo_list_metadata_loader_finished)
        except Exception:
            pass
        if wait:
            try:
                if loader.isRunning():
                    loader.wait(2500)
            except Exception:
                pass
            return
        self._pending_photo_list_metadata_loaders.append(loader)
        try:
            loader.finished.connect(
                lambda ldr=loader: (
                    self._pending_photo_list_metadata_loaders.remove(ldr)
                    if ldr in self._pending_photo_list_metadata_loaders else None
                )
            )
        except Exception:
            pass

    def _stop_photo_list_metadata_loader(
        self,
        *,
        wait: bool = False,
        reset_progress: bool = False,
    ) -> None:
        loader = self._photo_list_metadata_loader
        self._photo_list_metadata_loader = None
        if loader is not None:
            self._detach_photo_list_metadata_loader(loader, wait=wait)
        if wait and self._pending_photo_list_metadata_loaders:
            pending = list(self._pending_photo_list_metadata_loaders)
            self._pending_photo_list_metadata_loaders.clear()
            for worker in pending:
                try:
                    if worker.isRunning():
                        worker.wait(2500)
                except Exception:
                    pass
        if reset_progress:
            self._photo_list_metadata_loading = False
            self._set_photo_list_header_fast_mode(False)
            self.photo_list.setSortingEnabled(True)
            self._reset_photo_list_progress()

    def _restart_photo_list_metadata_loader(self) -> None:
        pending_paths = [
            path for path in self._list_photo_paths()
            if _path_key(path) in self._photo_list_metadata_pending_keys
        ]
        self._stop_photo_list_metadata_loader(wait=False, reset_progress=False)
        if not pending_paths:
            self._finish_photo_list_metadata_loading()
            return
        self._photo_list_metadata_loading = True
        self._set_photo_list_header_fast_mode(True)
        self.photo_list.setSortingEnabled(False)
        self._set_photo_list_progress(0, len(pending_paths))
        loader = EditorPhotoListMetadataLoader([str(path) for path in pending_paths], parent=self)
        loader.metadata_batch_ready.connect(self._on_photo_list_metadata_batch_ready)
        loader.progress_updated.connect(self._on_photo_list_metadata_progress)
        loader.finished.connect(self._on_photo_list_metadata_loader_finished)
        self._photo_list_metadata_loader = loader
        loader.start()

    def _finish_photo_list_metadata_loading(self) -> None:
        self._photo_list_metadata_loading = False
        self._set_photo_list_header_fast_mode(False)
        self.photo_list.setSortingEnabled(True)
        self.photo_list.resort()
        self.photo_list.refresh_row_numbers()
        if self.photo_list_progress.maximum() > 0:
            self.photo_list_progress.setValue(self.photo_list_progress.maximum())
            QTimer.singleShot(_PHOTO_LIST_META_PROGRESS_HIDE_DELAY_MS, self._reset_photo_list_progress)
        else:
            self._reset_photo_list_progress()

    def _apply_photo_list_metadata_batch(self, batch: dict[str, dict[str, Any]]) -> None:
        if not batch:
            return
        self._begin_photo_list_item_display_batch()
        try:
            for norm_path, raw_metadata in batch.items():
                path = Path(norm_path)
                key = _path_key(path)
                if not self._find_photo_item_by_path(path):
                    self._forget_photo_item(path)
                    continue
                if isinstance(raw_metadata, dict):
                    merged = dict(raw_metadata)
                    self.photo_list_metadata_cache[key] = merged
                    self.raw_metadata_cache[key] = merged
                self._photo_list_metadata_pending_keys.discard(key)
                settings = self.photo_render_overrides.get(key)
                self._update_photo_list_item_display(
                    path,
                    raw_metadata=raw_metadata,
                    settings=settings,
                    resort=False,
                )
        finally:
            self._end_photo_list_item_display_batch(resort=False)
        self._maybe_apply_pending_workspace_photo_selection()

    def _on_photo_list_metadata_batch_ready(self, batch: dict[str, dict[str, Any]]) -> None:
        if self.sender() is not self._photo_list_metadata_loader:
            return
        self._apply_photo_list_metadata_batch(batch)

    def _on_photo_list_metadata_progress(self, current: int, total: int) -> None:
        if self.sender() is not self._photo_list_metadata_loader:
            return
        self._set_photo_list_progress(current, total)

    def _on_photo_list_metadata_loader_finished(self) -> None:
        if self.sender() is not self._photo_list_metadata_loader:
            return
        self._photo_list_metadata_loader = None
        self._finish_photo_list_metadata_loading()
        self._maybe_apply_pending_workspace_photo_selection()

    def _reset_received_photo_import_state(self) -> None:
        self._received_photo_import_pending_paths.clear()
        self._received_photo_import_total = 0
        self._received_photo_import_processed = 0
        self._received_photo_import_added = 0
        self._received_photo_import_auto_report_db_count = 0
        self._received_photo_import_last_added_item = None
        self._received_photo_import_added_paths = []
        self._received_photo_import_completion_callbacks = []
        self._received_photo_import_progress_callbacks = []
        self._received_photo_import_existing_keys = set()
        self._received_photo_import_default_settings = None
        self._received_photo_import_select_last_added = False

    def _stop_received_photo_import(self, *, reset_progress: bool = False) -> None:
        if self._received_photo_import_timer.isActive():
            self._received_photo_import_timer.stop()
        self._reset_received_photo_import_state()
        if reset_progress:
            self._reset_receive_progress()

    def _next_photo_sequence_value(self) -> int:
        if self._next_photo_sequence_number <= 0:
            next_value = 1
            for idx in range(self.photo_list.topLevelItemCount()):
                item = self.photo_list.topLevelItem(idx)
                if item is None:
                    continue
                raw_value = item.data(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE)
                try:
                    candidate = int(raw_value)
                except Exception:
                    continue
                next_value = max(next_value, candidate + 1)
            self._next_photo_sequence_number = next_value
        sequence_value = self._next_photo_sequence_number
        self._next_photo_sequence_number += 1
        return sequence_value

    def _find_photo_item_by_path(self, path: Path) -> QTreeWidgetItem | None:
        key = _path_key(path)
        item = self._photo_item_map.get(key)
        if item is not None:
            return item
        for idx in range(self.photo_list.topLevelItemCount()):
            item = self.photo_list.topLevelItem(idx)
            if item is None:
                continue
            raw = item.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
            if isinstance(raw, str) and _path_key(Path(raw)) == key:
                self._photo_item_map[key] = item
                return item
        return None

    def _update_photo_list_item_display(
        self,
        path: Path,
        *,
        raw_metadata: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        resort: bool = True,
    ) -> None:
        item = self._find_photo_item_by_path(path)
        if item is None:
            return

        metadata = (
            raw_metadata
            if isinstance(raw_metadata, dict)
            else self._photo_list_display_metadata_for_path(path)
        )
        use_fast_display = _is_complete_list_metadata(metadata)
        photo_info = self._photo_info_for_display(path, raw_metadata=metadata)
        filename_text = self._display_filename_from_photo_info(photo_info) or path.name
        if use_fast_display:
            capture_time_text, capture_time_sort = self._fast_display_capture_time_from_raw(metadata)
            title = self._fast_display_title_from_raw(metadata)
            rating_value = self._fast_display_rating_from_raw(metadata)
            (
                shutter_text,
                shutter_sort,
                iso_text,
                iso_sort,
                aperture_text,
                aperture_sort,
            ) = self._camera_list_display_from_raw_metadata(metadata)
        else:
            capture_time_text, capture_time_sort = self._extract_display_capture_time_from_metadata(photo_info)
            title = self._extract_display_title_from_metadata(photo_info)
            rating_value = self._extract_display_rating_from_metadata(photo_info)
            (
                shutter_text,
                shutter_sort,
                iso_text,
                iso_sort,
                aperture_text,
                aperture_sort,
            ) = self._extract_display_camera_settings_from_metadata(photo_info)
        rating_text = self._format_rating_display(rating_value)
        active_settings = (
            settings
            if isinstance(settings, dict)
            else self._render_settings_for_path(path, prefer_current_ui=False)
        )
        ratio_value = _parse_ratio_value(active_settings.get("ratio"))
        ratio_text = self._format_ratio_display(ratio_value)

        in_batch = int(getattr(self, "_photo_list_display_batch_depth", 0)) > 0
        paused_sort = False
        if not resort and not in_batch and self.photo_list.isSortingEnabled():
            self.photo_list.setSortingEnabled(False)
            paused_sort = True
        try:
            item.setText(PHOTO_COL_NAME, filename_text)
            item.setText(PHOTO_COL_CAPTURE_TIME, capture_time_text)
            item.setText(PHOTO_COL_TITLE, title or "-")
            item.setText(PHOTO_COL_RATIO, ratio_text)
            item.setText(PHOTO_COL_RATING, rating_text)
            item.setText(PHOTO_COL_SHUTTER, shutter_text)
            item.setText(PHOTO_COL_ISO, iso_text)
            item.setText(PHOTO_COL_APERTURE, aperture_text)
            item.setToolTip(PHOTO_COL_NAME, str(path))
            item.setToolTip(PHOTO_COL_CAPTURE_TIME, capture_time_text if capture_time_text != "-" else "")
            item.setToolTip(PHOTO_COL_TITLE, title or "")
            item.setToolTip(PHOTO_COL_RATIO, ratio_text)
            item.setToolTip(PHOTO_COL_RATING, rating_text)
            item.setToolTip(PHOTO_COL_SHUTTER, shutter_text if shutter_text != "-" else "")
            item.setToolTip(PHOTO_COL_ISO, iso_text if iso_text != "-" else "")
            item.setToolTip(PHOTO_COL_APERTURE, aperture_text if aperture_text != "-" else "")
            item.setTextAlignment(PHOTO_COL_CAPTURE_TIME, int(Qt.AlignmentFlag.AlignCenter))
            item.setTextAlignment(PHOTO_COL_RATIO, int(Qt.AlignmentFlag.AlignCenter))
            item.setTextAlignment(PHOTO_COL_RATING, int(Qt.AlignmentFlag.AlignCenter))
            item.setTextAlignment(PHOTO_COL_SHUTTER, int(Qt.AlignmentFlag.AlignCenter))
            item.setTextAlignment(PHOTO_COL_ISO, int(Qt.AlignmentFlag.AlignCenter))
            item.setTextAlignment(PHOTO_COL_APERTURE, int(Qt.AlignmentFlag.AlignCenter))
            item.setData(PHOTO_COL_NAME, PHOTO_LIST_SORT_ROLE, (0, filename_text.casefold()))
            item.setData(PHOTO_COL_CAPTURE_TIME, PHOTO_LIST_SORT_ROLE, capture_time_sort)
            item.setData(PHOTO_COL_TITLE, PHOTO_LIST_SORT_ROLE, (0, title.casefold()) if title else (1, ""))
            item.setData(
                PHOTO_COL_RATIO,
                PHOTO_LIST_SORT_ROLE,
                self._ratio_sort_key(ratio_value),
            )
            item.setData(
                PHOTO_COL_RATING,
                PHOTO_LIST_SORT_ROLE,
                (0, int(rating_value)) if rating_value is not None else (1, 0),
            )
            item.setData(PHOTO_COL_SHUTTER, PHOTO_LIST_SORT_ROLE, shutter_sort)
            item.setData(PHOTO_COL_ISO, PHOTO_LIST_SORT_ROLE, iso_sort)
            item.setData(PHOTO_COL_APERTURE, PHOTO_LIST_SORT_ROLE, aperture_sort)
        finally:
            if paused_sort:
                self.photo_list.setSortingEnabled(True)
        if resort and not in_batch:
            self.photo_list.resort()

    def _list_photo_paths(self) -> list[Path]:
        paths: list[Path] = []
        for idx in range(self.photo_list.topLevelItemCount()):
            item = self.photo_list.topLevelItem(idx)
            if not item:
                continue
            raw = item.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
            if isinstance(raw, str):
                paths.append(Path(raw))
        return paths

    @staticmethod
    def _filter_supported_photo_paths(paths: Iterable[Path]) -> list[Path]:
        valid_paths: list[Path] = []
        for incoming in paths:
            try:
                path = incoming if isinstance(incoming, Path) else Path(str(incoming))
                path = path.resolve(strict=False)
            except Exception:
                continue
            if (
                not path.is_file()
                or is_apple_double_metadata_file(path)
                or path.suffix.lower() not in SUPPORTED_EXTENSIONS
            ):
                continue
            valid_paths.append(path)
        return valid_paths

    def _append_photo_path_to_list(
        self,
        path: Path,
        *,
        existing_keys: set[str],
        default_settings: dict[str, Any],
        sequence_value: int | None = None,
    ) -> tuple[bool, QTreeWidgetItem | None]:
        key = _path_key(path)
        if key in existing_keys:
            return (False, None)
        existing_keys.add(key)

        current_settings = self._photo_override_settings_from_snapshot(default_settings)
        self.photo_render_overrides[key] = current_settings
        self._photo_export_dirty_keys.add(key)
        item = PhotoListItem(["", "", "", "", "", "", "", "", "", ""])
        if sequence_value is None or sequence_value <= 0:
            sequence_value = self._next_photo_sequence_value()
        placeholder_metadata = {"SourceFile": str(path)}
        ratio_text = self._format_ratio_display(_parse_ratio_value(current_settings.get("ratio")))
        item.setText(PHOTO_COL_SEQ, str(sequence_value))
        item.setTextAlignment(PHOTO_COL_SEQ, int(Qt.AlignmentFlag.AlignCenter))
        item.setToolTip(PHOTO_COL_SEQ, str(sequence_value))
        item.setText(PHOTO_COL_NAME, path.name)
        item.setText(PHOTO_COL_CAPTURE_TIME, "-")
        item.setText(PHOTO_COL_TITLE, "-")
        item.setText(PHOTO_COL_RATIO, ratio_text)
        item.setText(PHOTO_COL_RATING, "-")
        item.setText(PHOTO_COL_SHUTTER, "-")
        item.setText(PHOTO_COL_ISO, "-")
        item.setText(PHOTO_COL_APERTURE, "-")
        item.setData(PHOTO_COL_SEQ, PHOTO_LIST_SORT_ROLE, (0, sequence_value))
        item.setData(PHOTO_COL_NAME, PHOTO_LIST_SORT_ROLE, (0, path.name.casefold()))
        item.setData(PHOTO_COL_CAPTURE_TIME, PHOTO_LIST_SORT_ROLE, (1, 0.0))
        item.setData(PHOTO_COL_TITLE, PHOTO_LIST_SORT_ROLE, (1, ""))
        item.setData(
            PHOTO_COL_RATIO,
            PHOTO_LIST_SORT_ROLE,
            self._ratio_sort_key(current_settings.get("ratio")),
        )
        item.setData(PHOTO_COL_RATING, PHOTO_LIST_SORT_ROLE, (1, 0))
        item.setData(PHOTO_COL_SHUTTER, PHOTO_LIST_SORT_ROLE, (1, 0.0))
        item.setData(PHOTO_COL_ISO, PHOTO_LIST_SORT_ROLE, (1, 0))
        item.setData(PHOTO_COL_APERTURE, PHOTO_LIST_SORT_ROLE, (1, 0.0))
        item.setData(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE, str(path))
        item.setData(
            PHOTO_COL_ROW,
            PHOTO_LIST_PHOTO_INFO_ROLE,
            _template_context.ensure_editor_photo_info(
                path,
                raw_metadata=placeholder_metadata,
                sidecar_path="",
                crop_box=current_settings.get("crop_box"),
                editor_row_number=sequence_value,
            ),
        )
        item.setData(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE, sequence_value)
        item.setData(PHOTO_COL_ROW, PHOTO_LIST_DISPLAY_ROW_ROLE, sequence_value)
        item.setData(PHOTO_COL_ROW, PHOTO_LIST_SORT_ROLE, (0, sequence_value))
        item.setToolTip(PHOTO_COL_NAME, str(path))
        item.setToolTip(PHOTO_COL_RATIO, ratio_text)
        item.setToolTip(PHOTO_COL_ROW, "")
        item.setTextAlignment(PHOTO_COL_ROW, int(Qt.AlignmentFlag.AlignCenter))
        item.setTextAlignment(PHOTO_COL_CAPTURE_TIME, int(Qt.AlignmentFlag.AlignCenter))
        item.setTextAlignment(PHOTO_COL_RATIO, int(Qt.AlignmentFlag.AlignCenter))
        item.setTextAlignment(PHOTO_COL_RATING, int(Qt.AlignmentFlag.AlignCenter))
        item.setTextAlignment(PHOTO_COL_SHUTTER, int(Qt.AlignmentFlag.AlignCenter))
        item.setTextAlignment(PHOTO_COL_ISO, int(Qt.AlignmentFlag.AlignCenter))
        item.setTextAlignment(PHOTO_COL_APERTURE, int(Qt.AlignmentFlag.AlignCenter))
        self.photo_list.addTopLevelItem(item)
        self._remember_photo_item(path, item)
        self.photo_list_metadata_cache[key] = placeholder_metadata
        self._photo_list_metadata_pending_keys.add(key)
        return (True, item)

    @staticmethod
    def _split_photo_input_paths(paths: Iterable[Path]) -> tuple[list[Path], list[Path]]:
        file_paths: list[Path] = []
        directory_paths: list[Path] = []
        seen_files: set[str] = set()
        seen_directories: set[str] = set()
        for incoming in paths:
            try:
                path = incoming if isinstance(incoming, Path) else Path(str(incoming))
                path = path.resolve(strict=False)
            except Exception:
                continue
            if path.is_dir():
                key = _path_key(path)
                if key not in seen_directories:
                    seen_directories.add(key)
                    directory_paths.append(path)
                continue
            if (
                not path.is_file()
                or is_apple_double_metadata_file(path)
                or path.suffix.lower() not in SUPPORTED_EXTENSIONS
            ):
                continue
            key = _path_key(path)
            if key in seen_files:
                continue
            seen_files.add(key)
            file_paths.append(path)
        return (file_paths, directory_paths)

    def _start_photo_input_discovery(
        self,
        directory_paths: Iterable[Path],
        *,
        select_last_added: bool = False,
        pre_added_report_db_count: int = 0,
    ) -> None:
        directories = list(directory_paths)
        if not directories:
            return
        worker = _PhotoInputDiscoveryWorker(directories, parent=self)
        worker_id = id(worker)
        self._photo_input_discovery_workers.append(worker)
        self._photo_input_discovery_import_options[worker_id] = {
            "select_last_added": bool(select_last_added),
            "pre_added_report_db_count": max(0, int(pre_added_report_db_count)),
        }
        worker.paths_ready.connect(self._on_photo_input_discovery_paths_ready)
        worker.progress_updated.connect(self._on_photo_input_discovery_progress)
        worker.finished_discovery.connect(
            lambda found_count, active_worker=worker: self._on_photo_input_discovery_finished(active_worker, found_count)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._set_status(f"正在扫描 {len(directories)} 个目录...")

    def _stop_photo_input_discovery_workers(self, *, wait: bool = False) -> None:
        workers = list(self._photo_input_discovery_workers)
        self._photo_input_discovery_workers.clear()
        self._photo_input_discovery_import_options.clear()
        for worker in workers:
            try:
                worker.paths_ready.disconnect(self._on_photo_input_discovery_paths_ready)
            except Exception:
                pass
            try:
                worker.progress_updated.disconnect(self._on_photo_input_discovery_progress)
            except Exception:
                pass
            worker.stop()
        if wait:
            for worker in workers:
                if worker.isRunning():
                    worker.wait(3000)

    def _on_photo_input_discovery_paths_ready(self, paths: object) -> None:
        sender = self.sender()
        if isinstance(sender, _PhotoInputDiscoveryWorker) and sender not in self._photo_input_discovery_workers:
            return
        path_batch = [Path(path) for path in paths] if isinstance(paths, list) else []
        if not path_batch:
            return
        options = self._photo_input_discovery_import_options.get(id(sender), {}) if sender is not None else {}
        pre_added_report_db_count = max(0, int(options.get("pre_added_report_db_count") or 0))
        if options:
            options["pre_added_report_db_count"] = 0
        auto_added_report_db_count = self._auto_add_report_db_paths_for_photos(path_batch)
        self._enqueue_received_photo_paths(
            path_batch,
            select_last_added=bool(options.get("select_last_added")),
            pre_added_report_db_count=pre_added_report_db_count + auto_added_report_db_count,
        )

    def _on_photo_input_discovery_progress(self, found_count: int) -> None:
        sender = self.sender()
        if isinstance(sender, _PhotoInputDiscoveryWorker) and sender not in self._photo_input_discovery_workers:
            return
        if found_count > 0 and not self._received_photo_import_pending_paths and not self._received_photo_import_timer.isActive():
            self._set_status(f"正在扫描目录，已发现 {found_count} 张照片...")

    def _on_photo_input_discovery_finished(self, worker: _PhotoInputDiscoveryWorker, found_count: int) -> None:
        worker_id = id(worker)
        was_active = worker in self._photo_input_discovery_workers
        options = self._photo_input_discovery_import_options.pop(worker_id, {})
        if not was_active and not options:
            return
        if was_active:
            self._photo_input_discovery_workers.remove(worker)
        pre_added_report_db_count = max(0, int(options.get("pre_added_report_db_count") or 0))
        if pre_added_report_db_count > 0:
            self._enqueue_received_photo_paths([], pre_added_report_db_count=pre_added_report_db_count)
        if self._photo_input_discovery_workers:
            return
        if self._received_photo_import_pending_paths:
            if not self._received_photo_import_timer.isActive():
                self._received_photo_import_timer.start()
            return
        if self._received_photo_import_timer.isActive():
            return
        if (
            self._received_photo_import_default_settings is not None
            or self._received_photo_import_total > 0
            or self._received_photo_import_auto_report_db_count > 0
        ):
            self._finish_received_photo_import()
            return
        if found_count == 0:
            self._set_status("目录中没有支持的图片文件。")

    def _begin_received_photo_import(self) -> None:
        if self._received_photo_import_default_settings is not None:
            return
        self._received_photo_import_existing_keys = {_path_key(path) for path in self._list_photo_paths()}
        self._received_photo_import_default_settings = self._build_current_render_settings()
        self.photo_list.setSortingEnabled(False)

    def _finish_received_photo_import(self) -> None:
        if self._received_photo_import_timer.isActive():
            self._received_photo_import_timer.stop()
        add_count = int(self._received_photo_import_added)
        total_auto_added_report_db_count = int(self._received_photo_import_auto_report_db_count)
        last_added_item = self._received_photo_import_last_added_item
        added_paths = list(self._received_photo_import_added_paths)
        completion_callbacks = list(self._received_photo_import_completion_callbacks)
        progress_total = max(self._received_photo_import_total, self._received_photo_import_processed)

        if self._received_photo_import_select_last_added and last_added_item is not None:
            self.photo_list.setCurrentItem(last_added_item)
        elif self.photo_list.currentItem() is None and self.photo_list.topLevelItemCount() > 0:
            first_item = self.photo_list.topLevelItem(0)
            if first_item is not None:
                self.photo_list.setCurrentItem(first_item)

        self.photo_list.setSortingEnabled(True)
        self.photo_list.resort()
        self.photo_list.refresh_row_numbers()
        if added_paths:
            self._restart_photo_list_metadata_loader()
        self._schedule_workspace_autosave()

        if progress_total > 0:
            self._emit_received_photo_import_progress(
                "imported",
                progress_total,
                progress_total,
                message=f"已完成导入 {progress_total} 张照片。",
            )
            self._schedule_receive_progress_reset()

        self._reset_received_photo_import_state()
        for callback in completion_callbacks:
            try:
                callback()
            except Exception:
                pass

        if add_count == 0 and total_auto_added_report_db_count == 0:
            self._set_status("没有新增照片。")
            return
        if add_count > 0 and total_auto_added_report_db_count > 0:
            self._set_status(f"已添加 {add_count} 张照片，并自动添加 {total_auto_added_report_db_count} 个 report.db。")
            return
        if add_count > 0:
            self._set_status(f"已添加 {add_count} 张照片。")
            return
        self._set_status(f"没有新增照片，已自动添加 {total_auto_added_report_db_count} 个 report.db。")

    def _process_received_photo_import_batch(self) -> None:
        pending_paths = self._received_photo_import_pending_paths
        if not pending_paths:
            if self._received_photo_import_timer.isActive():
                self._received_photo_import_timer.stop()
            if self._photo_input_discovery_workers:
                return
            self._finish_received_photo_import()
            return
        self._begin_received_photo_import()
        default_settings = self._received_photo_import_default_settings or self._build_current_render_settings()
        existing_keys = self._received_photo_import_existing_keys
        tick_t0 = time.perf_counter()
        processed = 0
        self.photo_list.setUpdatesEnabled(False)
        try:
            while pending_paths:
                path = pending_paths.popleft()
                self._received_photo_import_processed += 1
                processed += 1
                added, item = self._append_photo_path_to_list(
                    path,
                    existing_keys=existing_keys,
                    default_settings=default_settings,
                )
                if added:
                    self._received_photo_import_added += 1
                    self._received_photo_import_last_added_item = item
                    self._received_photo_import_added_paths.append(path)
                if processed >= _RECEIVED_PHOTO_IMPORT_BATCH_MAX:
                    break
                if (
                    processed >= _RECEIVED_PHOTO_IMPORT_BATCH_MIN
                    and (time.perf_counter() - tick_t0) >= _RECEIVED_PHOTO_IMPORT_BATCH_BUDGET_S
                ):
                    break
        finally:
            self.photo_list.setUpdatesEnabled(True)
            self.photo_list.update()

        total = max(self._received_photo_import_total, self._received_photo_import_processed)
        self._emit_received_photo_import_progress(
            "importing",
            self._received_photo_import_processed,
            total,
            message=f"正在导入接收照片 {self._received_photo_import_processed}/{total} ...",
        )
        if pending_paths:
            return
        if self._photo_input_discovery_workers:
            if self._received_photo_import_timer.isActive():
                self._received_photo_import_timer.stop()
            return
        self._finish_received_photo_import()

    def _enqueue_received_photo_paths(
        self,
        paths: Iterable[Path],
        *,
        pre_added_report_db_count: int = 0,
        select_last_added: bool = False,
        on_complete: Callable[[], None] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        valid_paths = self._filter_supported_photo_paths(paths)
        if not valid_paths and pre_added_report_db_count <= 0:
            if callable(on_complete):
                try:
                    on_complete()
                except Exception:
                    pass
            self._set_status("没有新增照片。")
            return
        for path in valid_paths:
            self._received_photo_import_pending_paths.append(path)
        self._received_photo_import_total += len(valid_paths)
        self._received_photo_import_auto_report_db_count += max(0, int(pre_added_report_db_count))
        self._received_photo_import_select_last_added = self._received_photo_import_select_last_added or select_last_added
        if callable(on_complete):
            self._received_photo_import_completion_callbacks.append(on_complete)
        if callable(progress_callback):
            self._received_photo_import_progress_callbacks.append(progress_callback)
        if self._received_photo_import_total > 0:
            self._emit_received_photo_import_progress(
                "import_pending",
                self._received_photo_import_processed,
                self._received_photo_import_total,
                message=f"准备导入 {self._received_photo_import_total} 张照片...",
            )
        if valid_paths and not self._received_photo_import_timer.isActive():
            self._received_photo_import_timer.start()
            return
        if (
            not valid_paths
            and not self._received_photo_import_pending_paths
            and not self._received_photo_import_timer.isActive()
        ):
            if self._photo_input_discovery_workers:
                return
            self._finish_received_photo_import()

    def _add_photo_paths(
        self,
        paths: Iterable[Path],
        *,
        select_last_added: bool = False,
        pre_added_report_db_count: int = 0,
    ) -> None:
        file_paths, directory_paths = self._split_photo_input_paths(paths)
        base_report_db_count = max(0, int(pre_added_report_db_count))

        if file_paths:
            auto_added_report_db_count = self._auto_add_report_db_paths_for_photos(file_paths)
            self._enqueue_received_photo_paths(
                file_paths,
                select_last_added=select_last_added,
                pre_added_report_db_count=base_report_db_count + auto_added_report_db_count,
            )
            base_report_db_count = 0

        if directory_paths:
            self._start_photo_input_discovery(
                directory_paths,
                select_last_added=select_last_added,
                pre_added_report_db_count=base_report_db_count,
            )
            return

        if not file_paths:
            self._enqueue_received_photo_paths(
                [],
                select_last_added=select_last_added,
                pre_added_report_db_count=base_report_db_count,
            )

    def _add_received_photo_paths(
        self,
        paths: Iterable[Path],
        on_complete: Callable[[], None] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """处理外部 received 文件：先补充 report.db，再按时间片分批导入。"""
        pending_paths = list(paths)
        pre_added_report_db_count = self._auto_add_report_db_paths_for_received_files(pending_paths)
        self._enqueue_received_photo_paths(
            pending_paths,
            select_last_added=True,
            pre_added_report_db_count=pre_added_report_db_count,
            on_complete=on_complete,
            progress_callback=progress_callback,
        )

    def add_received_file_paths(
        self,
        paths: Iterable[str | Path],
        on_complete: Callable[[], None] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """
        统一处理 argv / socket / FileOpen 三种入口收到的文件路径，并加入照片列表。
        可在任意线程调用；内部通过 QTimer.singleShot 投递到主线程执行。
        """
        normalized_paths = normalize_file_paths(paths)
        if not normalized_paths:
            return
        preview_paths = normalized_paths[:3]
        _log.info(
            "received file list count=%s, scheduling batch add to photo list preview=%s",
            len(normalized_paths),
            preview_paths,
        )
        path_objs = [Path(path_text) for path_text in normalized_paths]
        QTimer.singleShot(
            0,
            lambda pending_paths=path_objs, completion_callback=on_complete, progress_cb=progress_callback: self._add_received_photo_paths(
                pending_paths,
                on_complete=completion_callback,
                progress_callback=progress_cb,
            ),
        )

    def update_receive_transfer_progress(self, payload: dict[str, Any] | None) -> None:
        progress_payload = dict(payload or {})
        QTimer.singleShot(0, lambda current_payload=progress_payload: self._apply_receive_transfer_progress(current_payload))

    def _apply_receive_transfer_progress(self, payload: dict[str, Any]) -> None:
        phase = str(payload.get("phase") or "").strip().lower()
        try:
            total = max(0, int(payload.get("total") or 0))
        except Exception:
            total = 0
        try:
            current = max(0, int(payload.get("current") or 0))
        except Exception:
            current = 0
        message = str(payload.get("message") or "").strip()
        if phase == "receiving" and total > 0:
            self._set_receive_progress("接收", current, total)
            self._set_status(message or f"正在热接收照片 {current}/{total} ...")
            return
        if phase == "received" and total > 0 and not self._received_photo_import_pending_paths:
            self._set_receive_progress("接收", current or total, total)
            self._set_status(message or f"热接收完成，准备导入 {current or total} 张照片。")
            return
        if phase in {"import_pending", "importing", "imported", "completed"} and total > 0:
            self._set_receive_progress("导入", current, total)
            self._set_status(message or f"正在导入接收照片 {current}/{total} ...")
            return
        if phase == "cancelled":
            self._set_status(message or "热接收已取消。")
            if not self._received_photo_import_pending_paths and not self._received_photo_import_timer.isActive():
                self._schedule_receive_progress_reset()

    def _remove_selected_photos(self) -> None:
        selected_items = self.photo_list.selectedItems()
        if not selected_items:
            return

        removed_keys: list[str] = []
        for item in selected_items:
            raw = item.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
            if isinstance(raw, str):
                path = Path(raw)
                removed_keys.append(_path_key(path))
                self._forget_photo_item(path)
            row = self.photo_list.indexOfTopLevelItem(item)
            if row >= 0:
                self.photo_list.takeTopLevelItem(row)

        for key in removed_keys:
            self.raw_metadata_cache.pop(key, None)
            self.photo_render_overrides.pop(key, None)
            self._photo_export_dirty_keys.discard(key)
        if removed_keys:
            self._bird_box_cache.clear()
            self._drop_source_image_cache_for_keys(removed_keys)
            self.photo_list.refresh_row_numbers()
            if self._photo_list_metadata_pending_keys:
                self._restart_photo_list_metadata_loader()
            else:
                self._stop_photo_list_metadata_loader(wait=False, reset_progress=True)

        if self.photo_list.topLevelItemCount() == 0:
            self._next_photo_sequence_number = 0
            self.placeholder_path = None
            self.current_path = None
            self.current_photo_info = None
            self.current_source_image = None
            self.current_source_full_size = None
            self.current_raw_metadata = {}
            self.current_metadata_context = {}
            self.current_file_label.setText("当前照片: 未选择")
            self.last_rendered = None
            self._show_placeholder_preview()

        self._schedule_workspace_autosave()
        self._set_status(f"已删除 {len(selected_items)} 项。")

    def _clear_photos_state(self, *, status_message: str | None = None, show_placeholder: bool = True) -> None:
        self._cancel_workspace_restore_in_progress()
        self._stop_photo_input_discovery_workers(wait=True)
        self._stop_received_photo_import(reset_progress=True)
        self._stop_photo_list_metadata_loader(wait=False, reset_progress=True)
        self.photo_list.clear()
        self.raw_metadata_cache.clear()
        self.photo_list_metadata_cache.clear()
        self._photo_item_map.clear()
        self._photo_list_metadata_pending_keys.clear()
        self.photo_render_overrides.clear()
        self._photo_export_dirty_keys.clear()
        self._bird_box_cache.clear()
        self._source_image_cache.clear()
        self._preview_image_cache.clear()
        self._metadata_context_cache.clear()
        self._perf_decode_counts.clear()
        worker = getattr(self, "_bird_detect_worker", None)
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(100)
        self._bird_detect_worker = None
        self._crop_drag_active = False
        self._next_photo_sequence_number = 0
        self.placeholder_path = None
        self.current_path = None
        self.current_photo_info = None
        self.current_source_image = None
        self.current_source_full_size = None
        self.current_raw_metadata = {}
        self.current_metadata_context = {}
        self.current_file_label.setText("当前照片: 未选择")
        self.last_rendered = None
        if show_placeholder:
            self._show_placeholder_preview()
        else:
            self.preview_pixmap = None
            self.preview_overlay_state = EditorPreviewOverlayState()
            self._invalidate_original_mode_cache()
            self._refresh_preview_label(reset_view=True)
        self._schedule_workspace_autosave()
        if status_message is not None:
            self._set_status(status_message)

    def _clear_photos(self) -> None:
        self._clear_photos_state(status_message="已清空照片列表。")

    def _on_photo_selected(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if not current:
            return
        raw = current.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
        if not isinstance(raw, str):
            return
        path = Path(raw)
        if not path.exists():
            self._show_error("文件不存在", str(path))
            return

        with birdstamp_perf.span("select", path=str(path)):
            try:
                with birdstamp_perf.span("select.decode", path=str(path)):
                    image = self._decode_image_for_preview(path)
            except Exception as exc:
                self._show_error("读取失败", str(exc))
                return

            self.placeholder_path = None
            self.current_path = path
            self.current_source_image = image
            with birdstamp_perf.span("select.size", path=str(path)):
                self.current_source_full_size = self._read_source_full_size(path)
            self._invalidate_original_mode_cache()
            with birdstamp_perf.span("select.metadata", path=str(path)):
                self.current_raw_metadata = self._load_raw_metadata(path)
            with birdstamp_perf.span("select.context"):
                photo_info = current.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE)
                self.current_photo_info = _template_context.ensure_editor_photo_info(
                    photo_info if isinstance(photo_info, _template_context.PhotoInfo) else path,
                    raw_metadata=self.current_raw_metadata,
                )
                current.setData(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE, self.current_photo_info)
                self.current_metadata_context = self._cached_metadata_context(
                    self.current_photo_info,
                    self.current_raw_metadata,
                )
            with birdstamp_perf.span("select.apply_ui"):
                settings = self._render_settings_for_path(path, prefer_current_ui=False)
                self._set_photo_crop_box_for_path(path, settings.get("crop_box"))
                self._apply_render_settings_to_ui(settings)
            with birdstamp_perf.span("select.update_list"):
                self._update_photo_list_item_display(
                    path,
                    raw_metadata=self.current_raw_metadata,
                    settings=settings,
                    resort=False,
                )
            self.current_file_label.setText(f"当前照片: {path}")
            with birdstamp_perf.span("select.render_preview", path=str(path)):
                self.render_preview()

    def _cached_metadata_context(
        self,
        photo_info: _template_context.PhotoInfo,
        raw_metadata: dict[str, Any],
    ) -> dict[str, str]:
        cache_key = f"{_path_key(photo_info.path)}:{_metadata_digest_for_cache(raw_metadata)}"
        cached = self._metadata_context_cache.get(cache_key)
        if cached is not None:
            birdstamp_perf.plog("metadata_context cache_hit path=%s", photo_info.path)
            return cached
        context = _build_metadata_context(photo_info, raw_metadata)
        self._metadata_context_cache[cache_key] = context
        return context

    def _load_raw_metadata(self, path: Path) -> dict[str, Any]:
        key = _path_key(path)
        if key in self.raw_metadata_cache:
            birdstamp_perf.plog("metadata cache_hit path=%s", path)
            return self.raw_metadata_cache[key]

        resolved = path.resolve(strict=False)
        list_cached = self.photo_list_metadata_cache.get(key)
        can_reuse_list = (
            key not in self._photo_list_metadata_pending_keys
            and _is_complete_list_metadata(list_cached)
        )
        if (
            not can_reuse_list
            and _is_complete_list_metadata(list_cached)
            and key in self._photo_list_metadata_pending_keys
        ):
            birdstamp_perf.plog("[redundant-meta] path=%s reason=still_pending", path)

        raw_metadata: dict[str, Any]
        if can_reuse_list:
            raw_metadata = dict(list_cached)
            birdstamp_perf.plog("[reuse-meta-base] path=%s", path)
        else:
            try:
                with birdstamp_perf.span("metadata.extract", path=str(path)):
                    raw_metadata = extract_metadata_with_xmp_priority(resolved, mode="auto")
            except Exception:
                try:
                    raw_map = extract_many([resolved], mode="auto")
                    raw_metadata = raw_map.get(resolved) or extract_pillow_metadata(path)
                except Exception:
                    raw_metadata = extract_pillow_metadata(path)
            if not isinstance(raw_metadata, dict):
                raw_metadata = {"SourceFile": str(path)}

        # 通过 app_common.exif_io 统一读取文件列表依赖的 XMP/sidecar 字段（Title/Rating/Pick 等）。
        # 放在最后合并，确保列表显示与 Banner 模板字段优先使用 exif_io 的 XMP 结果。
        if not can_reuse_list:
            try:
                with birdstamp_perf.span("metadata.read_batch", path=str(path)):
                    batch_map = read_batch_metadata([str(resolved)])
            except Exception:
                batch_map = {}
            if isinstance(batch_map, dict) and batch_map:
                try:
                    batch_metadata = next(iter(batch_map.values()))
                except Exception:
                    batch_metadata = None
                if isinstance(batch_metadata, dict):
                    merged = dict(raw_metadata)
                    merged.update(batch_metadata)
                    raw_metadata = merged
        else:
            birdstamp_perf.plog("[skip-read_batch] path=%s reused_list=True", path)

        birdstamp_perf.plog(
            "metadata cache_miss path=%s reused_list=%s",
            path,
            can_reuse_list,
        )
        self.raw_metadata_cache[key] = raw_metadata
        self.photo_list_metadata_cache[key] = dict(raw_metadata)
        self._photo_list_metadata_pending_keys.discard(key)
        return raw_metadata

    def _load_raw_metadata_batch(self, paths: list[Path]) -> dict[str, dict[str, Any]]:
        """批量预取导出所需 metadata，避免逐张重复触发 exiftool / sidecar 读取。"""
        metadata_by_key: dict[str, dict[str, Any]] = {}
        pending_entries: list[tuple[Path, str, Path]] = []
        seen_keys: set[str] = set()
        current_key = _path_key(self.current_path) if self.current_path is not None else ""

        for path in paths:
            key = _path_key(path)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            cached = self.raw_metadata_cache.get(key)
            if isinstance(cached, dict) and cached:
                metadata_by_key[key] = cached
                continue

            if key == current_key and isinstance(self.current_raw_metadata, dict) and self.current_raw_metadata:
                current_metadata = dict(self.current_raw_metadata)
                self.raw_metadata_cache[key] = current_metadata
                self.photo_list_metadata_cache[key] = dict(current_metadata)
                self._photo_list_metadata_pending_keys.discard(key)
                metadata_by_key[key] = current_metadata
                continue

            pending_entries.append((path, key, path.resolve(strict=False)))

        if not pending_entries:
            return metadata_by_key

        resolved_paths = [resolved for _path, _key, resolved in pending_entries]
        try:
            full_batch = extract_many_with_xmp_priority(resolved_paths, mode="auto")
        except Exception:
            full_batch = {}
        try:
            batch_map = read_batch_metadata([str(resolved) for resolved in resolved_paths])
        except Exception:
            batch_map = {}

        for path, key, resolved in pending_entries:
            raw_metadata = full_batch.get(resolved)
            if isinstance(raw_metadata, dict) and raw_metadata:
                merged = dict(raw_metadata)
            else:
                list_cached = self.photo_list_metadata_cache.get(key)
                if isinstance(list_cached, dict) and list_cached:
                    merged = dict(list_cached)
                else:
                    try:
                        merged = extract_metadata_with_xmp_priority(resolved, mode="auto")
                    except Exception:
                        try:
                            raw_map = extract_many([resolved], mode="auto")
                            merged = raw_map.get(resolved) or extract_pillow_metadata(path)
                        except Exception:
                            merged = extract_pillow_metadata(path)

            if not isinstance(merged, dict):
                merged = {"SourceFile": str(path)}
            merged.setdefault("SourceFile", str(path))

            if isinstance(batch_map, dict) and batch_map:
                batch_metadata = batch_map.get(os.path.normpath(str(resolved))) or batch_map.get(str(resolved))
                if isinstance(batch_metadata, dict) and batch_metadata:
                    combined = dict(merged)
                    combined.update(batch_metadata)
                    merged = combined

            self.raw_metadata_cache[key] = merged
            self.photo_list_metadata_cache[key] = dict(merged)
            self._photo_list_metadata_pending_keys.discard(key)
            metadata_by_key[key] = merged

        return metadata_by_key

    def _suggest_video_output_path(self, container: str) -> Path:
        suffix = str(container or "mp4").strip().lower().lstrip(".") or "mp4"
        paths = self._list_photo_paths()
        remembered_dir = self._video_export_last_output_dir
        base_dir = remembered_dir if remembered_dir is not None and remembered_dir.is_dir() else None
        if paths:
            first_path = paths[0]
            if base_dir is None:
                base_dir = first_path.parent
            stem = f"{first_path.stem}__birdstamp_video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            return base_dir / f"{stem}.{suffix}"
        if base_dir is not None:
            return base_dir / f"birdstamp_video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{suffix}"
        return Path.cwd() / f"birdstamp_video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{suffix}"

    def _apply_global_export_settings_to_render_settings(self, settings: dict[str, Any]) -> None:
        """导出时强制使用当前界面的全局管线/叠加开关，避免照片级快照污染。"""
        global_export = self._current_global_export_settings()
        settings["draw_banner"] = bool(global_export.get("draw_banner", True))
        settings["draw_text"] = bool(global_export.get("draw_text", True))
        settings["draw_focus"] = bool(global_export.get("draw_focus", False))
        settings["max_long_edge"] = max(0, int(global_export.get("max_long_edge") or 0))
        settings["uniform_auto_crop"] = bool(global_export.get("uniform_auto_crop", False))
        try:
            stabilization = int(round(float(global_export.get("auto_crop_stabilization", 0))))
        except Exception:
            stabilization = 0
        settings["auto_crop_stabilization"] = max(0, min(100, stabilization))
        settings[PIPELINE_STAGE_ORDER_KEY] = list(
            normalize_pipeline_stage_order(global_export.get(PIPELINE_STAGE_ORDER_KEY))
        )
        stage_enabled = global_export.get(PIPELINE_STAGE_ENABLED_KEY)
        if isinstance(stage_enabled, dict):
            settings[PIPELINE_STAGE_ENABLED_KEY] = dict(stage_enabled)
        settings[STAGE_TEMPLATE_CROP_ENABLED_KEY] = bool(
            global_export.get(STAGE_TEMPLATE_CROP_ENABLED_KEY, True)
        )
        settings[STAGE_RESIZE_LIMIT_ENABLED_KEY] = bool(
            global_export.get(STAGE_RESIZE_LIMIT_ENABLED_KEY, True)
        )
        settings[STAGE_TEMPLATE_OVERLAY_ENABLED_KEY] = bool(
            global_export.get(STAGE_TEMPLATE_OVERLAY_ENABLED_KEY, True)
        )
        settings[STAGE_FOCUS_OVERLAY_ENABLED_KEY] = bool(
            global_export.get(STAGE_FOCUS_OVERLAY_ENABLED_KEY, True)
        )

    def _build_export_render_jobs(
        self,
        paths: list[Path],
        *,
        prefer_current_ui_for_current_path: bool = True,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[VideoFrameJob]:
        jobs: list[VideoFrameJob] = []
        current_key = _path_key(self.current_path) if self.current_path is not None else ""
        current_render_settings = self._build_current_render_settings()
        total = len(paths)
        metadata_by_key = self._load_raw_metadata_batch(paths)
        if callable(progress_callback):
            progress_callback(0, total)
        for index, path in enumerate(paths, start=1):
            raw_metadata = dict(metadata_by_key.get(_path_key(path)) or self._load_raw_metadata(path))
            photo_info = self._photo_info_for_display(path, raw_metadata=raw_metadata)
            metadata_context = _build_metadata_context(photo_info, raw_metadata)
            is_current_path = bool(current_key and _path_key(path) == current_key)
            if is_current_path and prefer_current_ui_for_current_path:
                settings = self._clone_render_settings(current_render_settings)
            else:
                settings = self._clone_render_settings(self._render_settings_for_path(path, prefer_current_ui=False))
            # 导出图像时，全局导出开关跟随当前界面；每张照片只保留模板/裁切重载。
            self._apply_global_export_settings_to_render_settings(settings)

            source_image = None

            jobs.append(
                VideoFrameJob(
                    path=path,
                    settings=settings,
                    raw_metadata=raw_metadata,
                    metadata_context=metadata_context,
                    photo_info=photo_info,
                    source_image=source_image,
                    source_paths=tuple(paths),
                )
            )
            if callable(progress_callback):
                progress_callback(index, total)
        if crop_plan_precompute_required(current_render_settings):
            if callable(progress_callback):
                progress_callback(0, total)
            prepare_uniform_auto_crop_plans(
                jobs,
                bird_box_cache=self._bird_box_cache,
                bird_box_lock=None,
                progress_callback=progress_callback,
            )
        return jobs

    def _cleanup_video_export_worker(self) -> None:
        self._video_export_worker = None

    def _on_video_export_progress(self, text: str) -> None:
        message = str(text or "").strip()
        if not message:
            return
        self.video_export_panel.set_status_text(message)
        self._set_status(message)

    def _consume_video_export_elapsed_time(self) -> float | None:
        started_at = self._video_export_started_at
        self._video_export_started_at = None
        if started_at is None:
            return None
        return max(0.0, time.perf_counter() - started_at)

    def _on_video_export_succeeded(self, output_path_text: str) -> None:
        output_path = Path(output_path_text)
        if self._pending_video_export_dirty_keys:
            self._photo_export_dirty_keys.difference_update(self._pending_video_export_dirty_keys)
            self._pending_video_export_dirty_keys.clear()
        elapsed = self._consume_video_export_elapsed_time()
        timing_text = (
            f" | 视频生成耗时 {self._format_export_elapsed_time(elapsed)}"
            if elapsed is not None
            else ""
        )
        message = f"视频导出完成: {output_path}{timing_text}"
        self.video_export_panel.set_busy(False, status_text=message)
        self._set_status(message)
        self._cleanup_video_export_worker()

    def _on_video_export_cancelled(self, message: str) -> None:
        self._pending_video_export_dirty_keys.clear()
        cancel_text = str(message or "").strip() or "视频导出已中断。"
        elapsed = self._consume_video_export_elapsed_time()
        if elapsed is not None:
            cancel_text = f"{cancel_text} | 已耗时 {self._format_export_elapsed_time(elapsed)}"
        self.video_export_panel.set_busy(False, status_text=cancel_text)
        self._set_status(cancel_text)
        self._cleanup_video_export_worker()
        QMessageBox.information(self, "视频导出已中断", cancel_text)

    def _on_video_export_failed(self, message: str) -> None:
        self._pending_video_export_dirty_keys.clear()
        error_text = str(message or "").strip() or "未知错误"
        elapsed = self._consume_video_export_elapsed_time()
        status_text = f"视频导出失败: {error_text}"
        if elapsed is not None:
            status_text = f"{status_text} | 已耗时 {self._format_export_elapsed_time(elapsed)}"
        self.video_export_panel.set_busy(False, status_text=status_text)
        self._set_status(status_text)
        self._cleanup_video_export_worker()
        self._show_error("视频导出失败", error_text)

    def _cancel_video_export(self) -> None:
        worker = self._video_export_worker
        if worker is None or not worker.isRunning():
            self.video_export_panel.set_busy(False)
            self._cleanup_video_export_worker()
            return
        worker.cancel()
        message = "正在中断视频导出，并保留已完成帧..."
        self.video_export_panel.set_status_text(message)
        self._set_status(message)

    def _start_video_export(self, request: VideoExportRequest) -> None:
        if self._video_export_worker is not None and self._video_export_worker.isRunning():
            self._set_status("已有视频导出任务在运行。")
            return

        paths = self._list_photo_paths()
        if not paths:
            self._set_status("照片列表为空。")
            return

        if find_ffmpeg_executable() is None:
            install_script = ffmpeg_install_script_path()
            if install_script is not None:
                self._show_error(
                    "未找到 ffmpeg",
                    f"请先运行安装脚本:\n{install_script}\n\n安装目标:\n{preferred_ffmpeg_binary_path()}",
                )
            else:
                self._show_error(
                    "未找到 ffmpeg",
                    f"请先安装 ffmpeg，或放到以下位置后再试：\n{preferred_ffmpeg_binary_path()}",
                )
            return

        try:
            default_path = self._suggest_video_output_path(request.container)
            selected_filter = "MP4 视频 (*.mp4)" if str(request.container).lower() == "mp4" else "MOV 视频 (*.mov)"
            file_path, _selected = QFileDialog.getSaveFileName(
                self,
                "导出视频",
                str(default_path),
                "MP4 视频 (*.mp4);;MOV 视频 (*.mov);;All Files (*.*)",
                selected_filter,
            )
            if not file_path:
                return

            options = VideoExportOptions(
                output_path=Path(file_path),
                container=request.container,
                codec=request.codec,
                fps=request.fps,
                preset=request.preset,
                crf=request.crf,
                frame_size_mode=request.frame_size_mode,
                frame_width=request.frame_width,
                frame_height=request.frame_height,
                preserve_temp_files=request.preserve_temp_files,
            )
            output_path = options.normalized_output_path()
            if not self._confirm_video_output_overwrite(output_path):
                self._set_status("已取消视频导出。")
                return
            self._video_export_last_output_dir = output_path.parent
            self._save_video_export_last_output_dir(output_path.parent)
            jobs = self._build_export_render_jobs(paths, prefer_current_ui_for_current_path=True)
        except Exception as exc:
            self._show_error("视频导出参数无效", str(exc))
            return

        dirty_path_keys = self._dirty_photo_path_keys(paths)
        self._pending_video_export_dirty_keys = set(dirty_path_keys)

        worker = VideoExportWorker(
            jobs=jobs,
            options=options,
            template_paths=dict(self.template_paths),
            dirty_path_keys=dirty_path_keys,
            parent=self,
        )
        worker.progressTextChanged.connect(self._on_video_export_progress)
        worker.exportSucceeded.connect(self._on_video_export_succeeded)
        worker.exportCancelled.connect(self._on_video_export_cancelled)
        worker.exportFailed.connect(self._on_video_export_failed)
        worker.finished.connect(worker.deleteLater)
        self._video_export_worker = worker
        self._video_export_started_at = time.perf_counter()

        self.video_export_panel.set_busy(True, status_text=f"准备生成视频，共 {len(jobs)} 帧。")
        self._set_status(f"准备生成视频，共 {len(jobs)} 帧。")
        worker.start()










    def _selected_photo_paths(self) -> list[Path]:
        selected_items = self.photo_list.selectedItems()
        paths: list[Path] = []
        if selected_items:
            for item in selected_items:
                raw = item.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
                if isinstance(raw, str):
                    paths.append(Path(raw))
        elif self.current_path is not None and not self._is_placeholder_active():
            paths.append(self.current_path)

        ordered: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = _path_key(path)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(path)
        return ordered

    def _apply_current_settings_to_selected_photos(self) -> None:
        targets = self._selected_photo_paths()
        if not targets:
            self._set_status("请先选择要应用设置的照片。")
            return

        snapshot = self._photo_override_settings_from_snapshot(self._build_current_render_settings())
        for path in targets:
            normalized = self._clone_render_settings(snapshot)
            previous_snapshot = self.photo_render_overrides.get(_path_key(path))
            self.photo_render_overrides[_path_key(path)] = normalized
            self._set_photo_crop_box_for_path(path, normalized.get("crop_box"))
            if previous_snapshot != normalized:
                self._mark_photo_export_dirty(path)
            self._update_photo_list_item_display(path, settings=normalized)

        if self.current_path is not None:
            current_key = _path_key(self.current_path)
            if any(_path_key(path) == current_key for path in targets):
                self.render_preview()

        self._schedule_workspace_autosave()
        self._set_status(f"已将当前裁切重载设置应用到 {len(targets)} 张照片。")

    def _apply_current_settings_to_all_photos(self) -> None:
        targets = self._list_photo_paths()
        if not targets:
            self._set_status("照片列表为空。")
            return

        snapshot = self._photo_override_settings_from_snapshot(self._build_current_render_settings())
        for path in targets:
            normalized = self._clone_render_settings(snapshot)
            previous_snapshot = self.photo_render_overrides.get(_path_key(path))
            self.photo_render_overrides[_path_key(path)] = normalized
            self._set_photo_crop_box_for_path(path, normalized.get("crop_box"))
            if previous_snapshot != normalized:
                self._mark_photo_export_dirty(path)
            self._update_photo_list_item_display(path, settings=normalized)

        if self.current_path is not None:
            self.render_preview()
        self._schedule_workspace_autosave()
        self._set_status(f"已将当前裁切重载设置应用到全部 {len(targets)} 张照片。")


def _ensure_positive_qt_application_font(app: Any) -> None:
    try:
        font = app.font()
        if font.pointSize() > 0:
            return
        pixel_size = font.pixelSize()
        if pixel_size > 0:
            screen = QGuiApplication.primaryScreen()
            dpi = float(screen.logicalDotsPerInchY()) if screen is not None else 96.0
            point_size = max(1, int(round(pixel_size * 72.0 / max(1.0, dpi))))
        else:
            point_size = 10
        font.setPointSize(point_size)
        app.setFont(font)
    except Exception as exc:
        _log.debug("qt application font normalization skipped: %s", exc)


def launch_gui(
    startup_file: Path | None = None,
    startup_files: list[Path] | None = None,
) -> None:
    _log.info(
        "launch_gui enter startup_file=%s startup_files=%s",
        str(startup_file) if startup_file else "",
        [str(path) for path in (startup_files or [])],
    )
    app = ensure_file_open_aware_application(sys.argv)
    _ensure_positive_qt_application_font(app)
    about_info = _load_birdstamp_about_info()
    app_name = _birdstamp_product_name(about_info)
    if hasattr(app, "setApplicationName"):
        app.setApplicationName(app_name)
    if hasattr(app, "setApplicationDisplayName"):
        app.setApplicationDisplayName(app_name)
    if hasattr(app, "setApplicationVersion"):
        app.setApplicationVersion(str(about_info.get("version", "")))
    reload_runtime_user_options()
    window = BirdStampEditorWindow()
    _log.info("editor window created")

    def on_files_received(
        paths: Iterable[str | Path],
        on_complete: Callable[[], None] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        window.add_received_file_paths(
            paths,
            on_complete=on_complete,
            progress_callback=on_progress,
        )

    def on_transfer_progress(payload: dict[str, Any]) -> None:
        window.update_receive_transfer_progress(payload)

    install_file_open_handler(app, on_files_received)
    _log.info("FileOpen handler installed")

    # 热接收：单例 IPC，其它进程通过 send_file_list_to_running_app 发来文件列表时加入本窗口照片列表
    receiver = SingleInstanceReceiver(
        SEND_TO_APP_ID,
        on_files_received,
        on_transfer_progress=on_transfer_progress,
    )
    if receiver.start():
        window._send_to_app_receiver = receiver  # 保持引用，避免被回收；退出时可选 stop()
        _log.info("single instance receiver started")
    else:
        window._send_to_app_receiver = None
        _log.info("single instance receiver unavailable")

    def _on_about_to_quit() -> None:
        _log.info("qt aboutToQuit emitted")
        active_receiver = getattr(window, "_send_to_app_receiver", None)
        if active_receiver is not None:
            try:
                active_receiver.stop()
            except Exception as exc:
                _log.warning("receiver stop failed: %s", exc)

    startup_inputs: list[Path] = []
    if startup_files:
        startup_inputs = list(startup_files)
    elif startup_file:
        startup_inputs = [startup_file]

    app.aboutToQuit.connect(_on_about_to_quit)
    window.showMaximized()
    _log.info("editor window shown")

    def _run_startup_tasks() -> None:
        window._run_deferred_startup_tasks()
        if startup_inputs:
            on_files_received(startup_inputs)

    QTimer.singleShot(0, _run_startup_tasks)
    exit_code = app.exec()
    _log.info("qt event loop exited code=%s window_visible=%s", exit_code, window.isVisible())
