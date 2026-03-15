# -*- coding: utf-8 -*-
"""EXIF 信息表格：按文本过滤、双击编辑值列、右键复制。"""

from __future__ import annotations

from .exif_helpers import META_DESCRIPTION_TAG_ID, META_IFD_NAME, META_TITLE_TAG_ID
from .qt_compat import (
    QAction,
    QApplication,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    _DoubleClicked,
    _ItemIsEditable,
    _ResizeStretch,
    _SelectRows,
)


class ExifTable(QTableWidget):
    """EXIF 信息表格，支持按文本过滤与双击编辑值列。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["分组", "标签", "值"])
        self.horizontalHeader().setSectionResizeMode(2, _ResizeStretch)
        self.setColumnWidth(1, 220)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(_SelectRows)
        self.setEditTriggers(_DoubleClicked)
        self.setStyleSheet(
            "QTableWidget { font-family: 'SF Mono', 'Monaco', 'Consolas', monospace; font-size: 12px; }"
        )
        self._all_rows: list[tuple] = []
        self._filtered_rows: list[tuple] = []
        self._filter_text = ""
        self._updating = False
        self._save_callback = None
        self.itemChanged.connect(self._on_item_changed)

    def set_save_callback(self, cb):
        self._save_callback = cb

    def set_exif(self, rows: list[tuple]):
        self._all_rows = list(rows)
        self._apply_filter(self._filter_text)

    def get_all_rows(self) -> list[tuple]:
        return list(self._all_rows)

    def set_filter_text(self, text):
        self._filter_text = str(text or "").strip()
        self._apply_filter(self._filter_text)

    def _apply_filter(self, text: str):
        self._filter_text = text
        if not text:
            self._filtered_rows = list(self._all_rows)
        else:
            key = text.lower()
            self._filtered_rows = [
                r for r in self._all_rows
                if key in (r[2] or "").lower() or key in (r[3] or "").lower() or key in (r[4] or "").lower()
            ]
        rows = self._filtered_rows
        self._updating = True
        self.setRowCount(len(rows))
        for i, row in enumerate(rows):
            ifd_name, tag_id, group, name, value_str, raw_value = row[:6]
            exiftool_key = row[6] if len(row) > 6 else None
            it0 = QTableWidgetItem(group)
            it0.setFlags(it0.flags() & ~_ItemIsEditable)
            self.setItem(i, 0, it0)
            it1 = QTableWidgetItem(name)
            it1.setFlags(it1.flags() & ~_ItemIsEditable)
            self.setItem(i, 1, it1)
            it2 = QTableWidgetItem(value_str)
            editable_exif = (
                isinstance(ifd_name, str)
                and ifd_name in ("0th", "Exif", "GPS", "1st", "Interop")
                and isinstance(tag_id, int)
                and raw_value is not None
            )
            editable_meta = (
                ifd_name == META_IFD_NAME
                and str(tag_id) in (META_TITLE_TAG_ID, META_DESCRIPTION_TAG_ID)
            )
            editable_exiftool_row = exiftool_key is not None and ifd_name is None and tag_id is None
            editable = editable_exif or editable_meta or editable_exiftool_row
            if editable:
                it2.setFlags(it2.flags() | _ItemIsEditable)
            else:
                it2.setFlags(it2.flags() & ~_ItemIsEditable)
            self.setItem(i, 2, it2)
        self.resizeRowsToContents()
        self._updating = False

    def _on_item_changed(self, item):
        if self._updating or not self._save_callback or item.column() != 2:
            return
        row = item.row()
        if row < 0 or row >= len(self._filtered_rows):
            return
        row_data = self._filtered_rows[row]
        ifd_name, tag_id, group, name, old_val, raw_value = row_data[:6]
        exiftool_key = row_data[6] if len(row_data) > 6 else None
        is_editable_exif = (
            isinstance(ifd_name, str)
            and ifd_name in ("0th", "Exif", "GPS", "1st", "Interop")
            and isinstance(tag_id, int)
            and raw_value is not None
        )
        is_editable_meta = (
            ifd_name == META_IFD_NAME
            and str(tag_id) in (META_TITLE_TAG_ID, META_DESCRIPTION_TAG_ID)
        )
        is_editable_exiftool_row = exiftool_key is not None and ifd_name is None and tag_id is None
        if not (is_editable_exif or is_editable_meta or is_editable_exiftool_row):
            return
        new_val = item.text().strip()
        if new_val == old_val:
            return
        self._save_callback(ifd_name, tag_id, new_val, raw_value, exiftool_key)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        copy_act = QAction("复制", self)
        copy_act.triggered.connect(self._copy_selection)
        menu.addAction(copy_act)
        menu.exec(event.globalPos())

    def _copy_selection(self):
        sel = self.selectedRanges()
        if not sel:
            item = self.currentItem()
            if item is not None:
                QApplication.clipboard().setText(item.text())
            return
        parts = []
        for r in sel:
            for row in range(r.topRow(), r.bottomRow() + 1):
                cells = []
                for col in range(r.leftColumn(), r.rightColumn() + 1):
                    it = self.item(row, col)
                    cells.append(it.text() if it is not None else "")
                parts.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(parts))
