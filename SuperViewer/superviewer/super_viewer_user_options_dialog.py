# -*- coding: utf-8 -*-
"""用户选项对话框：缩略图线程数、小缩略图尺寸、方向键速率、保持视图等。"""

from __future__ import annotations

import os

from app_common.superviewer_user_options import (
    KEY_NAVIGATION_FPS_OPTIONS,
    PERSISTENT_THUMB_SIZE_LEVELS,
    USER_OPTIONS_FILENAME,
    get_runtime_user_options,
    get_user_options_path,
)

from .qt_compat import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)


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
