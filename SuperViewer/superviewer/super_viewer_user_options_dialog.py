# -*- coding: utf-8 -*-
"""用户选项对话框：缩略图线程数、小缩略图尺寸、方向键速率、保持视图等。"""

from __future__ import annotations

import os

from app_common.settings_dialog import SettingsDialog
from app_common.collapsible_section import CollapsibleSection
from app_common.superviewer_user_options import (
    BIRD_SHARPNESS_PARAM_KEYS,
    BIRD_SHARPNESS_WORKERS_MAX,
    KEY_BIRD_SHARPNESS_WORKERS,
    KEY_NAVIGATION_FPS_OPTIONS,
    KEY_PERF_PROBES_ENABLED,
    KEY_BIRD_HOVER_COLOR,
    KEY_METADATA_MEMORY_MB,
    METADATA_MEMORY_DEFAULT_MB,
    METADATA_MEMORY_MIN_MB,
    METADATA_MEMORY_MAX_MB,
    PERSISTENT_THUMB_SIZE_LEVELS,
    USER_OPTIONS_FILENAME,
    get_runtime_user_options,
    get_user_options_path,
    bird_sharpness_params_to_options,
    normalize_user_options,
    valid_denoise_subdir,
)

from .bird_archive_ui import ArchiveOptionsForm, archive_icon, load_archive_options, save_archive_options
from .file_context_menu import menu_icon

from .qt_compat import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QWidget,
    QVBoxLayout,
)


try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QStyle
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QPalette
    from PyQt5.QtWidgets import QStyle

_TOOLTIP_ROLE = getattr(getattr(Qt, "ItemDataRole", Qt), "ToolTipRole")


class SuperViewerUserOptionsDialog(SettingsDialog):
    def __init__(self, parent=None, options: dict | None = None) -> None:
        super().__init__(parent)

        opts = normalize_user_options(options or get_runtime_user_options())
        cpu_count = max(1, os.cpu_count() or 1)
        max_workers = max(64, cpu_count * 2)
        metadata_default = max(1, min(8, cpu_count // 4 or 1))
        persistent_default = max(1, cpu_count - metadata_default)

        self.set_description(
            f"配置文件将保存在用户配置目录：{get_user_options_path()}\n"
            f"文件名：{USER_OPTIONS_FILENAME}"
        )

        self.option_groups: dict[str, CollapsibleSection] = {}

        def grid_group(title):
            group = CollapsibleSection(title)
            self.option_groups[title] = group
            grid = QGridLayout(group.body)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(8)
            return grid

        grid = grid_group("加载与缓存")

        row = 0
        grid.addWidget(QLabel("后台图像加载线程数"), row, 0)
        self._spin_thumb_loader_workers = QSpinBox(self)
        self._spin_thumb_loader_workers.setRange(1, max_workers)
        self._spin_thumb_loader_workers.setValue(int(opts.get("thumbnail_loader_workers", cpu_count)))
        self._spin_thumb_loader_workers.setToolTip("缩略图后台加载线程数，默认等于 CPU 逻辑核心数。")
        grid.addWidget(self._spin_thumb_loader_workers, row, 1)
        grid.addWidget(QLabel(f"默认 {cpu_count}"), row, 2)

        row += 1
        grid.addWidget(QLabel("元数据读取线程数"), row, 0)
        self._spin_metadata_loader_workers = QSpinBox(self)
        self._spin_metadata_loader_workers.setRange(1, max_workers)
        self._spin_metadata_loader_workers.setValue(int(opts.get("metadata_loader_workers", metadata_default)))
        self._spin_metadata_loader_workers.setToolTip("文件列表读取 EXIF/XMP 元数据的并行线程数，默认约为 CPU 逻辑核心数的 1/4，最多 8。")
        grid.addWidget(self._spin_metadata_loader_workers, row, 1)
        grid.addWidget(QLabel(f"默认 {metadata_default}"), row, 2)

        row += 1
        grid.addWidget(QLabel("元数据内存缓存"), row, 0)
        self._spin_metadata_memory = QSpinBox(self)
        self._spin_metadata_memory.setRange(METADATA_MEMORY_MIN_MB, METADATA_MEMORY_MAX_MB)
        self._spin_metadata_memory.setSuffix(" MiB")
        self._spin_metadata_memory.setSingleStep(64)
        self._spin_metadata_memory.setValue(int(opts[KEY_METADATA_MEMORY_MB]))
        self._spin_metadata_memory.setAccessibleName("元数据内存缓存")
        self._spin_metadata_memory.setToolTip(
            "跨目录保留已读取元数据的估算内存上限，最多 20,000 条。\n"
            "确认后立即生效；缩小上限时优先淘汰当前目录范围外的缓存。"
        )
        grid.addWidget(self._spin_metadata_memory, row, 1)
        grid.addWidget(QLabel(f"默认 {METADATA_MEMORY_DEFAULT_MB} MiB"), row, 2)

        row += 1
        grid.addWidget(QLabel("小缩略图生成线程数"), row, 0)
        self._spin_persistent_thumb_workers = QSpinBox(self)
        self._spin_persistent_thumb_workers.setRange(1, max_workers)
        self._spin_persistent_thumb_workers.setValue(int(opts.get("persistent_thumb_workers", persistent_default)))
        self._spin_persistent_thumb_workers.setToolTip("后台持久化缩略图生成线程数，默认使用扣除元数据读取后的剩余 CPU 线程。")
        grid.addWidget(self._spin_persistent_thumb_workers, row, 1)
        grid.addWidget(QLabel(f"默认 {persistent_default}"), row, 2)

        row += 1
        grid.addWidget(QLabel("小缩略图最大尺寸"), row, 0)
        self._combo_persistent_thumb_size = QComboBox(self)
        for size in PERSISTENT_THUMB_SIZE_LEVELS:
            self._combo_persistent_thumb_size.addItem(f"{size} x {size}", size)
        current_size = int(opts.get("persistent_thumb_max_size", 128))
        current_index = PERSISTENT_THUMB_SIZE_LEVELS.index(current_size) if current_size in PERSISTENT_THUMB_SIZE_LEVELS else 0
        self._combo_persistent_thumb_size.setCurrentIndex(current_index)
        self._combo_persistent_thumb_size.setToolTip("会生成不高于该值的 128/256/512/1024/2048 预览层级。")
        grid.addWidget(self._combo_persistent_thumb_size, row, 1)
        grid.addWidget(QLabel("默认 128"), row, 2)

        grid = grid_group("浏览行为")
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
        self._combo_key_navigation_fps.setToolTip("按住方向键连续浏览时，由应用计时器按该目标 FPS 回放照片序列。")
        grid.addWidget(self._combo_key_navigation_fps, row, 1)
        grid.addWidget(QLabel("默认 24 FPS"), row, 2)

        row += 1
        grid.addWidget(QLabel("预览图更换时保持缩放/位置"), row, 0)
        self._chk_keep_view = QCheckBox(self)
        self._chk_keep_view.setChecked(bool(opts.get("keep_view_on_switch", 1)))
        self._chk_keep_view.setToolTip("预览图更换时保持当前缩放比例和视图中心（不自动复位为适窗）。")
        grid.addWidget(self._chk_keep_view, row, 1)
        grid.addWidget(QLabel("默认开启"), row, 2)

        row += 1
        from .rarity_badge import ColorButton
        self._bird_hover_color = ColorButton(opts[KEY_BIRD_HOVER_COLOR], lambda: None, self)
        self._bird_hover_color.setAccessibleName("鸟名悬停框颜色")
        self._bird_hover_color.setToolTip("悬停逐只识别的鸟名时，自动居中并绘制空心框；保持当前缩放比例。")
        grid.addWidget(QLabel("鸟名悬停框颜色"), row, 0)
        grid.addWidget(self._bird_hover_color, row, 1)
        grid.addWidget(QLabel("默认红色"), row, 2)

        grid = grid_group("诊断")
        row = 0
        grid.addWidget(QLabel("性能探针日志"), row, 0)
        self._chk_perf_probes = QCheckBox(self)
        self._chk_perf_probes.setChecked(bool(opts.get(KEY_PERF_PROBES_ENABLED, 0)))
        self._chk_perf_probes.setToolTip("开启后在日志中记录图片切换、过滤、标星、标签写入等关键路径耗时。")
        grid.addWidget(self._chk_perf_probes, row, 1)
        grid.addWidget(QLabel("默认关闭"), row, 2)

        tabs = self.tabs
        standard_icons = getattr(QStyle, "StandardPixmap", QStyle)
        general = QWidget(tabs)
        general_layout = QVBoxLayout(general)
        for group in self.option_groups.values():
            general_layout.addWidget(group)
        self.add_page(general, "浏览与性能", self.style().standardIcon(standard_icons.SP_ComputerIcon))

        note = QLabel("缩略视图会根据当前缩略图大小自动匹配最合适的一档预览图。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #aaa; font-size: 12px;")
        self.option_groups["加载与缓存"].body.layout().addWidget(note, 5, 0, 1, 3)

        denoise = QWidget(tabs)
        denoise_layout = QVBoxLayout(denoise)
        denoise_grid = grid_group("输出文件")
        denoise_grid.setVerticalSpacing(10)
        self._combo_denoise_mode = QComboBox(denoise)
        from app_common.image_output_paths import OUTPUT_MODES
        for value, label in OUTPUT_MODES:
            self._combo_denoise_mode.addItem(label, value)
        self._combo_denoise_mode.setCurrentIndex(self._combo_denoise_mode.findData(opts["denoise_output_mode"]))
        denoise_grid.addWidget(QLabel("输出位置"), 0, 0)
        denoise_grid.addWidget(self._combo_denoise_mode, 0, 1, 1, 2)
        self._edit_denoise_subdir = QLineEdit(str(opts["denoise_subdir"]), denoise)
        denoise_grid.addWidget(QLabel("子目录名称"), 1, 0)
        denoise_grid.addWidget(self._edit_denoise_subdir, 1, 1, 1, 2)
        self._edit_denoise_directory = QLineEdit(str(opts["denoise_output_directory"]), denoise)
        self._button_denoise_directory = QPushButton("浏览…", denoise)
        self._button_denoise_directory.clicked.connect(self._choose_denoise_directory)
        denoise_grid.addWidget(QLabel("固定目录"), 2, 0)
        denoise_grid.addWidget(self._edit_denoise_directory, 2, 1)
        denoise_grid.addWidget(self._button_denoise_directory, 2, 2)
        self._combo_denoise_format = QComboBox(denoise)
        self._combo_denoise_format.addItem("TIFF（16 位，无损）", "tiff")
        self._combo_denoise_format.addItem("JPEG（质量 95）", "jpeg")
        self._combo_denoise_format.setCurrentIndex(self._combo_denoise_format.findData(opts["denoise_format"]))
        denoise_grid.addWidget(QLabel("输出格式"), 3, 0)
        denoise_grid.addWidget(self._combo_denoise_format, 3, 1, 1, 2)
        denoise_layout.addWidget(self.option_groups["输出文件"])
        denoise_grid = grid_group("降噪处理")
        self._spin_denoise_strength = QSpinBox(denoise)
        self._spin_denoise_strength.setRange(0, 100)
        self._spin_denoise_strength.setSuffix(" %")
        self._spin_denoise_strength.setValue(int(opts["denoise_strength"]))
        self._spin_denoise_strength.setToolTip("100% 使用完整降噪结果；降低强度会与原图混合，保留更多纹理。")
        denoise_grid.addWidget(QLabel("降噪强度"), 4, 0)
        denoise_grid.addWidget(self._spin_denoise_strength, 4, 1)
        self._combo_denoise_device = QComboBox(denoise)
        for label, value in (("自动（CUDA → MPS → CPU）", "auto"), ("CPU", "cpu")):
            self._combo_denoise_device.addItem(label, value)
        self._combo_denoise_device.setCurrentIndex(max(0, self._combo_denoise_device.findData(opts["denoise_device"])))
        denoise_grid.addWidget(QLabel("计算设备"), 5, 0)
        denoise_grid.addWidget(self._combo_denoise_device, 5, 1, 1, 2)
        self._spin_denoise_workers = QSpinBox(denoise)
        self._spin_denoise_workers.setRange(1, 4)
        self._spin_denoise_workers.setValue(int(opts["denoise_workers"]))
        self._spin_denoise_workers.setToolTip("多张照片并行解码和保存；模型逐块推理。实际并行数受共享线程池与可用内存限制。")
        denoise_grid.addWidget(QLabel("最多并行照片数"), 6, 0)
        denoise_grid.addWidget(self._spin_denoise_workers, 6, 1)
        denoise_layout.addWidget(self.option_groups["降噪处理"])
        denoise_note = QLabel("NAFNet RGB 降噪。原图保持不变，重名成片会自动编号。\n"
                             "RAW 将先渲染为 sRGB 再降噪。\n"
                             "在照片或目录右键菜单中开始降噪；新的设置用于下一批任务。", denoise)
        denoise_note.setWordWrap(True)
        denoise_layout.addWidget(denoise_note)
        self.add_page(denoise, "批量降噪", menu_icon("denoise"))

        from app_common.image_output_form import ImageOutputOptionsForm
        photo_edit = QWidget(tabs)
        photo_edit_layout = QVBoxLayout(photo_edit)
        self._photo_edit_output = ImageOutputOptionsForm(opts, photo_edit)
        self._add_option_group(photo_edit_layout, "编辑成片", self._photo_edit_output)
        cache_form = QWidget(photo_edit)
        cache_grid = QGridLayout(cache_form)
        cache_grid.setContentsMargins(0, 0, 0, 0)
        self._photo_edit_memory = QSpinBox(cache_form)
        self._photo_edit_memory.setRange(64, 4096)
        self._photo_edit_memory.setSuffix(" MiB")
        self._photo_edit_memory.setValue(opts["photo_edit_memory_mb"])
        self._photo_edit_disk = QSpinBox(cache_form)
        self._photo_edit_disk.setRange(0, 16384)
        self._photo_edit_disk.setSuffix(" MiB")
        self._photo_edit_disk.setValue(opts["photo_edit_disk_mb"])
        cache_grid.addWidget(QLabel("内存缓存"), 0, 0)
        cache_grid.addWidget(self._photo_edit_memory, 0, 1)
        cache_grid.addWidget(QLabel("磁盘预览缓存"), 1, 0)
        cache_grid.addWidget(self._photo_edit_disk, 1, 1)
        self._add_option_group(photo_edit_layout, "编辑缓存", cache_form)
        edit_note = QLabel("调整参数自动保存到原照片 XMP，可在编辑会话中撤销。\n"
                           "导出单独生成成片和 XMP，重名自动编号。磁盘缓存可重建，0 表示关闭；"
                           "缓存设置重启后生效。", photo_edit)
        edit_note.setWordWrap(True)
        photo_edit_layout.addWidget(edit_note)
        self.add_page(photo_edit, "照片编辑", menu_icon("process"))

        from .video_frame_options_form import VideoFrameOptionsForm
        video = QWidget(tabs)
        video_layout = QVBoxLayout(video)
        self._video_frame_form = VideoFrameOptionsForm(opts, video)
        self._add_option_group(video_layout, "视频帧提取", self._video_frame_form)
        self.add_page(video, "视频处理", menu_icon("process"))

        self._initial_archive_options = load_archive_options()
        self._archive_form = ArchiveOptionsForm(tabs, options=self._initial_archive_options)
        archive_page = QWidget(tabs)
        archive_layout = QVBoxLayout(archive_page)
        self._add_option_group(archive_layout, "归档规则", self._archive_form)
        self.add_page(archive_page, "珍禽入册", archive_icon())

        from .rarity_badge import RarityBadgesForm, ConservationBadgesForm
        self._rarity_badges_form = RarityBadgesForm(opts, tabs)
        self._conservation_badges_form = ConservationBadgesForm(opts, tabs)
        for form, label, icon in ((self._rarity_badges_form, "稀有度徽章", "star"),
                                 (self._conservation_badges_form, "保护等级徽章", "shield")):
            page = QWidget(tabs)
            page_layout = QVBoxLayout(page)
            self._add_option_group(page_layout, label, form)
            self.add_page(page, label, menu_icon(icon))

        sharpness = QWidget(tabs)
        sharpness_layout = QVBoxLayout(sharpness)
        parallel_grid = grid_group("并行处理")
        parallel_grid.setColumnStretch(2, 1)
        parallel_grid.addWidget(QLabel("同时检测照片数"), 0, 0)
        self._spin_bird_sharpness_workers = QSpinBox(sharpness)
        self._spin_bird_sharpness_workers.setRange(0, BIRD_SHARPNESS_WORKERS_MAX)
        automatic_workers = min(6, max(1, cpu_count // 2))
        self._spin_bird_sharpness_workers.setSpecialValueText(f"自动（{automatic_workers} 张）")
        self._spin_bird_sharpness_workers.setSuffix(" 张")
        self._spin_bird_sharpness_workers.setValue(int(opts[KEY_BIRD_SHARPNESS_WORKERS]))
        self._spin_bird_sharpness_workers.setAccessibleName("同时检测照片数")
        self._spin_bird_sharpness_workers.setToolTip(
            "0 为自动：CPU 逻辑核心数的一半，最多 6 张。\n"
            f"可手动设置 1–{BIRD_SHARPNESS_WORKERS_MAX} 张；保存后在下一次检测生效。\n"
            "若设置了 SuperViewer_ANALYSIS_WORKERS 环境变量，优先使用该值。"
        )
        parallel_grid.addWidget(self._spin_bird_sharpness_workers, 0, 1)
        parallel_note = QLabel(
            "保存后在下一次检测生效。\n"
            "提高并行数会占用更多内存。\n"
            "速度也取决于解码、识别和读写。\n"
            "不会改变清晰度分数或已检测结果。", sharpness)
        parallel_note.setWordWrap(True)
        parallel_note.setForegroundRole(getattr(QPalette, "ColorRole", QPalette).PlaceholderText)
        parallel_grid.addWidget(parallel_note, 1, 0, 1, 2)
        try:
            override = int(os.environ.get("SuperViewer_ANALYSIS_WORKERS", "0"))
        except ValueError:
            override = 0
        if override > 0:
            override_note = QLabel(
                f"当前环境变量覆盖此选项：同时检测 {min(override, BIRD_SHARPNESS_WORKERS_MAX)} 张。", sharpness)
            override_note.setWordWrap(True)
            parallel_grid.addWidget(override_note, 2, 0, 1, 2)
        parallel_group = self.option_groups["并行处理"]
        policy = getattr(QSizePolicy, "Policy", QSizePolicy)
        parallel_group.setSizePolicy(policy.Maximum, policy.Preferred)
        sharpness_layout.addWidget(parallel_group)
        from .bird_sharpness_params_form import AnalysisParamsForm

        # The same form as the trace window's 参数 tab: one pipeline, parameters from here for batch runs.
        self._bird_sharpness_form = AnalysisParamsForm(sharpness, expand_fields=False)
        self._bird_sharpness_form.set_params({name: opts[key] for name, key in BIRD_SHARPNESS_PARAM_KEYS.items()})
        self._spin_bird_sharpness_max_birds = self._bird_sharpness_form.max_birds
        self._combo_bird_sharpness_estimator = self._bird_sharpness_form.estimator
        self._bird_sharpness_tiles = self._bird_sharpness_form.tiles
        self._add_option_group(sharpness_layout, "清晰度参数", self._bird_sharpness_form)
        sharpness_note = QLabel("图像来源：默认测 RAW 里相机内嵌的全尺寸 JPEG（快）；选「RAW 解码」最准，门槛按它标定；"
                                "「降噪成片」只测已降噪的照片。\n"
                                "默认测量照片中的全部鸟，取最清晰的一只作为整张照片的清晰度。"
                                "设了上限时，压在相机焦点框上的鸟优先测量，其余按识别置信度 × 鸟框面积排序。\n"
                                "边缘统计方式：「标准」是门槛标定所用的方式；「密集」让小鸟的结果更稳，但仍属实验性。\n"
                                "识别模型：可换用其他 YOLO 检测模型，或用 SAM 重新抠出被枝叶挡住的鸟；未下载的模型可在此下载。\n"
                                "增强找鸟：没找到鸟时放大窗口再找，放大后的树叶也会被认成鸟，建议先在计算过程窗口里试门槛。\n"
                                "无鸟时的分块：没有鸟、没有焦点框时测全图；相机记录为手动对焦时测画面中心的焦平面（不是鸟）。\n"
                                "所有非默认的清晰度参数（以及 RAW 以外的图像来源）都以单独的算法版本记录，「跳过已检测」不会把不同设置的结果混用。"
                                "新的设置用于下一次检测和计算过程查看；计算过程窗口的「参数」页可临时改用其他参数对比。", sharpness)
        sharpness_note.setWordWrap(True)
        sharpness_note.setForegroundRole(getattr(QPalette, "ColorRole", QPalette).PlaceholderText)
        self._add_option_group(sharpness_layout, "参数说明", sharpness_note, expanded=False)
        self.add_page(sharpness, "鸟清晰度", menu_icon("process"))
        self._combo_denoise_mode.currentIndexChanged.connect(self._update_denoise_mode)
        self._update_denoise_mode()

    def _add_option_group(self, layout, title, content, *, expanded=True):
        group = CollapsibleSection(title, expanded=expanded)
        group.set_content_widget(content)
        self.option_groups[title] = group
        layout.addWidget(group)
        return group

    def _choose_denoise_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择降噪输出目录", self._edit_denoise_directory.text())
        if path:
            self._edit_denoise_directory.setText(path)

    def _update_denoise_mode(self, *_args) -> None:
        mode = self._combo_denoise_mode.currentData()
        self._edit_denoise_subdir.setEnabled(mode in {"source_subdir", "sibling_dir"})
        self._edit_denoise_directory.setEnabled(mode == "fixed")
        self._button_denoise_directory.setEnabled(mode == "fixed")

    def accept(self) -> None:
        error = self._photo_edit_output.validation_error()
        if error:
            QMessageBox.information(self, "照片编辑", error)
            return
        error = self._video_frame_form.validation_error()
        if error:
            QMessageBox.information(self, "视频处理", error)
            return
        if not valid_denoise_subdir(self._edit_denoise_subdir.text().strip()):
            QMessageBox.information(self, "批量降噪", "请输入兼容 Windows 的单个子目录名称，不能包含斜线或保留字符。")
            return
        if self._combo_denoise_mode.currentData() == "fixed" and not self._edit_denoise_directory.text().strip():
            QMessageBox.information(self, "批量降噪", "请选择固定输出目录。")
            return
        super().accept()

    def save_archive_options(self) -> None:
        """仅在确认且归档设置有修改时写入原有配置文件。"""
        options = self._archive_form.selected_options()
        if options != self._initial_archive_options:
            save_archive_options(options)
            self._initial_archive_options = options

    def selected_options(self) -> dict[str, int | str]:
        return {
            "thumbnail_loader_workers": int(self._spin_thumb_loader_workers.value()),
            "metadata_loader_workers": int(self._spin_metadata_loader_workers.value()),
            KEY_METADATA_MEMORY_MB: self._spin_metadata_memory.value(),
            "persistent_thumb_workers": int(self._spin_persistent_thumb_workers.value()),
            "persistent_thumb_max_size": int(self._combo_persistent_thumb_size.currentData()),
            "key_navigation_fps": int(self._combo_key_navigation_fps.currentData()),
            "keep_view_on_switch": int(self._chk_keep_view.isChecked()),
            KEY_PERF_PROBES_ENABLED: int(self._chk_perf_probes.isChecked()),
            KEY_BIRD_HOVER_COLOR: self._bird_hover_color.color,
            KEY_BIRD_SHARPNESS_WORKERS: self._spin_bird_sharpness_workers.value(),
            "denoise_output_mode": str(self._combo_denoise_mode.currentData()),
            "denoise_subdir": self._edit_denoise_subdir.text().strip(),
            "denoise_output_directory": self._edit_denoise_directory.text().strip(),
            "denoise_format": str(self._combo_denoise_format.currentData()),
            "denoise_strength": self._spin_denoise_strength.value(),
            "denoise_device": str(self._combo_denoise_device.currentData()),
            "denoise_workers": self._spin_denoise_workers.value(),
            **self._photo_edit_output.selected_options(),
            "photo_edit_memory_mb": self._photo_edit_memory.value(),
            "photo_edit_disk_mb": self._photo_edit_disk.value(),
            **self._video_frame_form.selected_options(),
            **self._rarity_badges_form.selected_options(),
            **self._conservation_badges_form.selected_options(),
            **bird_sharpness_params_to_options(self._bird_sharpness_form.params()),
        }
