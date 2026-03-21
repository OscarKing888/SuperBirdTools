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
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Iterable

from PIL import Image, ImageColor, ImageDraw, ImageOps
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
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
    QProgressBar,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app_common.about_dialog import load_about_info, load_about_images, show_about_dialog
from app_common.app_info_bar import AppInfoBar
from app_common.log import get_logger
from app_common.send_to_app import (
    SingleInstanceReceiver,
    ensure_file_open_aware_application,
    install_file_open_handler,
    normalize_file_paths,
)

import birdstamp
from birdstamp.config import get_app_resource_dir, get_config_path, resolve_bundled_path
from birdstamp.constants import SEND_TO_APP_ID, SUPPORTED_EXTENSIONS
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.discover import discover_inputs
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
    PHOTO_LIST_PHOTO_INFO_ROLE,
    PHOTO_LIST_PATH_ROLE,
    PHOTO_LIST_SEQUENCE_ROLE,
    PHOTO_LIST_SORT_ROLE,
    PhotoListItem,
    PhotoListWidget,
)
from birdstamp.gui.editor_collapsible import CollapsibleSection
from birdstamp.gui.editor_video_panel import VideoExportPanel, VideoExportRequest, VideoExportWorker
from birdstamp.gui.editor_crop_calculator import _BirdStampCropMixin
from birdstamp.gui.editor_renderer import _BirdStampRendererMixin
from birdstamp.gui.editor_exporter import _BirdStampExporterMixin
from birdstamp.video_export import (
    VideoExportOptions,
    VideoFrameJob,
    ffmpeg_install_script_path,
    find_ffmpeg_executable,
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
    try:
        with resources.as_file(resources.files("icons")) as res:
            icon_dir = Path(res)
    except Exception:
        icon_dir = Path(__file__).resolve().parent / "icons"
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


def _get_bird_detector_error_message() -> str:
    return editor_core.get_bird_detector_error_message()


_pil_to_qpixmap = editor_utils.pil_to_qpixmap
_log = get_logger("editor")
_PHOTO_LIST_META_PROGRESS_HIDE_DELAY_MS = 600
_ABOUT_CFG_FILENAME = "about.cfg"
_BIRDSTAMP_DEFAULT_APP_NAME = "极速鸟框 - 鸟类照片智能裁切与模板叠加工具"
_BIRDSTAMP_DEFAULT_PRODUCT_NAME = "极速鸟框"
_BIRDSTAMP_DEFAULT_SUBTITLE = "鸟类照片智能裁切与模板叠加"


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

class BirdStampEditorWindow(QMainWindow, _BirdStampCropMixin, _BirdStampRendererMixin, _BirdStampExporterMixin):
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
        self.photo_render_overrides: dict[str, dict[str, Any]] = {}
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
        self.last_rendered: Image.Image | None = None
        self.current_path: Path | None = None
        self.current_photo_info: _template_context.PhotoInfo | None = None
        self.current_source_image: Image.Image | None = None
        self.current_raw_metadata: dict[str, Any] = {}
        self.current_metadata_context: dict[str, str] = {}
        self.raw_metadata_cache: dict[str, dict[str, Any]] = {}
        self.photo_list_metadata_cache: dict[str, dict[str, Any]] = {}
        self._photo_item_map: dict[str, QTreeWidgetItem] = {}
        self._photo_list_metadata_pending_keys: set[str] = set()
        self._photo_list_metadata_loader: EditorPhotoListMetadataLoader | None = None
        self._pending_photo_list_metadata_loaders: list[EditorPhotoListMetadataLoader] = []
        self._photo_list_metadata_loading = False
        self._photo_list_header_fast_mode = False
        self._pending_preview_fit_reset: bool = False
        self._video_export_worker: VideoExportWorker | None = None
        self._video_export_started_at: float | None = None
        self._image_export_progress_token: int = 0
        self._image_export_active_worker_count: int = 0
        self._image_export_last_output_dir: Path | None = self._load_image_export_last_output_dir()
        self._batch_export_last_output_dir: Path | None = self._load_batch_export_last_output_dir()
        self._video_export_last_output_dir: Path | None = self._load_video_export_last_output_dir()
        # 占位图路径标记：非 None 时表示当前预览的是默认占位图而非用户照片
        self.placeholder_path: Path | None = None

        self.placeholder = _build_placeholder_image(1400, 900)

        self._preview_debounce_timer = QTimer(self)
        self._preview_debounce_timer.setSingleShot(True)
        self._preview_debounce_timer.setInterval(250)
        self._preview_debounce_timer.timeout.connect(self.render_preview)

        self._setup_ui()
        self._setup_shortcuts()
        self._apply_system_adaptive_style()
        self._reload_template_combo(preferred="default")
        self._set_status("就绪。请添加照片并选择模板。")
        self._show_placeholder_preview()
        self._start_bird_detector_preload()

        # 冷启动或「发送到本应用」传入的文件列表：加入照片列表
        files_to_add: list[Path] = []
        if startup_files:
            files_to_add = list(startup_files)
        elif startup_file:
            files_to_add = [startup_file]
        if files_to_add:
            self._add_photo_paths(files_to_add)

        # 初始化 report.db 行解析器（无缓存时返回 None）
        self._update_report_db_row_resolver()

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

    def _clear_report_dbs(self) -> None:
        """清空所有 report.db 记录与缓存。"""
        self.report_db_list.clear()
        self._report_db_entries.clear()
        self._report_db_cache.clear()
        self._update_report_db_row_resolver()

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

        photo_btn_row = QHBoxLayout()
        add_files_btn = QPushButton("添加照片")
        add_files_btn.clicked.connect(self._pick_files)
        photo_btn_row.addWidget(add_files_btn)

        add_dir_btn = QPushButton("添加目录")
        add_dir_btn.clicked.connect(self._pick_directory)
        photo_btn_row.addWidget(add_dir_btn)

        remove_btn = QPushButton("删除所选")
        remove_btn.clicked.connect(self._remove_selected_photos)
        photo_btn_row.addWidget(remove_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_photos)
        photo_btn_row.addWidget(clear_btn)
        photos_layout.addLayout(photo_btn_row)

        self.photo_list_progress = QProgressBar()
        self.photo_list_progress.setMinimum(0)
        self.photo_list_progress.setMaximum(1)
        self.photo_list_progress.setValue(0)
        self.photo_list_progress.setFixedHeight(18)
        self.photo_list_progress.setTextVisible(True)
        self.photo_list_progress.setFormat("照片信息 0/0")
        self.photo_list_progress.hide()
        photos_layout.addWidget(self.photo_list_progress)

        self.photo_list = PhotoListWidget()
        self.photo_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.photo_list.pathsDropped.connect(self._add_photo_paths)
        self.photo_list.currentItemChanged.connect(self._on_photo_selected)
        self.photo_list.setMinimumHeight(240)
        self.photo_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        photos_layout.addWidget(self.photo_list, stretch=1)

        hint = QLabel("支持拖入单张照片或整个目录")
        hint.setStyleSheet("color: #7A7A7A;")
        photos_layout.addWidget(hint)

        photos_section = CollapsibleSection("照片列表", expanded=True)
        photos_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        photos_section.set_content_widget(photos_content)
        left_layout.addWidget(photos_section, stretch=1)

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
        """构建左侧「模板」「模板选项重载」「导出」分组 UI。"""
        template_section_content = QWidget()
        template_section_layout = QVBoxLayout(template_section_content)
        template_section_layout.setContentsMargins(0, 0, 0, 0)
        template_section_layout.setSpacing(10)

        # ── 模板 ─────────────────────────────────────────────────────────────
        template_content = QWidget()
        template_layout = QHBoxLayout(template_content)
        template_layout.setContentsMargins(0, 0, 0, 0)

        self.template_combo = QComboBox()
        self.template_combo.currentTextChanged.connect(self._on_template_changed)
        template_layout.addWidget(self.template_combo, stretch=1)

        manage_template_btn = QPushButton("模板管理")
        manage_template_btn.clicked.connect(self._open_template_manager)
        template_layout.addWidget(manage_template_btn)
        template_section_layout.addWidget(template_content)

        # ── 模板选项重载（可滚动） ─────────────────────────────────────────
        override_content = QWidget()
        override_form = QFormLayout(override_content)
        _configure_form_layout(override_form)

        self.ratio_combo = QComboBox()
        for label, ratio in RATIO_OPTIONS:
            self.ratio_combo.addItem(label, ratio)
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio_changed)
        override_form.addRow("裁切比例", self.ratio_combo)

        self.center_mode_combo = QComboBox()
        self.center_mode_combo.addItem("鸟体", _CENTER_MODE_BIRD)
        self.center_mode_combo.addItem("焦点", _CENTER_MODE_FOCUS)
        self.center_mode_combo.addItem("图像中心", _CENTER_MODE_IMAGE)
        self.center_mode_combo.addItem("自定义", _CENTER_MODE_CUSTOM)
        self.center_mode_combo.currentIndexChanged.connect(self._on_crop_settings_changed)
        override_form.addRow("裁切中心", self.center_mode_combo)

        override_btn_row = QHBoxLayout()
        reset_override_btn = QPushButton("重置为模板值")
        reset_override_btn.setToolTip(
            "<b>重置为模板值</b><br>"
            "将「裁切比例」「裁切中心」以及当前模板<br>"
            "记录的裁剪框默认值恢复为<br>"
            "当前所选模板中存储的默认值。<br>"
            "<i>适合撤销手动调整、快速回到模板初始状态。</i>"
        )
        reset_override_btn.clicked.connect(self._reset_template_overrides)
        override_btn_row.addWidget(reset_override_btn)
        self.apply_all_btn = QPushButton("全部应用")
        self.apply_all_btn.setToolTip(
            "<b>全部应用</b><br>"
            "将当前「模板选项重载」中的所有设置<br>"
            "批量覆盖到已加载的每张照片，<br>"
            "包括裁切比例、中心模式以及<br>"
            "当前照片上调整过的裁剪框。<br>"
            "<i>仅影响本次会话的照片列表，不修改模板文件。</i>"
        )
        self.apply_all_btn.clicked.connect(self._apply_current_settings_to_all_photos)
        override_btn_row.addWidget(self.apply_all_btn)
        override_form.addRow("", override_btn_row)

        template_section_layout.addWidget(override_content)

        template_section = CollapsibleSection("模板与重载", expanded=True)
        template_section.set_content_widget(template_section_content)
        left_layout.addWidget(template_section)

        # ── 导出设置 ───────────────────────────────────────────────────────
        export_content = QWidget()
        export_root = QVBoxLayout(export_content)
        export_root.setContentsMargins(0, 0, 0, 0)
        export_root.setSpacing(8)

        global_export_group = QGroupBox("全局导出设置")
        global_export_form = QFormLayout(global_export_group)
        _configure_form_layout(global_export_form)
        self.draw_banner_check = QCheckBox("Banner 底")
        self.draw_banner_check.setChecked(True)
        self.draw_banner_check.toggled.connect(self._on_output_settings_changed)
        self.draw_text_check = QCheckBox("文本")
        self.draw_text_check.setChecked(True)
        self.draw_text_check.toggled.connect(self._on_output_settings_changed)
        self.draw_focus_check = QCheckBox("焦点")
        self.draw_focus_check.setChecked(False)
        self.draw_focus_check.toggled.connect(self._on_output_settings_changed)
        overlay_row_widget = QWidget()
        overlay_row_layout = QHBoxLayout(overlay_row_widget)
        overlay_row_layout.setContentsMargins(0, 0, 0, 0)
        overlay_row_layout.setSpacing(10)
        overlay_row_layout.addWidget(self.draw_banner_check)
        overlay_row_layout.addWidget(self.draw_text_check)
        overlay_row_layout.addWidget(self.draw_focus_check)
        overlay_row_layout.addStretch()
        global_export_form.addRow("叠加信息", overlay_row_widget)
        export_root.addWidget(global_export_group)

        image_export_group = QGroupBox("图片导出")
        image_export_layout = QVBoxLayout(image_export_group)
        image_export_layout.setContentsMargins(8, 8, 8, 8)
        image_export_layout.setSpacing(6)

        image_export_form = QFormLayout()
        image_export_form.setContentsMargins(0, 0, 0, 0)
        _configure_form_layout(image_export_form)

        self.output_format_combo = QComboBox()
        for suffix, label in OUTPUT_FORMAT_OPTIONS:
            self.output_format_combo.addItem(label, suffix)
        if self.output_format_combo.count() == 0:
            self.output_format_combo.addItem("PNG", "png")
            self.output_format_combo.addItem("JPG", "jpg")
        self.output_format_combo.currentIndexChanged.connect(self._on_output_settings_changed)
        image_export_form.addRow("输出格式", self.output_format_combo)

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
        image_export_form.addRow("最大长边", self.max_edge_combo)

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

        self.show_crop_effect_check = QCheckBox("显示裁切效果")
        self.show_crop_effect_check.setChecked(True)
        self.show_crop_effect_check.toggled.connect(self._on_preview_toolbar_toggled)
        preview_toolbar.addWidget(self.show_crop_effect_check)

        self.crop_edit_mode_check = QCheckBox("调整裁剪框")
        self.crop_edit_mode_check.setToolTip("在预览上拖动 9 宫格手柄调整裁剪范围；比例由「裁切比例」锁定（选「自由」时不锁定）")
        self.crop_edit_mode_check.toggled.connect(self._on_preview_toolbar_toggled)
        preview_toolbar.addWidget(self.crop_edit_mode_check)

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
        canvas = self.preview_label.canvas
        if hasattr(canvas, "crop_box_changed"):
            canvas.crop_box_changed.connect(self._on_canvas_crop_box_changed)
        if hasattr(self.preview_label, "display_scale_percent_changed"):
            self.preview_label.display_scale_percent_changed.connect(self._sync_preview_scale_combo)
        self._sync_preview_scale_combo(self.preview_label.current_display_scale_percent())
        right_layout.addWidget(self.preview_label, stretch=1)

        return right_panel

    def _setup_shortcuts(self) -> None:
        action_add = QAction(self)
        action_add.setShortcut(QKeySequence.StandardKey.Open)
        action_add.triggered.connect(self._pick_files)
        self.addAction(action_add)

        action_preview = QAction(self)
        action_preview.setShortcut(QKeySequence("Ctrl+R"))
        action_preview.triggered.connect(self.render_preview)
        self.addAction(action_preview)

        action_export_current = QAction(self)
        action_export_current.setShortcut(QKeySequence("Ctrl+E"))
        action_export_current.triggered.connect(self.export_current)
        self.addAction(action_export_current)

        action_export_all = QAction(self)
        action_export_all.setShortcut(QKeySequence("Ctrl+Shift+E"))
        action_export_all.triggered.connect(self.export_all)
        self.addAction(action_export_all)

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

    def _load_remembered_output_dir(self, key: str) -> Path | None:
        raw = self._load_editor_export_state_raw()
        if not isinstance(raw, dict):
            return None
        dir_text = str(raw.get(key) or "").strip()
        if not dir_text:
            return None
        try:
            path = Path(dir_text).expanduser().resolve(strict=False)
        except Exception:
            return None
        return path if path.is_dir() else None

    def _save_remembered_output_dir(self, key: str, directory: Path) -> None:
        try:
            target_dir = directory.expanduser().resolve(strict=False)
        except Exception:
            target_dir = Path(directory)
        state_path = self._editor_export_state_path()
        payload = self._load_editor_export_state_raw()
        payload[key] = str(target_dir)
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            _log.warning("save export state failed: key=%s err=%s", key, exc)

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
        self._stop_photo_list_metadata_loader(wait=True, reset_progress=True)
        super().closeEvent(event)

    def _on_preview_toolbar_toggled(self, _checked: bool) -> None:
        self._refresh_preview_label(preserve_view=True)

    def _on_preview_grid_mode_changed(self, _index: int) -> None:
        self._apply_preview_overlay_options_from_ui()

    def _on_preview_grid_line_width_changed(self, _index: int) -> None:
        self._apply_preview_overlay_options_from_ui()

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

    def _on_canvas_crop_box_changed(self, box: tuple[float, float, float, float]) -> None:
        """9 宫格裁切框变更。

        - 若当前裁切框尚未平移（仅缩放），则根据图像尺寸反算 top/bottom/left/right padding。
        - 若已经发生过平移，则将裁切中心改为自定义，并记录 custom_center_x/y。
        """
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
        self._on_crop_settings_changed()

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
        for idx in range(self.center_mode_combo.count()):
            if self.center_mode_combo.itemData(idx) == _CENTER_MODE_CUSTOM:
                self.center_mode_combo.blockSignals(True)
                try:
                    self.center_mode_combo.setCurrentIndex(idx)
                finally:
                    self.center_mode_combo.blockSignals(False)
                break

    def _on_preview_scale_mode_toggled(self, _checked: bool) -> None:
        self._refresh_preview_label(preserve_view=True)

    def _on_crop_effect_alpha_changed(self, value: int) -> None:
        alpha = max(0, min(255, int(value)))
        self.crop_effect_alpha_value_label.setText(str(alpha))
        self._apply_preview_overlay_options_from_ui()

    def _on_ratio_changed(self, *_args: Any) -> None:
        """裁切比例变更：使当前裁切框与新区比例一致（按中心约束），再走 settings 流程。"""
        new_ratio = self._selected_ratio()
        if (
            not _is_ratio_free(new_ratio)
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

    def _on_output_settings_changed(self, *_args: Any) -> None:
        if self.current_path is not None and not self._is_placeholder_active():
            key = _path_key(self.current_path)
            snapshot = self._photo_override_settings_from_snapshot(self._build_current_render_settings())
            self._set_photo_crop_box_for_path(self.current_path, snapshot.get("crop_box"))
            self.photo_render_overrides[key] = snapshot
            self._update_photo_list_item_display(self.current_path, settings=snapshot)
            self._invalidate_original_mode_cache()
        self._preview_debounce_timer.start()

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
        """将模板选项重载区的所有选项恢复为当前模板中存储的值。"""
        self._apply_template_ratio_to_main_output()
        self._apply_template_output_settings_to_main_output()
        self._apply_template_crop_padding_to_main_output()
        self._invalidate_original_mode_cache()
        self._pending_preview_fit_reset = True
        if self.current_path:
            self._on_output_settings_changed()
        else:
            self.render_preview()

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
        center_idx = self.center_mode_combo.findData(center)
        if center_idx >= 0:
            self.center_mode_combo.blockSignals(True)
            try:
                self.center_mode_combo.setCurrentIndex(center_idx)
            finally:
                self.center_mode_combo.blockSignals(False)

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
        found = discover_inputs(Path(folder), recursive=True)
        if not found:
            QMessageBox.information(self, "添加目录", "目录中没有支持的图片文件")
            return
        self._add_photo_paths(found)

    def _format_ratio_display(self, ratio: float | None) -> str:
        parsed = _parse_ratio_value(ratio)
        if parsed is None:
            return "原比例"
        idx = self._ratio_combo_index_for_value(parsed)
        if idx >= 0:
            label = str(self.ratio_combo.itemText(idx) or "").strip()
            if label:
                return label
        text = f"{parsed:.4f}".rstrip("0").rstrip(".")
        return text or "原比例"

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
            if text:
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
        for norm_path, raw_metadata in batch.items():
            path = Path(norm_path)
            key = _path_key(path)
            if not self._find_photo_item_by_path(path):
                self._forget_photo_item(path)
                continue
            if isinstance(raw_metadata, dict):
                self.photo_list_metadata_cache[key] = dict(raw_metadata)
            self._photo_list_metadata_pending_keys.discard(key)
            settings = self.photo_render_overrides.get(key)
            self._update_photo_list_item_display(
                path,
                raw_metadata=raw_metadata,
                settings=settings,
                resort=False,
            )
        self.photo_list.refresh_row_numbers()

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

    def _next_photo_sequence_value(self) -> int:
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
        return next_value

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
        photo_info = self._photo_info_for_display(path, raw_metadata=metadata)
        filename_text = self._display_filename_from_photo_info(photo_info) or path.name
        capture_time_text, capture_time_sort = self._extract_display_capture_time_from_metadata(photo_info)
        title = self._extract_display_title_from_metadata(photo_info)
        rating_value = self._extract_display_rating_from_metadata(photo_info)
        rating_text = self._format_rating_display(rating_value)
        (
            shutter_text,
            shutter_sort,
            iso_text,
            iso_sort,
            aperture_text,
            aperture_sort,
        ) = self._extract_display_camera_settings_from_metadata(photo_info)
        active_settings = (
            settings
            if isinstance(settings, dict)
            else self._render_settings_for_path(path, prefer_current_ui=False)
        )
        ratio_value = _parse_ratio_value(active_settings.get("ratio"))
        ratio_text = self._format_ratio_display(ratio_value)

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
            (0, float(ratio_value)) if ratio_value is not None else (1, 0.0),
        )
        item.setData(
            PHOTO_COL_RATING,
            PHOTO_LIST_SORT_ROLE,
            (0, int(rating_value)) if rating_value is not None else (1, 0),
        )
        item.setData(PHOTO_COL_SHUTTER, PHOTO_LIST_SORT_ROLE, shutter_sort)
        item.setData(PHOTO_COL_ISO, PHOTO_LIST_SORT_ROLE, iso_sort)
        item.setData(PHOTO_COL_APERTURE, PHOTO_LIST_SORT_ROLE, aperture_sort)
        if resort:
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

    def _add_photo_paths(
        self,
        paths: Iterable[Path],
        *,
        select_last_added: bool = False,
        pre_added_report_db_count: int = 0,
    ) -> None:
        valid_paths: list[Path] = []
        for incoming in paths:
            try:
                path = incoming if isinstance(incoming, Path) else Path(str(incoming))
                path = path.resolve(strict=False)
            except Exception:
                continue
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            valid_paths.append(path)

        auto_added_report_db_count = self._auto_add_report_db_paths_for_photos(valid_paths)
        total_auto_added_report_db_count = max(0, int(pre_added_report_db_count)) + auto_added_report_db_count

        existing_keys = {_path_key(path) for path in self._list_photo_paths()}
        default_settings = self._build_current_render_settings()
        add_count = 0
        last_added_item: QTreeWidgetItem | None = None
        added_paths: list[Path] = []

        for path in valid_paths:
            key = _path_key(path)
            if key in existing_keys:
                continue
            existing_keys.add(key)

            current_settings = self._photo_override_settings_from_snapshot(default_settings)
            self.photo_render_overrides[key] = current_settings
            item = PhotoListItem(["", "", "", "", "", "", "", "", "", ""])
            sequence_value = self._next_photo_sequence_value()
            item.setData(PHOTO_COL_SEQ, PHOTO_LIST_SORT_ROLE, (0, sequence_value))
            item.setData(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE, str(path))
            item.setData(
                PHOTO_COL_ROW,
                PHOTO_LIST_PHOTO_INFO_ROLE,
                _template_context.ensure_editor_photo_info(path, crop_box=current_settings.get("crop_box")),
            )
            item.setData(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE, sequence_value)
            item.setData(PHOTO_COL_ROW, PHOTO_LIST_SORT_ROLE, (0, sequence_value))
            item.setToolTip(PHOTO_COL_ROW, "")
            item.setTextAlignment(PHOTO_COL_ROW, int(Qt.AlignmentFlag.AlignCenter))
            self.photo_list.addTopLevelItem(item)
            self._remember_photo_item(path, item)
            self.photo_list_metadata_cache[key] = {"SourceFile": str(path)}
            self._photo_list_metadata_pending_keys.add(key)
            self._update_photo_list_item_display(
                path,
                raw_metadata=self.photo_list_metadata_cache[key],
                settings=current_settings,
                resort=False,
            )
            add_count += 1
            last_added_item = item
            added_paths.append(path)

        if add_count == 0 and total_auto_added_report_db_count == 0:
            self._set_status("没有新增照片。")
            return

        if select_last_added and last_added_item is not None:
            self.photo_list.setCurrentItem(last_added_item)
        elif self.photo_list.currentItem() is None and self.photo_list.topLevelItemCount() > 0:
            first_item = self.photo_list.topLevelItem(0)
            if first_item is not None:
                self.photo_list.setCurrentItem(first_item)

        self.photo_list.resort()
        self.photo_list.refresh_row_numbers()
        if added_paths:
            self._restart_photo_list_metadata_loader()

        if add_count > 0 and total_auto_added_report_db_count > 0:
            self._set_status(f"已添加 {add_count} 张照片，并自动添加 {total_auto_added_report_db_count} 个 report.db。")
            return
        if add_count > 0:
            self._set_status(f"已添加 {add_count} 张照片。")
            return
        self._set_status(f"没有新增照片，已自动添加 {total_auto_added_report_db_count} 个 report.db。")

    def _add_received_photo_paths(self, paths: Iterable[Path]) -> None:
        """处理外部 received 文件：先补充 report.db，再沿用现有加图逻辑。"""
        pending_paths = list(paths)
        pre_added_report_db_count = self._auto_add_report_db_paths_for_received_files(pending_paths)
        self._add_photo_paths(
            pending_paths,
            select_last_added=True,
            pre_added_report_db_count=pre_added_report_db_count,
        )

    def add_received_file_paths(self, paths: Iterable[str | Path]) -> None:
        """
        统一处理 argv / socket / FileOpen 三种入口收到的文件路径，并加入照片列表。
        可在任意线程调用；内部通过 QTimer.singleShot 投递到主线程执行。
        """
        normalized_paths = normalize_file_paths(paths)
        if not normalized_paths:
            return
        _log.info(
            "received file list count=%s, scheduling add to photo list: %s",
            len(normalized_paths),
            normalized_paths,
        )
        path_objs = [Path(path_text) for path_text in normalized_paths]
        QTimer.singleShot(
            0,
            lambda pending_paths=path_objs: self._add_received_photo_paths(pending_paths),
        )

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
        if removed_keys:
            self._bird_box_cache.clear()
            self.photo_list.refresh_row_numbers()
            if self._photo_list_metadata_pending_keys:
                self._restart_photo_list_metadata_loader()
            else:
                self._stop_photo_list_metadata_loader(wait=False, reset_progress=True)

        if self.photo_list.topLevelItemCount() == 0:
            self.placeholder_path = None
            self.current_path = None
            self.current_photo_info = None
            self.current_source_image = None
            self.current_raw_metadata = {}
            self.current_metadata_context = {}
            self.current_file_label.setText("当前照片: 未选择")
            self.last_rendered = None
            self._show_placeholder_preview()

        self._set_status(f"已删除 {len(selected_items)} 项。")

    def _clear_photos(self) -> None:
        self._stop_photo_list_metadata_loader(wait=False, reset_progress=True)
        self.photo_list.clear()
        self.raw_metadata_cache.clear()
        self.photo_list_metadata_cache.clear()
        self._photo_item_map.clear()
        self._photo_list_metadata_pending_keys.clear()
        self.photo_render_overrides.clear()
        self._bird_box_cache.clear()
        self.placeholder_path = None
        self.current_path = None
        self.current_photo_info = None
        self.current_source_image = None
        self.current_raw_metadata = {}
        self.current_metadata_context = {}
        self.current_file_label.setText("当前照片: 未选择")
        self.last_rendered = None
        self._show_placeholder_preview()
        self._set_status("已清空照片列表。")

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

        try:
            image = decode_image(path, decoder="auto")
        except Exception as exc:
            self._show_error("读取失败", str(exc))
            return

        self.placeholder_path = None
        self.current_path = path
        self.current_source_image = image
        self._invalidate_original_mode_cache()
        self.current_raw_metadata = self._load_raw_metadata(path)
        photo_info = current.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE)
        self.current_photo_info = _template_context.ensure_editor_photo_info(
            photo_info if isinstance(photo_info, _template_context.PhotoInfo) else path,
            raw_metadata=self.current_raw_metadata,
        )
        current.setData(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE, self.current_photo_info)
        self.current_metadata_context = _build_metadata_context(self.current_photo_info, self.current_raw_metadata)
        settings = self._render_settings_for_path(path, prefer_current_ui=False)
        self._set_photo_crop_box_for_path(path, settings.get("crop_box"))
        self._apply_render_settings_to_ui(settings)
        self._update_photo_list_item_display(path, raw_metadata=self.current_raw_metadata, settings=settings)
        self.current_file_label.setText(f"当前照片: {path}")
        self.render_preview()

    def _load_raw_metadata(self, path: Path) -> dict[str, Any]:
        key = _path_key(path)
        if key in self.raw_metadata_cache:
            return self.raw_metadata_cache[key]

        resolved = path.resolve(strict=False)
        raw_metadata: dict[str, Any]
        try:
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
        try:
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
        export_draw_banner = _parse_bool_value(current_render_settings.get("draw_banner"), True)
        export_draw_text = _parse_bool_value(current_render_settings.get("draw_text"), True)
        export_draw_focus = _parse_bool_value(current_render_settings.get("draw_focus"), False)
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
            # 导出图像时，叠加信息的三个总开关统一跟随当前界面状态，
            # 但仍保留每张照片各自的模板/裁切重载。
            settings["draw_banner"] = export_draw_banner
            settings["draw_text"] = export_draw_text
            settings["draw_focus"] = export_draw_focus

            source_image = None
            if self.current_source_image is not None and is_current_path:
                source_image = self.current_source_image.copy()

            jobs.append(
                VideoFrameJob(
                    path=path,
                    settings=settings,
                    raw_metadata=raw_metadata,
                    metadata_context=metadata_context,
                    photo_info=photo_info,
                    source_image=source_image,
                )
            )
            if callable(progress_callback):
                progress_callback(index, total)
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
        cancel_text = str(message or "").strip() or "视频导出已中断。"
        elapsed = self._consume_video_export_elapsed_time()
        if elapsed is not None:
            cancel_text = f"{cancel_text} | 已耗时 {self._format_export_elapsed_time(elapsed)}"
        self.video_export_panel.set_busy(False, status_text=cancel_text)
        self._set_status(cancel_text)
        self._cleanup_video_export_worker()
        QMessageBox.information(self, "视频导出已中断", cancel_text)

    def _on_video_export_failed(self, message: str) -> None:
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

        worker = VideoExportWorker(
            jobs=jobs,
            options=options,
            template_paths=dict(self.template_paths),
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
            self.photo_render_overrides[_path_key(path)] = normalized
            self._set_photo_crop_box_for_path(path, normalized.get("crop_box"))
            self._update_photo_list_item_display(path, settings=normalized)

        if self.current_path is not None:
            current_key = _path_key(self.current_path)
            if any(_path_key(path) == current_key for path in targets):
                self.render_preview()

        self._set_status(f"已将当前裁切重载设置应用到 {len(targets)} 张照片。")

    def _apply_current_settings_to_all_photos(self) -> None:
        targets = self._list_photo_paths()
        if not targets:
            self._set_status("照片列表为空。")
            return

        snapshot = self._photo_override_settings_from_snapshot(self._build_current_render_settings())
        for path in targets:
            normalized = self._clone_render_settings(snapshot)
            self.photo_render_overrides[_path_key(path)] = normalized
            self._set_photo_crop_box_for_path(path, normalized.get("crop_box"))
            self._update_photo_list_item_display(path, settings=normalized)

        if self.current_path is not None:
            self.render_preview()
        self._set_status(f"已将当前裁切重载设置应用到全部 {len(targets)} 张照片。")


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
    about_info = _load_birdstamp_about_info()
    app_name = _birdstamp_product_name(about_info)
    if hasattr(app, "setApplicationName"):
        app.setApplicationName(app_name)
    if hasattr(app, "setApplicationDisplayName"):
        app.setApplicationDisplayName(app_name)
    if hasattr(app, "setApplicationVersion"):
        app.setApplicationVersion(str(about_info.get("version", "")))
    window = BirdStampEditorWindow()
    _log.info("editor window created")

    def on_files_received(paths: Iterable[str | Path]) -> None:
        window.add_received_file_paths(paths)

    install_file_open_handler(app, on_files_received)
    _log.info("FileOpen handler installed")

    # 热接收：单例 IPC，其它进程通过 send_file_list_to_running_app 发来文件列表时加入本窗口照片列表
    receiver = SingleInstanceReceiver(SEND_TO_APP_ID, on_files_received)
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
    if startup_inputs:
        QTimer.singleShot(0, lambda pending_paths=startup_inputs: on_files_received(pending_paths))
    exit_code = app.exec()
    _log.info("qt event loop exited code=%s window_visible=%s", exit_code, window.isVisible())
