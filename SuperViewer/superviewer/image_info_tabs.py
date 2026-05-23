# -*- coding: utf-8 -*-
"""Extensible right-side image information tabs for SuperViewer."""
from __future__ import annotations

import os
from abc import ABCMeta, abstractmethod
from pathlib import Path
from typing import Callable

from .exif_helpers import (
    load_tag_label_chinese_from_settings,
    save_tag_label_chinese_to_settings,
)
from .exif_tag_order_dialog import ExifTagOrderDialog
from .exif_table import ExifTable
from .qt_compat import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class _ImageInfoTabPanelMeta(type(QWidget), ABCMeta):
    """Qt QWidget + ABC compatible metaclass."""


class ImageInfoTabPanel(QWidget, metaclass=_ImageInfoTabPanelMeta):
    """Base class for right-side image information tab panels."""

    tab_title = "信息"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_photo_path = ""
        self.create_ui()

    def current_photo_path(self) -> str:
        return self._current_photo_path

    def on_photo_selected(self, path: str):
        self._current_photo_path = os.path.normpath(path) if path else ""
        return self.refresh_ui()

    def refresh_current_photo(self):
        return self.refresh_ui()

    @abstractmethod
    def create_ui(self) -> None:
        """Create child widgets and layout."""

    @abstractmethod
    def refresh_ui(self):
        """Refresh the panel for ``current_photo_path``."""


class ImageInfoTabWidget(QTabWidget):
    """Container that dispatches image-selection events to all info tabs."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._panels: list[ImageInfoTabPanel] = []

    def add_info_panel(self, panel: ImageInfoTabPanel) -> None:
        self._panels.append(panel)
        self.addTab(panel, panel.tab_title)

    def panels(self) -> list[ImageInfoTabPanel]:
        return list(self._panels)

    def on_photo_selected(self, path: str) -> dict[str, object]:
        results: dict[str, object] = {}
        for panel in self._panels:
            results[panel.__class__.__name__] = panel.on_photo_selected(path)
        return results


class ImageInfoTabPanel_EXIF(ImageInfoTabPanel):
    """EXIF metadata tab."""

    tab_title = "EXIF"

    def __init__(
        self,
        metadata_rows_loader: Callable[[str, bool], list[tuple]],
        save_callback: Callable[[str, object, str, object, object], None],
        parent=None,
    ) -> None:
        self._metadata_rows_loader = metadata_rows_loader
        self._save_callback = save_callback
        self._last_rows: list[tuple] = []
        super().__init__(parent)

    def create_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self.exif_filter = QLineEdit()
        self.exif_filter.setPlaceholderText("按分组、标签或值过滤…")
        self.exif_filter.setClearButtonEnabled(True)
        self.exif_filter.setStyleSheet("QLineEdit { padding: 6px; font-size: 13px; }")
        self.exif_filter.textChanged.connect(self._on_exif_filter_changed)
        top_row.addWidget(self.exif_filter, stretch=1)

        self.check_tag_chinese = QCheckBox("中文标签")
        self.check_tag_chinese.setChecked(load_tag_label_chinese_from_settings())
        self.check_tag_chinese.setToolTip("勾选显示汉字标签名，否则显示英文")
        self.check_tag_chinese.toggled.connect(self._on_tag_label_lang_toggled)
        top_row.addWidget(self.check_tag_chinese)

        self.btn_config_order = QPushButton("配置显示顺序")
        self.btn_config_order.setToolTip("设置优先显示的 EXIF 标签及顺序")
        self.btn_config_order.clicked.connect(self._open_tag_order_config)
        top_row.addWidget(self.btn_config_order)
        layout.addLayout(top_row)

        self.exif_table = ExifTable(self)
        self.exif_table.set_save_callback(self._save_callback)
        layout.addWidget(self.exif_table, stretch=1)

    def refresh_ui(self) -> list[tuple]:
        path = self.current_photo_path()
        if not path or not os.path.isfile(path):
            self._last_rows = []
            self.exif_table.set_exif([])
            return []
        rows = self._metadata_rows_loader(
            path,
            load_tag_label_chinese_from_settings(),
        )
        self._last_rows = list(rows or [])
        self.exif_table.set_exif(self._last_rows)
        return list(self._last_rows)

    def last_rows(self) -> list[tuple]:
        return list(self._last_rows)

    def _on_exif_filter_changed(self, text: str) -> None:
        self.exif_table.set_filter_text(text)

    def _on_tag_label_lang_toggled(self, checked: bool) -> None:
        save_tag_label_chinese_to_settings(checked)
        # 如果当前没有选中照片，则只更新后续加载的显示语言。
        if not self.current_photo_path():
            return
        self.refresh_current_photo()

    def _open_tag_order_config(self) -> None:
        use_chinese = load_tag_label_chinese_from_settings()
        dialog = ExifTagOrderDialog(self, use_chinese=use_chinese)
        if dialog.exec():
            self.refresh_current_photo()


class ImageInfoTabPanel_Tags(ImageInfoTabPanel):
    """Custom photo tags tab backed by the shared file-list tag store."""

    tab_title = "TAG"

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

        self.btn_clear = QPushButton("清除当前照片 TAG")
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
