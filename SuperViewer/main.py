#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super Viewer - 图片元信息查看器
支持拖拽图片到窗口，预览图像并展示扩展元信息面板。
"""

import json
import os
import sys
import shutil
import re
import subprocess
import tempfile
import time as _time
from pathlib import Path


def _ensure_default_log_file_env() -> None:
    if os.environ.get("APP_COMMON_LOG_FILE", "").strip():
        return
    try:
        repo_root = Path(__file__).resolve().parent.parent
        log_dir = repo_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        os.environ["APP_COMMON_LOG_FILE"] = str(log_dir / "SuperViewer.log")
    except OSError:
        return


_ensure_default_log_file_env()

from app_common import show_about_dialog, load_about_images, load_about_info
from app_common.log import get_logger
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log
from app_common.exif_io import (
    PhotoMetaDataXMP,
    close_exiftool_process,
    find_xmp_sidecar,
    _get_exiftool_tag_target,
)
from app_common.file_browser import DirectoryBrowserWidget
from app_common.image_formats import HEIF_EXTENSIONS, IMAGE_EXTENSIONS, RAW_EXTENSIONS
from app_common.preview_canvas import (
    PREVIEW_COMPOSITION_GRID_LINE_WIDTHS,
    PREVIEW_COMPOSITION_GRID_MODES,
    configure_preview_scale_preset_combo,
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
    sync_preview_scale_preset_combo,
)
from app_common.triangle_toggle_splitter import TriangleToggleSplitter
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
    KEY_PERF_PROBES_ENABLED,
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
        META_DESCRIPTION_TAG_ID,
        META_IFD_NAME,
        META_TITLE_TAG_ID,
        apply_tag_priority,
        load_all_exif,
        load_display_description,
        load_display_title,
        load_exif_piexif,
        load_hyperfocal_coc_mm_from_settings,
        load_tag_priority_from_settings,
        load_preview_grid_mode_from_settings,
        load_preview_grid_line_width_from_settings,
        save_preview_grid_mode_to_settings,
        save_preview_grid_line_width_to_settings,
        _format_exception_message,
        _normalize_meta_edit_text,
    )
    from .superviewer.paths_settings import (
        _build_main_window_title,
        _get_app_dir,
        _get_app_icon_path,
        _apply_runtime_app_identity,
        _get_config_resource_path,
        _get_product_display_name,
        _get_resource_path,
        load_main_splitter_state_from_settings,
        load_last_selected_directory_from_settings,
        save_main_splitter_state_to_settings,
        save_last_selected_directory_to_settings,
    )
    from .superviewer.focus_preview_loader import (
        _load_exifread_metadata_for_focus,
        _load_focus_box_for_preview,
        _load_preview_pixmap_for_canvas,
        _resolve_focus_calc_image_size,
    )
    from .superviewer.focus_box_loader import FocusBoxLoader
    from .superviewer.preview_panel import PreviewPanel
    from .superviewer.image_info_tabs import (
        ImageInfoTabPanel_EXIF,
        ImageInfoTabPanel_ImageInfo,
        ImageInfoTabPanel_Tags,
        ImageInfoTabWidget,
    )
    from .superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
    from .superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
    from .superviewer import qt_compat
    from .superviewer.qt_compat import (
        QAction,
        QApplication,
        QCheckBox,
        QColor,
        QComboBox,
        QDialog,
        QFileDialog,
        QHBoxLayout,
        QIcon,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPalette,
        QPainter,
        QPen,
        QPixmap,
        QTimer,
        QVBoxLayout,
        QWidget,
        _Horizontal,
    )
except ImportError:
    from superviewer.exif_helpers import (
        META_DESCRIPTION_TAG_ID,
        META_IFD_NAME,
        META_TITLE_TAG_ID,
        apply_tag_priority,
        load_all_exif,
        load_display_description,
        load_display_title,
        load_exif_piexif,
        load_hyperfocal_coc_mm_from_settings,
        load_tag_priority_from_settings,
        load_preview_grid_mode_from_settings,
        load_preview_grid_line_width_from_settings,
        save_preview_grid_mode_to_settings,
        save_preview_grid_line_width_to_settings,
        _format_exception_message,
        _normalize_meta_edit_text,
    )
    from superviewer.paths_settings import (
        _build_main_window_title,
        _get_app_dir,
        _get_app_icon_path,
        _apply_runtime_app_identity,
        _get_config_resource_path,
        _get_product_display_name,
        _get_resource_path,
        load_main_splitter_state_from_settings,
        load_last_selected_directory_from_settings,
        save_main_splitter_state_to_settings,
        save_last_selected_directory_to_settings,
    )
    from superviewer.focus_preview_loader import (
        _load_exifread_metadata_for_focus,
        _load_focus_box_for_preview,
        _load_preview_pixmap_for_canvas,
        _resolve_focus_calc_image_size,
    )
    from superviewer.focus_box_loader import FocusBoxLoader
    from superviewer.preview_panel import PreviewPanel
    from superviewer.image_info_tabs import (
        ImageInfoTabPanel_EXIF,
        ImageInfoTabPanel_ImageInfo,
        ImageInfoTabPanel_Tags,
        ImageInfoTabWidget,
    )
    from superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
    from superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
    from superviewer import qt_compat
    from superviewer.qt_compat import (
        QAction,
        QApplication,
        QCheckBox,
        QColor,
        QComboBox,
        QDialog,
        QFileDialog,
        QHBoxLayout,
        QIcon,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPalette,
        QPainter,
        QPen,
        QPixmap,
        QTimer,
        QVBoxLayout,
        QWidget,
        _Horizontal,
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
PREVIEW_SCALE_COMBO_WIDTH = 96


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
        self.setWindowTitle(_build_main_window_title(info))
        self.setMinimumSize(900, 600)
        self.resize(1500, 960)
        self._init_menu_bar()
        self._main_splitter: TriangleToggleSplitter | None = None
        self._main_splitter_state_save_timer = QTimer(self)
        self._main_splitter_state_save_timer.setSingleShot(True)
        self._main_splitter_state_save_timer.timeout.connect(self._save_main_splitter_state)
        icon_path = _get_app_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # 主分割器：目录树 | 文件列表 | 图片预览 | 元信息 Tab
        splitter = TriangleToggleSplitter(_Horizontal)
        self._main_splitter = splitter

        # ── 面板 1：目录浏览器 ──
        self._dir_browser = DirectoryBrowserWidget()
        self._dir_browser.setMinimumWidth(140)
        splitter.addWidget(self._dir_browser)

        # ── 面板 2：图像文件列表 ──
        self._file_list = SuperViewerTaggedFileListPanel()
        self._file_list.setMinimumWidth(520)
        splitter.addWidget(self._file_list)

        self._pending_dir_browser_sync_file_path = ""
        self._dir_browser_sync_timer = QTimer(self)
        self._dir_browser_sync_timer.setSingleShot(True)
        self._dir_browser_sync_timer.timeout.connect(self._sync_directory_browser_to_pending_file)

        # 连接目录选择 → 文件列表加载
        self._dir_browser.directory_selected.connect(self._on_directory_selected)
        # 连接文件列表选中 → 预览 + 元信息刷新
        self._file_list.file_fast_preview_requested.connect(self._on_file_fast_preview_requested)
        self._file_list.file_selected.connect(self._on_file_selected_from_list)

        # ── 面板 3：App 信息 + 文件名 + 拖放预览区 ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.file_label = QLabel("未选择图片")
        self.file_label.setStyleSheet("color: #aaa; font-size: 12px;")
        self.file_label.setWordWrap(True)
        left_layout.addWidget(self.file_label)
        overlay_row = QHBoxLayout()
        overlay_row.setContentsMargins(0, 0, 0, 0)
        overlay_row.setSpacing(8)
        self.check_show_focus = QCheckBox("显示对焦点")
        self.check_show_focus.setChecked(True)
        self.check_show_focus.setToolTip("在预览图上叠加显示相机对焦点（来自原始 RAW/HEIF 元数据）。")
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
        self.combo_preview_scale = QComboBox(self)
        configure_preview_scale_preset_combo(
            self.combo_preview_scale,
            tooltip="设置预览缩放比例，表示当前显示像素相对原图像素的百分比。",
            fixed_width=PREVIEW_SCALE_COMBO_WIDTH,
        )
        self.combo_preview_scale.activated.connect(self._on_preview_scale_preset_activated)
        overlay_row.addWidget(self.combo_preview_scale)
        overlay_row.addStretch(1)
        left_layout.addLayout(overlay_row)
        self.preview_panel = PreviewPanel(central)
        self.preview_panel.set_show_focus_enabled(self.check_show_focus.isChecked())
        self.preview_panel.set_composition_grid_mode(self.combo_preview_grid.currentData())
        self.preview_panel.set_composition_grid_line_width(self.combo_preview_grid_line_width.currentData())
        self.preview_panel.display_scale_percent_changed.connect(self._sync_preview_scale_combo)
        self._sync_preview_scale_combo(self.preview_panel.current_display_scale_percent())
        left_layout.addWidget(self.preview_panel, stretch=1)
        splitter.addWidget(left_widget)

        # ── 面板 4：可扩展元信息 Tab ──
        self.image_info_tabs = ImageInfoTabWidget(self)
        self.image_info_tabs.setMinimumWidth(300)
        self.image_info_panel = ImageInfoTabPanel_ImageInfo(
            self._file_list.available_photo_tags,
            self._file_list.photo_tags_for_path,
            self._file_list.set_photo_tag_for_paths,
            self._rename_photo_from_info_panel,
            metadata_provider=lambda path: self._file_list.get_photo_metadata_for_path(path, allow_slow_read=True),
            comment_save_callback=self._save_photo_comment_from_info_panel,
            preview_pixmap_provider=self.preview_panel.source_pixmap_for_path,
            write_enabled_provider=self._file_writes_allowed,
            write_disabled_tooltip_provider=self._file_writes_disabled_message,
            tag_write_enabled_provider=self._sidecar_writes_allowed,
            parent=self.image_info_tabs,
        )
        self.tags_info_panel = ImageInfoTabPanel_Tags(
            self._file_list.available_photo_tags,
            self._file_list.photo_tags_for_path,
            self._file_list.set_photo_tag_for_paths,
            self._file_list.clear_photo_tags_for_paths,
            self._sidecar_writes_allowed,
            self.image_info_tabs,
        )
        self.exif_info_panel = ImageInfoTabPanel_EXIF(
            self._load_metadata_rows_for_current_path,
            self._save_exif_value,
            self.image_info_tabs,
        )

        self.image_info_tabs.add_info_panel(self.image_info_panel)
        self.image_info_tabs.add_info_panel(self.tags_info_panel)
        self.image_info_tabs.add_info_panel(self.exif_info_panel)
        self.image_info_tabs.on_photo_selected("")
        splitter.addWidget(self.image_info_tabs)
        splitter.set_handle_toggle_target(3, 3)

        # 各面板初始宽度：目录树 200 | 文件列表 320 | 预览 380 | 元信息 320
        if not splitter.restore_panel_state(load_main_splitter_state_from_settings()):
            splitter.setSizes([220, 680, 520, 340])
        splitter.splitterMoved.connect(self._queue_save_main_splitter_state)
        splitter.stateChanged.connect(self._queue_save_main_splitter_state)
        layout.addWidget(splitter)

        self._current_exif_path = None

        # 「显示对焦点」异步加载状态
        self._focus_loader: FocusBoxLoader | None = None
        self._focus_request_sequence = 0
        self._focus_display_request_id = 0

        # preview_panel 的 parent 为 central，回调挂在 left_widget 上供拖放/选图后调用
        left_widget.on_image_loaded = self.on_image_loaded

        if not initial_received_files:
            QTimer.singleShot(0, self._restore_last_selected_directory)

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

    def _queue_save_main_splitter_state(self, *_args) -> None:
        try:
            self._main_splitter_state_save_timer.start(250)
        except Exception:
            self._save_main_splitter_state()

    def _save_main_splitter_state(self) -> None:
        splitter = getattr(self, "_main_splitter", None)
        if not isinstance(splitter, TriangleToggleSplitter):
            return
        try:
            save_main_splitter_state_to_settings(splitter.export_panel_state())
        except Exception:
            pass

    def _sync_directory_browser_to_file_selection(self, path: str) -> None:
        display_path = self._file_list.get_selected_display_path()
        target_path = display_path or path
        if not target_path:
            return
        self._pending_dir_browser_sync_file_path = os.path.normpath(str(target_path))
        if self._dir_browser_sync_timer.isActive():
            self._dir_browser_sync_timer.stop()
        self._dir_browser_sync_timer.start(0)

    def _sync_directory_browser_to_pending_file(self) -> None:
        target_path = self._pending_dir_browser_sync_file_path
        self._pending_dir_browser_sync_file_path = ""
        if not target_path:
            return
        try:
            self._dir_browser.select_file_parent_directory(target_path, emit_signal=False)
        except Exception as exc:
            _log.debug("[dir_browser.sync] failed path=%r: %s", target_path, exc)

    def _on_file_selected_from_list(self, path: str):
        """文件列表中选中图像文件，触发预览和元信息刷新（等同于拖放）。"""
        t0 = _time.perf_counter()
        probe_t0 = perf_counter()
        perf_log(_log, "[PERF][image_switch][main] START source=%r", path)
        self._sync_directory_browser_to_file_selection(path)
        preview_t0 = _time.perf_counter()
        quick_size_fn = getattr(self._file_list, "preview_quick_size", None)
        quick_size = quick_size_fn() if callable(quick_size_fn) else None
        self.preview_panel.set_image(path, quick_size=quick_size)
        preview_ms = (_time.perf_counter() - preview_t0) * 1000.0
        info_t0 = _time.perf_counter()
        self.on_image_loaded(path)
        info_ms = (_time.perf_counter() - info_t0) * 1000.0
        perf_log(
            _log,
            "[PERF][image_switch][main] END source=%r preview_ms=%.1f info_ms=%.1f total_ms=%.1f",
            path,
            preview_ms,
            info_ms,
            (_time.perf_counter() - t0) * 1000.0,
        )
        perf_log(
            _log,
            "[image.switch.main] source=%r preview_ms=%.1f info_ms=%.1f total_ms=%.1f",
            path,
            preview_ms,
            info_ms,
            elapsed_ms(probe_t0),
        )

    def _on_file_fast_preview_requested(self, path: str):
        """连续方向键长按时直接预览原始文件，不再切到 report 派生预览图。"""
        t0 = _time.perf_counter()
        probe_t0 = perf_counter()
        perf_log(_log, "[PERF][fast_preview][main] START source=%r", path)
        preview_t0 = _time.perf_counter()
        quick_size_fn = getattr(self._file_list, "preview_quick_size", None)
        quick_size = quick_size_fn() if callable(quick_size_fn) else None
        self.preview_panel.set_image(path, load_full=False, quick_size=quick_size)
        # 方向键长按高速浏览时不启动焦点提取线程，避免重复 I/O 拖慢翻图
        self._update_preview_focus_box(path, allow_async_load=False)
        preview_ms = (_time.perf_counter() - preview_t0) * 1000.0
        perf_log(
            _log,
            "[PERF][fast_preview][main] END source=%r preview_ms=%.1f total_ms=%.1f",
            path,
            preview_ms,
            (_time.perf_counter() - t0) * 1000.0,
        )
        perf_log(
            _log,
            "[image.fast_preview] source=%r preview_ms=%.1f total_ms=%.1f",
            path,
            preview_ms,
            elapsed_ms(probe_t0),
        )

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
        perf_probe_act = QAction("性能探针日志", self)
        perf_probe_act.setCheckable(True)
        perf_probe_act.setChecked(bool(get_runtime_user_options().get(KEY_PERF_PROBES_ENABLED, 0)))
        perf_probe_act.setToolTip("开启后在日志中记录图片切换、过滤、标星、标签写入等关键路径耗时。")
        perf_probe_act.triggered.connect(self._set_perf_probes_enabled)
        self._perf_probe_action = perf_probe_act
        settings_menu.addAction(perf_probe_act)

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
        self._sync_perf_probe_action()
        self._file_list.apply_user_options()
        self.preview_panel.set_keep_view_on_switch(
            bool(normalized.get("keep_view_on_switch", 1))
        )
        QMessageBox.information(
            self,
            "已保存",
            f"用户选项已保存到：\n{get_user_options_path()}",
        )

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
        self._file_list.apply_user_options()
        _log.info("[PERF_PROBE] enabled=%s config=%r", bool(normalized.get(KEY_PERF_PROBES_ENABLED, 0)), get_user_options_path())

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

    def _on_preview_scale_preset_activated(self, index: int) -> None:
        percent = self.combo_preview_scale.itemData(index)
        try:
            parsed = float(percent)
        except Exception:
            return
        self.preview_panel.set_display_scale_percent(parsed, preserve_view=True)
        self._sync_preview_scale_combo(self.preview_panel.current_display_scale_percent())

    def _sync_preview_scale_combo(self, scale_percent: object) -> None:
        sync_preview_scale_preset_combo(self.combo_preview_scale, scale_percent)

    def _load_metadata_rows_for_current_path(self, path: str, tag_label_chinese: bool) -> list[tuple]:
        rows = load_all_exif(path, tag_label_chinese=tag_label_chinese)
        return apply_tag_priority(rows, load_tag_priority_from_settings())

    def _sync_metadata_after_exif_save(self, path: str, meta_tag_id: str, value: str) -> None:
        meta_updates: dict[str, str] = {}
        if meta_tag_id == META_TITLE_TAG_ID:
            meta_updates.update({
                "title": value,
                "XMP-dc:Title": value,
                "IFD0:XPTitle": value,
                "IFD0:DocumentName": value,
            })
        elif meta_tag_id == META_DESCRIPTION_TAG_ID:
            meta_updates.update({
                "Description": value,
                "XMP-dc:Description": value,
                "XMP:Description": value,
                "IFD0:XPComment": value,
                "IFD0:ImageDescription": value,
                "EXIF:UserComment": value,
            })
        if not meta_updates:
            return
        try:
            self._file_list.sync_metadata_edit_for_path(path, meta_updates=meta_updates)
        except Exception:
            _log.exception("[_sync_metadata_after_exif_save] path=%r meta_tag_id=%r", path, meta_tag_id)

    def _write_xmp_meta_value(self, path: str, meta_tag_id: str, value: str) -> bool:
        xmp_meta = PhotoMetaDataXMP()
        if meta_tag_id == META_DESCRIPTION_TAG_ID:
            return bool(xmp_meta.write_description(path, value))
        if meta_tag_id == META_TITLE_TAG_ID:
            return bool(xmp_meta.write_title(path, value))
        return False

    def _save_exif_value(self, ifd_name: str, tag_id, new_val: str, raw_value, exiftool_key=None):
        """Write an edited EXIF-table value to XMP sidecar only."""
        path = self._current_exif_path
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "无法保存", "未选择图片或文件不存在。")
            return
        try:
            if ifd_name == META_IFD_NAME and str(tag_id) in (META_TITLE_TAG_ID, META_DESCRIPTION_TAG_ID):
                meta_tag_id = str(tag_id)
                new_text = _normalize_meta_edit_text(new_val)
                old_text = _normalize_meta_edit_text(raw_value if raw_value is not None else "")
                if new_text == old_text:
                    QMessageBox.information(self, "未变更", "输入内容与当前值一致，未执行写入。")
                    return
                if not self._write_xmp_meta_value(path, meta_tag_id, new_text):
                    raise RuntimeError("无法写入 XMP sidecar。")
                self._sync_metadata_after_exif_save(path, meta_tag_id, new_text)
            else:
                tag_key = str(exiftool_key or "").strip()
                if not tag_key and ifd_name is not None and tag_id is not None:
                    try:
                        tag_key = _get_exiftool_tag_target(str(ifd_name), int(tag_id)) or ""
                    except Exception:
                        tag_key = ""
                if not tag_key:
                    raise RuntimeError("无法确定可写入 XMP sidecar 的标签名。")
                if not PhotoMetaDataXMP().write(path, {tag_key: new_val}):
                    raise RuntimeError("无法写入 XMP sidecar。请确认 exiftool 可用或该字段支持直接 sidecar 写入。")
            self.exif_info_panel.refresh_current_photo()
            QMessageBox.information(self, "已保存", "元数据已写入 XMP sidecar。")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", _format_exception_message(exc))

    @staticmethod
    def _same_filesystem_key(path_a: str | os.PathLike, path_b: str | os.PathLike) -> bool:
        return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path_a)))) == os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(path_b)))
        )

    @staticmethod
    def _case_safe_rename_path(source: Path, target: Path) -> None:
        source_text = os.path.normpath(str(source))
        target_text = os.path.normpath(str(target))
        if source_text == target_text:
            return
        if MainWindow._same_filesystem_key(source_text, target_text):
            for idx in range(1000):
                tmp = source.with_name(f".{source.name}.rename-{os.getpid()}-{idx}.tmp")
                if not tmp.exists():
                    break
            else:
                raise RuntimeError("无法创建临时重命名路径。")
            os.rename(source_text, str(tmp))
            os.rename(str(tmp), target_text)
            return
        os.rename(source_text, target_text)

    @staticmethod
    def _rename_target_path(source_path: str, requested_name: str) -> Path:
        source = Path(source_path)
        clean_name = str(requested_name or "").strip()
        if not clean_name:
            raise ValueError("文件名不能为空。")
        invalid_chars = set('<>:"/\\|?*')
        if any(ch in invalid_chars for ch in clean_name):
            raise ValueError('文件名不能包含 <>:"/\\|?* 等字符。')
        if clean_name in (".", ".."):
            raise ValueError("文件名无效。")
        name_path = Path(clean_name)
        target_name = clean_name if name_path.suffix else f"{clean_name}{source.suffix}"
        return source.with_name(target_name)

    def _file_writes_allowed(self, path: str | None = None) -> bool:
        return True

    def _sidecar_writes_allowed(self) -> bool:
        return True

    def _file_writes_disabled_message(self, action: str = "写入操作", path: str | None = None) -> str:
        return ""

    def _rename_photo_from_info_panel(self, path: str, requested_name: str) -> str:
        if not self._file_writes_allowed(path):
            raise PermissionError(self._file_writes_disabled_message("重命名", path))
        source_path = os.path.normpath(os.path.abspath(path)) if path else ""
        if not source_path or not os.path.isfile(source_path):
            raise FileNotFoundError("当前图片不存在，无法重命名。")

        source = Path(source_path)
        target = self._rename_target_path(source_path, requested_name)
        target_path = os.path.normpath(os.path.abspath(str(target)))
        if source_path == target_path:
            return source_path

        same_photo_key = self._same_filesystem_key(source_path, target_path)
        if not same_photo_key and os.path.exists(target_path):
            raise FileExistsError(f"目标文件已存在：{target_path}")

        sidecar_pairs: list[tuple[Path, Path]] = []
        xmp_sidecar_source = find_xmp_sidecar(source_path)
        seen_sidecars: set[str] = set()
        for sidecar_source in (xmp_sidecar_source,):
            if not sidecar_source or not os.path.isfile(sidecar_source):
                continue
            sidecar_key = os.path.normcase(os.path.normpath(os.path.abspath(sidecar_source)))
            if sidecar_key in seen_sidecars:
                continue
            seen_sidecars.add(sidecar_key)
            sidecar_suffix = Path(sidecar_source).suffix or ".xmp"
            sidecar_target = Path(target_path).with_suffix(sidecar_suffix)
            sidecar_target_text = os.path.normpath(os.path.abspath(str(sidecar_target)))
            if (
                not self._same_filesystem_key(sidecar_source, sidecar_target_text)
                and os.path.exists(sidecar_target_text)
            ):
                raise FileExistsError(f"目标 sidecar 已存在：{sidecar_target_text}")
            sidecar_pairs.append((Path(sidecar_source), Path(sidecar_target_text)))

        renamed_photo = False
        try:
            self._case_safe_rename_path(source, Path(target_path))
            renamed_photo = True
            for sidecar_source, sidecar_target in sidecar_pairs:
                self._case_safe_rename_path(sidecar_source, sidecar_target)
        except Exception:
            if renamed_photo and os.path.exists(target_path) and not os.path.exists(source_path):
                try:
                    self._case_safe_rename_path(Path(target_path), source)
                except Exception:
                    pass
            raise

        self._current_exif_path = target_path
        self.file_label.setText(target_path)
        self.file_label.setToolTip(target_path)
        self.preview_panel.set_image(target_path)

        current_dir = self._file_list.get_current_dir() or str(Path(target_path).parent)
        self._file_list.set_pending_selection([target_path], current_path=target_path, apply_immediately=False)
        self._file_list.load_directory(current_dir, force_reload=True)
        return target_path

    def _save_photo_comment_from_info_panel(self, path: str, comment: str) -> bool:
        if not self._file_writes_allowed(path):
            raise PermissionError(self._file_writes_disabled_message("保存注释", path))
        source_path = os.path.normpath(os.path.abspath(path)) if path else ""
        if not source_path or not os.path.isfile(source_path):
            raise FileNotFoundError("当前图片不存在，无法保存注释。")

        text = str(comment or "").strip()
        xmp_meta = PhotoMetaDataXMP()
        saved = bool(xmp_meta.write_description(source_path, text))
        if not saved:
            raise RuntimeError("无法写入 sidecar 注释。")

        self._file_list.sync_metadata_edit_for_path(
            source_path,
            meta_updates={
                "Description": text,
                "XMP-dc:Description": text,
                "XMP:Description": text,
                "IFD0:XPComment": text,
                "IFD0:ImageDescription": text,
                "EXIF:UserComment": text,
            },
        )
        return True

    def on_image_loaded(self, path: str):
        """图片被拖入或选择后调用。"""
        t0 = _time.perf_counter()
        probe_t0 = perf_counter()
        perf_log(_log, "[PERF][image_switch][info] START path=%r", path)
        self._current_exif_path = path
        label_t0 = _time.perf_counter()
        self.file_label.setText(path)
        self.file_label.setToolTip(path)
        label_ms = (_time.perf_counter() - label_t0) * 1000.0
        tabs_t0 = _time.perf_counter()
        self.image_info_tabs.on_photo_selected(path)
        tabs_ms = (_time.perf_counter() - tabs_t0) * 1000.0
        self._update_preview_focus_box(path)
        perf_log(
            _log,
            "[PERF][image_switch][info] END path=%r label_ms=%.1f tabs_ms=%.1f total_ms=%.1f",
            path,
            label_ms,
            tabs_ms,
            (_time.perf_counter() - t0) * 1000.0,
        )
        perf_log(
            _log,
            "[image.info] path=%r label_ms=%.1f tabs_ms=%.1f total_ms=%.1f",
            path,
            label_ms,
            tabs_ms,
            elapsed_ms(probe_t0),
        )

    def _on_preview_overlay_toggled(self, _checked: bool) -> None:
        """「显示对焦点」开关：同步 canvas 并按需加载/清除当前图的对焦点框。"""
        enabled = self.check_show_focus.isChecked()
        self.preview_panel.set_show_focus_enabled(enabled)
        if enabled:
            if self._current_exif_path:
                self._update_preview_focus_box(self._current_exif_path)
        else:
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(None)

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
        """为「显示对焦点」解析元数据来源（仅源文件）：当前文件若为 RAW/HEIF 则直接用，
        否则取同目录同 stem 的 RAW/HEIF 源文件。"""
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

    def _get_cached_focus_box_for_preview(self, path: str, focus_source_path: str = "") -> tuple[bool, object | None]:
        getter = getattr(self._file_list, "get_cached_focus_box_state_for_path", None)
        if not callable(getter):
            return (False, None)
        seen: set[str] = set()
        for candidate in (path, focus_source_path):
            norm = os.path.normpath(candidate) if candidate else ""
            if not norm:
                continue
            key = os.path.normcase(norm)
            if key in seen:
                continue
            seen.add(key)
            try:
                checked, focus_box = getter(norm)
            except Exception:
                continue
            if checked:
                return (True, focus_box)
        return (False, None)

    def _update_preview_focus_box(self, path: str, *, allow_async_load: bool = True) -> None:
        """根据当前预览图尺寸与元数据，异步加载并更新 PreviewCanvas 的对焦点框。

        `allow_async_load=False` 时仅清除显示、不启动新线程（用于方向键高速浏览）。
        """
        if not self.check_show_focus.isChecked():
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(None)
            return
        if not path or not os.path.isfile(path):
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(None)
            return
        focus_source_path = self._resolve_focus_metadata_source_path(path)
        cached_checked, cached_focus_box = self._get_cached_focus_box_for_preview(path, focus_source_path)
        if cached_checked:
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(cached_focus_box)
            return
        size = self.preview_panel.get_preview_image_size()
        if not size:
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(None)
            return
        preview_path = self.preview_panel.current_path() or path
        if (not preview_path or not os.path.isfile(preview_path)) and (
            not focus_source_path or not os.path.isfile(focus_source_path)
        ):
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(None)
            return
        if not allow_async_load:
            self._stop_focus_loader()
            self.preview_panel.set_focus_box(None)
            return
        self._stop_focus_loader()
        self._focus_request_sequence += 1
        request_id = self._focus_request_sequence
        self._focus_display_request_id = request_id
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
            "",
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

    def _stop_focus_loader(self) -> None:
        loader = self._focus_loader
        if loader is None:
            return
        try:
            loader.focus_loaded.disconnect(self._on_focus_box_loaded)
        except Exception:
            pass
        loader.requestInterruption()
        self._focus_loader = None

    def _on_focus_box_loaded(self, request_id: int, focus_box, used_path: str) -> None:
        loader = self.sender()
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

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._stop_focus_loader()
        except Exception:
            pass
        try:
            if self._main_splitter_state_save_timer.isActive():
                self._main_splitter_state_save_timer.stop()
            self._save_main_splitter_state()
        except Exception:
            pass
        try:
            self._file_list.close_tag_store()
        except Exception:
            pass
        try:
            self.preview_panel.shutdown()
        except Exception:
            pass
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
        try:
            if getattr(window, "_single_instance_receiver", None):
                window._single_instance_receiver.stop()
        finally:
            close_exiftool_process()

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
