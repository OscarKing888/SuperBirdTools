# -*- coding: utf-8 -*-
"""Image summary information tab for SuperViewer."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .image_info_tab_base import ImageInfoTabPanel
from .qt_compat import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPixmap,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    _AlignCenter,
    _KeepAspectRatio,
    _SmoothTransformation,
)
from .tag_menu import add_filterable_tag_actions


_PREVIEW_HEIGHT = 180


def _exec_menu(menu: QMenu, pos):
    if hasattr(menu, "exec"):
        return menu.exec(pos)
    return menu.exec_(pos)  # type: ignore[attr-defined]


def _format_file_size(size_bytes: int | float | None) -> str:
    try:
        size = float(size_bytes)
    except (TypeError, ValueError):
        return "-"
    if size < 0:
        return "-"
    units = ("B", "KB", "MB", "GB", "TB")
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.2f} {units[idx]}"


def _format_timestamp(ts: float | int | None) -> str:
    if ts is None:
        return "-"
    try:
        from datetime import datetime

        return datetime.fromtimestamp(float(ts)).strftime("%Y/%m/%d %H:%M")
    except Exception:
        return "-"


def _rating_text(value) -> str:
    try:
        rating = int(value or 0)
    except Exception:
        rating = 0
    rating = max(0, min(5, rating))
    return "★" * rating + "☆" * (5 - rating)


def _metadata_text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("x-default", "default", "en", "zh"):
            text = _metadata_text_value(value.get(key))
            if text:
                return text
        for item in value.values():
            text = _metadata_text_value(item)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = _metadata_text_value(item)
            if text:
                return text
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00").strip()
    return str(value).strip("\x00").strip()


def _metadata_comment(metadata: dict) -> str:
    for key in (
        "Description",
        "XMP-dc:Description",
        "XMP-dc:description",
        "XMP:Description",
        "IFD0:XPComment",
        "IFD0:ImageDescription",
        "EXIF:UserComment",
        "ExifIFD:UserComment",
        "Comment",
    ):
        text = _metadata_text_value(metadata.get(key))
        if text:
            return text
    return ""


class ImageInfoTabPanel_ImageInfo(ImageInfoTabPanel):
    """Image preview, filename, tags, and basic file information tab."""

    tab_title = "图片信息"

    def __init__(
        self,
        available_tags_provider: Callable[[], list[str]],
        tags_for_path_provider: Callable[[str], set[str]],
        set_tag_callback: Callable[[list[str], str, bool], None],
        rename_callback: Callable[[str, str], str],
        metadata_provider: Callable[[str], dict] | None = None,
        comment_save_callback: Callable[[str, str], bool] | None = None,
        parent=None,
    ) -> None:
        self._available_tags_provider = available_tags_provider
        self._tags_for_path_provider = tags_for_path_provider
        self._set_tag_callback = set_tag_callback
        self._rename_callback = rename_callback
        self._metadata_provider = metadata_provider
        self._comment_save_callback = comment_save_callback
        self._preview_pixmap: QPixmap | None = None
        self._current_tags: set[str] = set()
        self._current_comment = ""
        self._updating_comment = False
        self._updating_name = False
        super().__init__(parent)

    def create_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame if hasattr(QFrame, "Shape") else QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        self.preview_label = QLabel("未选择图片")
        self.preview_label.setAlignment(_AlignCenter)
        self.preview_label.setFixedHeight(_PREVIEW_HEIGHT)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding if hasattr(QSizePolicy, "Policy") else QSizePolicy.Expanding, QSizePolicy.Policy.Fixed if hasattr(QSizePolicy, "Policy") else QSizePolicy.Fixed)
        self.preview_label.setStyleSheet(
            "QLabel { background: #202124; border: 1px solid #36383d; "
            "border-radius: 8px; color: #888; }"
        )
        layout.addWidget(self.preview_label)

        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("添加注释")
        self.comment_edit.setStyleSheet(
            "QLineEdit { padding: 8px 10px; font-size: 14px; "
            "border: 1px solid #303238; border-radius: 7px; }"
        )
        self.comment_edit.editingFinished.connect(self._commit_comment_edit)
        layout.addWidget(self.comment_edit)

        self.filename_edit = QLineEdit()
        self.filename_edit.setPlaceholderText("文件名")
        self.filename_edit.setStyleSheet(
            "QLineEdit { padding: 8px 10px; font-size: 14px; "
            "border: 1px solid #303238; border-radius: 7px; }"
        )
        self.filename_edit.editingFinished.connect(self._commit_filename_edit)
        layout.addWidget(self.filename_edit)

        self._add_separator(layout)
        layout.addWidget(self._section_title("标签"))
        self.tags_container = QWidget(content)
        self.tags_layout = QHBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        layout.addWidget(self.tags_container)

        self._add_separator(layout)
        layout.addWidget(self._section_title("基本信息"))
        self.basic_rows: dict[str, QLabel] = {}
        for label in ("文件夹", "评分", "尺寸", "文件大小", "格式", "添加日期", "创建日期", "修改日期"):
            self._add_basic_row(layout, label)

        layout.addStretch(1)
        scroll.setWidget(content)

    def refresh_ui(self) -> dict[str, str]:
        path = self.current_photo_path()
        has_file = bool(path and os.path.isfile(path))
        metadata = self._load_metadata(path) if has_file else {}
        comment = _metadata_comment(metadata)
        self.comment_edit.setEnabled(has_file)
        self.filename_edit.setEnabled(has_file)

        self._updating_comment = True
        try:
            self._current_comment = comment
            self.comment_edit.setText(comment)
            self.comment_edit.setToolTip(comment)
        finally:
            self._updating_comment = False

        self._updating_name = True
        try:
            self.filename_edit.setText(Path(path).stem if has_file else "")
            self.filename_edit.setToolTip(path if has_file else "")
        finally:
            self._updating_name = False

        self._load_preview(path if has_file else "")
        self._current_tags = self._load_current_tags(path) if has_file else set()
        self._rebuild_tag_chips()
        info = self._load_basic_info(path if has_file else "", metadata=metadata)
        self._set_basic_info(info)
        return info

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_preview_pixmap()

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #b8b8b8; font-size: 13px; font-weight: 600;")
        return label

    def _add_separator(self, layout: QVBoxLayout) -> None:
        line = QFrame()
        if hasattr(QFrame, "Shape"):
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Plain)
        else:
            line.setFrameShape(QFrame.HLine)
            line.setFrameShadow(QFrame.Plain)
        line.setStyleSheet("color: #303238;")
        layout.addWidget(line)

    def _add_basic_row(self, layout: QVBoxLayout, label_text: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(label_text)
        label.setFixedWidth(64)
        label.setStyleSheet("color: #d7d7d7; font-size: 13px;")
        value = QLabel("-")
        value.setWordWrap(True)
        value.setStyleSheet("color: #cfcfcf; font-size: 13px;")
        row.addWidget(label)
        row.addWidget(value, stretch=1)
        layout.addLayout(row)
        self.basic_rows[label_text] = value

    def _load_preview(self, path: str) -> None:
        self._preview_pixmap = None
        if path:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self._preview_pixmap = pixmap
        self._update_preview_pixmap()

    def _update_preview_pixmap(self) -> None:
        if self._preview_pixmap is None or self._preview_pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("未选择图片" if not self.current_photo_path() else "无法预览")
            return
        target_w = max(32, self.preview_label.width() - 2)
        target_h = max(32, self.preview_label.height() - 2)
        scaled = self._preview_pixmap.scaled(
            target_w,
            target_h,
            _KeepAspectRatio,
            _SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled)

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

    def _clear_tag_layout(self) -> None:
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_tag_chips(self) -> None:
        self._clear_tag_layout()
        path = self.current_photo_path()
        has_file = bool(path and os.path.isfile(path))
        tags = sorted(self._current_tags)
        if not tags:
            btn = self._make_add_tag_button("＋ 添加标签")
            btn.setEnabled(has_file)
            self.tags_layout.addWidget(btn, stretch=1)
            return

        for tag in tags:
            chip = self._make_tag_chip(tag)
            self.tags_layout.addWidget(chip)
        btn_add = self._make_add_tag_button("＋")
        btn_add.setFixedWidth(32)
        btn_add.setEnabled(has_file)
        self.tags_layout.addWidget(btn_add)
        self.tags_layout.addStretch(1)

    def _make_tag_chip(self, tag: str) -> QWidget:
        chip = QFrame()
        chip.setStyleSheet(
            "QFrame { border: 1px solid #4a4c52; border-radius: 7px; "
            "background: #2b2d31; }"
        )
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(9, 4, 5, 4)
        layout.setSpacing(5)
        label = QLabel(tag)
        label.setStyleSheet("color: #f0f0f0; font-size: 13px; border: none; background: transparent;")
        btn = QToolButton(chip)
        btn.setText("×")
        btn.setToolTip(f"删除标签「{tag}」")
        btn.setAutoRaise(True)
        btn.setStyleSheet("QToolButton { color: #aaa; border: none; font-size: 14px; }")
        btn.clicked.connect(lambda checked=False, t=tag: self._set_current_tag(t, False))
        layout.addWidget(label)
        layout.addWidget(btn)
        return chip

    def _make_add_tag_button(self, text: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(text)
        btn.setToolTip("添加标签")
        btn.setAutoRaise(False)
        btn.setStyleSheet(
            "QToolButton { padding: 5px 10px; border: 1px solid #34363b; "
            "border-radius: 7px; background: #2a2c30; color: #e6e6e6; font-size: 13px; }"
            "QToolButton:hover { background: #34373d; }"
        )
        btn.clicked.connect(lambda checked=False, b=btn: self._show_add_tag_menu(b))
        return btn

    def _show_add_tag_menu(self, button: QToolButton) -> None:
        path = self.current_photo_path()
        if not path or not os.path.isfile(path):
            return
        available = self._load_available_tags()
        addable = [tag for tag in available if tag not in self._current_tags]
        menu = QMenu(self)
        if not available:
            act = menu.addAction("tags.cfg 未配置")
            act.setEnabled(False)
        elif not addable:
            act = menu.addAction("没有可添加的标签")
            act.setEnabled(False)
        else:
            add_filterable_tag_actions(
                menu,
                addable,
                lambda tag, checked=False: self._set_current_tag(tag, True),
            )
        _exec_menu(menu, button.mapToGlobal(button.rect().bottomLeft()))

    def _set_current_tag(self, tag: str, enabled: bool) -> None:
        path = self.current_photo_path()
        if not path or not os.path.isfile(path):
            return
        try:
            self._set_tag_callback([path], tag, enabled)
        except Exception as exc:
            QMessageBox.warning(self, "TAG", f"保存标签失败：\n{exc}")
            return
        self.refresh_current_photo()

    def _commit_comment_edit(self) -> None:
        if self._updating_comment:
            return
        path = self.current_photo_path()
        if not path or not os.path.isfile(path):
            return
        comment = self.comment_edit.text().strip()
        if comment == self._current_comment:
            return
        if self._comment_save_callback is None:
            QMessageBox.warning(self, "注释", "当前没有可用的注释保存器。")
            self.refresh_current_photo()
            return
        try:
            saved = bool(self._comment_save_callback(path, comment))
        except Exception as exc:
            QMessageBox.warning(self, "注释保存失败", str(exc))
            self.refresh_current_photo()
            return
        if not saved:
            QMessageBox.warning(self, "注释保存失败", "无法写入 EXIF 或 sidecar。")
            self.refresh_current_photo()
            return
        self._current_comment = comment
        self.refresh_current_photo()

    def _commit_filename_edit(self) -> None:
        if self._updating_name:
            return
        path = self.current_photo_path()
        if not path or not os.path.isfile(path):
            return
        requested_name = self.filename_edit.text().strip()
        if not requested_name or requested_name == Path(path).stem:
            self.refresh_current_photo()
            return
        try:
            new_path = self._rename_callback(path, requested_name)
        except Exception as exc:
            QMessageBox.warning(self, "重命名失败", str(exc))
            self.refresh_current_photo()
            return
        if new_path:
            self.on_photo_selected(new_path)

    def _load_basic_info(self, path: str, *, metadata: dict | None = None) -> dict[str, str]:
        if not path or not os.path.isfile(path):
            return {
                "文件夹": "-",
                "评分": "☆☆☆☆☆",
                "尺寸": "-",
                "文件大小": "-",
                "格式": "-",
                "添加日期": "-",
                "创建日期": "-",
                "修改日期": "-",
            }

        p = Path(path)
        try:
            stat = p.stat()
        except OSError:
            stat = None
        metadata = metadata if isinstance(metadata, dict) else self._load_metadata(path)
        width, height = self._image_size(path)
        created_ts = getattr(stat, "st_birthtime", None) if stat is not None else None
        if created_ts is None and stat is not None:
            created_ts = stat.st_ctime

        return {
            "文件夹": str(p.parent),
            "评分": _rating_text(metadata.get("rating")),
            "尺寸": f"{width} × {height}" if width and height else "-",
            "文件大小": _format_file_size(stat.st_size if stat is not None else None),
            "格式": (p.suffix[1:] or "-").upper(),
            "添加日期": _format_timestamp(stat.st_ctime if stat is not None else None),
            "创建日期": _format_timestamp(created_ts),
            "修改日期": _format_timestamp(stat.st_mtime if stat is not None else None),
        }

    def _load_metadata(self, path: str) -> dict:
        if self._metadata_provider is None:
            return {}
        try:
            data = self._metadata_provider(path)
        except Exception:
            return {}
        return dict(data) if isinstance(data, dict) else {}

    def _image_size(self, path: str) -> tuple[int | None, int | None]:
        pixmap = self._preview_pixmap
        if pixmap is not None and not pixmap.isNull():
            return int(pixmap.width()), int(pixmap.height())
        try:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                return int(pixmap.width()), int(pixmap.height())
        except Exception:
            pass
        return None, None

    def _set_basic_info(self, info: dict[str, str]) -> None:
        for key, label in self.basic_rows.items():
            value = str(info.get(key) or "-")
            label.setText(value)
            label.setToolTip(value if key == "文件夹" else "")


__all__ = [
    "ImageInfoTabPanel_ImageInfo",
]
