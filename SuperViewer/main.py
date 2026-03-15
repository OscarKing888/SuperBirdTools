#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super Viewer - 图片 EXIF等元信息查看器
支持拖拽图片到窗口，使用 piexif 读取并展示全部 EXIF 数据。
"""

import json
import os
import sys
import shutil
import re
import subprocess
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

try:
    from PyQt6.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QListView,
        QMenu,
        QDialog,
        QPushButton,
        QToolButton,
        QDialogButtonBox,
        QTableWidget,
        QTableWidgetItem,
        QSplitter,
        QFrame,
        QFileDialog,
        QMessageBox,
        QHeaderView,
        QAbstractItemView,
        QScrollArea,
        QGroupBox,
        QGridLayout,
        QCheckBox,
        QComboBox,
        QSpinBox,
        QTabWidget,
        QTreeView,
        QTreeWidget,
        QTreeWidgetItem,
        QSizePolicy,
        QStyledItemDelegate,
        QStackedWidget,
        QSlider,
    )
    from PyQt6.QtCore import Qt, QMimeData, QSize, QDir, QThread, QTimer, pyqtSignal, QModelIndex, QRect
    from PyQt6.QtGui import QPixmap, QImage, QTransform, QDragEnterEvent, QDropEvent, QFont, QPalette, QColor, QAction, QIcon, QFileSystemModel, QPainter, QBrush, QPen
except ImportError:
    from PyQt5.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QListView,
        QMenu,
        QDialog,
        QPushButton,
        QToolButton,
        QDialogButtonBox,
        QTableWidget,
        QTableWidgetItem,
        QSplitter,
        QFrame,
        QFileDialog,
        QMessageBox,
        QHeaderView,
        QAbstractItemView,
        QScrollArea,
        QGroupBox,
        QGridLayout,
        QCheckBox,
        QComboBox,
        QSpinBox,
        QTabWidget,
        QTreeView,
        QTreeWidget,
        QTreeWidgetItem,
        QFileSystemModel,
        QSizePolicy,
        QStyledItemDelegate,
        QStackedWidget,
        QSlider,
    )
    from PyQt5.QtCore import Qt, QMimeData, QSize, QDir, QThread, QTimer, pyqtSignal, QModelIndex, QRect
    from PyQt5.QtGui import QPixmap, QImage, QTransform, QDragEnterEvent, QDropEvent, QFont, QPalette, QColor, QAction, QIcon, QPainter, QBrush, QPen

from app_common import show_about_dialog, load_about_images, load_about_info, AppInfoBar
from app_common.log import get_logger
from app_common.exif_io import (
    get_exiftool_executable_path,
    run_exiftool_json,
    write_exif_with_exiftool,
    write_exif_with_exiftool_by_key,
    write_meta_with_exiftool,
    write_meta_with_piexif,
    _get_exiftool_tag_target,
    read_xmp_sidecar,
    extract_metadata_with_xmp_priority,
)
from app_common.file_browser import DirectoryBrowserWidget, FileListPanel
from app_common.focus_calc import (
    extract_focus_box,
    resolve_focus_camera_type_from_metadata,
    resolve_focus_display_orientation,
)
from app_common.preview_canvas import (
    PREVIEW_COMPOSITION_GRID_LINE_WIDTHS,
    PREVIEW_COMPOSITION_GRID_MODES,
    PreviewCanvas,
    PreviewOverlayOptions,
    PreviewOverlayState,
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
)
from app_common.report_db import PHOTO_COLUMNS, find_report_root, ReportDB
from app_common.send_to_app import (
    ensure_file_open_aware_application,
    get_initial_file_list_from_argv,
    install_file_open_handler,
    send_file_list_to_running_app,
    SingleInstanceReceiver,
    send_files_to_app,
    get_external_apps,
)
from app_common.send_to_app.settings_ui import show_external_apps_settings_dialog
from app_common.superviewer_user_options import (
    USER_OPTIONS_FILENAME,
    PERSISTENT_THUMB_SIZE_LEVELS,
    KEY_NAVIGATION_FPS_OPTIONS,
    get_user_options_path,
    get_runtime_user_options,
    get_keep_view_on_switch,
    save_user_options,
    reload_runtime_user_options,
    apply_runtime_user_options,
)

PREVIEW_GRID_MODE_ITEMS = (
    ("none", "构图线：不显示"),
    ("thirds", "构图线：均分九宫格"),
    ("golden_thirds", "构图线：黄金分割九宫格"),
    ("square", "构图线：方格网格"),
    ("diag_square", "构图线：对角线 + 方格"),
    ("crosshair", "构图线：中心十字线"),
)
PREVIEW_GRID_MODE_COMBO_WIDTH = 190
PREVIEW_GRID_LINE_WIDTH_COMBO_WIDTH = 120
PHOTO_PREVIEW_MEMORY_CACHE_LIMIT = 2048
PHOTO_PREVIEW_FOCUS_SIZE_VARIANT_LIMIT = 4
FOCUS_PRELOAD_BATCH_SIZE = 12
FOCUS_PRELOAD_CANONICAL_SIZE = (0, 0)
FOCUS_CACHE_STATUS_UNKNOWN = "unknown"
FOCUS_CACHE_STATUS_LOADING = "loading"
FOCUS_CACHE_STATUS_READY = "ready"
FOCUS_CACHE_STATUS_MISS = "miss"


@dataclass(slots=True)
class PhotoFocusMemoryCacheState:
    """
    单张照片在某一种预览图像尺寸下的焦点缓存。

    这里按预览图像像素尺寸分桶，而不是只按文件路径缓存，
    是为了兼容 report.db 保底路径在拿不到原始宽高时仍可能依赖当前预览尺寸。
    以后如果新增别的尺寸相关 overlay，也沿着这个结构扩展。
    """

    status: str = FOCUS_CACHE_STATUS_UNKNOWN
    focus_box: tuple[float, float, float, float] | None = None
    used_path: str = ""
    request_id: int = 0
    size_independent: bool = False


@dataclass(slots=True)
class PhotoPreviewMemoryEntry:
    """
    单张照片的预览期内内存缓存。

    焦点、构图辅助线、检测框等和“当前预览体验”强相关的内存态，
    统一收敛到这里，避免未来把缓存散落成多个平行 dict。
    这样后续 AI Coding 工具扩展新 overlay 时，只需要沿着这个 entry 加字段。
    """

    source_path: str
    preview_path: str = ""
    focus_source_path: str = ""
    focus_states_by_preview_size: "OrderedDict[tuple[int, int], PhotoFocusMemoryCacheState]" = field(
        default_factory=OrderedDict
    )

    def get_or_create_focus_state(self, size: tuple[int, int]) -> PhotoFocusMemoryCacheState:
        size_key = (max(0, int(size[0])), max(0, int(size[1])))
        state = self.focus_states_by_preview_size.get(size_key)
        if state is None:
            state = PhotoFocusMemoryCacheState()
            self.focus_states_by_preview_size[size_key] = state
        else:
            self.focus_states_by_preview_size.move_to_end(size_key)
        while len(self.focus_states_by_preview_size) > PHOTO_PREVIEW_FOCUS_SIZE_VARIANT_LIMIT:
            self.focus_states_by_preview_size.popitem(last=False)
        return state

    def find_reusable_focus_state(self) -> PhotoFocusMemoryCacheState | None:
        for size_key, state in reversed(list(self.focus_states_by_preview_size.items())):
            if not state.size_independent:
                continue
            if state.status not in (FOCUS_CACHE_STATUS_READY, FOCUS_CACHE_STATUS_MISS):
                continue
            self.focus_states_by_preview_size.move_to_end(size_key)
            return state
        return None


def _build_preview_grid_line_width_icon(width: int) -> QIcon:
    """生成线宽预览图标，便于在下拉框里直观看到粗细。"""
    line_width = normalize_preview_composition_grid_line_width(width)
    pixmap = QPixmap(56, 16)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    except Exception:
        pass
    pen = QPen(QColor(0, 0, 0, 112))
    pen.setWidth(line_width)
    if hasattr(pen, "setCosmetic"):
        pen.setCosmetic(True)
    painter.setPen(pen)
    y = pixmap.height() / 2.0
    painter.drawLine(6, int(round(y)), pixmap.width() - 6, int(round(y)))
    painter.end()
    return QIcon(pixmap)

# PyQt5/6 枚举兼容
if hasattr(Qt, "AlignmentFlag"):
    _AlignCenter = Qt.AlignmentFlag.AlignCenter
    _LeftButton = Qt.MouseButton.LeftButton
    _KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    _SmoothTransformation = Qt.TransformationMode.SmoothTransformation
else:
    _AlignCenter = Qt.AlignCenter
    _LeftButton = Qt.LeftButton
    _KeepAspectRatio = Qt.KeepAspectRatio
    _SmoothTransformation = Qt.SmoothTransformation
if hasattr(QFrame, "Shape"):
    _FrameBox = QFrame.Shape.Box
    _FrameSunken = QFrame.Shadow.Sunken
else:
    _FrameBox = QFrame.Box
    _FrameSunken = QFrame.Sunken
if hasattr(QHeaderView, "ResizeMode"):
    _ResizeStretch = QHeaderView.ResizeMode.Stretch
else:
    _ResizeStretch = QHeaderView.Stretch
if hasattr(QAbstractItemView, "SelectionBehavior"):
    _SelectRows = QAbstractItemView.SelectionBehavior.SelectRows
elif hasattr(QAbstractItemView, "SelectRows"):
    _SelectRows = QAbstractItemView.SelectRows
else:
    _SelectRows = QAbstractItemView.SelectRows
if hasattr(QAbstractItemView, "EditTrigger"):
    _NoEditTriggers = QAbstractItemView.EditTrigger.NoEditTriggers
    _DoubleClicked = QAbstractItemView.EditTrigger.DoubleClicked
else:
    _NoEditTriggers = QAbstractItemView.NoEditTriggers
    _DoubleClicked = QAbstractItemView.DoubleClicked
try:
    _ItemIsEditable = Qt.ItemFlag.ItemIsEditable
except AttributeError:
    _ItemIsEditable = Qt.ItemIsEditable
try:
    _UserRole = Qt.ItemDataRole.UserRole
except AttributeError:
    _UserRole = Qt.UserRole
# QSplitter 水平方向：PyQt6 为 Qt.Orientation.Horizontal，PyQt5/PySide 为 Qt.Horizontal 或整型 1
_orient = getattr(Qt, "Orientation", None)
_Horizontal = getattr(_orient, "Horizontal", None) if _orient else None
if _Horizontal is None:
    _Horizontal = getattr(Qt, "Horizontal", 1)
if hasattr(QMessageBox, "Icon"):
    _MsgInfo = QMessageBox.Icon.Information
else:
    _MsgInfo = QMessageBox.Information
if hasattr(QMessageBox, "StandardButton"):
    _MsgOk = QMessageBox.StandardButton.Ok
else:
    _MsgOk = QMessageBox.Ok


import piexif
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS as PIL_TAGS

# 可选：用于 RAW 的 EXIF 回退读取
try:
    import exifread
except ImportError:
    exifread = None

# 可选：用于从 RAW 提取嵌入缩略图
try:
    import rawpy
except ImportError:
    rawpy = None

# 可选：HEIC/HEIF 读取与预览（Pillow 需注册后才能 Image.open(heic)）
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None

# 支持的图片扩展名（含各家相机 RAW 与 HEIC/HEIF）
IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif",
    ".heic", ".heif", ".hif",
    # Canon
    ".cr2", ".cr3", ".crw",
    # Nikon
    ".nef", ".nrw",
    # Sony
    ".arw", ".srf", ".sr2",
    # Panasonic
    ".rw2", ".raw",
    # Olympus
    ".orf", ".ori",
    # Fujifilm
    ".raf",
    # Adobe / Leica 等
    ".dng",
    # Pentax
    ".pef", ".ptx",
    # Sigma
    ".x3f",
    # Leica
    ".rwl",
    # 其他常见 RAW
    ".3fr", ".dcr", ".kdc", ".mef", ".mrw", ".rwz",
)
# 去重并保持顺序
IMAGE_EXTENSIONS = tuple(dict.fromkeys(e.lower() for e in IMAGE_EXTENSIONS))
RAW_EXTENSIONS = frozenset(
    e for e in IMAGE_EXTENSIONS
    if e not in (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".heic", ".heif", ".hif")
)
HEIF_EXTENSIONS = frozenset({".heic", ".heif", ".hif"})
PIEXIF_WRITABLE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".jpe", ".webp", ".tif", ".tiff"})
EXIFTOOL_IFD_GROUP_MAP = {
    "0th": "IFD0",
    "Exif": "EXIF",
    "GPS": "GPS",
    "1st": "IFD1",
    "Interop": "InteropIFD",
}


# 与主程序同目录下的配置文件
CONFIG_FILENAME = "super_viewer.cfg"
_log = get_logger("main")
META_IFD_NAME = "Meta"
META_TITLE_TAG_ID = "Title"
META_TITLE_PRIORITY_KEY = f"{META_IFD_NAME}:{META_TITLE_TAG_ID}"
META_DESCRIPTION_TAG_ID = "Description"
META_DESCRIPTION_PRIORITY_KEY = f"{META_IFD_NAME}:{META_DESCRIPTION_TAG_ID}"
CALC_IFD_NAME = "Calc"
HYPERFOCAL_TAG_ID = "HyperfocalDistance"
HYPERFOCAL_PRIORITY_KEY = f"{CALC_IFD_NAME}:{HYPERFOCAL_TAG_ID}"
# exiftool 中与“标题/描述”语义重复的 key，已在列表前部用 Meta:Title / Meta:Description 显示，不再在后面重复
EXIFTOOL_KEYS_DUPLICATE_OF_TITLE = frozenset({"XMP-dc:Title", "IFD0:XPTitle", "IFD0:DocumentName"})
EXIFTOOL_KEYS_DUPLICATE_OF_DESCRIPTION = frozenset(
    {
        "XMP-dc:Description",
        "IFD0:XPComment",
        "IFD0:ImageDescription",
        "EXIF:UserComment",
        "ExifIFD:UserComment",
    }
)
APP_ICON_CANDIDATES = (
    os.path.join("icons", "app_icon.png"),
    os.path.join("icons", "app_icon.ico"),
    os.path.join("icons", "app_icon.icns"),
)
def _get_app_dir() -> str:
    """返回当前程序目录（脚本目录或打包后可执行文件目录）。"""
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    if not app_dir:
        app_dir = os.getcwd()
    return app_dir


def _get_resource_path(relative_path: str) -> str | None:
    """按运行环境查找资源文件路径。"""
    candidates = [os.path.join(_get_app_dir(), relative_path)]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, relative_path))
        if sys.platform == "darwin":
            # macOS .app 中资源通常位于 Contents/Resources
            candidates.append(
                os.path.abspath(os.path.join(_get_app_dir(), "..", "Resources", relative_path))
            )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None
def _get_app_icon_path() -> str | None:
    """返回应用图标路径。"""
    for rel in APP_ICON_CANDIDATES:
        p = _get_resource_path(rel)
        if p:
            return p
    return None


LAST_SELECTED_DIRECTORY_FILENAME = "last_selected_directory.txt"
LEGACY_LAST_FOLDER_FILENAME = ".last_folder.txt"
USER_STATE_DIRNAME = "SuperViewer"


def _get_user_state_dir() -> str:
    """
    返回存放用户级状态文件的目录。

    这里不要落到 build/app 目录，避免打包产物被清理后把上次打开目录一起删掉。
    Windows 优先用 `%APPDATA%`，macOS 用 `~/Library/Application Support`，
    其他平台回退到用户 home 下的隐藏目录。
    """
    if sys.platform.startswith("win"):
        base_dir = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base_dir, USER_STATE_DIRNAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", USER_STATE_DIRNAME)
    return os.path.join(os.path.expanduser("~"), f".{USER_STATE_DIRNAME.lower()}")


def _get_last_selected_directory_file_path() -> str:
    """返回用户目录下的 last_selected_directory.txt 完整路径。"""
    return os.path.join(_get_user_state_dir(), LAST_SELECTED_DIRECTORY_FILENAME)


def _get_legacy_last_folder_file_path() -> str:
    """兼容旧版程序目录下的 .last_folder.txt。"""
    return os.path.join(_get_app_dir(), LEGACY_LAST_FOLDER_FILENAME)


def _get_legacy_last_selected_directory_file_path() -> str:
    """兼容旧版程序目录下的 last_selected_directory.txt。"""
    return os.path.join(_get_app_dir(), LAST_SELECTED_DIRECTORY_FILENAME)


def _read_last_selected_directory_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.readline().strip()
    except Exception:
        return None
    if not raw:
        return None
    resolved = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(resolved):
        return None
    return resolved


def load_last_folder_from_file() -> str | None:
    """读取上次打开的目录；优先读用户目录，兼容旧版程序目录文件。"""
    path = _read_last_selected_directory_file(_get_last_selected_directory_file_path())
    if path:
        return path
    path = _read_last_selected_directory_file(_get_legacy_last_selected_directory_file_path())
    if path:
        return path
    return _read_last_selected_directory_file(_get_legacy_last_folder_file_path())


def save_last_folder_to_file(path: str) -> None:
    """将上次打开的目录写入用户目录下的 last_selected_directory.txt。"""
    if not path or not os.path.isdir(path):
        return
    try:
        file_path = _get_last_selected_directory_file_path()
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(os.path.abspath(path))
    except Exception:
        pass


def _get_config_path() -> str:
    """返回 super_viewer.cfg 的完整路径，与当前运行的主程序同目录。"""
    app_dir = _get_app_dir()
    return os.path.join(app_dir, CONFIG_FILENAME)


def _get_config_resource_path() -> str:
    """返回可读取的 super_viewer.cfg 路径，打包后优先使用资源目录内的配置。"""
    return _get_resource_path(CONFIG_FILENAME) or _get_config_path()


def _load_settings() -> dict:
    """读取 EXIF.cfg，失败返回空字典。"""
    candidates = [_get_config_path()]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, CONFIG_FILENAME))
        if sys.platform == "darwin":
            candidates.append(os.path.abspath(os.path.join(_get_app_dir(), "..", "Resources", CONFIG_FILENAME)))
    seen = set()
    for path in candidates:
        norm = os.path.normpath(path)
        if norm in seen or not os.path.isfile(path):
            continue
        seen.add(norm)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.pop("last_selected_directory", None)
                return data
        except Exception:
            continue
    return {}


def _save_settings(data: dict):
    """写入 EXIF.cfg（UTF-8）。"""
    path = _get_config_path()
    try:
        data = dict(data or {})
        data.pop("last_selected_directory", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class SuperViewerUserOptionsDialog(QDialog):
    def __init__(self, parent=None, options: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("用户选项")
        self.setModal(True)
        self.resize(520, 260)

        opts = dict(options or get_runtime_user_options())
        cpu_count = max(1, os.cpu_count() or 1)
        max_workers = max(64, cpu_count * 2)

        layout = QVBoxLayout(self)

        info = QLabel(
            f"配置文件将保存在程序目录：{get_user_options_path()}\n"
            f"文件名：{USER_OPTIONS_FILENAME}"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(info)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        row = 0
        grid.addWidget(QLabel("后台图像加载线程数"), row, 0)
        self._spin_thumb_loader_workers = QSpinBox(self)
        self._spin_thumb_loader_workers.setRange(1, max_workers)
        self._spin_thumb_loader_workers.setValue(int(opts.get("thumbnail_loader_workers", cpu_count)))
        self._spin_thumb_loader_workers.setToolTip("缩略图后台加载线程数，默认等于 CPU 逻辑核心数。")
        grid.addWidget(self._spin_thumb_loader_workers, row, 1)
        grid.addWidget(QLabel(f"默认 {cpu_count}"), row, 2)

        row += 1
        grid.addWidget(QLabel("小缩略图生成线程数"), row, 0)
        self._spin_persistent_thumb_workers = QSpinBox(self)
        self._spin_persistent_thumb_workers.setRange(1, max_workers)
        self._spin_persistent_thumb_workers.setValue(int(opts.get("persistent_thumb_workers", cpu_count)))
        self._spin_persistent_thumb_workers.setToolTip("后台持久化小缩略图生成线程数，默认等于 CPU 逻辑核心数。")
        grid.addWidget(self._spin_persistent_thumb_workers, row, 1)
        grid.addWidget(QLabel(f"默认 {cpu_count}"), row, 2)

        row += 1
        grid.addWidget(QLabel("小缩略图最大尺寸"), row, 0)
        self._combo_persistent_thumb_size = QComboBox(self)
        for size in PERSISTENT_THUMB_SIZE_LEVELS:
            self._combo_persistent_thumb_size.addItem(f"{size} x {size}", size)
        current_size = int(opts.get("persistent_thumb_max_size", 128))
        current_index = PERSISTENT_THUMB_SIZE_LEVELS.index(current_size) if current_size in PERSISTENT_THUMB_SIZE_LEVELS else 0
        self._combo_persistent_thumb_size.setCurrentIndex(current_index)
        self._combo_persistent_thumb_size.setToolTip("会生成不高于该值的 128/256/512 预览层级。")
        grid.addWidget(self._combo_persistent_thumb_size, row, 1)
        grid.addWidget(QLabel("默认 128"), row, 2)

        row += 1
        grid.addWidget(QLabel("方向键连续浏览速率"), row, 0)
        self._combo_key_navigation_fps = QComboBox(self)
        for fps in KEY_NAVIGATION_FPS_OPTIONS:
            self._combo_key_navigation_fps.addItem(f"{fps} FPS", fps)
        current_fps = int(opts.get("key_navigation_fps", 24))
        current_index = KEY_NAVIGATION_FPS_OPTIONS.index(24)
        if current_fps in KEY_NAVIGATION_FPS_OPTIONS:
            current_index = KEY_NAVIGATION_FPS_OPTIONS.index(current_fps)
        self._combo_key_navigation_fps.setCurrentIndex(current_index)
        self._combo_key_navigation_fps.setToolTip("按住方向键连续浏览时，按该 FPS 节流移动速度。")
        grid.addWidget(self._combo_key_navigation_fps, row, 1)
        grid.addWidget(QLabel("默认 24 FPS"), row, 2)

        row += 1
        grid.addWidget(QLabel("预览图更换时保持缩放/位置"), row, 0)
        self._chk_keep_view = QCheckBox(self)
        self._chk_keep_view.setChecked(bool(opts.get("keep_view_on_switch", 1)))
        self._chk_keep_view.setToolTip("预览图更换时保持当前缩放比例和视图中心（不自动复位为适窗）。")
        grid.addWidget(self._chk_keep_view, row, 1)
        grid.addWidget(QLabel("默认开启"), row, 2)

        layout.addLayout(grid)

        note = QLabel("缩略视图会根据当前缩略图大小自动匹配最合适的一档预览图。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            (
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                if hasattr(QDialogButtonBox.StandardButton, "Ok")
                else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
            ),
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_options(self) -> dict[str, int]:
        return {
            "thumbnail_loader_workers": int(self._spin_thumb_loader_workers.value()),
            "persistent_thumb_workers": int(self._spin_persistent_thumb_workers.value()),
            "persistent_thumb_max_size": int(self._combo_persistent_thumb_size.currentData()),
            "key_navigation_fps": int(self._combo_key_navigation_fps.currentData()),
            "keep_view_on_switch": int(self._chk_keep_view.isChecked()),
        }


def load_last_selected_directory_from_settings() -> str | None:
    """读取上次在目录树中选中的目录路径。"""
    return load_last_folder_from_file()


def save_last_selected_directory_to_settings(path: str) -> None:
    """保存目录树最后一次选中的目录路径。"""
    save_last_folder_to_file(path)
    if os.path.isfile(_get_config_path()):
        _save_settings(_load_settings())

# IFD 分组显示名称
IFD_DISPLAY_NAMES = {
    META_IFD_NAME: "文件信息",
    "0th": "图像 (0th IFD)",
    "Exif": "Exif IFD",
    "GPS": "GPS",
    "1st": "缩略图 (1st IFD)",
    "Interop": "Interop IFD",
    CALC_IFD_NAME: "计算信息",
    "thumbnail": "缩略图数据",
}

# EXIF 中常见编码，按优先级尝试，避免乱码与跨平台显示问题
_EXIF_ENCODINGS = ("utf-8", "utf-16", "utf-16-be", "latin-1", "cp1252", "gbk", "gb2312", "big5")


def _safe_decode_bytes(data: bytes) -> str:
    """
    将 EXIF 字节按多种编码尝试解码，避免乱码。
    Latin-1 作为最后回退（任意字节可解码），保证不抛错且显示稳定。
    """
    if not data:
        return ""
    # 去除尾部常见填充 \x00
    data = data.rstrip(b"\x00")
    if not data:
        return ""
    for enc in _EXIF_ENCODINGS:
        try:
            s = data.decode(enc)
            # 若解码结果含大量替换符，可能误用编码，继续尝试
            if "\ufffd" in s and enc != "latin-1":
                continue
            return _sanitize_display_string(s)
        except (UnicodeDecodeError, LookupError):
            continue
    return _sanitize_display_string(data.decode("latin-1"))


def _sanitize_display_string(s: str) -> str:
    """
    清理用于界面显示的字符串：去掉控制字符与空字节，避免各系统显示异常或截断。
    """
    if not s:
        return s
    # 替换 NUL 及 C0 控制字符，保留 \t \n \r
    result = []
    for c in s:
        code = ord(c)
        if code == 0:
            result.append(" ")
        elif code < 32 and c not in "\t\n\r":
            result.append(" ")
        else:
            result.append(c)
    return "".join(result).strip()


def _decoded_looks_text(s: str) -> bool:
    """
    解码后的字符串是否像可读文本（用于决定显示为文本还是十六进制）。
    若大量替换符或大量控制字符则视为二进制/乱码，应显示为 hex。
    """
    if not s or len(s) < 2:
        return True
    n = len(s)
    replacement = s.count("\ufffd")
    if replacement > n * 0.15:  # 超过 15% 为替换符，可能解码错误
        return False
    # 可接受字符：可打印、空格、\t\n\r、以及常见 CJK 等
    ok = 0
    for c in s:
        code = ord(c)
        if code == 0 or (code < 32 and c not in "\t\n\r"):
            continue  # 控制字符不计入 ok
        if code < 127 or (code >= 0x4E00 and code <= 0x9FFF) or (code >= 0x3000 and code <= 0x303F):
            ok += 1
        else:
            ok += 1  # 其他 Unicode（如拉丁扩展、符号）也视为可读
    return ok >= n * 0.5  # 至少一半像可读字符


def _tuple_as_bytes(value: tuple) -> bytes | None:
    """若 tuple 全为 0–255 的整数则视为字节序列，返回 bytes；否则返回 None。"""
    if not value:
        return None
    try:
        if all(isinstance(x, int) and 0 <= x <= 255 for x in value):
            return bytes(value)
    except (TypeError, ValueError):
        pass
    return None


def _format_hex_bytes(data: bytes) -> str:
    """将二进制数据格式化为十六进制字符串（过长时截断）。"""
    if len(data) <= 64:
        return data.hex()
    return data[:64].hex() + "..."


def get_tag_type(ifd_name: str, tag_id: int) -> int | None:
    """读取 piexif 标签定义类型（piexif.TYPES.*），失败返回 None。"""
    info = piexif.TAGS.get(ifd_name, {}).get(tag_id)
    if isinstance(info, dict):
        t = info.get("type")
        if isinstance(t, int):
            return t
    return None


def format_exif_value(value, expected_type: int | None = None):
    """
    将 piexif 原始值格式化为可读字符串。
    规则：
    1) 仅当标签定义类型为 ASCII 时，按文本解码显示；
    2) 对于非文本定义的二进制值（bytes / byte tuple），统一显示 hex；
    3) 数值型（Rational/整数等）保持数值可读显示。
    """
    text_type = getattr(piexif.TYPES, "Ascii", 2)
    rational_types = {getattr(piexif.TYPES, "Rational", 5), getattr(piexif.TYPES, "SRational", 10)}

    if value is None:
        return ""
    if isinstance(value, bytes):
        if expected_type == text_type:
            s = _safe_decode_bytes(value)
            if len(s) > 2048:
                return s[:2048] + "\n... (已截断)"
            return s
        return _format_hex_bytes(value)
    if isinstance(value, tuple):
        if (
            expected_type in rational_types
            and len(value) == 2
            and isinstance(value[0], int)
            and isinstance(value[1], int)
        ):
            if value[1] != 0:
                return f"{value[0]}/{value[1]} ({value[0] / value[1]:.4f})"
            return f"{value[0]}/{value[1]}"
        data = _tuple_as_bytes(value)
        if data is not None:
            if expected_type == text_type:
                s = _safe_decode_bytes(data)
                if len(s) > 2048:
                    return s[:2048] + "\n... (已截断)"
                return s
            return _format_hex_bytes(data)
        # 非字节数组的 tuple（如 SHORT/LONG/RATIONAL 数组）保持数值可读
        return ", ".join(str(format_exif_value(v)) for v in value)
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _to_float_exif_number(v) -> float | None:
    """将 EXIF 数值（含有理数）转为浮点，失败返回 None。"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], int):
        if v[1] == 0:
            return None
        return float(v[0]) / float(v[1])
    return None


def _to_float_text_number(v) -> float | None:
    """Parse exiftool values like '1/800', '400 mm', 5.6 into float."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list):
        for x in v:
            f = _to_float_text_number(x)
            if f is not None:
                return f
        return None
    s = _sanitize_display_string(str(v or "")).strip()
    if not s:
        return None
    if "/" in s:
        a, _, b = s.partition("/")
        try:
            num = float(a.strip())
            den = float(b.strip().split()[0]) if b.strip() else 0.0
            if den != 0:
                return num / den
        except (TypeError, ValueError):
            pass
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _calc_hyperfocal_distance_from_exiftool_obj(obj: dict, default_coc_mm: float = 0.03) -> float | None:
    """Calculate hyperfocal distance from exiftool json object."""
    if not isinstance(obj, dict):
        return None

    def _pick(*keys):
        for k in keys:
            if k in obj:
                f = _to_float_text_number(obj.get(k))
                if f is not None:
                    return f
        return None

    f_mm = _pick("ExifIFD:FocalLength", "EXIF:FocalLength", "Composite:FocalLength")
    n = _pick("ExifIFD:FNumber", "EXIF:FNumber", "Composite:Aperture")
    if f_mm is None or n is None or f_mm <= 0 or n <= 0:
        return None

    coc_mm = default_coc_mm if default_coc_mm > 0 else 0.03
    focal_35 = _pick("ExifIFD:FocalLengthIn35mmFormat", "EXIF:FocalLengthIn35mmFormat")
    if focal_35 is not None and focal_35 > 0:
        crop = focal_35 / f_mm
        if crop > 0:
            coc_mm = 0.03 / crop
    if coc_mm <= 0:
        coc_mm = 0.03

    h_mm = (f_mm * f_mm) / (n * coc_mm) + f_mm
    if h_mm <= 0:
        return None
    return h_mm / 1000.0


def _calc_hyperfocal_distance_m(exif_data: dict, default_coc_mm: float = 0.03) -> float | None:
    """
    计算超焦距（米）。
    公式：H = f^2 / (N * c) + f
    其中 f=焦距(mm), N=光圈值, c=弥散圆(mm)。
    """
    exif_ifd = exif_data.get("Exif") if isinstance(exif_data, dict) else None
    if not isinstance(exif_ifd, dict):
        return None
    f_mm = _to_float_exif_number(exif_ifd.get(37386))   # FocalLength
    n = _to_float_exif_number(exif_ifd.get(33437))      # FNumber
    if f_mm is None or n is None or f_mm <= 0 or n <= 0:
        return None

    coc_mm = default_coc_mm if default_coc_mm > 0 else 0.03
    focal_35 = _to_float_exif_number(exif_ifd.get(41989))  # FocalLengthIn35mmFilm
    if focal_35 is not None and focal_35 > 0:
        crop = focal_35 / f_mm
        if crop > 0:
            coc_mm = 0.03 / crop
    if coc_mm <= 0:
        coc_mm = 0.03

    h_mm = (f_mm * f_mm) / (n * coc_mm) + f_mm
    if h_mm <= 0:
        return None
    return h_mm / 1000.0


def _format_hyperfocal_distance(value_m: float | None) -> str:
    """格式化超焦距显示文本。"""
    if value_m is None:
        return "无法计算"
    return f"{value_m:.2f} m"


def _extract_exiftool_text_value(value) -> str:
    """Normalize exiftool json value to display text."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for k in ("x-default", "zh-CN", "zh-cn", "en-US", "en-us"):
            if k in value:
                s = _extract_exiftool_text_value(value.get(k))
                if s:
                    return s
        for v in value.values():
            s = _extract_exiftool_text_value(v)
            if s:
                return s
        return ""
    if isinstance(value, list):
        parts = [_extract_exiftool_text_value(v) for v in value]
        parts = [p for p in parts if p]
        return " ".join(parts)
    return _sanitize_display_string(str(value))


def _is_likely_mojibake_meta_text(s: str) -> bool:
    """
    Detect common mojibake pattern like '??ͷѻȸ' (GBK bytes decoded as UTF-8).
    Keep heuristic conservative: only flag when placeholder + suspicious script mix appears.
    """
    txt = _sanitize_display_string(str(s or "")).strip()
    if not txt:
        return False
    has_cjk = any(0x4E00 <= ord(ch) <= 0x9FFF for ch in txt)
    if has_cjk:
        return False
    has_placeholder = ("?" in txt) or ("\ufffd" in txt)
    has_suspicious_script = any(
        (0x0370 <= ord(ch) <= 0x052F) or (0x0180 <= ord(ch) <= 0x024F)
        for ch in txt
    )
    return has_placeholder and has_suspicious_script


def _pick_preferred_meta_text(*candidates) -> str:
    """
    Pick first non-empty candidate; if earlier candidate looks mojibake, fallback to next healthy one.
    """
    cleaned = []
    for c in candidates:
        s = _extract_exiftool_text_value(c)
        if s:
            cleaned.append(s)
    if not cleaned:
        return ""
    for s in cleaned:
        if not _is_likely_mojibake_meta_text(s):
            return s
    return cleaned[0]


def _load_macos_mdls_text(path: str, attr_name: str) -> str | None:
    """读取 macOS Spotlight 元数据字段（kMDItem*）。"""
    if sys.platform != "darwin":
        return None
    try:
        cp = subprocess.run(
            ["mdls", "-name", attr_name, "-raw", path],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if cp.returncode != 0:
        return None
    s = _sanitize_display_string(cp.stdout.strip())
    if not s or s in ("(null)", "null"):
        return None
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = _sanitize_display_string(s[1:-1])
    return s or None


def _decode_xp_text_value(value) -> str | None:
    """解码 XP* 文本字段（常见为 UTF-16LE）。"""
    data = None
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, tuple):
        data = _tuple_as_bytes(value)
    if not data:
        return None
    # XP* 通常为 UTF-16LE，以 0x0000 结尾。不能直接 rstrip，否则可能丢掉最后一个字符。
    while len(data) >= 2 and data[-2:] == b"\x00\x00":
        data = data[:-2]
    if len(data) % 2 == 1:
        data = data[:-1]
    if not data:
        return None
    try:
        s = data.decode("utf-16-le", errors="ignore")
    except Exception:
        s = _safe_decode_bytes(data)
    s = _sanitize_display_string(s)
    return s or None


def _decode_xp_title_value(value) -> str | None:
    """解码 XPTitle（0th:40091）。"""
    return _decode_xp_text_value(value)


def _decode_xp_comment_value(value) -> str | None:
    """解码 XPComment（0th:40092）。"""
    return _decode_xp_text_value(value)


def _extract_ifd_text_value(ifd_data: dict, tag_id: int) -> str | None:
    """从指定 IFD 标签中提取文本。"""
    if not isinstance(ifd_data, dict):
        return None
    v = ifd_data.get(tag_id)
    if isinstance(v, bytes):
        s = _safe_decode_bytes(v)
    elif isinstance(v, tuple):
        b = _tuple_as_bytes(v)
        s = _safe_decode_bytes(b) if b is not None else None
    else:
        s = _sanitize_display_string(str(v)) if v is not None else None
    return s or None


def _decode_user_comment_value(value) -> str | None:
    """解码 UserComment（Exif:37510）。"""
    data = None
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, tuple):
        data = _tuple_as_bytes(value)
    if not data:
        return None
    if len(data) >= 8:
        prefix = data[:8]
        payload = data[8:]
        if prefix.startswith(b"ASCII"):
            s = _safe_decode_bytes(payload)
            return s or None
        if prefix.startswith(b"UNICODE"):
            for enc in ("utf-16-be", "utf-16-le", "utf-8"):
                try:
                    s = _sanitize_display_string(payload.decode(enc, errors="ignore"))
                    if s:
                        return s
                except Exception:
                    continue
        if prefix.startswith(b"JIS"):
            for enc in ("shift_jis", "cp932", "utf-8"):
                try:
                    s = _sanitize_display_string(payload.decode(enc, errors="ignore"))
                    if s:
                        return s
                except Exception:
                    continue
    s = _safe_decode_bytes(data)
    return s or None


def _extract_title_from_exif_data(exif_data: dict | None) -> str | None:
    """从 EXIF 数据中提取标题候选。"""
    if not isinstance(exif_data, dict):
        return None
    ifd0 = exif_data.get("0th")
    if not isinstance(ifd0, dict):
        return None

    # 1) XPTitle
    xp_title = _decode_xp_title_value(ifd0.get(40091))
    if xp_title:
        return xp_title

    # 2) DocumentName（仅标题字段，避免与描述互相污染）
    s = _extract_ifd_text_value(ifd0, 269)
    if s:
        if len(s) <= 120 and "\n" not in s:
            return s
    return None


def _extract_description_from_exif_data(exif_data: dict | None) -> str | None:
    """从 EXIF 数据中提取描述候选。"""
    if not isinstance(exif_data, dict):
        return None
    ifd0 = exif_data.get("0th")
    if isinstance(ifd0, dict):
        # 1) XPComment
        xp_comment = _decode_xp_comment_value(ifd0.get(40092))
        if xp_comment:
            return xp_comment
        # 2) ImageDescription（仅描述字段，避免与标题互相污染）
        s = _extract_ifd_text_value(ifd0, 270)
        if s:
            return s
    # 3) UserComment
    exif_ifd = exif_data.get("Exif")
    if isinstance(exif_ifd, dict):
        s = _decode_user_comment_value(exif_ifd.get(37510))
        if s:
            return s
    return None


def load_display_title(path: str, exif_data: dict | None = None) -> str:
    """
    读取用于展示的标题：
    1) EXIF 标题字段（XPTitle/DocumentName）
    2) macOS More Info 标题（kMDItemTitle）
    3) 无则返回“（未设置）”
    """
    title = _extract_title_from_exif_data(exif_data)
    if title:
        return title
    title = _load_macos_mdls_text(path, "kMDItemTitle")
    if title:
        return title
    return "（未设置）"


def load_display_description(path: str, exif_data: dict | None = None) -> str:
    """
    读取用于展示的描述：
    1) EXIF 描述字段（XPComment/ImageDescription/UserComment）
    2) macOS More Info 描述（kMDItemDescription）
    3) 无则返回“（未设置）”
    """
    desc = _extract_description_from_exif_data(exif_data)
    if desc:
        return desc
    desc = _load_macos_mdls_text(path, "kMDItemDescription")
    if desc:
        return desc
    return "（未设置）"


def _split_tag_name_tokens(name: str) -> list[str]:
    """将 EXIF 原始标签名切分为可读 token。"""
    if not name:
        return []
    s = str(name).strip()
    # 常见缩写先保护，避免被后续规则拆碎
    protected_tokens = ("YCbCr", "GPS", "Exif", "EXIF", "JPEG", "TIFF", "CFA", "XP", "XMP", "ISO", "DNG", "OECF")
    placeholders = {}
    for idx, token in enumerate(protected_tokens):
        ph = f"zzph{chr(97 + idx)}zz"
        if token in s:
            s = s.replace(token, f" {ph} ")
            placeholders[ph] = token
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", s)
    s = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return []
    tokens = [x for x in s.split(" ") if x]
    return [placeholders.get(x, x) for x in tokens]


def _format_english_tag_name(name: str) -> str:
    """将原始标签名格式化为更可读的英文。"""
    tokens = _split_tag_name_tokens(name)
    if not tokens:
        return _sanitize_display_string(str(name or ""))
    return " ".join(tokens)


def load_tag_name_token_map_zh_from_settings(data: dict | None = None) -> dict:
    """从 super_viewer.cfg 读取标签分词中文映射（exif_tag_name_token_map_zh）。"""
    default_map = {}
    if data is None:
        data = _load_settings()
    val = data.get("exif_tag_name_token_map_zh")
    if not isinstance(val, dict):
        return default_map
    merged = dict(default_map)
    for k, v in val.items():
        if isinstance(k, str) and isinstance(v, str):
            kk = _sanitize_display_string(k)
            vv = _sanitize_display_string(v)
            if kk and vv:
                merged[kk] = vv
    return merged


def _translate_tag_name_to_chinese(name: str, token_map: dict | None = None) -> str:
    """将英文 EXIF 标签名尽量转换为中文可读名称。"""
    if not name:
        return ""
    if token_map is None:
        token_map = load_tag_name_token_map_zh_from_settings()
    # 优先使用官方/常见精确名
    fast = token_map.get(name)
    if fast:
        return fast
    parts = []
    for tok in _split_tag_name_tokens(name):
        zh = token_map.get(tok)
        if zh is None:
            zh = token_map.get(tok.lower())
        parts.append(zh if zh else tok)
    if not parts:
        return _sanitize_display_string(str(name))
    text = " ".join(parts)
    # 连续中文词去空格，ASCII/缩写与中文之间保留空格
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return _sanitize_display_string(text)


def _build_default_exif_tag_names_zh(token_map: dict | None = None) -> dict:
    """基于 piexif 全量标签生成默认中文名映射。"""
    if token_map is None:
        token_map = load_tag_name_token_map_zh_from_settings()
    result = {}
    for ifd_name in ("0th", "Exif", "GPS", "1st", "Interop"):
        ifd_data = piexif.TAGS.get(ifd_name, {})
        for tag_id, info in ifd_data.items():
            key = f"{ifd_name}:{tag_id}"
            if isinstance(info, dict):
                raw_name = str(info.get("name", f"Tag {tag_id}"))
            else:
                raw_name = str(info)
            result[key] = _translate_tag_name_to_chinese(raw_name, token_map=token_map)
    result[META_TITLE_PRIORITY_KEY] = "标题"
    result[META_DESCRIPTION_PRIORITY_KEY] = "描述"
    result[HYPERFOCAL_PRIORITY_KEY] = "超焦距"
    return result


def load_exif_tag_names_zh_from_settings() -> dict:
    """从 super_viewer.cfg 读取 EXIF 标签中文名映射（key 为 ifd_name:tag_id），并补全缺失项。"""
    data = _load_settings()
    token_map = load_tag_name_token_map_zh_from_settings(data)
    merged = _build_default_exif_tag_names_zh(token_map=token_map)
    val = data.get("exif_tag_names_zh")
    if not isinstance(val, dict):
        return merged
    for k, v in val.items():
        if isinstance(k, str) and isinstance(v, str):
            vv = _sanitize_display_string(v)
            if vv:
                merged[k] = vv
    return merged


def get_tag_name(ifd_name: str, tag_id: int, use_chinese: bool = False, names_zh: dict | None = None) -> str:
    """
    获取 tag 的可读名称。
    use_chinese=True 时优先返回中文；若映射缺失则按标签英文名自动生成中文。
    """
    if ifd_name == META_IFD_NAME and str(tag_id) == META_TITLE_TAG_ID:
        if use_chinese:
            if names_zh is None:
                names_zh = load_exif_tag_names_zh_from_settings()
            zh_name = names_zh.get(META_TITLE_PRIORITY_KEY) if isinstance(names_zh, dict) else None
            return _sanitize_display_string(zh_name) if isinstance(zh_name, str) and zh_name.strip() else "标题"
        return "Title"
    if ifd_name == META_IFD_NAME and str(tag_id) == META_DESCRIPTION_TAG_ID:
        if use_chinese:
            if names_zh is None:
                names_zh = load_exif_tag_names_zh_from_settings()
            zh_name = names_zh.get(META_DESCRIPTION_PRIORITY_KEY) if isinstance(names_zh, dict) else None
            return _sanitize_display_string(zh_name) if isinstance(zh_name, str) and zh_name.strip() else "描述"
        return "Description"
    if ifd_name == "thumbnail":
        return "（二进制数据）"
    if ifd_name == CALC_IFD_NAME and str(tag_id) == HYPERFOCAL_TAG_ID:
        if use_chinese:
            if names_zh is None:
                names_zh = load_exif_tag_names_zh_from_settings()
            zh_name = names_zh.get(HYPERFOCAL_PRIORITY_KEY) if isinstance(names_zh, dict) else None
            return _sanitize_display_string(zh_name) if isinstance(zh_name, str) and zh_name.strip() else "超焦距"
        return "Hyperfocal Distance"
    key = f"{ifd_name}:{tag_id}"
    t = piexif.TAGS.get(ifd_name, {})
    info = t.get(tag_id)
    raw_name = ""
    if isinstance(info, dict):
        raw_name = str(info.get("name", f"Tag {tag_id}"))
    elif info is None:
        raw_name = f"Tag {tag_id}"
    else:
        raw_name = str(info)
    raw_name = _sanitize_display_string(raw_name)
    if use_chinese:
        if names_zh is None:
            names_zh = load_exif_tag_names_zh_from_settings()
        zh_name = names_zh.get(key) if isinstance(names_zh, dict) else None
        if isinstance(zh_name, str) and zh_name.strip():
            return _sanitize_display_string(zh_name)
        auto_zh = _translate_tag_name_to_chinese(raw_name)
        return auto_zh if auto_zh else f"标签 {tag_id}"
    if info is None:
        return f"Tag {tag_id}"
    return _format_english_tag_name(raw_name) or f"Tag {tag_id}"


def load_exif_piexif(path: str) -> dict | None:
    """使用 piexif 加载 EXIF（JPEG/WebP/TIFF）。"""
    try:
        return piexif.load(path)
    except Exception:
        return None


def load_exif_heic(path: str) -> dict | None:
    """使用 pillow-heif 加载 HEIC/HEIF/HIF 的 EXIF，返回与 piexif 相同的 dict 结构；无库或失败时返回 None。"""
    if pillow_heif is None:
        return None
    ext = Path(path).suffix.lower()
    if ext not in HEIF_EXTENSIONS:
        return None
    try:
        heif_file = pillow_heif.open_heif(path)
        if not heif_file or len(heif_file) == 0:
            return None
        img = heif_file[0]
        exif_bytes = None
        if hasattr(img, "info") and isinstance(getattr(img, "info", None), dict):
            exif_bytes = img.info.get("exif")
        if not exif_bytes:
            exif_bytes = getattr(img, "exif", None)
        if not exif_bytes or not isinstance(exif_bytes, bytes):
            return None
        return piexif.load(exif_bytes)
    except Exception:
        return None


def _load_raw_full_as_pixmap(path: str) -> QPixmap | None:
    """
    使用 rawpy 解码 RAW 为完整原图并转为 QPixmap（应用 EXIF 方向）。
    仅当 rawpy 可用且文件为支持的 RAW 格式时有效。
    """
    if rawpy is None or Path(path).suffix.lower() not in RAW_EXTENSIONS:
        return None
    try:
        import numpy as np
        with rawpy.imread(path) as rp:
            rgb = rp.postprocess()
        if rgb is None or rgb.size == 0:
            return None
        # rawpy 返回 (height, width, 3)，uint8；QImage 需要 (width, height, bpl, format)
        h, w = rgb.shape[0], rgb.shape[1]
        if rgb.dtype != np.uint8:
            rgb = (rgb.astype(np.float32) * (255.0 / rgb.max())).astype(np.uint8)
        data = rgb.copy().tobytes()
        bpl = w * 3
        fmt = QImage.Format.Format_RGB888 if hasattr(QImage.Format, "Format_RGB888") else QImage.Format_RGB888
        # 这里必须立刻 deep copy：QImage(data, ...) 默认引用外部缓冲区，
        # 在 mac/Qt 上可能出现右侧/底部黑块伪影（缓冲区生命周期/延迟转换导致）。
        qimg = QImage(data, w, h, bpl, fmt).copy()
        if qimg.isNull():
            return None
        pix = QPixmap.fromImage(qimg)
        return _apply_orientation_to_pixmap(pix, _get_orientation_from_file(path))
    except Exception:
        return None


def get_raw_thumbnail(path: str) -> bytes | None:
    """
    从 RAW 文件中获取嵌入的 JPEG 缩略图字节，用于预览。
    先尝试 piexif（TIFF 系 RAW 如 DNG/CR2/NEF），再尝试 rawpy。
    返回 None 表示无法获取缩略图。
    """
    if Path(path).suffix.lower() not in RAW_EXTENSIONS:
        return None
    # 1) piexif：TIFF 系 RAW 的 thumbnail 为 JPEG 字节
    try:
        data = piexif.load(path)
        thumb = data.get("thumbnail")
        if isinstance(thumb, bytes) and len(thumb) > 100:
            return thumb
    except Exception:
        pass
    # 2) rawpy：多数 RAW 格式的嵌入预览/缩略图
    if rawpy is None:
        return None
    try:
        with rawpy.imread(path) as rp:
            thumb = rp.extract_thumb()
        if thumb is None:
            return None
        if hasattr(rawpy, "ThumbFormat") and thumb.format == rawpy.ThumbFormat.JPEG:
            if isinstance(thumb.data, bytes):
                return thumb.data
    except Exception:
        pass
    return None


# EXIF Orientation 标签号 (TIFF/EXIF 0x0112)
ORIENTATION_TAG = 274


def _get_orientation_from_file(path: str) -> int:
    """
    从文件中读取 EXIF Orientation 值 (1–8)。
    先尝试 piexif（JPEG/TIFF/部分 RAW），再尝试 exifread（RAW）。
    返回 1 表示正常方向或未找到。
    """
    try:
        data = piexif.load(path)
        for ifd in ("0th", "Exif"):
            if data.get(ifd) and ORIENTATION_TAG in data[ifd]:
                v = data[ifd][ORIENTATION_TAG]
                if isinstance(v, int) and 1 <= v <= 8:
                    return v
    except Exception:
        pass
    if exifread:
        try:
            with open(path, "rb") as f:
                tags = exifread.process_file(f, details=False)
            # exifread 中 Orientation 可能在 "Image Orientation" 等
            for key in ("Image Orientation", "EXIF Orientation"):
                if key in tags:
                    try:
                        v = int(tags[key].values[0])
                        if 1 <= v <= 8:
                            return v
                    except (IndexError, ValueError, TypeError):
                        pass
        except Exception:
            pass
    return 1


def _apply_orientation_to_pixmap(pix: QPixmap, orientation: int) -> QPixmap:
    """
    根据 EXIF Orientation (1–8) 对 QPixmap 做旋转/翻转，返回新 QPixmap。
    标准仅此标签 (274) 决定显示方向；竖拍常见为 6(90° CW) / 8(270° CW)，
    部分相机/RAW 写入方向与常见约定相反，故 6/8 在此处互换以修正竖版倒置。
    """
    if orientation == 1 or pix.isNull():
        return pix
    tr = QTransform()
    if orientation == 2:
        tr.scale(-1, 1)
    elif orientation == 3:
        tr.rotate(180)
    elif orientation == 4:
        tr.scale(1, -1)
    elif orientation == 5:
        tr.rotate(-90)
        tr.scale(-1, 1)
    elif orientation == 6:
        tr.rotate(90)   # 竖拍：与 8 互换以兼容部分相机/RAW 的相反约定
    elif orientation == 7:
        tr.rotate(90)
        tr.scale(-1, 1)
    elif orientation == 8:
        tr.rotate(-90)  # 竖拍：与 6 互换以兼容部分相机/RAW 的相反约定
    else:
        return pix
    return pix.transformed(tr, _SmoothTransformation)


def _load_preview_pixmap_with_orientation(path: str) -> QPixmap | None:
    """
    加载图片并应用 EXIF 方向，使竖拍照片以竖版显示。
    仅对 PIL 可解码的格式（JPEG/PNG/WebP/TIFF 等）应用；失败或 RAW 时返回 None，由调用方回退。
    """
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            w, h = img.size
            data = img.tobytes()
        bpl = w * 3
        fmt = QImage.Format.Format_RGB888 if hasattr(QImage.Format, "Format_RGB888") else QImage.Format_RGB888
        # 这里必须立刻 deep copy：避免 QImage 引用 Python bytes 缓冲导致显示伪影。
        qimg = QImage(data, w, h, bpl, fmt).copy()
        if qimg.isNull():
            return None
        return QPixmap.fromImage(qimg)
    except Exception:
        return None


def load_exif_exifread(path: str) -> list[tuple[str, str, str]]:
    """使用 ExifRead 加载 EXIF（用于 RAW 等 piexif 不支持或失败时）。返回 [(group, name, value), ...]。"""
    if exifread is None:
        return []
    rows = []
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=True, extract_thumbnail=False)
        for key, tag in tags.items():
            if key in ("JPEGThumbnail", "TIFFThumbnail", "Filename"):
                continue
            # key 形如 "Image Make"、"EXIF DateTimeOriginal"
            if " " in key:
                group, name = key.split(None, 1)
            else:
                group, name = "ExifRead", key
            try:
                value_str = str(tag.printable) if hasattr(tag, "printable") else str(tag)
            except Exception:
                value_str = str(tag)
            value_str = _sanitize_display_string(value_str)
            rows.append((group, name, value_str))
    except Exception:
        pass
    return rows


def load_exif_pillow(path: str) -> list[tuple[str, str, str]]:
    """使用 Pillow 加载 EXIF（作为补充，如 PNG 等）。返回 [(ifd, name, value), ...]。"""
    rows = []
    try:
        img = Image.open(path)
        exif = img.getexif()
        if not exif:
            return rows
        for tag_id, value in exif.items():
            name = PIL_TAGS.get(tag_id, f"Tag {tag_id}")
            if isinstance(value, bytes):
                if tag_id == 37510 and len(value) > 8:  # UserComment，去掉前 8 字节编码标识
                    value = value[8:]
                    if not value.strip(b"\x00"):
                        rows.append(("Pillow Exif", str(name), "（无内容）"))
                        continue
                s = _safe_decode_bytes(value)
                if _decoded_looks_text(s):
                    value = s[:2048] + ("\n... (已截断)" if len(s) > 2048 else "")
                else:
                    value = value.hex() if len(value) <= 64 else value.hex() + "..."
            else:
                value = _sanitize_display_string(str(value))
            rows.append(("Pillow Exif", str(name), value))
        img.close()
    except Exception:
        pass
    return rows


def _parse_value_back(s: str, raw_value) -> tuple | bytes | int:
    """将用户输入的字符串按原始类型转回 EXIF 可写格式。"""
    if raw_value is None:
        return s.encode("utf-8")
    if isinstance(raw_value, bytes):
        return s.encode("utf-8")
    if isinstance(raw_value, int):
        try:
            return int(s.strip())
        except ValueError:
            return raw_value
    if isinstance(raw_value, tuple):
        if len(raw_value) == 2 and isinstance(raw_value[0], int) and isinstance(raw_value[1], int):
            # 有理数：支持 "a/b" 或 "1.5"
            s = s.strip()
            if "/" in s:
                a, _, b = s.partition("/")
                try:
                    return (int(a.strip()), int(b.strip()) if b.strip() else 1)
                except ValueError:
                    pass
            try:
                f = float(s)
                from fractions import Fraction
                fr = Fraction(f).limit_denominator(10000)
                return (fr.numerator, fr.denominator)
            except ValueError:
                pass
            return raw_value
        # XMLPacket / XMP 等：原为整数元组（字节），编辑后按 UTF-8 写回 bytes
        if len(raw_value) > 2 and all(isinstance(x, int) and 0 <= x <= 255 for x in raw_value):
            return s.encode("utf-8")
        if all(isinstance(x, int) for x in raw_value):
            try:
                return tuple(int(x) for x in s.replace(",", " ").split())
            except ValueError:
                return raw_value
    return s.encode("utf-8")


def _format_exception_message(e: Exception) -> str:
    """将异常格式化为可读文本，避免弹窗空白。"""
    msg = str(e).strip()
    if msg:
        return msg
    rep = repr(e).strip()
    if rep and rep != "Exception()":
        return rep
    return f"{type(e).__name__}（无详细错误信息）"


def _build_windows_app_id(app_name: str) -> str:
    """构造稳定的 Windows AppUserModelID。"""
    base = re.sub(r"[^A-Za-z0-9.]+", "", app_name) or "SuperViewer"
    return f"oskch.{base}"


def _get_product_display_name(about_info: dict | None = None) -> str:
    """Return the short product name used for window/app titles."""
    raw_name = ""
    if isinstance(about_info, dict):
        raw_name = _sanitize_display_string(about_info.get("app_name", "")) or ""
    if not raw_name:
        raw_name = "Super Viewer"
    short_name = raw_name.split(" - ", 1)[0].strip()
    return short_name or "Super Viewer"


def _build_main_window_title(about_info: dict | None = None) -> str:
    """Build the visible main window title from about config fields."""
    if not isinstance(about_info, dict):
        return "Super Viewer"

    app_name = _sanitize_display_string(about_info.get("app_name", "")) or "Super Viewer"
    version = _sanitize_display_string(about_info.get("version", "")) or ""
    author = _sanitize_display_string(about_info.get("作者", "")) or ""

    parts: list[str] = [app_name]
    if version:
        parts.append(version)
    if author:
        parts.append(author)
    return " - ".join(parts)


def _set_macos_process_name_via_objc(name: str) -> bool:
    """
    使用 Objective-C runtime 直接设置 macOS 进程名。
    该路径不依赖 PyObjC，可在仅有标准 Python 环境时生效。
    """
    try:
        import ctypes

        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    except Exception:
        return False

    try:
        objc_get_class = objc.objc_getClass
        objc_get_class.restype = ctypes.c_void_p
        objc_get_class.argtypes = [ctypes.c_char_p]

        sel_register_name = objc.sel_registerName
        sel_register_name.restype = ctypes.c_void_p
        sel_register_name.argtypes = [ctypes.c_char_p]

        msg_send_noarg = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        msg_send_cstr = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p
        )(("objc_msgSend", objc))
        msg_send_obj = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(("objc_msgSend", objc))

        ns_string_cls = objc_get_class(b"NSString")
        ns_process_info_cls = objc_get_class(b"NSProcessInfo")
        if not ns_string_cls or not ns_process_info_cls:
            return False

        sel_string_with_utf8 = sel_register_name(b"stringWithUTF8String:")
        sel_process_info = sel_register_name(b"processInfo")
        sel_set_process_name = sel_register_name(b"setProcessName:")

        ns_name = msg_send_cstr(ns_string_cls, sel_string_with_utf8, name.encode("utf-8"))
        if not ns_name:
            return False
        proc_info = msg_send_noarg(ns_process_info_cls, sel_process_info)
        if not proc_info:
            return False
        msg_send_obj(proc_info, sel_set_process_name, ns_name)
        return True
    except Exception:
        return False


def _apply_runtime_app_identity(app_name: str):
    """
    尽量把系统层面的应用名设置为 app_name，避免 Dock/任务栏显示为 Python。
    - macOS: 设置 NSProcessInfo 名称与 NSBundle 名称字段
    - Windows: 设置 AppUserModelID
    """
    name = _sanitize_display_string(app_name or "Super Viewer") or "Super Viewer"

    if sys.platform == "darwin":
        pyobjc_process_name_ok = False
        try:
            from Foundation import NSProcessInfo

            NSProcessInfo.processInfo().setProcessName_(name)
            pyobjc_process_name_ok = True
        except Exception:
            pass
        if not pyobjc_process_name_ok:
            _set_macos_process_name_via_objc(name)
        try:
            from Foundation import NSBundle

            bundle = NSBundle.mainBundle()
            if bundle is not None:
                info = bundle.localizedInfoDictionary()
                if info is None:
                    info = bundle.infoDictionary()
                if info is not None:
                    info["CFBundleName"] = name
                    info["CFBundleDisplayName"] = name
                    info["CFBundleExecutable"] = name
        except Exception:
            pass

    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_build_windows_app_id(name))
        except Exception:
            pass


def _normalize_meta_edit_text(text: str | None) -> str:
    """统一元数据编辑值，避免“（未设置）”被当成真实内容。"""
    s = _sanitize_display_string(str(text or ""))
    if s in ("（未设置）", "(未设置)", "<未设置>"):
        return ""
    return s


def _build_exiftool_key_to_piexif_key() -> dict:
    """exiftool Group:Tag -> piexif ifd:tag_id，用于排序与隐藏过滤。"""
    out = {}
    for ifd_name in ("0th", "Exif", "GPS", "1st", "Interop"):
        for tag_id in piexif.TAGS.get(ifd_name, {}):
            t = _get_exiftool_tag_target(ifd_name, tag_id)
            if t:
                out[t] = f"{ifd_name}:{tag_id}"
    return out


EXIFTOOL_KEY_TO_PIEXIF_KEY = _build_exiftool_key_to_piexif_key()

# exiftool group alias -> canonical group used by _build_exiftool_key_to_piexif_key
EXIFTOOL_GROUP_ALIASES = {
    "IFD0": "IFD0",
    "IFD1": "IFD1",
    "GPS": "GPS",
    "EXIF": "EXIF",
    "ExifIFD": "EXIF",
    "SubIFD": "EXIF",
    "Interop": "InteropIFD",
    "InteropIFD": "InteropIFD",
}

# exiftool alias keys that should map to configured piexif-style keys.
# This keeps exif_tag_priority (e.g. Exif:40962) effective even when exiftool returns aliases.
EXIFTOOL_ALIAS_KEY_TO_PIEXIF_KEY = {
    "IFD0:ModifyDate": "0th:306",
    "EXIF:ISO": "Exif:34855",
    "ExifIFD:ISO": "Exif:34855",
    "EXIF:ExifImageWidth": "Exif:40962",
    "EXIF:ExifImageHeight": "Exif:40963",
    "ExifIFD:ExifImageWidth": "Exif:40962",
    "ExifIFD:ExifImageHeight": "Exif:40963",
    # Keep compatibility with existing priority key 37378 in config.
    "EXIF:ExposureCompensation": "Exif:37378",
    "ExifIFD:ExposureCompensation": "Exif:37378",
}


def map_exiftool_key_to_piexif_key(exiftool_key: str | None) -> str | None:
    """
    Normalize exiftool Group:Tag key to piexif style key (ifd:tag_id).
    Returns None when no reliable mapping exists.
    """
    if not isinstance(exiftool_key, str):
        return None
    key = exiftool_key.strip()
    if not key or ":" not in key:
        return None

    mapped = EXIFTOOL_KEY_TO_PIEXIF_KEY.get(key)
    if mapped:
        return mapped

    mapped = EXIFTOOL_ALIAS_KEY_TO_PIEXIF_KEY.get(key)
    if mapped:
        return mapped

    group, tag_name = key.split(":", 1)
    group_norm = EXIFTOOL_GROUP_ALIASES.get(group)
    if not group_norm:
        return None

    mapped = EXIFTOOL_KEY_TO_PIEXIF_KEY.get(f"{group_norm}:{tag_name}")
    if mapped:
        return mapped

    mapped = EXIFTOOL_ALIAS_KEY_TO_PIEXIF_KEY.get(f"{group_norm}:{tag_name}")
    if mapped:
        return mapped
    return None


def get_tag_name_for_exiftool_key(
    exiftool_key: str, tag_name: str, use_chinese: bool, names_zh: dict | None = None
) -> str:
    """
    根据 exiftool 的 Group:Tag 键和原始标签名，返回显示用标签名。
    用于 exiftool 加载的行（ifd_name/tag_id 为 None）在中文/英文切换时正确显示。
    """
    if not use_chinese:
        return _format_english_tag_name(tag_name) or _sanitize_display_string(tag_name)
    if names_zh is None:
        names_zh = load_exif_tag_names_zh_from_settings()
    zh = names_zh.get(exiftool_key) if isinstance(names_zh, dict) else None
    if isinstance(zh, str) and zh.strip():
        return _sanitize_display_string(zh)
    piexif_key = map_exiftool_key_to_piexif_key(exiftool_key)
    if piexif_key:
        parts = piexif_key.split(":", 1)
        if len(parts) == 2:
            try:
                ifd_name, tag_id = parts[0], int(parts[1])
                return get_tag_name(ifd_name, tag_id, use_chinese=True, names_zh=names_zh)
            except ValueError:
                pass
    auto_zh = _translate_tag_name_to_chinese(tag_name)
    return auto_zh if auto_zh else _sanitize_display_string(tag_name)


def get_all_exif_tag_keys(use_chinese: bool = False) -> list[tuple]:
    """
    从 piexif.TAGS 收集所有可配置的 (key, 显示文本)。
    key = "ifd_name:tag_id"，显示文本 = "分组 - 标签名"。
    """
    result = []
    names_zh = load_exif_tag_names_zh_from_settings() if use_chinese else None
    title_name = get_tag_name(META_IFD_NAME, META_TITLE_TAG_ID, use_chinese=use_chinese, names_zh=names_zh)
    result.append((META_TITLE_PRIORITY_KEY, f"{IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME)} - {title_name}"))
    desc_name = get_tag_name(META_IFD_NAME, META_DESCRIPTION_TAG_ID, use_chinese=use_chinese, names_zh=names_zh)
    result.append((META_DESCRIPTION_PRIORITY_KEY, f"{IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME)} - {desc_name}"))
    calc_name = get_tag_name(CALC_IFD_NAME, HYPERFOCAL_TAG_ID, use_chinese=use_chinese, names_zh=names_zh)
    result.append((HYPERFOCAL_PRIORITY_KEY, f"{IFD_DISPLAY_NAMES.get(CALC_IFD_NAME, CALC_IFD_NAME)} - {calc_name}"))
    for ifd_name in ("0th", "Exif", "GPS", "1st", "Interop"):
        ifd_data = piexif.TAGS.get(ifd_name, {})
        if not ifd_data:
            continue
        group = IFD_DISPLAY_NAMES.get(ifd_name, ifd_name)
        for tag_id, info in ifd_data.items():
            name = get_tag_name(ifd_name, tag_id, use_chinese=use_chinese, names_zh=names_zh)
            key = f"{ifd_name}:{tag_id}"
            result.append((key, f"{group} - {name}"))
    return result


# 内置默认显示顺序：相机、镜头、曝光、ISO、时间等常用项
DEFAULT_EXIF_TAG_PRIORITY = [
    META_TITLE_PRIORITY_KEY,  # 元数据标题（macOS More Info / EXIF 标题）
    META_DESCRIPTION_PRIORITY_KEY,  # 元数据描述（macOS More Info / EXIF 描述）
    HYPERFOCAL_PRIORITY_KEY,  # 计算值：超焦距
    "0th:271",   # Make 制造商
    "0th:272",   # Model 型号
    "0th:306",   # DateTime
    "Exif:33434",  # ExposureTime 曝光时间
    "Exif:33437",  # FNumber 光圈
    "Exif:37386",  # FocalLength 焦距
    "Exif:37382",  # SubjectDistance 对焦距离
    "Exif:41996",  # SubjectDistanceRange 对焦距离范围
    "Exif:34855",  # ISOSpeedRatings
    "Exif:36867",  # DateTimeOriginal 拍摄时间
    "Exif:42036",  # LensModel 镜头型号
    "Exif:41987",  # WhiteBalance 白平衡
    "Exif:37378",  # ExposureBias 曝光补偿
    "Exif:40962",  # ExifImageWidth
    "Exif:40963",  # ExifImageLength
]


def load_tag_priority_from_settings() -> list:
    """从 super_viewer.cfg 读取优先显示的 tag key 列表，缺省时返回内置默认顺序。"""
    data = _load_settings()
    val = data.get("exif_tag_priority", [])
    lst = list(val) if isinstance(val, list) else []
    base = lst if lst else DEFAULT_EXIF_TAG_PRIORITY.copy()
    normalized = []
    seen = set()
    for key in (META_TITLE_PRIORITY_KEY, META_DESCRIPTION_PRIORITY_KEY, HYPERFOCAL_PRIORITY_KEY, *base):
        if not isinstance(key, str) or not key or key in seen:
            continue
        normalized.append(key)
        seen.add(key)
    return normalized


def save_tag_priority_to_settings(priority_keys: list):
    """将优先显示的 tag key 列表写入 EXIF.cfg。"""
    data = _load_settings()
    normalized = []
    seen = set()
    for key in (META_TITLE_PRIORITY_KEY, META_DESCRIPTION_PRIORITY_KEY, HYPERFOCAL_PRIORITY_KEY, *(list(priority_keys) if isinstance(priority_keys, list) else [])):
        if not isinstance(key, str) or not key or key in seen:
            continue
        normalized.append(key)
        seen.add(key)
    data["exif_tag_priority"] = normalized
    _save_settings(data)


def load_exif_tag_hidden_from_settings() -> set:
    """从 super_viewer.cfg 读取禁止显示的 tag key 集合（ifd:tag_id），如 0th:279。仅由 cfg 配置，无默认项。"""
    data = _load_settings()
    val = data.get("exif_tag_hidden", [])
    lst = val if isinstance(val, list) else []
    return {str(k).strip() for k in lst if isinstance(k, str) and k.strip()}


def save_exif_tag_hidden_to_settings(hidden_keys: list):
    """将禁止显示的 tag key 列表写入 EXIF.cfg。"""
    data = _load_settings()
    normalized = []
    seen = set()
    for k in (list(hidden_keys) if isinstance(hidden_keys, list) else []):
        s = str(k).strip() if k is not None else ""
        if not s or s in seen:
            continue
        normalized.append(s)
        seen.add(s)
    data["exif_tag_hidden"] = normalized
    _save_settings(data)


def load_tag_label_chinese_from_settings() -> bool:
    """是否使用中文显示 EXIF 标签名。"""
    data = _load_settings()
    return bool(data.get("exif_tag_label_chinese", False))


def save_tag_label_chinese_to_settings(use_chinese: bool):
    """保存 EXIF 标签名显示语言。"""
    data = _load_settings()
    data["exif_tag_label_chinese"] = use_chinese
    _save_settings(data)


def load_preview_grid_mode_from_settings() -> str:
    """读取预览区构图辅助线模式。"""
    data = _load_settings()
    return normalize_preview_composition_grid_mode(data.get("preview_grid_mode", "none"))


def save_preview_grid_mode_to_settings(mode: str | None) -> None:
    """保存预览区构图辅助线模式。"""
    normalized = normalize_preview_composition_grid_mode(mode)
    data = _load_settings()
    data["preview_grid_mode"] = normalized
    _save_settings(data)


def load_preview_grid_line_width_from_settings() -> int:
    """读取预览区构图辅助线线宽。"""
    data = _load_settings()
    return normalize_preview_composition_grid_line_width(data.get("preview_grid_line_width", 1))


def save_preview_grid_line_width_to_settings(width: int | str | None) -> None:
    """保存预览区构图辅助线线宽。"""
    normalized = normalize_preview_composition_grid_line_width(width)
    data = _load_settings()
    data["preview_grid_line_width"] = normalized
    _save_settings(data)


def load_hyperfocal_coc_mm_from_settings() -> float:
    """读取超焦距计算的默认弥散圆（mm），缺省 0.03。"""
    data = _load_settings()
    val = data.get("hyperfocal_coc_mm", 0.03)
    try:
        f = float(val)
        if f > 0:
            return f
    except (TypeError, ValueError):
        pass
    return 0.03


def apply_tag_priority(rows: list[tuple], priority_keys: list[str]) -> list[tuple]:
    """
    按配置的 tag 顺序重排：先把全部 EXIF 读入列表，按 exif_tag_priority 顺序
    在「EXIF 信息」列表前部显示设定的 tag，已显示的从列表中移除，再把剩余项追加到列表。
    """

    def row_key(row):
        if len(row) > 6 and row[6]:
            mapped = map_exiftool_key_to_piexif_key(row[6])
            return mapped if mapped else row[6]
        if len(row) < 2:
            return None
        ifd_name, tag_id = row[0], row[1]
        if ifd_name is None or tag_id is None:
            return None
        return f"{ifd_name}:{tag_id}"

    def row_signature(row):
        if len(row) < 5:
            return None
        name = _sanitize_display_string(str(row[3] or "")).strip().lower()
        value = _sanitize_display_string(str(row[4] or "")).strip()
        if not name and not value:
            return None
        return f"{name}\x1f{value}"

    normalized_priority = [k for k in priority_keys if isinstance(k, str) and k]
    if not normalized_priority:
        return list(rows)

    exif_info_list = list(rows)
    display_list = []
    displayed_keys = set()

    for key in normalized_priority:
        # 先在 exif_info_list 找到该 key 的首个条目放入前部，
        # 再从 exif_info_list 移除该 key 的所有条目，避免后面重复显示。
        matched_row = None
        remaining_rows = []
        for row in exif_info_list:
            if row_key(row) == key:
                if matched_row is None:
                    matched_row = row
                continue
            remaining_rows.append(row)
        exif_info_list = remaining_rows
        if matched_row is not None:
            display_list.append(matched_row)
            displayed_keys.add(key)
            # 标题/描述在前部显示后，移除其语义重复的 exiftool 行，避免后面重复显示
            if key == META_TITLE_PRIORITY_KEY:
                exif_info_list = [r for r in exif_info_list if not (len(r) > 6 and r[6] in EXIFTOOL_KEYS_DUPLICATE_OF_TITLE)]
            elif key == META_DESCRIPTION_PRIORITY_KEY:
                exif_info_list = [r for r in exif_info_list if not (len(r) > 6 and r[6] in EXIFTOOL_KEYS_DUPLICATE_OF_DESCRIPTION)]

    # 再次兜底：若剩余项中仍有已在前部显示过的 key，则不再重复显示。
    exif_info_list = [r for r in exif_info_list if row_key(r) not in displayed_keys]

    # 再做一层明显重复项去重：同“标签名+值”只保留首次出现。
    seen_signatures = set()
    for row in display_list:
        sig = row_signature(row)
        if sig:
            seen_signatures.add(sig)
    for row in exif_info_list:
        sig = row_signature(row)
        if sig and sig in seen_signatures:
            continue
        if sig:
            seen_signatures.add(sig)
        display_list.append(row)
    return display_list


def load_all_exif_exiftool(path: str, tag_label_chinese: bool = False) -> list[tuple]:
    """
    用 exiftool -j -G1 加载 EXIF，返回 7 元组列表，第 7 项为 exiftool_key（Group:Tag）。
    无 exiftool 或失败返回 []。
    """
    lst = run_exiftool_json(path)
    if not lst or not isinstance(lst[0], dict):
        return []
    obj = lst[0]
    names_zh = load_exif_tag_names_zh_from_settings() if tag_label_chinese else None
    hidden_keys = load_exif_tag_hidden_from_settings()
    hidden_exiftool = set()
    for k in hidden_keys:
        parts = k.split(":", 1)
        if len(parts) == 2:
            try:
                ifd_name, tag_id = parts[0], int(parts[1])
                t = _get_exiftool_tag_target(ifd_name, tag_id)
                if t:
                    hidden_exiftool.add(t)
            except ValueError:
                pass

    def _fmt(v):
        return _extract_exiftool_text_value(v)

    title_value = _pick_preferred_meta_text(
        obj.get("XMP-dc:Title"),
        obj.get("IFD0:XPTitle"),
        obj.get("IFD0:DocumentName"),
    )
    desc_value = _pick_preferred_meta_text(
        obj.get("XMP-dc:Description"),
        obj.get("IFD0:XPComment"),
        obj.get("IFD0:ImageDescription"),
        obj.get("EXIF:UserComment"),
        obj.get("ExifIFD:UserComment"),
    )
    desc_raw_value = _normalize_meta_edit_text(desc_value)
    rows = []
    rows.append(
        (
            META_IFD_NAME,
            META_TITLE_TAG_ID,
            IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME),
            get_tag_name(META_IFD_NAME, META_TITLE_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
            title_value,
            _normalize_meta_edit_text(title_value),
            None,
        )
    )
    rows.append(
        (
            META_IFD_NAME,
            META_DESCRIPTION_TAG_ID,
            IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME),
            get_tag_name(META_IFD_NAME, META_DESCRIPTION_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
            desc_value,
            desc_raw_value,
            None,
        )
    )
    rows.append(
        (
            CALC_IFD_NAME,
            HYPERFOCAL_TAG_ID,
            IFD_DISPLAY_NAMES.get(CALC_IFD_NAME, CALC_IFD_NAME),
            get_tag_name(CALC_IFD_NAME, HYPERFOCAL_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
            _format_hyperfocal_distance(
                _calc_hyperfocal_distance_from_exiftool_obj(
                    obj, default_coc_mm=load_hyperfocal_coc_mm_from_settings()
                )
            ),
            None,
            None,
        )
    )
    skip_keys = {"SourceFile", "File:FileName", "File:Directory", "File:FileSize", "File:FileModifyDate", "File:FileAccessDate", "File:FileCreateDate", "File:FilePermissions", "File:FileType", "File:FileTypeExtension", "File:MIMEType"}
    skip_keys |= EXIFTOOL_KEYS_DUPLICATE_OF_TITLE | EXIFTOOL_KEYS_DUPLICATE_OF_DESCRIPTION
    for key, value in obj.items():
        if not isinstance(key, str) or ":" not in key or key in skip_keys:
            continue
        group, tag_name = key.split(":", 1)
        if group in {"System", "ExifTool", "File"}:
            continue
        mapped_key = map_exiftool_key_to_piexif_key(key)
        if key in hidden_exiftool or (mapped_key in hidden_keys if mapped_key else False):
            continue
        value_str = _fmt(value)
        display_name = get_tag_name_for_exiftool_key(key, tag_name, tag_label_chinese, names_zh)
        rows.append((None, None, group, display_name, value_str, value, key))
    return rows


def load_all_exif(path: str, tag_label_chinese: bool = False) -> list[tuple]:
    """
    加载全部 EXIF，返回 [(ifd_name, tag_id, 分组, 标签名, 值字符串, raw_value, exiftool_key?), ...]。
    有 exiftool 时优先用 exiftool 读取（兼容性更好）；否则用 piexif/heic/exifread/pillow。
    """
    _log.info("[load_all_exif] EXIF 查询 path=%r", path)
    if get_exiftool_executable_path():
        exif_rows = load_all_exif_exiftool(path, tag_label_chinese=tag_label_chinese)
        if len(exif_rows) > 2:
            _log.info("[load_all_exif] 完成 来源=exiftool path=%r 条数=%s", path, len(exif_rows))
            return exif_rows
    rows = []
    names_zh = load_exif_tag_names_zh_from_settings() if tag_label_chinese else None
    data = load_exif_piexif(path) or (load_exif_heic(path) if Path(path).suffix.lower() in HEIF_EXTENSIONS else None)
    if data:
        _log.info("[load_all_exif] 来源=文件内(%s) path=%r", "heic" if Path(path).suffix.lower() in HEIF_EXTENSIONS else "piexif", path)
    title_value = load_display_title(path, exif_data=data)
    desc_value = load_display_description(path, exif_data=data)
    desc_raw_value = _normalize_meta_edit_text(desc_value)
    has_front_desc = bool(desc_raw_value)

    def _is_image_description_name(tag_name: str | None) -> bool:
        """判断标签名是否为图像描述（用于去重显示）。"""
        if not tag_name:
            return False
        s = _sanitize_display_string(str(tag_name)).strip()
        if not s:
            return False
        if s in ("图像描述", "ImageDescription", "Image Description"):
            return True
        key = re.sub(r"[\s_-]+", "", s).lower()
        return key == "imagedescription"
    exiftool_key_for = _get_exiftool_tag_target
    rows.append(
        (
            META_IFD_NAME,
            META_TITLE_TAG_ID,
            IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME),
            get_tag_name(META_IFD_NAME, META_TITLE_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
            title_value,
            _normalize_meta_edit_text(title_value),
            None,
        )
    )
    rows.append(
        (
            META_IFD_NAME,
            META_DESCRIPTION_TAG_ID,
            IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME),
            get_tag_name(META_IFD_NAME, META_DESCRIPTION_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
            desc_value,
            desc_raw_value,
            None,
        )
    )
    hidden_keys = load_exif_tag_hidden_from_settings()
    if data:
        hyperfocal_m = _calc_hyperfocal_distance_m(data, default_coc_mm=load_hyperfocal_coc_mm_from_settings())
        rows.append(
            (
                CALC_IFD_NAME,
                HYPERFOCAL_TAG_ID,
                IFD_DISPLAY_NAMES.get(CALC_IFD_NAME, CALC_IFD_NAME),
                get_tag_name(CALC_IFD_NAME, HYPERFOCAL_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
                _format_hyperfocal_distance(hyperfocal_m),
                None,  # 计算值，不可编辑
                None,
            )
        )
        for ifd_name in ("0th", "Exif", "GPS", "1st", "Interop"):
            ifd_data = data.get(ifd_name)
            if not ifd_data or not isinstance(ifd_data, dict):
                continue
            group = IFD_DISPLAY_NAMES.get(ifd_name, ifd_name)
            for tag_id, value in ifd_data.items():
                if f"{ifd_name}:{tag_id}" in hidden_keys:
                    continue
                # 前置“描述”已有值时，隐藏后续重复的 ImageDescription(0th:270)
                if has_front_desc and ifd_name == "0th" and tag_id == 270:
                    continue
                name = get_tag_name(ifd_name, tag_id, use_chinese=tag_label_chinese, names_zh=names_zh)
                raw = value
                tag_type = get_tag_type(ifd_name, tag_id)
                ek = exiftool_key_for(ifd_name, tag_id)
                rows.append((ifd_name, tag_id, group, name, format_exif_value(value, expected_type=tag_type), raw, ek))
        if data.get("thumbnail"):
            rows.append((None, None, IFD_DISPLAY_NAMES["thumbnail"], "（存在）", "是", None, None))
    n_before = len(rows)
    if len(rows) <= 2 and Path(path).suffix.lower() in RAW_EXTENSIONS and exifread:
        for group, name, value in load_exif_exifread(path):
            if has_front_desc and _is_image_description_name(name):
                continue
            rows.append((None, None, group, name, value, None, None))
        if len(rows) > n_before:
            _log.info("[load_all_exif] 补充 来源=exifread path=%r 新增条数=%s", path, len(rows) - n_before)
    n_before = len(rows)
    if len(rows) <= 2:
        for group, name, value in load_exif_pillow(path):
            if has_front_desc and _is_image_description_name(name):
                continue
            rows.append((None, None, group, name, value, None, None))
        if len(rows) > n_before:
            _log.info("[load_all_exif] 补充 来源=pillow path=%r 新增条数=%s", path, len(rows) - n_before)
    # 当 exiftool 不可用时，尝试读取 XMP sidecar 文件补充元数据
    n_before = len(rows)
    if not get_exiftool_executable_path():
        try:
            for group, name, value in read_xmp_sidecar(path):
                rows.append((None, None, group, name, value, None, None))
            if len(rows) > n_before:
                _log.info("[load_all_exif] 补充 来源=XMP_sidecar path=%r 新增条数=%s", path, len(rows) - n_before)
        except Exception:
            pass
    _log.info("[load_all_exif] 完成 path=%r 总条数=%s", path, len(rows))
    return rows


def _format_report_metadata_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _is_title_like_row(row: tuple) -> bool:
    if len(row) > 6 and row[6] in EXIFTOOL_KEYS_DUPLICATE_OF_TITLE:
        return True
    if len(row) >= 2 and row[0] == META_IFD_NAME and str(row[1]) == META_TITLE_TAG_ID:
        return True
    return False


def build_report_metadata_rows(report_row: dict | None) -> list[tuple]:
    if not isinstance(report_row, dict):
        return []

    rows: list[tuple] = []
    ordered_names = ["bird_species_cn", "bird_species_en"] + [
        name for name, _sql, _default in PHOTO_COLUMNS
        if name not in {"bird_species_cn", "bird_species_en"}
    ]
    name_map = {
        "bird_species_cn": "标题",
        "bird_species_en": "标题Eng",
        "title": "标题Raw",
    }
    for col_name in ordered_names:
        display_name = name_map.get(col_name, col_name)
        value_str = _format_report_metadata_value(report_row.get(col_name))
        rows.append((None, None, "ReportDB", display_name, value_str, report_row.get(col_name), None))
    return rows


def merge_report_metadata_rows(rows: list[tuple], report_row: dict | None) -> list[tuple]:
    if not isinstance(report_row, dict):
        return list(rows)
    merged_rows = list(rows)
    species_title = str(report_row.get("bird_species_cn") or "").strip()
    if species_title:
        merged_rows = [row for row in merged_rows if not _is_title_like_row(row)]
    return build_report_metadata_rows(report_row) + merged_rows


def _load_preview_pixmap_for_canvas(path: str) -> QPixmap | None:
    """加载预览用 QPixmap（原图，含方向修正），供 PreviewPanel 使用。"""
    pix = _load_preview_pixmap_with_orientation(path)
    is_raw = Path(path).suffix.lower() in RAW_EXTENSIONS
    if (pix is None or pix.isNull()) and is_raw:
        pix = _load_raw_full_as_pixmap(path)
    if (pix is None or pix.isNull()) and is_raw:
        thumb_data = get_raw_thumbnail(path)
        if thumb_data:
            pix = QPixmap()
            if pix.loadFromData(thumb_data):
                pix = _apply_orientation_to_pixmap(pix, _get_orientation_from_file(path))
                return pix
    if pix is None or pix.isNull():
        pix = QPixmap(path)
    if pix is not None and not pix.isNull() and is_raw:
        pix = _apply_orientation_to_pixmap(pix, _get_orientation_from_file(path))
    return pix


def _run_exiftool_json_for_focus(path: str) -> dict | None:
    """
    为焦点提取执行更完整的 exiftool 读取（-j -G1 -n -a -u）。
    这样可尽量拿到数值化字段与机型私有字段（如 FocusX/SubjectArea 等）。
    """
    exiftool_path = get_exiftool_executable_path()
    if not exiftool_path:
        return None
    path_norm = os.path.normpath(path)
    use_argfile = sys.platform.startswith("win") and any(ord(c) > 127 for c in path_norm)
    cmd_common = [
        exiftool_path,
        "-j",
        "-G1",
        "-n",
        "-a",
        "-u",
        "-charset",
        "filename=UTF8",
        "-api",
        "largefilesupport=1",
    ]
    try:
        if use_argfile:
            fd, argfile_path = tempfile.mkstemp(suffix=".args", prefix="exiftool_focus_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(path_norm + "\n")
                cp = subprocess.run(
                    [*cmd_common, "-@", argfile_path],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            finally:
                try:
                    os.unlink(argfile_path)
                except OSError:
                    pass
        else:
            cp = subprocess.run(
                [*cmd_common, path_norm],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        if cp.returncode != 0 or not (cp.stdout or "").strip():
            return None
        payload = json.loads(cp.stdout)
    except Exception:
        return None
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
        return None
    return payload if isinstance(payload, dict) else None


def _load_exifread_metadata_for_focus(path: str) -> dict[str, object]:
    """
    针对 RAW 焦点提取的 exifread 补充读取。
    仅抽取焦点相关与尺寸/机型关键字段，避免引入无关大块数据。
    """
    if exifread is None:
        return {}
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=True, extract_thumbnail=False)
    except Exception:
        return {}
    if not isinstance(tags, dict) or not tags:
        return {}

    out: dict[str, object] = {}

    def _tag_value(tag_obj):
        values = getattr(tag_obj, "values", None)
        if values not in (None, []):
            return values
        printable = getattr(tag_obj, "printable", None)
        if printable not in (None, ""):
            return printable
        return str(tag_obj)

    for key, tag in tags.items():
        lk = str(key).strip().lower()
        if not lk:
            continue
        keep = (
            lk in {"image make", "image model", "image orientation", "exif exifimagewidth", "exif exifimagelength"}
            or lk.startswith("makernote tag 0x2027")
            or lk.startswith("makernote tag 0x204a")
            or ("focus" in lk)
            or ("subject" in lk)
            or ("region" in lk)
        )
        if not keep:
            continue
        out[str(key)] = _tag_value(tag)

    # 常用别名，方便 focus_calc 的机型与尺寸解析
    if "Image Make" in out:
        out.setdefault("Make", out["Image Make"])
    if "Image Model" in out:
        out.setdefault("Model", out["Image Model"])
    if "EXIF ExifImageWidth" in out:
        out.setdefault("ExifImageWidth", out["EXIF ExifImageWidth"])
    if "EXIF ExifImageLength" in out:
        out.setdefault("ExifImageHeight", out["EXIF ExifImageLength"])

    return out


def _focus_metadata_value_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _merge_focus_metadata_parts(parts: list[tuple[str, dict | None]]) -> tuple[dict | None, list[str]]:
    merged: dict[str, object] = {}
    used_providers: list[str] = []
    for label, part in parts:
        if not isinstance(part, dict) or not part:
            continue
        used_providers.append(f"{label}:{len(part)}")
        for key, value in part.items():
            key_text = str(key).strip()
            if not key_text or not _focus_metadata_value_present(value):
                continue
            if key_text not in merged or not _focus_metadata_value_present(merged.get(key_text)):
                merged[key_text] = value
    return (merged or None), used_providers


def _load_heif_piexif_metadata_for_focus(path: str) -> dict[str, object]:
    data = load_exif_heic(path)
    if not isinstance(data, dict) or not data:
        return {}

    out: dict[str, object] = {"SourceFile": path}
    for ifd_name, ifd_data in data.items():
        if not isinstance(ifd_data, dict):
            continue
        tag_defs = piexif.TAGS.get(ifd_name, {})
        for tag_id, raw_value in ifd_data.items():
            info = tag_defs.get(tag_id)
            if not isinstance(info, dict):
                continue
            tag_name = str(info.get("name") or "").strip()
            if not tag_name:
                continue
            out[f"{ifd_name}:{tag_name}"] = raw_value

            if tag_name == "Make":
                out.setdefault("Make", raw_value)
            elif tag_name == "Model":
                out.setdefault("Model", raw_value)
            elif tag_name == "Orientation":
                out.setdefault("Orientation", raw_value)
            elif tag_name == "ExifImageWidth":
                out.setdefault("ExifImageWidth", raw_value)
            elif tag_name == "ExifImageLength":
                out.setdefault("ExifImageHeight", raw_value)
            elif tag_name == "ImageWidth":
                out.setdefault("ImageWidth", raw_value)
            elif tag_name == "ImageLength":
                out.setdefault("ImageHeight", raw_value)

    return out


def _load_focus_metadata_for_path(path: str) -> dict | None:
    path_text = str(path or "").strip()
    if not path_text:
        return None

    ext = Path(path_text).suffix.lower()
    primary = None
    try:
        primary = extract_metadata_with_xmp_priority(Path(path_text), mode="auto")
    except Exception:
        primary = None

    if ext in RAW_EXTENSIONS:
        parts = [
            ("exiftool", _run_exiftool_json_for_focus(path_text)),
            ("primary", primary if isinstance(primary, dict) else None),
            ("exifread", _load_exifread_metadata_for_focus(path_text)),
        ]
    elif ext in HEIF_EXTENSIONS:
        parts = [
            ("exiftool", _run_exiftool_json_for_focus(path_text)),
            ("heif_piexif", _load_heif_piexif_metadata_for_focus(path_text)),
            ("primary", primary if isinstance(primary, dict) else None),
            ("exifread", _load_exifread_metadata_for_focus(path_text)),
        ]
    else:
        parts = [
            ("exiftool", _run_exiftool_json_for_focus(path_text)),
            ("primary", primary if isinstance(primary, dict) else None),
        ]

    merged, providers = _merge_focus_metadata_parts(parts)
    _log.info(
        "[_load_focus_metadata_for_path] path=%r ext=%r providers=%s merged_keys=%s",
        path_text,
        ext,
        providers or ["none"],
        len(merged or {}),
    )
    return merged


def _focus_box_from_center_and_span(
    center_x: float, center_y: float, span_x: float, span_y: float
) -> tuple[float, float, float, float]:
    """由归一化中心与宽高比得到 (l,t,r,b)，并 clamp 到 [0,1]。"""
    cx = max(0.0, min(1.0, center_x))
    cy = max(0.0, min(1.0, center_y))
    sx = max(0.01, min(1.0, span_x))
    sy = max(0.01, min(1.0, span_y))
    half_x = sx * 0.5
    half_y = sy * 0.5
    left = cx - half_x
    right = cx + half_x
    top = cy - half_y
    bottom = cy + half_y
    if left < 0.0:
        right = min(1.0, right - left)
        left = 0.0
    if right > 1.0:
        left = max(0.0, left - (right - 1.0))
        right = 1.0
    if top < 0.0:
        bottom = min(1.0, bottom - top)
        top = 0.0
    if bottom > 1.0:
        top = max(0.0, top - (bottom - 1.0))
        bottom = 1.0
    return (left, top, right, bottom)


def _resolve_focus_display_orientation_for_path(
    path: str,
    raw_metadata: dict | None = None,
    *,
    camera_type=None,
) -> int:
    """
    为“显示对焦点”解析预览显示方向。

    注意：HEIF/HIF 可能没有标准 EXIF Orientation，但厂商私有字段仍会标记竖拍。
    因此焦点框路径要优先使用合并后的 metadata resolver，只有拿不到时才回退旧文件读取逻辑。
    """
    metadata = raw_metadata if isinstance(raw_metadata, dict) and raw_metadata else _load_focus_metadata_for_path(path)
    if isinstance(metadata, dict) and metadata:
        return resolve_focus_display_orientation(metadata, camera_type=camera_type)
    return _get_orientation_from_file(path)


def _load_focus_box_from_report_db(
    path: str,
    width: int,
    height: int,
    ref_size: tuple[int, int] | None = None,
    raw_metadata: dict | None = None,
    camera_type=None,
) -> tuple[float, float, float, float] | None:
    """
    从 report.db 的 focus_x、focus_y 构造焦点框（保底），框大小 128×128 像素。
    归一化时使用传入的 width/height 作为坐标系。
    焦点框显示方向仍要走 metadata-aware resolver，避免竖拍 HIF/HEIF 按横图显示。
    """
    if width <= 0 or height <= 0:
        return None
    try:
        directory = str(Path(path).parent)
        stem = Path(path).stem
        if not stem:
            return None
        report_root = find_report_root(directory)
        if not report_root:
            return None
        db = ReportDB.open_if_exists(report_root)
        if not db:
            return None
        try:
            row = db.get_photo(stem)
        finally:
            db.close()
        if not row:
            return None
        fx, fy = row.get("focus_x"), row.get("focus_y")
        if fx is None or fy is None:
            return None
        fx, fy = float(fx), float(fy)
        ref_w = float(ref_size[0]) if ref_size and len(ref_size) > 0 and int(ref_size[0]) > 0 else float(width)
        ref_h = float(ref_size[1]) if ref_size and len(ref_size) > 1 and int(ref_size[1]) > 0 else float(height)
        if ref_w <= 0 or ref_h <= 0:
            return None
        if fx <= 1.0 and fy <= 1.0:
            cx, cy = fx, fy
        else:
            cx = max(0.0, min(1.0, fx / ref_w))
            cy = max(0.0, min(1.0, fy / ref_h))
        span_x = 128.0 / ref_w
        span_y = 128.0 / ref_h
        box = _focus_box_from_center_and_span(cx, cy, span_x, span_y)
        orientation = _resolve_focus_display_orientation_for_path(
            path,
            raw_metadata,
            camera_type=camera_type,
        )
        _log.info(
            "[_load_focus_box_from_report_db] path=%r focus=(%s,%s) ref_size=%sx%s orientation=%s box=%r",
            path,
            fx,
            fy,
            int(ref_w),
            int(ref_h),
            orientation,
            box,
        )
        return _transform_focus_box_by_orientation(box, orientation)
    except Exception:
        _log.exception("[_load_focus_box_from_report_db] path=%r", path)
        return None


def _load_focus_box_for_preview(path: str, width: int, height: int, *, allow_report_db_fallback: bool = True):
    """
    用 focus_calc + exiftool 元数据提取焦点框，返回归一化坐标 (l,t,r,b)。
    无元数据或提取失败时尝试 report.db 的 focus_x/focus_y 保底（128×128）。
    """
    if width <= 0 or height <= 0:
        return None

    raw_metadata = _load_focus_metadata_for_path(path)
    if raw_metadata is None:
        _log.info("[_load_focus_box_for_preview] no metadata path=%r", path)
        if allow_report_db_fallback:
            focus_box = _load_focus_box_from_report_db(path, width, height)
            if focus_box is not None:
                _log.info("[_load_focus_box_for_preview] fallback report_db path=%r focus_box=%r", path, focus_box)
            return focus_box
        return None

    focus_width, focus_height = _resolve_focus_calc_image_size(raw_metadata, fallback=(width, height))
    camera_type = resolve_focus_camera_type_from_metadata(raw_metadata)
    try:
        focus_box = extract_focus_box(
            raw_metadata,
            focus_width,
            focus_height,
            camera_type=camera_type,
        )
        if focus_box is None:
            if allow_report_db_fallback:
                focus_box = _load_focus_box_from_report_db(
                    path,
                    width,
                    height,
                    ref_size=(focus_width, focus_height),
                    raw_metadata=raw_metadata,
                    camera_type=camera_type,
                )
                if focus_box is not None:
                    _log.info(
                        "[_load_focus_box_for_preview] fallback report_db path=%r focus_box=%r",
                        path,
                        focus_box,
                    )
                    return focus_box
            _log.info(
                "[_load_focus_box_for_preview] focus_calc none path=%r camera_type=%s calc_size=%sx%s",
                path,
                str(getattr(camera_type, "value", camera_type)),
                focus_width,
                focus_height,
            )
            return None
        orientation = _resolve_focus_display_orientation_for_path(
            path,
            raw_metadata,
            camera_type=camera_type,
        )
        mapped_box = _transform_focus_box_by_orientation(focus_box, orientation)
        _log.info(
            "[_load_focus_box_for_preview] path=%r camera_type=%s calc_size=%sx%s orientation=%s focus_box=%r",
            path,
            str(getattr(camera_type, "value", camera_type)),
            focus_width,
            focus_height,
            orientation,
            mapped_box,
        )
        return mapped_box
    except Exception:
        _log.exception("[_load_focus_box_for_preview] failed path=%r", path)
        if allow_report_db_fallback:
            focus_box = _load_focus_box_from_report_db(
                path,
                width,
                height,
                ref_size=(focus_width, focus_height),
                raw_metadata=raw_metadata,
                camera_type=camera_type,
            )
            if focus_box is not None:
                _log.info("[_load_focus_box_for_preview] fallback report_db after exception path=%r", path)
            return focus_box
        return None


def _resolve_focus_report_fallback_ref_size(path: str, fallback: tuple[int, int]) -> tuple[tuple[int, int], bool]:
    raw_metadata = _load_focus_metadata_for_path(path)
    if isinstance(raw_metadata, dict) and raw_metadata:
        return _resolve_focus_calc_image_size(raw_metadata, fallback=fallback), True
    fw = int(fallback[0]) if fallback and len(fallback) > 0 else 0
    fh = int(fallback[1]) if fallback and len(fallback) > 1 else 0
    if fw > 0 and fh > 0:
        return (fw, fh), False
    return (1, 1), False


class FocusBoxLoader(QThread):
    focus_loaded = pyqtSignal(int, object, str)  # (request_id, focus_box_or_none, used_path)

    def __init__(
        self,
        request_id: int,
        photo_cache_key: str,
        preview_path: str,
        source_path: str,
        width: int,
        height: int,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._photo_cache_key = str(photo_cache_key or "")
        self._preview_path = os.path.normpath(preview_path) if preview_path else ""
        self._source_path = os.path.normpath(source_path) if source_path else ""
        self._width = int(width)
        self._height = int(height)
        self._result_size_independent = False

    @property
    def request_id(self) -> int:
        return self._request_id

    @property
    def photo_cache_key(self) -> str:
        return self._photo_cache_key

    @property
    def preview_size_key(self) -> tuple[int, int]:
        return (self._width, self._height)

    @property
    def result_size_independent(self) -> bool:
        return self._result_size_independent

    def run(self) -> None:
        candidates: list[tuple[str, str]] = []
        if self._source_path and os.path.isfile(self._source_path):
            candidates.append(("source", self._source_path))
        if self._preview_path and os.path.isfile(self._preview_path):
            same_as_source = (
                self._source_path
                and os.path.normcase(self._preview_path) == os.path.normcase(self._source_path)
            )
            if not same_as_source:
                candidates.append(("preview", self._preview_path))

        # 焦点元数据通常在源文件而不在 temp/thumbnail JPEG，上面优先 source 可以减少长按方向键时的等待。
        seen_candidate_paths: set[str] = set()
        deduped_candidates: list[tuple[str, str]] = []
        for label, candidate_path in candidates:
            norm_candidate = os.path.normcase(candidate_path)
            if not candidate_path or norm_candidate in seen_candidate_paths:
                continue
            seen_candidate_paths.add(norm_candidate)
            deduped_candidates.append((label, candidate_path))
        candidates = deduped_candidates
        _log.info(
            "[FocusBoxLoader.run] START request_id=%s preview=%r source=%r candidates=%s",
            self._request_id,
            self._preview_path,
            self._source_path,
            [(label, path) for label, path in candidates],
        )
        for label, candidate_path in candidates:
            if self.isInterruptionRequested():
                _log.info("[FocusBoxLoader.run] interrupted request_id=%s", self._request_id)
                return
            focus_box = _load_focus_box_for_preview(
                candidate_path,
                self._width,
                self._height,
                allow_report_db_fallback=False,
            )
            _log.info(
                "[FocusBoxLoader.run] tried request_id=%s label=%s path=%r focus_box=%r",
                self._request_id,
                label,
                candidate_path,
                focus_box,
            )
            if focus_box:
                self._result_size_independent = True
                self.focus_loaded.emit(self._request_id, focus_box, candidate_path)
                return

        fallback_used_path = self._source_path or self._preview_path
        fallback_box = None
        if fallback_used_path and os.path.isfile(fallback_used_path):
            fallback_ref_size, fallback_size_independent = _resolve_focus_report_fallback_ref_size(
                fallback_used_path,
                fallback=(self._width, self._height),
            )
            fallback_box = _load_focus_box_from_report_db(
                fallback_used_path,
                self._width,
                self._height,
                ref_size=fallback_ref_size,
            )
            _log.info(
                "[FocusBoxLoader.run] report fallback request_id=%s path=%r ref_size=%s focus_box=%r",
                self._request_id,
                fallback_used_path,
                fallback_ref_size,
                fallback_box,
            )
            self._result_size_independent = bool(fallback_size_independent)
        self.focus_loaded.emit(self._request_id, fallback_box, fallback_used_path)


class FocusCachePreloadWorker(QThread):
    focus_batch_ready = pyqtSignal(int, object)  # (token, list[(source_path, focus_box, used_path)])

    def __init__(self, token: int, tasks: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._token = int(token)
        self._tasks = [
            (
                os.path.normpath(source_path) if source_path else "",
                os.path.normpath(load_path) if load_path else "",
            )
            for source_path, load_path in (tasks or [])
        ]

    @property
    def token(self) -> int:
        return self._token

    def run(self) -> None:
        batch: list[tuple[str, tuple[float, float, float, float], str]] = []
        _log.info("[FocusCachePreloadWorker.run] START token=%s tasks=%s", self._token, len(self._tasks))
        for source_path, load_path in self._tasks:
            if self.isInterruptionRequested():
                _log.info("[FocusCachePreloadWorker.run] interrupted token=%s", self._token)
                return
            if not source_path or not load_path or not os.path.isfile(load_path):
                continue
            try:
                focus_box = _load_focus_box_for_preview(
                    load_path,
                    1,
                    1,
                    allow_report_db_fallback=False,
                )
            except Exception:
                _log.exception("[FocusCachePreloadWorker.run] preload failed source=%r load=%r", source_path, load_path)
                continue
            if not focus_box:
                continue
            batch.append((source_path, focus_box, load_path))
            if len(batch) >= FOCUS_PRELOAD_BATCH_SIZE:
                _log.info(
                    "[FocusCachePreloadWorker.run] emit token=%s batch=%s",
                    self._token,
                    len(batch),
                )
                self.focus_batch_ready.emit(self._token, list(batch))
                batch.clear()
        if batch:
            _log.info(
                "[FocusCachePreloadWorker.run] emit final token=%s batch=%s",
                self._token,
                len(batch),
            )
            self.focus_batch_ready.emit(self._token, list(batch))
        _log.info("[FocusCachePreloadWorker.run] END token=%s", self._token)


def _resolve_focus_calc_image_size(raw_metadata: dict, fallback: tuple[int, int]) -> tuple[int, int]:
    """
    为 focus_calc 解析尽量接近元数据坐标系的原图尺寸。
    优先用 exiftool 元数据里的宽高，拿不到时回退到当前预览图尺寸。
    """

    def _parse_int(value) -> int | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            iv = int(value)
            return iv if iv > 0 else None
        m = re.search(r"(\d+)", str(value))
        if not m:
            return None
        try:
            iv = int(m.group(1))
            return iv if iv > 0 else None
        except Exception:
            return None

    def _parse_pair(value) -> tuple[int, int] | None:
        if value is None:
            return None
        nums = re.findall(r"\d+", str(value))
        if len(nums) < 2:
            return None
        try:
            w = int(nums[0])
            h = int(nums[1])
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        return (w, h)

    lookup: dict[str, object] = {}
    for k, v in (raw_metadata or {}).items():
        key = str(k).strip().lower()
        if not key:
            continue
        lookup.setdefault(key, v)
        if ":" in key:
            lookup.setdefault(key.split(":")[-1], v)

    key_pairs = [
        ("exif:exifimagewidth", "exif:exifimageheight"),
        ("exifimagewidth", "exifimageheight"),
        ("exif:imagewidth", "exif:imageheight"),
        ("rawimagewidth", "rawimageheight"),
        ("imagewidth", "imageheight"),
        ("file:imagewidth", "file:imageheight"),
    ]
    for w_key, h_key in key_pairs:
        w = _parse_int(lookup.get(w_key))
        h = _parse_int(lookup.get(h_key))
        if w and h:
            return (w, h)

    for pair_key in (
        "composite:imagesize",
        "imagesize",
        "exif:image size",
    ):
        parsed = _parse_pair(lookup.get(pair_key))
        if parsed:
            return parsed

    fw = int(fallback[0]) if fallback and len(fallback) > 0 else 0
    fh = int(fallback[1]) if fallback and len(fallback) > 1 else 0
    if fw > 0 and fh > 0:
        return (fw, fh)
    return (1, 1)


def _transform_focus_box_by_orientation(focus_box, orientation: int):
    """
    将原图坐标系中的归一化焦点框按 EXIF Orientation 映射到预览坐标系。
    预览图已做方向修正，因此焦点框也需要同步变换。
    """
    if not focus_box:
        return None
    try:
        left, top, right, bottom = [float(v) for v in focus_box]
    except Exception:
        return None
    left = max(0.0, min(1.0, left))
    top = max(0.0, min(1.0, top))
    right = max(0.0, min(1.0, right))
    bottom = max(0.0, min(1.0, bottom))
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top

    o = int(orientation or 1)
    if o == 1:
        return (left, top, right, bottom)

    def _map_point(x: float, y: float) -> tuple[float, float]:
        # 采用 EXIF Orientation 的标准归一化点映射（输出坐标系为方向修正后的图像）。
        if o == 2:
            return (1.0 - x, y)
        if o == 3:
            return (1.0 - x, 1.0 - y)
        if o == 4:
            return (x, 1.0 - y)
        if o == 5:
            return (y, x)
        if o == 6:
            return (1.0 - y, x)
        if o == 7:
            return (1.0 - y, 1.0 - x)
        if o == 8:
            return (y, 1.0 - x)
        return (x, y)

    pts = [
        _map_point(left, top),
        _map_point(right, top),
        _map_point(left, bottom),
        _map_point(right, bottom),
    ]
    xs = [max(0.0, min(1.0, p[0])) for p in pts]
    ys = [max(0.0, min(1.0, p[1])) for p in pts]
    nl, nr = min(xs), max(xs)
    nt, nb = min(ys), max(ys)
    if nr - nl < 1e-6 or nb - nt < 1e-6:
        return None
    return (nl, nt, nr, nb)


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
        # 切图时先清空旧焦点框，避免新图加载后短暂显示上一张图的焦点位置。
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
        """返回带当前 overlays 的导出图，供导出逻辑复用。"""
        return self._canvas.render_source_pixmap_with_overlays()

    def save_source_pixmap_with_overlays(
        self,
        path: str,
        fmt: str | None = None,
        quality: int = -1,
    ) -> bool:
        """
        保存带当前 overlays 的导出图。

        导出图片时不要直接保存原始 pixmap，统一走这里，避免漏掉焦点框/构图线。
        """
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


class ExifTable(QTableWidget):
    """EXIF 信息表格，支持按文本过滤与双击编辑值列。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["分组", "标签", "值"])
        self.horizontalHeader().setSectionResizeMode(2, _ResizeStretch)
        self.setColumnWidth(1, 220)  # “标签”列加宽以便显示全
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(_SelectRows)
        self.setEditTriggers(_DoubleClicked)
        self.setStyleSheet(
            "QTableWidget { font-family: 'SF Mono', 'Monaco', 'Consolas', monospace; font-size: 12px; }"
        )
        self._all_rows: list[tuple] = []
        self._filtered_rows: list[tuple] = []
        self._filter_text = ""
        self._updating = False
        self._save_callback = None
        self.itemChanged.connect(self._on_item_changed)

    def set_save_callback(self, cb):
        self._save_callback = cb

    def set_exif(self, rows: list[tuple]):
        self._all_rows = list(rows)
        self._apply_filter(self._filter_text)

    def get_all_rows(self) -> list[tuple]:
        """返回当前全部行数据（用于切换标签语言时重算标签名）。"""
        return list(self._all_rows)

    def set_filter_text(self, text):
        self._filter_text = str(text or "").strip()
        self._apply_filter(self._filter_text)

    def _apply_filter(self, text: str):
        self._filter_text = text
        if not text:
            self._filtered_rows = list(self._all_rows)
        else:
            key = text.lower()
            # 6-tuple: (ifd_name, tag_id, group, name, value_str, raw_value)，过滤用 group/name/value_str 即索引 2,3,4
            self._filtered_rows = [
                r for r in self._all_rows
                if key in (r[2] or "").lower() or key in (r[3] or "").lower() or key in (r[4] or "").lower()
            ]
        rows = self._filtered_rows
        self._updating = True
        self.setRowCount(len(rows))
        for i, row in enumerate(rows):
            ifd_name, tag_id, group, name, value_str, raw_value = row[:6]
            exiftool_key = row[6] if len(row) > 6 else None
            it0 = QTableWidgetItem(group)
            it0.setFlags(it0.flags() & ~_ItemIsEditable)
            self.setItem(i, 0, it0)
            it1 = QTableWidgetItem(name)
            it1.setFlags(it1.flags() & ~_ItemIsEditable)
            self.setItem(i, 1, it1)
            it2 = QTableWidgetItem(value_str)
            editable_exif = (
                isinstance(ifd_name, str)
                and ifd_name in ("0th", "Exif", "GPS", "1st", "Interop")
                and isinstance(tag_id, int)
                and raw_value is not None
            )
            editable_meta = (
                ifd_name == META_IFD_NAME
                and str(tag_id) in (META_TITLE_TAG_ID, META_DESCRIPTION_TAG_ID)
            )
            editable_exiftool_row = exiftool_key is not None and ifd_name is None and tag_id is None
            editable = editable_exif or editable_meta or editable_exiftool_row
            if editable:
                it2.setFlags(it2.flags() | _ItemIsEditable)
            else:
                it2.setFlags(it2.flags() & ~_ItemIsEditable)
            self.setItem(i, 2, it2)
        self.resizeRowsToContents()
        self._updating = False

    def _on_item_changed(self, item):
        if self._updating or not self._save_callback or item.column() != 2:
            return
        row = item.row()
        if row < 0 or row >= len(self._filtered_rows):
            return
        row_data = self._filtered_rows[row]
        ifd_name, tag_id, group, name, old_val, raw_value = row_data[:6]
        exiftool_key = row_data[6] if len(row_data) > 6 else None
        is_editable_exif = (
            isinstance(ifd_name, str)
            and ifd_name in ("0th", "Exif", "GPS", "1st", "Interop")
            and isinstance(tag_id, int)
            and raw_value is not None
        )
        is_editable_meta = (
            ifd_name == META_IFD_NAME
            and str(tag_id) in (META_TITLE_TAG_ID, META_DESCRIPTION_TAG_ID)
        )
        is_editable_exiftool_row = exiftool_key is not None and ifd_name is None and tag_id is None
        if not (is_editable_exif or is_editable_meta or is_editable_exiftool_row):
            return
        new_val = item.text().strip()
        if new_val == old_val:
            return
        self._save_callback(ifd_name, tag_id, new_val, raw_value, exiftool_key)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        copy_act = QAction("复制", self)
        copy_act.triggered.connect(self._copy_selection)
        menu.addAction(copy_act)
        menu.exec(event.globalPos())

    def _copy_selection(self):
        """将选中单元格文本复制到剪贴板。"""
        sel = self.selectedRanges()
        if not sel:
            item = self.currentItem()
            if item is not None:
                QApplication.clipboard().setText(item.text())
            return
        parts = []
        for r in sel:
            for row in range(r.topRow(), r.bottomRow() + 1):
                cells = []
                for col in range(r.leftColumn(), r.rightColumn() + 1):
                    it = self.item(row, col)
                    cells.append(it.text() if it is not None else "")
                parts.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(parts))


class ExifTagOrderDialog(QDialog):
    """配置 EXIF 标签优先显示顺序与禁止显示列表的对话框。"""

    def __init__(self, parent=None, use_chinese: bool = False):
        super().__init__(parent)
        self.setWindowTitle("EXIF 显示顺序")
        self.setMinimumSize(480, 420)
        self._all_tags = get_all_exif_tag_keys(use_chinese=use_chinese)
        self._priority_keys = []
        self._hidden_keys = []  # 列表顺序用于保存到 cfg

        tabs = QTabWidget()
        # Tab1: 显示顺序
        order_w = QWidget()
        order_layout = QVBoxLayout(order_w)
        order_layout.addWidget(QLabel("以下标签将优先显示在 EXIF 列表顶部，按顺序排列："))
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        order_layout.addWidget(self.list_widget)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_up = QPushButton("上移")
        self.btn_up.clicked.connect(self._move_up)
        self.btn_down = QPushButton("下移")
        self.btn_down.clicked.connect(self._move_down)
        self.btn_remove = QPushButton("删除")
        self.btn_remove.clicked.connect(self._remove)
        self.btn_add = QPushButton("添加…")
        self.btn_add.clicked.connect(self._add_tag)
        btn_layout.addWidget(self.btn_up)
        btn_layout.addWidget(self.btn_down)
        btn_layout.addWidget(self.btn_remove)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addStretch()
        order_layout.addLayout(btn_layout)
        tabs.addTab(order_w, "显示顺序")

        # Tab2: 禁止显示
        hidden_w = QWidget()
        hidden_layout = QVBoxLayout(hidden_w)
        hidden_layout.addWidget(QLabel("以下标签将不在 EXIF 列表中显示（格式如 0th:279）："))
        self.hidden_list_widget = QListWidget()
        self.hidden_list_widget.setAlternatingRowColors(True)
        hidden_layout.addWidget(self.hidden_list_widget)
        hidden_btn = QHBoxLayout()
        hidden_btn.addStretch()
        self.btn_hidden_add = QPushButton("添加…")
        self.btn_hidden_add.clicked.connect(self._add_hidden_tag)
        self.btn_hidden_remove = QPushButton("删除")
        self.btn_hidden_remove.clicked.connect(self._remove_hidden)
        hidden_btn.addWidget(self.btn_hidden_add)
        hidden_btn.addWidget(self.btn_hidden_remove)
        hidden_btn.addStretch()
        hidden_layout.addLayout(hidden_btn)
        tabs.addTab(hidden_w, "禁止显示")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            if hasattr(QDialogButtonBox.StandardButton, "Ok")
            else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)
        self._load_from_settings()

    def _load_from_settings(self):
        self._priority_keys = load_tag_priority_from_settings()
        data = _load_settings()
        val = data.get("exif_tag_hidden", [])
        lst = val if isinstance(val, list) else []
        self._hidden_keys = [str(k).strip() for k in lst if isinstance(k, str) and k.strip()]
        self._refresh_list()
        self._refresh_hidden_list()

    def _refresh_list(self):
        key_to_text = {k: t for k, t in self._all_tags}
        self.list_widget.clear()
        for key in self._priority_keys:
            text = key_to_text.get(key, key)
            item = QListWidgetItem(text)
            item.setData(_UserRole, key)
            self.list_widget.addItem(item)

    def _move_up(self):
        row = self.list_widget.currentRow()
        if row <= 0:
            return
        keys = self._priority_keys
        keys[row], keys[row - 1] = keys[row - 1], keys[row]
        self._priority_keys = keys
        self._refresh_list()
        self.list_widget.setCurrentRow(row - 1)

    def _move_down(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._priority_keys) - 1:
            return
        keys = self._priority_keys
        keys[row], keys[row + 1] = keys[row + 1], keys[row]
        self._priority_keys = keys
        self._refresh_list()
        self.list_widget.setCurrentRow(row + 1)

    def _remove(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self._priority_keys.pop(row)
        self._refresh_list()
        if self.list_widget.count():
            self.list_widget.setCurrentRow(min(row, self.list_widget.count() - 1))

    def _add_tag(self):
        d = QDialog(self)
        d.setWindowTitle("选择要优先显示的标签")
        d.setMinimumSize(400, 350)
        layout = QVBoxLayout(d)
        layout.addWidget(QLabel("搜索："))
        search = QLineEdit()
        search.setPlaceholderText("输入分组或标签名过滤…")
        layout.addWidget(search)
        all_list = QListWidget()
        existing = set(self._priority_keys)
        for key, text in self._all_tags:
            if key in existing:
                continue
            item = QListWidgetItem(text)
            item.setData(_UserRole, key)
            all_list.addItem(item)
        layout.addWidget(all_list)

        def _filter_list(text):
            t = str(text or "").strip().lower()
            for i in range(all_list.count()):
                it = all_list.item(i)
                it.setHidden(bool(t) and t not in it.text().lower())

        search.textChanged.connect(_filter_list)
        chosen_key = [None]

        def on_accept():
            cur = all_list.currentItem()
            if cur is not None:
                chosen_key[0] = cur.data(_UserRole)
            d.accept()

        all_list.doubleClicked.connect(on_accept)
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            if hasattr(QDialogButtonBox.StandardButton, "Ok")
            else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bbox.accepted.connect(on_accept)
        bbox.rejected.connect(d.reject)
        layout.addWidget(bbox)
        if d.exec():
            key = chosen_key[0]
            if key and key not in self._priority_keys:
                self._priority_keys.append(key)
                self._refresh_list()

    def _refresh_hidden_list(self):
        self.hidden_list_widget.clear()
        key_to_text = {k: t for k, t in self._all_tags}
        for key in self._hidden_keys:
            text = key_to_text.get(key, key)
            item = QListWidgetItem(text)
            item.setData(_UserRole, key)
            self.hidden_list_widget.addItem(item)

    def _add_hidden_tag(self):
        d = QDialog(self)
        d.setWindowTitle("选择要禁止显示的标签")
        d.setMinimumSize(400, 350)
        layout = QVBoxLayout(d)
        layout.addWidget(QLabel("搜索："))
        search = QLineEdit()
        search.setPlaceholderText("输入分组或标签名过滤…")
        layout.addWidget(search)
        all_list = QListWidget()
        existing = set(self._hidden_keys)
        for key, text in self._all_tags:
            if key in existing:
                continue
            item = QListWidgetItem(text)
            item.setData(_UserRole, key)
            all_list.addItem(item)
        layout.addWidget(all_list)

        def _filter_list(text):
            t = str(text or "").strip().lower()
            for i in range(all_list.count()):
                it = all_list.item(i)
                it.setHidden(bool(t) and t not in it.text().lower())

        search.textChanged.connect(_filter_list)
        chosen_key = [None]

        def on_accept():
            cur = all_list.currentItem()
            if cur is not None:
                chosen_key[0] = cur.data(_UserRole)
            d.accept()

        all_list.doubleClicked.connect(on_accept)
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            if hasattr(QDialogButtonBox.StandardButton, "Ok")
            else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bbox.accepted.connect(on_accept)
        bbox.rejected.connect(d.reject)
        layout.addWidget(bbox)
        if d.exec():
            key = chosen_key[0]
            if key and key not in self._hidden_keys:
                self._hidden_keys.append(key)
                self._refresh_hidden_list()

    def _remove_hidden(self):
        row = self.hidden_list_widget.currentRow()
        if row < 0:
            return
        self._hidden_keys.pop(row)
        self._refresh_hidden_list()
        if self.hidden_list_widget.count():
            self.hidden_list_widget.setCurrentRow(min(row, self.hidden_list_widget.count() - 1))

    def get_priority_keys(self):
        return list(self._priority_keys)

    def accept(self):
        save_tag_priority_to_settings(self._priority_keys)
        save_exif_tag_hidden_to_settings(self._hidden_keys)
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, initial_received_files=None):
        super().__init__()
        info = load_about_info(_get_config_resource_path())
        product_name = _get_product_display_name(info)
        self.setWindowTitle(_build_main_window_title(info))
        self.setMinimumSize(900, 600)
        self.resize(1500, 960)
        self._init_menu_bar()
        icon_path = _get_app_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # 主分割器：目录树 | 文件列表 | 图片预览 | EXIF 表格
        splitter = QSplitter(_Horizontal)

        # ── 面板 1：目录浏览器 ──
        self._dir_browser = DirectoryBrowserWidget()
        self._dir_browser.setMinimumWidth(140)
        splitter.addWidget(self._dir_browser)

        # ── 面板 2：图像文件列表 ──
        self._file_list = FileListPanel()
        self._file_list.setMinimumWidth(520)
        splitter.addWidget(self._file_list)

        # 连接目录选择 → 文件列表加载
        self._dir_browser.directory_selected.connect(self._on_directory_selected)
        # 连接文件列表选中 → 预览 + EXIF 刷新
        self._file_list.file_fast_preview_requested.connect(self._on_file_fast_preview_requested)
        self._file_list.file_selected.connect(self._on_file_selected_from_list)
        self._file_list.focus_cache_batch_ready.connect(self._on_metadata_focus_cache_batch_ready)

        # ── 面板 3：App 信息 + 文件名 + 拖放预览区 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        app_info_path = _get_resource_path("icons/app_icon.png") or _get_app_icon_path()
        app_info_widget = AppInfoBar(
            self,
            title=product_name,
            subtitle="查看与编辑EXIF",
            icon_path=app_info_path,
            on_about_clicked=self._show_about_dialog,
        )
        left_layout.addWidget(app_info_widget)

        self.file_label = QLabel("未选择图片")
        self.file_label.setStyleSheet("color: #aaa; font-size: 12px;")
        self.file_label.setWordWrap(True)
        left_layout.addWidget(self.file_label)
        overlay_row = QHBoxLayout()
        overlay_row.setContentsMargins(0, 0, 0, 0)
        overlay_row.setSpacing(8)
        self.check_show_focus = QCheckBox("显示对焦点")
        self.check_show_focus.setChecked(True)
        self.check_show_focus.toggled.connect(self._on_preview_overlay_toggled)
        overlay_row.addWidget(self.check_show_focus)
        self.combo_preview_grid = QComboBox(self)
        self.combo_preview_grid.setFixedWidth(PREVIEW_GRID_MODE_COMBO_WIDTH)
        valid_preview_grid_modes = set(PREVIEW_COMPOSITION_GRID_MODES)
        for mode, label in PREVIEW_GRID_MODE_ITEMS:
            if mode in valid_preview_grid_modes:
                self.combo_preview_grid.addItem(label, mode)
        current_grid_mode = load_preview_grid_mode_from_settings()
        current_index = self.combo_preview_grid.findData(current_grid_mode)
        if current_index < 0:
            current_index = self.combo_preview_grid.findData("none")
        if current_index < 0 and self.combo_preview_grid.count() > 0:
            current_index = 0
        if current_index >= 0:
            self.combo_preview_grid.setCurrentIndex(current_index)
        self.combo_preview_grid.setToolTip("设置预览图上的构图辅助线，可选均分九宫格、黄金分割、方格、对角线等。")
        self.combo_preview_grid.currentIndexChanged.connect(self._on_preview_grid_mode_changed)
        overlay_row.addWidget(self.combo_preview_grid)
        self.combo_preview_grid_line_width = QComboBox(self)
        self.combo_preview_grid_line_width.setFixedWidth(PREVIEW_GRID_LINE_WIDTH_COMBO_WIDTH)
        valid_preview_grid_line_widths = set(PREVIEW_COMPOSITION_GRID_LINE_WIDTHS)
        for line_width in PREVIEW_COMPOSITION_GRID_LINE_WIDTHS:
            if line_width in valid_preview_grid_line_widths:
                self.combo_preview_grid_line_width.addItem(
                    _build_preview_grid_line_width_icon(line_width),
                    f"{line_width} px",
                    line_width,
                )
        current_line_width = load_preview_grid_line_width_from_settings()
        current_width_index = self.combo_preview_grid_line_width.findData(current_line_width)
        if current_width_index < 0:
            current_width_index = self.combo_preview_grid_line_width.findData(1)
        if current_width_index < 0 and self.combo_preview_grid_line_width.count() > 0:
            current_width_index = 0
        if current_width_index >= 0:
            self.combo_preview_grid_line_width.setCurrentIndex(current_width_index)
        self.combo_preview_grid_line_width.setToolTip("设置构图辅助线线宽，列表图标按 1 到 4 像素直观显示粗细。")
        self.combo_preview_grid_line_width.currentIndexChanged.connect(self._on_preview_grid_line_width_changed)
        overlay_row.addWidget(self.combo_preview_grid_line_width)
        overlay_row.addStretch(1)
        left_layout.addLayout(overlay_row)
        self.preview_panel = PreviewPanel(central)
        self.preview_panel.set_show_focus_enabled(self.check_show_focus.isChecked())
        self.preview_panel.set_composition_grid_mode(self.combo_preview_grid.currentData())
        self.preview_panel.set_composition_grid_line_width(self.combo_preview_grid_line_width.currentData())
        left_layout.addWidget(self.preview_panel, stretch=1)
        splitter.addWidget(left_widget)

        # ── 面板 4：EXIF 表格 ──
        group = QGroupBox("元信息")
        group.setStyleSheet("QGroupBox { font-weight: bold; }")
        group_layout = QVBoxLayout(group)
        top_row = QHBoxLayout()
        self.exif_filter = QLineEdit()
        self.exif_filter.setPlaceholderText("按分组、标签或值过滤…")
        self.exif_filter.setClearButtonEnabled(True)
        self.exif_filter.setStyleSheet("QLineEdit { padding: 6px; font-size: 13px; }")
        self.exif_filter.textChanged.connect(self._on_exif_filter_changed)
        top_row.addWidget(self.exif_filter)
        self.check_tag_chinese = QCheckBox("中文标签")
        self.check_tag_chinese.setChecked(load_tag_label_chinese_from_settings())
        self.check_tag_chinese.setToolTip("勾选显示汉字标签名，否则显示英文")
        self.check_tag_chinese.toggled.connect(self._on_tag_label_lang_toggled)
        top_row.addWidget(self.check_tag_chinese)
        self.btn_config_order = QPushButton("配置显示顺序")
        self.btn_config_order.setToolTip("设置优先显示的 EXIF 标签及顺序")
        self.btn_config_order.clicked.connect(self._open_tag_order_config)
        top_row.addWidget(self.btn_config_order)
        group_layout.addLayout(top_row)
        self.exif_table = ExifTable(self)
        self.exif_table.set_save_callback(self._save_exif_value)
        group_layout.addWidget(self.exif_table)
        splitter.addWidget(group)

        # 各面板初始宽度：目录树 200 | 文件列表 320 | 预览 380 | EXIF 320
        splitter.setSizes([220, 680, 520, 340])
        layout.addWidget(splitter)

        self._current_exif_path = None
        self._current_preview_source_path: str = ""
        self._focus_loader: FocusBoxLoader | None = None
        self._focus_display_request_id: int = 0
        self._focus_request_sequence: int = 0
        self._focus_preload_worker: FocusCachePreloadWorker | None = None
        self._focus_preload_token: int = 0
        self._photo_preview_memory_cache: "OrderedDict[str, PhotoPreviewMemoryEntry]" = OrderedDict()

        # preview_panel 的 parent 为 central，回调挂在 left_widget 上供拖放/选图后调用
        left_widget.on_image_loaded = self.on_image_loaded

        if not initial_received_files:
            self._restore_last_selected_directory()

    def _get_report_row_for_current_path(self, path: str) -> dict | None:
        try:
            return self._file_list.get_report_row_for_path(path)
        except Exception:
            return None

    def _load_metadata_rows_for_current_path(self, path: str, tag_label_chinese: bool) -> list[tuple]:
        rows = load_all_exif(path, tag_label_chinese=tag_label_chinese)
        rows = apply_tag_priority(rows, load_tag_priority_from_settings())
        rows = merge_report_metadata_rows(rows, self._get_report_row_for_current_path(path))
        return rows

    def _sync_report_metadata_after_save(self, path: str, meta_tag_id: str, value: str) -> None:
        report_fields: dict[str, str] = {}
        meta_updates: dict[str, str] = {}
        if meta_tag_id == META_TITLE_TAG_ID:
            report_fields["title"] = value
            meta_updates["title"] = value
        elif meta_tag_id == META_DESCRIPTION_TAG_ID:
            report_fields["caption"] = value
        if not report_fields and not meta_updates:
            return
        try:
            self._file_list.sync_metadata_edit_for_path(
                path,
                report_fields=report_fields,
                meta_updates=meta_updates,
            )
        except Exception:
            _log.exception("[_sync_report_metadata_after_save] path=%r meta_tag_id=%r", path, meta_tag_id)

    def _on_directory_selected(self, path: str):
        """目录树选中目录后，保存路径到设置与 .last_folder.txt，并刷新文件列表。"""
        save_last_selected_directory_to_settings(path)
        self._file_list.load_directory(path)

    def _restore_last_selected_directory(self) -> None:
        """启动时从 .last_folder.txt 或设置恢复并展开上次选中的目录。"""
        last_dir = load_last_selected_directory_from_settings()
        if not last_dir:
            return
        try:
            self._dir_browser.select_directory(last_dir, emit_signal=True)
        except Exception:
            pass

    @staticmethod
    def _normalize_photo_preview_cache_key(path: str) -> str:
        if not path:
            return ""
        try:
            normalized = os.path.abspath(os.path.normpath(path))
        except Exception:
            normalized = os.path.normpath(path)
        return os.path.normcase(normalized)

    def _prune_photo_preview_memory_cache(self) -> None:
        while len(self._photo_preview_memory_cache) > PHOTO_PREVIEW_MEMORY_CACHE_LIMIT:
            self._photo_preview_memory_cache.popitem(last=False)

    def _get_photo_preview_memory_entry(self, path: str, *, create: bool) -> PhotoPreviewMemoryEntry | None:
        cache_key = self._normalize_photo_preview_cache_key(path)
        if not cache_key:
            return None
        entry = self._photo_preview_memory_cache.get(cache_key)
        if entry is None and create:
            entry = PhotoPreviewMemoryEntry(source_path=os.path.abspath(os.path.normpath(path)))
            self._photo_preview_memory_cache[cache_key] = entry
            self._prune_photo_preview_memory_cache()
            return entry
        if entry is not None:
            self._photo_preview_memory_cache.move_to_end(cache_key)
        return entry

    def _get_photo_focus_memory_state(
        self,
        path: str,
        preview_path: str,
        focus_source_path: str,
        preview_size: tuple[int, int],
        *,
        create: bool,
    ) -> tuple[str, tuple[int, int], PhotoPreviewMemoryEntry | None, PhotoFocusMemoryCacheState | None]:
        cache_key = self._normalize_photo_preview_cache_key(path)
        size_key = (max(0, int(preview_size[0])), max(0, int(preview_size[1])))
        entry = self._get_photo_preview_memory_entry(path, create=create)
        if entry is None:
            return cache_key, size_key, None, None
        if preview_path:
            entry.preview_path = os.path.normpath(preview_path)
        if focus_source_path:
            entry.focus_source_path = os.path.normpath(focus_source_path)
        if create:
            return cache_key, size_key, entry, entry.get_or_create_focus_state(size_key)
        state = entry.focus_states_by_preview_size.get(size_key)
        if state is not None:
            entry.focus_states_by_preview_size.move_to_end(size_key)
        return cache_key, size_key, entry, state

    def _reset_loading_focus_memory_state(self, loader: FocusBoxLoader) -> None:
        cache_key = loader.photo_cache_key
        if not cache_key:
            return
        entry = self._photo_preview_memory_cache.get(cache_key)
        if entry is None:
            return
        state = entry.focus_states_by_preview_size.get(loader.preview_size_key)
        if state is None:
            return
        if state.status != FOCUS_CACHE_STATUS_LOADING or state.request_id != loader.request_id:
            return
        state.status = FOCUS_CACHE_STATUS_UNKNOWN
        state.focus_box = None
        state.used_path = ""
        state.request_id = 0
        state.size_independent = False
        entry.focus_states_by_preview_size.move_to_end(loader.preview_size_key)
        self._photo_preview_memory_cache.move_to_end(cache_key)

    def _apply_preview_overlay_options_to_preview(self) -> None:
        """将预览 overlay 选项同步到 canvas，切图和高速浏览都复用这一入口。"""
        self.preview_panel.set_show_focus_enabled(self.check_show_focus.isChecked())
        self.preview_panel.set_composition_grid_mode(self.combo_preview_grid.currentData())
        self.preview_panel.set_composition_grid_line_width(self.combo_preview_grid_line_width.currentData())

    def _set_current_preview_source_path(self, path: str) -> None:
        self._current_preview_source_path = os.path.normpath(path) if path else ""

    def _store_reusable_focus_cache_entry(
        self,
        source_path: str,
        focus_box: tuple[float, float, float, float],
        used_path: str,
    ) -> None:
        _, _, entry, state = self._get_photo_focus_memory_state(
            source_path,
            "",
            used_path,
            FOCUS_PRELOAD_CANONICAL_SIZE,
            create=True,
        )
        if entry is None or state is None:
            return
        state.status = FOCUS_CACHE_STATUS_READY
        state.focus_box = focus_box
        state.used_path = os.path.normpath(used_path) if used_path else ""
        state.request_id = 0
        state.size_independent = True

    def _on_metadata_focus_cache_batch_ready(self, batch: dict[str, dict]) -> None:
        """
        文件列表 metadata 批处理顺带带回来的焦点缓存。

        这条链路只缓存“可由文件 metadata 直接算出”的结果；若当前预览正好命中，
        立即回填 canvas，这样目录刚载入完时无需等用户逐张点开才有焦点。
        """
        if not isinstance(batch, dict) or not batch:
            return
        for source_path, payload in batch.items():
            if not source_path or not isinstance(payload, dict):
                continue
            focus_box = payload.get("focus_box")
            used_path = payload.get("used_path") or source_path
            if not focus_box:
                continue
            self._store_reusable_focus_cache_entry(source_path, focus_box, used_path)
        current_path = self._current_preview_source_path
        if not current_path:
            return
        current_key = self._normalize_photo_preview_cache_key(current_path)
        if any(self._normalize_photo_preview_cache_key(source_path) == current_key for source_path in batch):
            self._update_preview_focus_box(current_path, allow_async_load=False)

    def _build_focus_preload_tasks(
        self,
        paths: list[str],
        *,
        prioritize_path: str = "",
    ) -> list[tuple[str, str]]:
        prioritized_norm = os.path.normpath(prioritize_path) if prioritize_path else ""
        available_path_keys = {
            os.path.normcase(os.path.normpath(raw_path))
            for raw_path in (paths or [])
            if raw_path
        }
        ordered_paths: list[str] = []
        seen_paths: set[str] = set()
        if prioritized_norm and os.path.normcase(prioritized_norm) in available_path_keys:
            ordered_paths.append(prioritized_norm)
            seen_paths.add(os.path.normcase(prioritized_norm))
        for raw_path in paths or []:
            norm_path = os.path.normpath(raw_path) if raw_path else ""
            if not norm_path:
                continue
            dedup_key = os.path.normcase(norm_path)
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)
            ordered_paths.append(norm_path)

        tasks: list[tuple[str, str]] = []
        for source_path in ordered_paths:
            entry = self._get_photo_preview_memory_entry(source_path, create=False)
            if entry is not None and entry.find_reusable_focus_state() is not None:
                continue
            load_path = self._resolve_focus_metadata_source_path(source_path) or source_path
            if not load_path or not os.path.isfile(load_path):
                continue
            tasks.append((source_path, load_path))
        return tasks

    def _stop_focus_preload(self) -> None:
        worker = self._focus_preload_worker
        if worker is None:
            return
        try:
            worker.focus_batch_ready.disconnect(self._on_focus_preload_batch_ready)
        except Exception:
            pass
        try:
            worker.finished.disconnect(self._on_focus_preload_finished)
        except Exception:
            pass
        worker.requestInterruption()
        self._focus_preload_worker = None

    def _start_focus_preload(self, paths: list[str], *, prioritize_path: str = "") -> None:
        tasks = self._build_focus_preload_tasks(paths, prioritize_path=prioritize_path)
        self._stop_focus_preload()
        if not tasks:
            _log.info("[_start_focus_preload] skip: no uncached tasks prioritize=%r", prioritize_path)
            return
        self._focus_preload_token += 1
        token = self._focus_preload_token
        worker = FocusCachePreloadWorker(token, tasks, self)
        worker.focus_batch_ready.connect(self._on_focus_preload_batch_ready)
        worker.finished.connect(self._on_focus_preload_finished)
        self._focus_preload_worker = worker
        _log.info(
            "[_start_focus_preload] token=%s tasks=%s prioritize=%r",
            token,
            len(tasks),
            prioritize_path,
        )
        worker.start()

    def _on_file_list_loaded_for_focus_preload(self, paths: list[str]) -> None:
        self._start_focus_preload(paths, prioritize_path=self._current_preview_source_path)

    def _on_focus_preload_batch_ready(self, token: int, batch: list[tuple[str, tuple[float, float, float, float], str]]) -> None:
        if token != self._focus_preload_token:
            _log.info(
                "[_on_focus_preload_batch_ready] ignore stale token=%s current=%s batch=%s",
                token,
                self._focus_preload_token,
                len(batch or []),
            )
            return
        for source_path, focus_box, used_path in batch or []:
            if not source_path or not focus_box:
                continue
            self._store_reusable_focus_cache_entry(source_path, focus_box, used_path)
        current_path = self._current_preview_source_path
        if current_path and batch:
            current_key = self._normalize_photo_preview_cache_key(current_path)
            if any(self._normalize_photo_preview_cache_key(source_path) == current_key for source_path, _box, _used_path in batch):
                self._update_preview_focus_box(current_path, allow_async_load=False)

    def _on_focus_preload_finished(self) -> None:
        worker = self.sender()
        if worker is None:
            return
        if self._focus_preload_worker is worker:
            self._focus_preload_worker = None

    def _on_file_selected_from_list(self, path: str):
        """文件列表中选中图像文件，触发预览和 EXIF 加载（等同于拖放）。"""
        preview_path = self._file_list.resolve_preview_path(path)
        _log.info("[_on_file_selected_from_list] source=%r preview=%r", path, preview_path)
        self._set_current_preview_source_path(path)
        self.preview_panel.set_image(preview_path)
        self.on_image_loaded(path)

    def _on_file_fast_preview_requested(self, path: str):
        """连续方向键长按时，优先用小缩略图刷新 PreviewCanvas。"""
        preview_path = self._file_list.resolve_preview_path(path, prefer_fast_preview=True)
        _log.info("[_on_file_fast_preview_requested] source=%r preview=%r", path, preview_path)
        self._set_current_preview_source_path(path)
        self.preview_panel.set_image(preview_path)
        self._update_preview_focus_box(path, allow_async_load=self.check_show_focus.isChecked())

    @staticmethod
    def _find_source_file_by_stem(path: str) -> str | None:
        """同目录同 stem 下优先查找 RAW/HEIF 源文件，供对焦点提取。"""
        try:
            folder = Path(path).parent
            stem_l = Path(path).stem.lower()
        except Exception:
            return None
        if not folder or not folder.is_dir() or not stem_l:
            return None
        preferred_exts = [".arw", ".hif", ".heif", ".heic"]
        all_exts = preferred_exts + sorted(RAW_EXTENSIONS) + sorted(HEIF_EXTENSIONS)
        ext_rank: dict[str, int] = {}
        for idx, ext in enumerate(all_exts):
            ext_l = str(ext).lower()
            if ext_l and ext_l not in ext_rank:
                ext_rank[ext_l] = idx
        best_path = None
        best_rank = 10**9
        try:
            for entry in os.scandir(folder):
                if not entry.is_file():
                    continue
                p = Path(entry.name)
                if p.stem.lower() != stem_l:
                    continue
                ext_l = p.suffix.lower()
                if ext_l not in ext_rank:
                    continue
                rank = ext_rank[ext_l]
                if rank < best_rank:
                    best_rank = rank
                    best_path = os.path.normpath(entry.path)
        except Exception:
            return None
        return best_path

    def _resolve_focus_metadata_source_path(self, path: str) -> str:
        """
        为“显示对焦点”解析元数据来源路径（仅源文件）：
        1) 当前文件（若为 RAW/HEIF）
        2) 同目录同 stem 的 RAW/HEIF 文件
        """
        path_norm = os.path.normpath(path) if path else ""
        if not path_norm:
            return ""

        ext = Path(path_norm).suffix.lower()
        if os.path.isfile(path_norm) and (ext in RAW_EXTENSIONS or ext in HEIF_EXTENSIONS):
            return path_norm

        sibling_source = self._find_source_file_by_stem(path_norm)
        if sibling_source:
            return sibling_source
        return ""

    def _init_menu_bar(self):
        file_menu = self.menuBar().addMenu("文件")
        extern_apps = get_external_apps()
        if extern_apps:
            send_menu = file_menu.addMenu("发送到外部应用")
            for app in extern_apps:
                name = (app.get("name") or app.get("path") or "未命名").strip()
                act = QAction(name, self)
                act.triggered.connect(lambda checked=False, a=app: self._send_to_external_app(a))
                send_menu.addAction(act)
        settings_act = QAction("外部应用设置...", self)
        settings_act.triggered.connect(self._open_external_apps_settings)
        file_menu.addAction(settings_act)
        file_menu.addSeparator()

        settings_menu = self.menuBar().addMenu("设置")
        user_options_act = QAction("用户选项...", self)
        user_options_act.triggered.connect(self._open_user_options_dialog)
        settings_menu.addAction(user_options_act)

        help_menu = self.menuBar().addMenu("帮助")
        about_action = QAction("关于...", self)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _send_to_external_app(self, app: dict) -> None:
        """将当前选中的文件发送到指定外部应用。"""
        if not self._current_exif_path or not os.path.isfile(self._current_exif_path):
            QMessageBox.information(self, "发送", "请先选择要发送的文件。")
            return
        send_files_to_app([self._current_exif_path], app, base_directory=_get_app_dir())

    def _open_external_apps_settings(self) -> None:
        def on_saved():
            self.menuBar().clear()
            self._init_menu_bar()

        show_external_apps_settings_dialog(self, on_saved=on_saved)

    def _open_user_options_dialog(self) -> None:
        dialog = SuperViewerUserOptionsDialog(self, options=get_runtime_user_options())
        accepted_code = QDialog.DialogCode.Accepted if hasattr(QDialog, "DialogCode") else QDialog.Accepted
        if dialog.exec() != accepted_code:
            return
        options = dialog.selected_options()
        try:
            normalized = save_user_options(options)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", f"无法写入用户选项：\n{exc}")
            return
        apply_runtime_user_options(normalized)
        self._file_list.apply_user_options()
        self.preview_panel.set_keep_view_on_switch(
            bool(normalized.get("keep_view_on_switch", 1))
        )
        QMessageBox.information(
            self,
            "已保存",
            f"用户选项已保存到：\n{get_user_options_path()}",
        )

    def _on_received_file_list(self, paths: list) -> None:
        """由单例 IPC 或启动时传入的文件列表回调（在主线程执行）。"""
        if not paths:
            return
        self._open_received_file_list(paths)

    def _open_received_file_list(self, paths: list) -> None:
        """打开「发送到本应用」收到的文件列表：与目录列表多选同等——打开首文件所在目录，待加载完成后多选收到的路径。"""
        if not paths:
            return
        normalized = [os.path.abspath(os.path.normpath(str(p))) for p in paths if p]
        if not normalized:
            return
        first = normalized[0]
        parent = os.path.dirname(first)
        if not parent or not os.path.isdir(parent):
            return
        self._file_list.set_pending_selection(normalized)
        self._dir_browser.select_directory(parent, emit_signal=True)

    def _show_about_dialog(self):
        about_cfg_path = _get_config_resource_path()
        info = load_about_info(about_cfg_path)
        about_images = load_about_images(about_cfg_path)
        logo_path = _get_resource_path("icons/app_icon.png") or _get_app_icon_path()
        show_about_dialog(self, info, logo_path=logo_path, images=about_images)

    def _on_preview_overlay_toggled(self, _checked: bool) -> None:
        self.preview_panel.set_show_focus_enabled(self.check_show_focus.isChecked())

    def _on_preview_grid_mode_changed(self, index: int) -> None:
        mode = self.combo_preview_grid.itemData(index)
        if mode is None:
            mode = self.combo_preview_grid.currentData()
        normalized = normalize_preview_composition_grid_mode(mode)
        self.preview_panel.set_composition_grid_mode(normalized)
        save_preview_grid_mode_to_settings(normalized)

    def _on_preview_grid_line_width_changed(self, index: int) -> None:
        width = self.combo_preview_grid_line_width.itemData(index)
        if width is None:
            width = self.combo_preview_grid_line_width.currentData()
        normalized = normalize_preview_composition_grid_line_width(width)
        self.preview_panel.set_composition_grid_line_width(normalized)
        save_preview_grid_line_width_to_settings(normalized)

    def _on_exif_filter_changed(self, text: str):
        self.exif_table.set_filter_text(text)

    def _on_tag_label_lang_toggled(self, checked: bool):
        save_tag_label_chinese_to_settings(checked)
        rows = self.exif_table.get_all_rows()
        if not rows:
            return
        names_zh = load_exif_tag_names_zh_from_settings() if checked else None
        new_rows = []
        for r in rows:
            if r[0] is not None and r[1] is not None:
                name = get_tag_name(r[0], r[1], use_chinese=checked, names_zh=names_zh)
            else:
                exiftool_key = r[6] if len(r) > 6 else None
                tag_name_raw = (exiftool_key.split(":", 1)[1] if exiftool_key and ":" in exiftool_key else None) or r[3]
                name = (
                    get_tag_name_for_exiftool_key(exiftool_key, tag_name_raw, checked, names_zh)
                    if exiftool_key
                    else r[3]
                )
            exiftool_key = r[6] if len(r) > 6 else None
            new_rows.append((r[0], r[1], r[2], name, r[4], r[5], exiftool_key))
        self.exif_table.set_exif(new_rows)

    def _open_tag_order_config(self):
        use_chinese = load_tag_label_chinese_from_settings()
        d = ExifTagOrderDialog(self, use_chinese=use_chinese)
        if d.exec():
            if self._current_exif_path and os.path.isfile(self._current_exif_path):
                rows = self._load_metadata_rows_for_current_path(self._current_exif_path, tag_label_chinese=use_chinese)
                self.exif_table.set_exif(rows)

    def _save_exif_value(self, ifd_name: str, tag_id, new_val: str, raw_value, exiftool_key=None):
        """将编辑后的 EXIF 值写回文件。有 exiftool 时优先用 exiftool 写入（兼容性更好）。"""
        path = self._current_exif_path
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "无法保存", "未选择图片或文件不存在。")
            return
        ext = Path(path).suffix.lower()
        has_exiftool = bool(get_exiftool_executable_path())
        try:
            if ifd_name == META_IFD_NAME and str(tag_id) in (META_TITLE_TAG_ID, META_DESCRIPTION_TAG_ID):
                meta_tag_id = str(tag_id)
                new_text = _normalize_meta_edit_text(new_val)
                old_text = _normalize_meta_edit_text(raw_value if raw_value is not None else "")
                if new_text == old_text:
                    QMessageBox.information(self, "未变更", "输入内容与当前值一致，未执行写入。")
                    return
                if has_exiftool:
                    write_meta_with_exiftool(path, meta_tag_id, new_text)
                elif ext in PIEXIF_WRITABLE_EXTENSIONS:
                    write_meta_with_piexif(path, meta_tag_id, new_text)
                else:
                    raise RuntimeError("未找到 exiftool，无法写入该格式。请配置 exiftools_win/exiftools_mac 或将其加入 PATH。")
                self._sync_report_metadata_after_save(path, meta_tag_id, new_text)
            elif has_exiftool:
                if exiftool_key:
                    write_exif_with_exiftool_by_key(path, exiftool_key, new_val)
                elif ifd_name is not None and tag_id is not None:
                    write_exif_with_exiftool(path, ifd_name, tag_id, new_val, raw_value)
                else:
                    raise RuntimeError("无法写入该标签。")
            elif ext in PIEXIF_WRITABLE_EXTENSIONS and ifd_name is not None and tag_id is not None:
                if tag_id == 37510:
                    new_raw = b"ASCII\x00\x00\x00" + new_val.encode("utf-8")
                else:
                    new_raw = _parse_value_back(new_val, raw_value)
                if new_raw == raw_value:
                    QMessageBox.information(self, "未变更", "输入内容解析后与原值一致，未执行写入。")
                    return
                try:
                    data = piexif.load(path)
                    if ifd_name not in data or not isinstance(data[ifd_name], dict):
                        data[ifd_name] = {}
                    data[ifd_name][tag_id] = new_raw
                    exif_bytes = piexif.dump(data)
                    piexif.insert(exif_bytes, path)
                    verify_data = piexif.load(path)
                    verify_ifd = verify_data.get(ifd_name)
                    verify_raw = verify_ifd.get(tag_id) if isinstance(verify_ifd, dict) else None
                    if verify_raw != new_raw:
                        tag_type = get_tag_type(ifd_name, tag_id)
                        old_fmt = format_exif_value(new_raw, expected_type=tag_type)
                        new_fmt = format_exif_value(verify_raw, expected_type=tag_type)
                        if old_fmt != new_fmt:
                            raise RuntimeError("写入后校验失败：文件中的值与目标值不一致。")
                except Exception as e:
                    if type(e).__name__ == "InvalidImageDataError" and get_exiftool_executable_path():
                        write_exif_with_exiftool(path, ifd_name, tag_id, new_val, raw_value)
                    else:
                        raise
            else:
                raise RuntimeError("未找到 exiftool，无法写入该格式。请配置 exiftools_win/exiftools_mac 或将其加入 PATH。")
            rows = self._load_metadata_rows_for_current_path(path, tag_label_chinese=load_tag_label_chinese_from_settings())
            self.exif_table.set_exif(rows)
            QMessageBox.information(self, "已保存", "EXIF 已写入文件。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", _format_exception_message(e))

    def on_image_loaded(self, path: str):
        """图片被拖入或选择后调用。"""
        _log.info("[on_image_loaded] 选中照片 开始查询 EXIF path=%r", path)
        self._set_current_preview_source_path(path)
        self._current_exif_path = path
        self.file_label.setText(path)
        self.file_label.setToolTip(path)
        self._update_preview_focus_box(path)
        rows = self._load_metadata_rows_for_current_path(path, tag_label_chinese=load_tag_label_chinese_from_settings())
        if not rows:
            _log.info("[on_image_loaded] EXIF 查询 未查到 path=%r", path)
            QMessageBox.information(
                self,
                "无 EXIF",
                "该图片未包含 EXIF 信息或格式暂不支持。\n支持格式：JPEG、WebP、TIFF（piexif）；HEIC/HEIF/HIF（可选 pillow-heif）；各家相机 RAW（CR2/NEF/ARW/DNG 等，可选 exifread）；其他格式会尝试用 Pillow 读取。",
            )
        self.exif_table.set_exif(rows)

    def _update_preview_focus_box(self, path: str, *, allow_async_load: bool = True) -> None:
        """
        根据当前预览图尺寸与元数据更新 PreviewCanvas 的焦点框。

        `allow_async_load=False` 时只命中内存缓存，不启动新的焦点提取线程，
        这样方向键高 FPS 快速预览时不会被重复的元数据 I/O 拖慢。
        """
        if not path or not os.path.isfile(path):
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(None)
            self._apply_preview_overlay_options_to_preview()
            return
        size = self.preview_panel.get_preview_image_size()
        if not size:
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(None)
            self._apply_preview_overlay_options_to_preview()
            return
        preview_path = self.preview_panel.current_path() or path
        focus_source_path = self._resolve_focus_metadata_source_path(path)
        if (not preview_path or not os.path.isfile(preview_path)) and (not focus_source_path or not os.path.isfile(focus_source_path)):
            self._stop_focus_loader()
            _log.info("[_update_preview_focus_box] skip: no usable path preview=%r focus_source=%r", preview_path, focus_source_path)
            self.preview_panel.set_focus_box(None)
            self._apply_preview_overlay_options_to_preview()
            return
        cache_key, size_key, _entry, focus_state = self._get_photo_focus_memory_state(
            path,
            preview_path,
            focus_source_path,
            size,
            create=True,
        )
        reusable_focus_state = _entry.find_reusable_focus_state() if _entry is not None else None
        if focus_state is not None and focus_state.status == FOCUS_CACHE_STATUS_READY:
            self._stop_focus_loader()
            _log.info(
                "[_update_preview_focus_box] cache hit ready preview=%r focus_source=%r size=%sx%s used_path=%r",
                preview_path,
                focus_source_path,
                size_key[0],
                size_key[1],
                focus_state.used_path,
            )
            self.preview_panel.set_focus_box(focus_state.focus_box)
            self._apply_preview_overlay_options_to_preview()
            return
        if (
            focus_state is not None
            and reusable_focus_state is not None
            and reusable_focus_state is not focus_state
        ):
            focus_state.status = reusable_focus_state.status
            focus_state.focus_box = reusable_focus_state.focus_box
            focus_state.used_path = reusable_focus_state.used_path
            focus_state.request_id = reusable_focus_state.request_id
            focus_state.size_independent = True
            self._stop_focus_loader()
            _log.info(
                "[_update_preview_focus_box] cache hit shared preview=%r focus_source=%r size=%sx%s used_path=%r status=%s",
                preview_path,
                focus_source_path,
                size_key[0],
                size_key[1],
                reusable_focus_state.used_path,
                reusable_focus_state.status,
            )
            self.preview_panel.set_focus_box(focus_state.focus_box if focus_state.status == FOCUS_CACHE_STATUS_READY else None)
            self._apply_preview_overlay_options_to_preview()
            return
        if focus_state is not None and focus_state.status == FOCUS_CACHE_STATUS_MISS:
            self._stop_focus_loader()
            _log.info(
                "[_update_preview_focus_box] cache hit miss preview=%r focus_source=%r size=%sx%s",
                preview_path,
                focus_source_path,
                size_key[0],
                size_key[1],
            )
            self.preview_panel.set_focus_box(None)
            self._apply_preview_overlay_options_to_preview()
            return
        active_loader = self._focus_loader
        if isinstance(active_loader, FocusBoxLoader):
            if active_loader.photo_cache_key == cache_key and active_loader.preview_size_key == size_key:
                if focus_state is not None:
                    focus_state.status = FOCUS_CACHE_STATUS_LOADING
                    focus_state.request_id = active_loader.request_id
                    focus_state.size_independent = False
                _log.info(
                    "[_update_preview_focus_box] reuse in-flight request_id=%s preview=%r focus_source=%r size=%sx%s",
                    active_loader.request_id,
                    preview_path,
                    focus_source_path,
                    size_key[0],
                    size_key[1],
                )
                self.preview_panel.set_focus_box(None)
                self._apply_preview_overlay_options_to_preview()
                return
        if not allow_async_load:
            self._stop_focus_loader()
            _log.info(
                "[_update_preview_focus_box] cache cold skip async preview=%r focus_source=%r size=%sx%s",
                preview_path,
                focus_source_path,
                size_key[0],
                size_key[1],
            )
            self.preview_panel.set_focus_box(None)
            self._apply_preview_overlay_options_to_preview()
            return
        self._stop_focus_loader()
        self._focus_request_sequence += 1
        request_id = self._focus_request_sequence
        self._focus_display_request_id = request_id
        if focus_state is not None:
            focus_state.status = FOCUS_CACHE_STATUS_LOADING
            focus_state.focus_box = None
            focus_state.used_path = ""
            focus_state.request_id = request_id
            focus_state.size_independent = False
        _log.info(
            "[_update_preview_focus_box] async request_id=%s preview=%r focus_source=%r size=%sx%s",
            request_id,
            preview_path,
            focus_source_path,
            size[0],
            size[1],
        )
        loader = FocusBoxLoader(
            request_id,
            cache_key,
            preview_path,
            focus_source_path,
            size[0],
            size[1],
            self,
        )
        loader.focus_loaded.connect(self._on_focus_box_loaded)
        self._focus_loader = loader
        loader.start()
        self.preview_panel.set_focus_box(None)
        self._apply_preview_overlay_options_to_preview()

    def _stop_focus_loader(self) -> None:
        loader = self._focus_loader
        if loader is None:
            return
        self._reset_loading_focus_memory_state(loader)
        try:
            loader.focus_loaded.disconnect(self._on_focus_box_loaded)
        except Exception:
            pass
        loader.requestInterruption()
        self._focus_loader = None

    def _on_focus_box_loaded(self, request_id: int, focus_box, used_path: str) -> None:
        loader = self.sender()
        if isinstance(loader, FocusBoxLoader):
            entry = self._photo_preview_memory_cache.get(loader.photo_cache_key)
            if entry is not None:
                state = entry.get_or_create_focus_state(loader.preview_size_key)
                state.focus_box = focus_box
                state.used_path = os.path.normpath(used_path) if used_path else ""
                state.request_id = request_id
                state.status = FOCUS_CACHE_STATUS_READY if focus_box else FOCUS_CACHE_STATUS_MISS
                state.size_independent = loader.result_size_independent
                self._photo_preview_memory_cache.move_to_end(loader.photo_cache_key)
        if request_id != self._focus_display_request_id:
            _log.info(
                "[_on_focus_box_loaded] ignore stale request_id=%s current=%s used_path=%r",
                request_id,
                self._focus_display_request_id,
                used_path,
            )
            return
        if isinstance(loader, FocusBoxLoader):
            try:
                loader.focus_loaded.disconnect(self._on_focus_box_loaded)
            except Exception:
                pass
            if self._focus_loader is loader:
                self._focus_loader = None
        _log.info(
            "[_on_focus_box_loaded] request_id=%s used_path=%r focus_box=%r",
            request_id,
            used_path,
            focus_box,
        )
        self.preview_panel.set_focus_box(focus_box)
        self._apply_preview_overlay_options_to_preview()

    def _apply_show_focus_to_preview(self) -> None:
        """将「显示对焦点」复选框状态同步到预览 canvas，确保选项生效。"""
        self._apply_preview_overlay_options_to_preview()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_focus_loader()
        self._stop_focus_preload()
        super().closeEvent(event)


def main():
    # 打包运行时将工作目录设为 exe 所在目录，便于资源与插件加载
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(os.path.abspath(sys.executable))
        if app_dir and os.path.isdir(app_dir):
            os.chdir(app_dir)
    reload_runtime_user_options()
    about_info = load_about_info(_get_config_resource_path())
    app_name = _get_product_display_name(about_info)
    _apply_runtime_app_identity(app_name)

    # 冷启动/二次启动：解析命令行文件列表，若已有实例在运行则转发后退出
    # 先创建支持 macOS QFileOpenEvent 的 QApplication：
    # 1) 冷启动 open -a App file
    # 2) 已运行实例接收 Finder/LaunchServices 的热打开事件
    # 3) 第二实例转发 QLocalSocket 前也已完成 Qt 初始化
    app = ensure_file_open_aware_application(sys.argv)
    argv_files = get_initial_file_list_from_argv()
    app_id = (app_name or "SuperViewer").strip()
    if argv_files and send_file_list_to_running_app(app_id, argv_files):
        return
    if hasattr(app, "setApplicationName"):
        app.setApplicationName(app_name)
    if hasattr(app, "setApplicationDisplayName"):
        app.setApplicationDisplayName(app_name)
    if hasattr(app, "setApplicationVersion"):
        app.setApplicationVersion(about_info.get("version", ""))
    icon_path = _get_app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(50, 50, 50))
    palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    app.setPalette(palette)
    window = MainWindow(initial_received_files=argv_files if argv_files else None)

    # 单例接收：其它进程「发送到本应用」时回调到主线程
    def on_files_received(paths):
        QTimer.singleShot(0, (lambda p: lambda: window._on_received_file_list(p))(paths))

    # macOS FileOpen 事件在窗口创建前可能已经到达，这里安装回调后会自动冲刷缓存。
    install_file_open_handler(app, on_files_received)

    receiver = SingleInstanceReceiver(app_id, on_files_received)
    if not receiver.start():
        _log.warning("[main] SingleInstanceReceiver failed to listen (another instance may be running)")
    window._single_instance_receiver = receiver

    def stop_receiver():
        if getattr(window, "_single_instance_receiver", None):
            window._single_instance_receiver.stop()

    app.aboutToQuit.connect(stop_receiver)
    window.showMaximized()
    if argv_files:
        QTimer.singleShot(100, (lambda p: lambda: window._open_received_file_list(p))(argv_files))
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:  # 打包后无控制台时把错误写入文件便于排查
        if getattr(sys, "frozen", False):
            import traceback
            app_dir = os.path.dirname(os.path.abspath(sys.executable))
            log_path = os.path.join(app_dir, "superviewer_error.txt")
            try:
                with open(log_path, "w", encoding="utf-8") as f:
                    traceback.print_exc(file=f)
            except Exception:
                pass
        raise
