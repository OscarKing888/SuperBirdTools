# -*- coding: utf-8 -*-
"""Custom tag image information tab for SuperViewer."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

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
        path = self.current_photo_path()
        available = self._load_available_tags()
        if available != self._available_tags:
            self._available_tags = available
            self._rebuild_tag_checkboxes()

        has_file = bool(path and os.path.isfile(path))
        if has_file:
            self.photo_label.setText(Path(path).name)
            self.photo_label.setToolTip(path)
            current_tags = self._load_current_tags(path)
        else:
            self.photo_label.setText("未选择图片")
            self.photo_label.setToolTip("")
            current_tags = set()

        self._updating = True
        try:
            for tag, check in self._tag_checks.items():
                check.setEnabled(has_file)
                check.setChecked(tag in current_tags)
            self.btn_clear.setEnabled(has_file and bool(current_tags))
        finally:
            self._updating = False

        if not available:
            self.empty_label.setText("tags.cfg 未配置")
            self.empty_label.show()
        elif not has_file:
            self.empty_label.setText("未选择图片")
            self.empty_label.show()
        else:
            self.empty_label.hide()
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
