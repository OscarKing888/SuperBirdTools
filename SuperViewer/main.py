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
from pathlib import Path

import piexif

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

# 开发时从包内相对导入，打包后 entry/main 作为顶层脚本无父包，改用绝对导入 superviewer
try:
    from .superviewer.exif_helpers import (
        HEIF_EXTENSIONS,
        META_DESCRIPTION_TAG_ID,
        META_IFD_NAME,
        META_TITLE_TAG_ID,
        PIEXIF_WRITABLE_EXTENSIONS,
        apply_tag_priority,
        format_exif_value,
        get_tag_name,
        get_tag_name_for_exiftool_key,
        get_tag_type,
        load_all_exif,
        load_display_description,
        load_display_title,
        load_exif_tag_names_zh_from_settings,
        load_exif_piexif,
        load_hyperfocal_coc_mm_from_settings,
        load_tag_label_chinese_from_settings,
        load_tag_priority_from_settings,
        load_preview_grid_mode_from_settings,
        load_preview_grid_line_width_from_settings,
        merge_report_metadata_rows,
        save_tag_label_chinese_to_settings,
        save_preview_grid_mode_to_settings,
        save_preview_grid_line_width_to_settings,
        _format_exception_message,
        _normalize_meta_edit_text,
        _parse_value_back,
    )
    from .superviewer.exif_tag_order_dialog import ExifTagOrderDialog
    from .superviewer.focus_box_loader import FocusBoxLoader
    from .superviewer.focus_cache_preload_worker import FOCUS_PRELOAD_BATCH_SIZE, FocusCachePreloadWorker
    from .superviewer.focus_preview_loader import (
        RAW_EXTENSIONS,
        _load_exifread_metadata_for_focus,
        _load_focus_box_for_preview,
        _load_preview_pixmap_for_canvas,
        _resolve_focus_calc_image_size,
    )
    from .superviewer.paths_settings import (
        _build_main_window_title,
        _get_app_dir,
        _get_app_icon_path,
        _apply_runtime_app_identity,
        _get_config_resource_path,
        _get_product_display_name,
        _get_resource_path,
        load_last_selected_directory_from_settings,
        save_last_selected_directory_to_settings,
    )
    from .superviewer.photo_focus_memory_cache_state import (
        FOCUS_CACHE_STATUS_LOADING,
        FOCUS_CACHE_STATUS_MISS,
        FOCUS_CACHE_STATUS_READY,
        FOCUS_CACHE_STATUS_UNKNOWN,
        PhotoFocusMemoryCacheState,
    )
    from .superviewer.photo_preview_memory_entry import PhotoPreviewMemoryEntry
    from .superviewer.preview_panel import PreviewPanel
    from .superviewer.exif_table import ExifTable
    from .superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
    from .superviewer import qt_compat
    from .superviewer.qt_compat import (
        QAction,
        QApplication,
        QCheckBox,
        QColor,
        QComboBox,
        QDialog,
        QFileDialog,
        QGroupBox,
        QHBoxLayout,
        QIcon,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPalette,
        QPainter,
        QPen,
        QPushButton,
        QPixmap,
        QSplitter,
        QTimer,
        QVBoxLayout,
        QWidget,
        _Horizontal,
    )
except ImportError:
    from superviewer.exif_helpers import (
        HEIF_EXTENSIONS,
        META_DESCRIPTION_TAG_ID,
        META_IFD_NAME,
        META_TITLE_TAG_ID,
        PIEXIF_WRITABLE_EXTENSIONS,
        apply_tag_priority,
        format_exif_value,
        get_tag_name,
        get_tag_name_for_exiftool_key,
        get_tag_type,
        load_all_exif,
        load_display_description,
        load_display_title,
        load_exif_tag_names_zh_from_settings,
        load_exif_piexif,
        load_hyperfocal_coc_mm_from_settings,
        load_tag_label_chinese_from_settings,
        load_tag_priority_from_settings,
        load_preview_grid_mode_from_settings,
        load_preview_grid_line_width_from_settings,
        merge_report_metadata_rows,
        save_tag_label_chinese_to_settings,
        save_preview_grid_mode_to_settings,
        save_preview_grid_line_width_to_settings,
        _format_exception_message,
        _normalize_meta_edit_text,
        _parse_value_back,
    )
    from superviewer.exif_tag_order_dialog import ExifTagOrderDialog
    from superviewer.focus_box_loader import FocusBoxLoader
    from superviewer.focus_cache_preload_worker import FOCUS_PRELOAD_BATCH_SIZE, FocusCachePreloadWorker
    from superviewer.focus_preview_loader import (
        RAW_EXTENSIONS,
        _load_exifread_metadata_for_focus,
        _load_focus_box_for_preview,
        _load_preview_pixmap_for_canvas,
        _resolve_focus_calc_image_size,
    )
    from superviewer.paths_settings import (
        _build_main_window_title,
        _get_app_dir,
        _get_app_icon_path,
        _apply_runtime_app_identity,
        _get_config_resource_path,
        _get_product_display_name,
        _get_resource_path,
        load_last_selected_directory_from_settings,
        save_last_selected_directory_to_settings,
    )
    from superviewer.photo_focus_memory_cache_state import (
        FOCUS_CACHE_STATUS_LOADING,
        FOCUS_CACHE_STATUS_MISS,
        FOCUS_CACHE_STATUS_READY,
        FOCUS_CACHE_STATUS_UNKNOWN,
        PhotoFocusMemoryCacheState,
    )
    from superviewer.photo_preview_memory_entry import PhotoPreviewMemoryEntry
    from superviewer.preview_panel import PreviewPanel
    from superviewer.exif_table import ExifTable
    from superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
    from superviewer import qt_compat
    from superviewer.qt_compat import (
        QAction,
        QApplication,
        QCheckBox,
        QColor,
        QComboBox,
        QDialog,
        QFileDialog,
        QGroupBox,
        QHBoxLayout,
        QIcon,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPalette,
        QPainter,
        QPen,
        QPushButton,
        QPixmap,
        QSplitter,
        QTimer,
        QVBoxLayout,
        QWidget,
        _Horizontal,
    )

# Re-export for scripts (import main)
# RAW_EXTENSIONS, _load_preview_pixmap_for_canvas, _load_exifread_metadata_for_focus,
# _resolve_focus_calc_image_size, _load_focus_box_for_preview from focus_preview_loader above
# RAW_EXTENSIONS, _load_preview_pixmap_for_canvas, _load_exifread_metadata_for_focus,
# _resolve_focus_calc_image_size, _load_focus_box_for_preview already in namespace from imports above

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
FOCUS_PRELOAD_CANONICAL_SIZE = (0, 0)


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


_log = get_logger("main")

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
