# -*- coding: utf-8 -*-
"""用户选项对话框：缩略图线程数、小缩略图尺寸、方向键速率、保持视图等。"""

from __future__ import annotations

import os

from app_common.settings_dialog import SettingsDialog
from app_common.collapsible_section import CollapsibleSection
from app_common.superviewer_user_options import (
    DIRECT_PREVIEW_BY_FILE_SIZE,
    DIRECT_PREVIEW_BY_PIXELS,
    KEY_DIRECT_PREVIEW_LIMIT_MODE,
    KEY_DIRECT_PREVIEW_MAX_FILE_MB,
    KEY_DIRECT_PREVIEW_MAX_PIXELS,
    MAX_DIRECT_PREVIEW_FILE_MB,
    MAX_DIRECT_PREVIEW_PIXELS,
    KEY_NAVIGATION_FPS_OPTIONS,
    KEY_PERF_PROBES_ENABLED,
    PERSISTENT_THUMB_SIZE_LEVELS,
    get_runtime_user_options,
    get_user_options_path,
    normalize_user_options,
)

from .qt_compat import (
    QCheckBox,
    QComboBox,
    QWidget,
    QGridLayout,
    QLabel,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
)

from .file_context_menu import menu_icon


class SuperViewerUserOptionsDialog(SettingsDialog):
    def __init__(self, parent=None, options: dict | None = None) -> None:
        super().__init__(parent)
        opts = normalize_user_options(options if options is not None else get_runtime_user_options())
        cpu_count = max(1, os.cpu_count() or 1)
        max_workers = max(64, cpu_count * 2)

        self.set_description(f"配置文件：{get_user_options_path()}")
        browsing = QWidget(self)
        browsing_layout = QVBoxLayout(browsing)
        performance = QWidget(self)
        performance_layout = QVBoxLayout(performance)
        self.add_page(browsing, "浏览与预览", menu_icon("folder"))
        self.add_page(performance, "性能与缓存", menu_icon("process"))

        def section(layout, title):
            group = CollapsibleSection(title, self)
            grid = QGridLayout(group.body)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(8)
            layout.addWidget(group)
            return grid

        grid = section(performance_layout, "线程与缓存")
        row = 0
        grid.addWidget(QLabel("后台图像加载线程数"), row, 0)
        self._spin_thumb_loader_workers = QSpinBox(self)
        self._spin_thumb_loader_workers.setRange(1, max_workers)
        self._spin_thumb_loader_workers.setValue(int(opts.get("thumbnail_loader_workers", cpu_count)))
        self._spin_thumb_loader_workers.setToolTip("缩略图后台加载线程数，默认等于 CPU 逻辑核心数。")
        grid.addWidget(self._spin_thumb_loader_workers, row, 1)
        grid.addWidget(QLabel(f"默认 {cpu_count}"), row, 2)

        row += 1
        metadata_default = max(1, min(8, cpu_count // 4 or 1))
        grid.addWidget(QLabel("元数据读取线程数"), row, 0)
        self._spin_metadata_loader_workers = QSpinBox(self)
        self._spin_metadata_loader_workers.setRange(1, max_workers)
        self._spin_metadata_loader_workers.setValue(int(opts.get("metadata_loader_workers", metadata_default)))
        self._spin_metadata_loader_workers.setToolTip("统一任务池为元数据读取预留的并发额度；更改后重启应用生效。")
        grid.addWidget(self._spin_metadata_loader_workers, row, 1)
        grid.addWidget(QLabel(f"默认 {metadata_default}"), row, 2)

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

        grid = section(browsing_layout, "浏览行为")
        row = 0
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

        grid = section(browsing_layout, "原图直显")
        grid.addWidget(QLabel("判断方式"), 0, 0)
        self._combo_direct_preview_mode = QComboBox(self)
        self._combo_direct_preview_mode.addItem("按分辨率", DIRECT_PREVIEW_BY_PIXELS)
        self._combo_direct_preview_mode.addItem("按文件大小", DIRECT_PREVIEW_BY_FILE_SIZE)
        self._combo_direct_preview_mode.setCurrentIndex(
            self._combo_direct_preview_mode.findData(opts[KEY_DIRECT_PREVIEW_LIMIT_MODE]))
        grid.addWidget(self._combo_direct_preview_mode, 0, 1)

        grid.addWidget(QLabel("分辨率上限"), 1, 0)
        self._spin_direct_preview_mp = QDoubleSpinBox(self)
        self._spin_direct_preview_mp.setDecimals(6)
        self._spin_direct_preview_mp.setRange(0, MAX_DIRECT_PREVIEW_PIXELS / 1_000_000)
        self._spin_direct_preview_mp.setSingleStep(1)
        self._spin_direct_preview_mp.setSuffix(" MP")
        self._spin_direct_preview_mp.setValue(opts[KEY_DIRECT_PREVIEW_MAX_PIXELS] / 1_000_000)
        self._spin_direct_preview_mp.setToolTip(
            "按宽 × 高计算；1 MP = 1,000,000 像素。默认 41.943040 MP，保持原来的像素阈值。")
        grid.addWidget(self._spin_direct_preview_mp, 1, 1)

        grid.addWidget(QLabel("文件大小上限"), 2, 0)
        self._spin_direct_preview_file_mb = QSpinBox(self)
        self._spin_direct_preview_file_mb.setRange(0, MAX_DIRECT_PREVIEW_FILE_MB)
        self._spin_direct_preview_file_mb.setSuffix(" MB")
        self._spin_direct_preview_file_mb.setValue(opts[KEY_DIRECT_PREVIEW_MAX_FILE_MB])
        self._spin_direct_preview_file_mb.setToolTip(
            "1 MB = 1024 × 1024 字节。文件大小按压缩后的文件计算，不代表解码后的内存占用。")
        grid.addWidget(self._spin_direct_preview_file_mb, 2, 1)
        self._combo_direct_preview_mode.currentIndexChanged.connect(self._sync_direct_preview_controls)
        self._sync_direct_preview_controls()
        hint = QLabel("鼠标和方向键选图均使用此设置。\n上限内直接显示原图；超过上限先显示快速预览。\n设为 0 关闭直显，保存后下次选图生效。")
        hint.setWordWrap(True)
        grid.addWidget(hint, 3, 0, 1, 2)

        grid = section(performance_layout, "诊断")
        row = 0
        grid.addWidget(QLabel("性能探针日志"), row, 0)
        self._chk_perf_probes = QCheckBox(self)
        self._chk_perf_probes.setChecked(bool(opts.get(KEY_PERF_PROBES_ENABLED, 0)))
        self._chk_perf_probes.setToolTip("开启后在日志中记录图片切换、过滤、标星、标签写入等关键路径耗时。")
        grid.addWidget(self._chk_perf_probes, row, 1)
        grid.addWidget(QLabel("默认关闭"), row, 2)

        note = QLabel("缩略视图会根据当前缩略图大小自动匹配预览层级。\n元数据读取线程数更改后重启应用生效。")
        note.setWordWrap(True)
        performance_layout.addWidget(note)
        preview_note = QLabel("首次打开或切换目录的首张图片自动适应窗口。")
        preview_note.setWordWrap(True)
        browsing_layout.addWidget(preview_note)

    def _sync_direct_preview_controls(self, *_args) -> None:
        by_pixels = self._combo_direct_preview_mode.currentData() == DIRECT_PREVIEW_BY_PIXELS
        self._spin_direct_preview_mp.setEnabled(by_pixels)
        self._spin_direct_preview_file_mb.setEnabled(not by_pixels)

    def selected_options(self) -> dict[str, int]:
        return {
            "thumbnail_loader_workers": int(self._spin_thumb_loader_workers.value()),
            "metadata_loader_workers": int(self._spin_metadata_loader_workers.value()),
            "persistent_thumb_workers": int(self._spin_persistent_thumb_workers.value()),
            "persistent_thumb_max_size": int(self._combo_persistent_thumb_size.currentData()),
            "key_navigation_fps": int(self._combo_key_navigation_fps.currentData()),
            "keep_view_on_switch": int(self._chk_keep_view.isChecked()),
            KEY_PERF_PROBES_ENABLED: int(self._chk_perf_probes.isChecked()),
            KEY_DIRECT_PREVIEW_LIMIT_MODE: int(self._combo_direct_preview_mode.currentData()),
            KEY_DIRECT_PREVIEW_MAX_PIXELS: round(self._spin_direct_preview_mp.value() * 1_000_000),
            KEY_DIRECT_PREVIEW_MAX_FILE_MB: int(self._spin_direct_preview_file_mb.value()),
        }
