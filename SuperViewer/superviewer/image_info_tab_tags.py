# -*- coding: utf-8 -*-
"""Custom tag image information tab for SuperViewer."""
from __future__ import annotations

import os
import time as _time
from collections import Counter
from pathlib import Path
from typing import Callable

from app_common.log import get_logger
from app_common.perf_probe import perf_log
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS

from .image_info_tab_base import ImageInfoTabPanel
from .ui_theme import PanelThemeColors, current_panel_colors
from .qt_compat import (
    QCheckBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTimer,
    Qt,
    QVBoxLayout,
    QWidget,
)


_log = get_logger("superviewer.image_info_tab_tags")
_CheckState = getattr(Qt, "CheckState", Qt)


def mark_write_action_disabled(target, tooltip: str = "") -> None:
    if target is not None and tooltip:
        try:
            target.setToolTip(tooltip)
        except Exception:
            pass


def superpicky_sidecar_write_disabled_tooltip(action: str = "写入操作") -> str:
    return ""


class ImageInfoTabPanel_Tags(ImageInfoTabPanel):
    """Custom photo tags tab backed by the shared file-list tag store."""

    tab_title = "标签管理"

    def __init__(
        self,
        available_tags_provider: Callable[[], list[str]],
        tags_for_path_provider: Callable[[str], set[str]],
        set_tag_callback: Callable[[list[str], str, bool], None],
        clear_tags_callback: Callable[[list[str]], None],
        write_enabled_provider: Callable[[], bool] | None = None,
        parent=None,
        *,
        edit_tags_callback: Callable[[], None] | None = None,
        selected_paths_provider: Callable[[], list[str]] | None = None,
        refresh_enabled_provider: Callable[[], bool] | None = None,
    ) -> None:
        self._available_tags_provider = available_tags_provider
        self._tags_for_path_provider = tags_for_path_provider
        self._set_tag_callback = set_tag_callback
        self._clear_tags_callback = clear_tags_callback
        self._write_enabled_provider = write_enabled_provider
        self._edit_tags_callback = edit_tags_callback
        self._selected_paths_provider = selected_paths_provider
        self._refresh_enabled_provider = refresh_enabled_provider
        self._shutdown_requested = False
        self._tag_checks: dict[str, QCheckBox] = {}
        self._available_tags: list[str] = []
        self._updating = False
        super().__init__(parent)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_if_enabled)

    def selected_photo_paths(self) -> list[str]:
        """Take a fresh selection snapshot; an empty selection never edits the preview."""
        if self._selected_paths_provider is None:
            path = self.current_photo_path()
            paths = [path] if path and os.path.isfile(path) else []
        else:
            paths = self._selected_paths_provider()
        unique = {}
        for path in paths:
            if path:
                normalized = os.path.normpath(path)
                if Path(normalized).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    unique.setdefault(os.path.normcase(normalized), normalized)
        return list(unique.values())

    def _refresh_enabled(self) -> bool:
        return not self._shutdown_requested and (
            self._refresh_enabled_provider is None or self._refresh_enabled_provider()
        )

    def schedule_refresh(self) -> None:
        # 合并模型重建/多选的一组信号；长按播放和隐藏页不读取标签。
        if self._refresh_enabled():
            self._refresh_timer.start(0)

    def _refresh_if_enabled(self) -> None:
        if self._refresh_enabled():
            self.refresh_current_photo()

    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        self._refresh_timer.stop()

    def closeEvent(self, event) -> None:
        self.request_shutdown()
        super().closeEvent(event)

    def _writes_allowed(self) -> bool:
        if self._write_enabled_provider is None:
            return True
        try:
            return bool(self._write_enabled_provider())
        except Exception:
            return False

    def _write_disabled_tooltip(self, action: str = "写入操作") -> str:
        return superpicky_sidecar_write_disabled_tooltip(action)

    def create_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.photo_label = QLabel("未选择图片")
        self.photo_label.setWordWrap(True)
        layout.addWidget(self.photo_label)

        self.selection_hint = QLabel("部分勾选表示仅部分图片有此标签；点击后为全部选中图片添加。")
        self.selection_hint.setWordWrap(True)
        self.selection_hint.hide()
        layout.addWidget(self.selection_hint)

        self.btn_clear = QPushButton("清除当前照片所有标签")
        self.btn_clear.clicked.connect(self._clear_current_photo_tags)
        layout.addWidget(self.btn_clear)

        self.btn_edit = QPushButton("编辑标签…")
        self.btn_edit.setEnabled(self._edit_tags_callback is not None)
        self.btn_edit.clicked.connect(self._edit_tag_library)
        layout.addWidget(self.btn_edit)

        self.empty_label = QLabel("")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.tag_container = QWidget(scroll)
        self.tag_layout = QVBoxLayout(self.tag_container)
        self.tag_layout.setContentsMargins(2, 2, 2, 2)
        self.tag_layout.setSpacing(4)
        self.tag_layout.addStretch(1)
        scroll.setWidget(self.tag_container)
        layout.addWidget(scroll, stretch=1)

    def apply_theme(self, colors: PanelThemeColors | None = None) -> None:
        theme = colors or current_panel_colors()
        self.photo_label.setStyleSheet(
            "color: %s; font-size: 12px;" % theme.secondary_text
        )
        self.empty_label.setStyleSheet(
            "color: %s; font-size: 12px; padding: 4px;" % theme.muted_text
        )
        self.selection_hint.setStyleSheet("color: %s;" % theme.secondary_text)

    def refresh_ui(self) -> set[str]:
        t0 = _time.perf_counter()
        paths = self.selected_photo_paths()
        available_t0 = _time.perf_counter()
        available = self._load_available_tags()
        available_ms = (_time.perf_counter() - available_t0) * 1000.0
        rebuild_ms = 0.0
        if available != self._available_tags:
            self._available_tags = available
            rebuild_t0 = _time.perf_counter()
            self._rebuild_tag_checkboxes()
            rebuild_ms = (_time.perf_counter() - rebuild_t0) * 1000.0

        has_file = bool(paths)
        multi = len(paths) > 1
        current_tags_ms = 0.0
        if has_file:
            self.photo_label.setText(f"已选择 {len(paths)} 张图片" if multi else Path(paths[0]).name)
            self.photo_label.setToolTip("\n".join(paths[:20]))
            current_tags_t0 = _time.perf_counter()
            tag_counts = Counter(tag for path in paths for tag in self._load_current_tags(path))
            current_tags = set(tag_counts)
            current_tags_ms = (_time.perf_counter() - current_tags_t0) * 1000.0
        else:
            self.photo_label.setText("未选择图片")
            self.photo_label.setToolTip("")
            current_tags = set()
            tag_counts = Counter()

        self.selection_hint.setVisible(multi)
        self.btn_clear.setText("清除所选图片所有标签" if multi else "清除当前照片所有标签")

        checkbox_t0 = _time.perf_counter()
        can_write = has_file and self._writes_allowed()
        disabled_tip = self._write_disabled_tooltip("编辑标签") if has_file and not can_write else ""
        self._updating = True
        try:
            for tag, check in self._tag_checks.items():
                check.setEnabled(can_write)
                count = tag_counts[tag]
                partial = 0 < count < len(paths)
                check.setToolTip(disabled_tip if has_file and not can_write else (
                    f"{count}/{len(paths)} 张图片有标签「{tag}」；点击可为所选图片统一添加或移除。"
                ))
                # 仅混合状态启用三态；空白直接勾选，混合直接全选，已全选直接清除。
                check.setTristate(partial)
                check.setCheckState(_CheckState.PartiallyChecked if partial else (
                    _CheckState.Checked if count else _CheckState.Unchecked
                ))
            self.btn_clear.setEnabled(can_write and bool(current_tags))
            self.btn_clear.setToolTip(disabled_tip if has_file and not can_write else "")
            if has_file and not can_write:
                mark_write_action_disabled(self.btn_clear, disabled_tip)
        finally:
            self._updating = False
        checkbox_ms = (_time.perf_counter() - checkbox_t0) * 1000.0

        empty_t0 = _time.perf_counter()
        if not available:
            self.empty_label.setText("tags.cfg 未配置")
            self.empty_label.show()
        elif not has_file:
            self.empty_label.setText("未选择图片")
            self.empty_label.show()
        else:
            self.empty_label.hide()
        empty_ms = (_time.perf_counter() - empty_t0) * 1000.0
        perf_log(
            _log,
            "[PERF][image_switch][ImageInfoTabPanel_Tags.refresh_ui] path=%r has_file=%s available=%s checked=%s available_ms=%.1f rebuild_ms=%.1f current_tags_ms=%.1f checkbox_ms=%.1f empty_ms=%.1f total_ms=%.1f",
            paths[0] if paths else "",
            has_file,
            len(available),
            len(current_tags),
            available_ms,
            rebuild_ms,
            current_tags_ms,
            checkbox_ms,
            empty_ms,
            (_time.perf_counter() - t0) * 1000.0,
        )
        return set(current_tags)

    def _load_available_tags(self) -> list[str]:
        try:
            return list(self._available_tags_provider())
        except Exception as exc:
            QMessageBox.warning(self, "TAG", f"读取标签配置失败：\n{exc}")
            return []

    def _edit_tag_library(self) -> None:
        if self._edit_tags_callback is not None:
            self._edit_tags_callback()
            self.refresh_current_photo()

    def _load_current_tags(self, path: str) -> set[str]:
        try:
            return set(self._tags_for_path_provider(path))
        except Exception as exc:
            QMessageBox.warning(self, "TAG", f"读取当前照片标签失败：\n{exc}")
            return set()

    def _rebuild_tag_checkboxes(self) -> None:
        t0 = _time.perf_counter()
        while self.tag_layout.count():
            item = self.tag_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tag_checks = {}
        for tag in self._available_tags:
            check = QCheckBox(tag)
            check.setToolTip(f"为当前照片设置 TAG「{tag}」")
            # 混合 → 全选的布尔值不变，clicked 仍覆盖鼠标和空格键操作。
            check.clicked.connect(lambda checked=False, t=tag: self._on_tag_toggled(t, bool(checked)))
            self._tag_checks[tag] = check
            self.tag_layout.addWidget(check)
        self.tag_layout.addStretch(1)
        perf_log(
            _log,
            "[PERF][image_switch][ImageInfoTabPanel_Tags._rebuild_tag_checkboxes] available=%s total_ms=%.1f",
            len(self._available_tags),
            (_time.perf_counter() - t0) * 1000.0,
        )

    def _on_tag_toggled(self, tag: str, checked: bool) -> None:
        if self._updating:
            return
        paths = self.selected_photo_paths()
        if not paths:
            self.refresh_current_photo()
            return
        if not self._writes_allowed():
            QMessageBox.warning(self, "写入失败", self._write_disabled_tooltip("保存标签"))
            self.refresh_current_photo()
            return
        try:
            self._set_tag_callback(paths, tag, checked)
        except Exception as exc:
            QMessageBox.warning(self, "TAG", f"保存标签失败：\n{exc}")
        self.refresh_current_photo()

    def _clear_current_photo_tags(self) -> None:
        paths = self.selected_photo_paths()
        if not paths:
            self.refresh_current_photo()
            return
        if not self._writes_allowed():
            QMessageBox.warning(self, "写入失败", self._write_disabled_tooltip("清除标签"))
            self.refresh_current_photo()
            return
        try:
            self._clear_tags_callback(paths)
        except Exception as exc:
            QMessageBox.warning(self, "TAG", f"清除标签失败：\n{exc}")
        self.refresh_current_photo()


__all__ = [
    "ImageInfoTabPanel_Tags",
]
