# -*- coding: utf-8 -*-
"""Custom tag image information tab for SuperViewer."""
from __future__ import annotations

import os
import time as _time
from pathlib import Path
from typing import Callable

from app_common.log import get_logger

from .image_info_tab_base import ImageInfoTabPanel
from .qt_compat import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


_log = get_logger("superviewer.image_info_tab_tags")


class ImageInfoTabPanel_Tags(ImageInfoTabPanel):
    """Custom photo tags tab backed by the shared file-list tag store."""

    tab_title = "标签管理"

    def __init__(
        self,
        available_tags_provider: Callable[[], list[str]],
        tags_for_path_provider: Callable[[str], set[str]],
        set_tag_callback: Callable[[list[str], str, bool], None],
        clear_tags_callback: Callable[[list[str]], None],
        parent=None,
    ) -> None:
        self._available_tags_provider = available_tags_provider
        self._tags_for_path_provider = tags_for_path_provider
        self._set_tag_callback = set_tag_callback
        self._clear_tags_callback = clear_tags_callback
        self._tag_checks: dict[str, QCheckBox] = {}
        self._available_tags: list[str] = []
        self._updating = False
        super().__init__(parent)

    def create_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self.photo_label = QLabel("未选择图片")
        self.photo_label.setStyleSheet("color: #aaa; font-size: 12px;")
        self.photo_label.setWordWrap(True)
        top_row.addWidget(self.photo_label, stretch=1)

        self.btn_clear = QPushButton("清除当前照片所有标签")
        self.btn_clear.clicked.connect(self._clear_current_photo_tags)
        top_row.addWidget(self.btn_clear)
        layout.addLayout(top_row)

        self.empty_label = QLabel("")
        self.empty_label.setStyleSheet("color: #888; font-size: 12px; padding: 4px;")
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

    def refresh_ui(self) -> set[str]:
        t0 = _time.perf_counter()
        path = self.current_photo_path()
        available_t0 = _time.perf_counter()
        available = self._load_available_tags()
        available_ms = (_time.perf_counter() - available_t0) * 1000.0
        rebuild_ms = 0.0
        if available != self._available_tags:
            self._available_tags = available
            rebuild_t0 = _time.perf_counter()
            self._rebuild_tag_checkboxes()
            rebuild_ms = (_time.perf_counter() - rebuild_t0) * 1000.0

        has_file = bool(path and os.path.isfile(path))
        current_tags_ms = 0.0
        if has_file:
            self.photo_label.setText(Path(path).name)
            self.photo_label.setToolTip(path)
            current_tags_t0 = _time.perf_counter()
            current_tags = self._load_current_tags(path)
            current_tags_ms = (_time.perf_counter() - current_tags_t0) * 1000.0
        else:
            self.photo_label.setText("未选择图片")
            self.photo_label.setToolTip("")
            current_tags = set()

        checkbox_t0 = _time.perf_counter()
        self._updating = True
        try:
            for tag, check in self._tag_checks.items():
                check.setEnabled(has_file)
                check.setChecked(tag in current_tags)
            self.btn_clear.setEnabled(has_file and bool(current_tags))
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
        _log.info(
            "[PERF][image_switch][ImageInfoTabPanel_Tags.refresh_ui] path=%r has_file=%s available=%s checked=%s available_ms=%.1f rebuild_ms=%.1f current_tags_ms=%.1f checkbox_ms=%.1f empty_ms=%.1f total_ms=%.1f",
            path,
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
            check.toggled.connect(lambda checked=False, t=tag: self._on_tag_toggled(t, bool(checked)))
            self._tag_checks[tag] = check
            self.tag_layout.addWidget(check)
        self.tag_layout.addStretch(1)
        _log.info(
            "[PERF][image_switch][ImageInfoTabPanel_Tags._rebuild_tag_checkboxes] available=%s total_ms=%.1f",
            len(self._available_tags),
            (_time.perf_counter() - t0) * 1000.0,
        )

    def _on_tag_toggled(self, tag: str, checked: bool) -> None:
        if self._updating:
            return
        path = self.current_photo_path()
        if not path or not os.path.isfile(path):
            return
        try:
            self._set_tag_callback([path], tag, checked)
        except Exception as exc:
            QMessageBox.warning(self, "TAG", f"保存标签失败：\n{exc}")
        self.refresh_current_photo()

    def _clear_current_photo_tags(self) -> None:
        path = self.current_photo_path()
        if not path or not os.path.isfile(path):
            return
        try:
            self._clear_tags_callback([path])
        except Exception as exc:
            QMessageBox.warning(self, "TAG", f"清除标签失败：\n{exc}")
        self.refresh_current_photo()


__all__ = [
    "ImageInfoTabPanel_Tags",
]
