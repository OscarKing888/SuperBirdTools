# -*- coding: utf-8 -*-
"""识鸟候选表格：数据保留在模型中，只为可见单元格绘制采纳按钮。"""
from dataclasses import dataclass
import json
from pathlib import Path

try:
    from PyQt6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, pyqtSignal
    from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QStyle,
        QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem, QTableView)
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, pyqtSignal
    from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QStyle,
        QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem, QTableView)


@dataclass(eq=False)
class ResultEntry:
    result: object
    first_row: int
    row_count: int
    error: str = ""
    stale: bool = False
    pending_index: int | None = None


class BirdIDResultsModel(QAbstractTableModel):
    HEADERS = ("文件名", "状态", "操作", "候选", "中文鸟名", "置信度", "英文鸟名", "拼音", "学名",
               "稀有度", "保护等级", "说明", "地理筛选提示", "定位 / 检测信息")
    ACTION_COLUMN = 2
    STATUS = {"success": "已确认", "candidate": "待确定", "skipped": "跳过", "failed": "失败", "cancelled": "取消"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.pending = False

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section] if orientation == Qt.Orientation.Horizontal else section + 1

    def append_result(self, result):
        count = max(1, len(result.response.get("results", [])))
        entry = ResultEntry(result, len(self.rows), count)
        self.beginInsertRows(QModelIndex(), entry.first_row, entry.first_row + count - 1)
        self.rows.extend((entry, i) for i in range(count))
        self.endInsertRows()
        return entry

    def can_adopt(self, row):
        entry, index = self.rows[row]
        result = entry.result
        return (not self.pending and not entry.stale and result.status in {"success", "candidate"}
                and result.saved_fingerprint is not None and result.source_fingerprint is not None
                and index < len(result.response.get("results", [])) and index != result.accepted_index)

    def refresh_entry(self, entry):
        self.dataChanged.emit(self.index(entry.first_row, 0),
                              self.index(entry.first_row + entry.row_count - 1, len(self.HEADERS) - 1))

    def set_pending(self, pending):
        self.pending = pending
        if self.rows:
            self.dataChanged.emit(self.index(0, self.ACTION_COLUMN), self.index(len(self.rows) - 1, self.ACTION_COLUMN))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry, number = self.rows[index.row()]
        result = entry.result
        candidates = result.response.get("results", [])
        candidate = candidates[number] if candidates else {}
        col = index.column()
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 0:
                return result.source
            if col in (1, self.ACTION_COLUMN):
                return entry.error or result.message
            return self.data(index)
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if col == 0:
            return Path(result.source).name
        if col == 1:
            if entry.stale:
                return "结果已过期"
            if entry.error:
                return "采纳未保存"
            if result.status == "success" and number != result.accepted_index:
                return "候选"
            return self.STATUS.get(result.status, result.status)
        if col == self.ACTION_COLUMN:
            if not candidate:
                return "—"
            if result.accepted_index == number:
                return "已采纳"
            return "保存中…" if entry.pending_index == number else "采纳"
        if col == 3:
            return str(candidate.get("rank", number + 1)) if candidate else "—"
        if col == 5:
            return f"{float(candidate['confidence']):.1f}%" if candidate else "—"
        if col == 9:
            value = candidate.get("gbif_rarity_100")
            return "—" if value is None else f"{value:g} / 100"
        if col == 11:
            return candidate.get("description") or (result.message if not candidate else "—")
        if col == 12:
            return str(result.response.get("warning") or "—")
        if col == 13:
            return "\n".join(f"{label}：{json.dumps(result.response[key], ensure_ascii=False)}"
                             for key, label in (("gps_info", "GPS"), ("geo_info", "地理筛选"), ("yolo_info", "检测"))
                             if result.response.get(key) is not None) or "—"
        key = {4: "cn_name", 6: "en_name", 7: "pinyin_name", 8: "scientific_name", 10: "iucn_category"}.get(col)
        return candidate.get(key) or "—"


class AdoptDelegate(QStyledItemDelegate):
    requested = pyqtSignal(int)

    @staticmethod
    def button_rect(rect):
        return rect.adjusted(5, 4, -5, -4)

    def paint(self, painter, option, index):
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)
        button = QStyleOptionButton()
        button.rect = self.button_rect(option.rect)
        button.text = index.data()
        button.palette = option.palette
        button.state = QStyle.StateFlag.State_Raised
        if index.model().can_adopt(index.row()):
            button.state |= QStyle.StateFlag.State_Enabled
        if option.state & QStyle.StateFlag.State_HasFocus:
            button.state |= QStyle.StateFlag.State_HasFocus
        style.drawControl(QStyle.ControlElement.CE_PushButton, button, painter, option.widget)

    def editorEvent(self, event, model, option, index):
        if not model.can_adopt(index.row()):
            return False
        click = (event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton
                 and self.button_rect(option.rect).contains(event.pos()))
        key = (event.type() == QEvent.Type.KeyPress and event.key() in
               (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.isAutoRepeat())
        if click or key:
            self.requested.emit(index.row())
            return True
        return False


class BirdIDResultsTable(QTableView):
    adopt_requested = pyqtSignal(object, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.results = BirdIDResultsModel(self)
        self.setModel(self.results)
        self.setAccessibleName("识鸟结果表格")
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setWordWrap(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((180, 100, 100, 60, 120, 90, 200, 150, 200, 100, 100, 300, 240, 280)):
            self.setColumnWidth(column, width)
        delegate = AdoptDelegate(self)
        delegate.requested.connect(self._request)
        self.setItemDelegateForColumn(self.results.ACTION_COLUMN, delegate)

    def _request(self, row):
        if self.results.can_adopt(row):
            entry, index = self.results.rows[row]
            self.adopt_requested.emit(entry, index)

    def append_result(self, result):
        # 用户向上查看旧结果时，不被新结果强制拉回底部。
        bar = self.verticalScrollBar()
        follow = bar.value() == bar.maximum()
        entry = self.results.append_result(result)
        if follow:
            self.scrollToBottom()
        return entry
