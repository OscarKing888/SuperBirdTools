# -*- coding: utf-8 -*-
"""识鸟候选表格：数据保留在模型中，只为可见单元格绘制采纳按钮。"""
from dataclasses import dataclass
import json
from pathlib import Path

try:
    from PyQt6.QtGui import QFont, QPainter, QPalette, QPen
    from PyQt6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QRect, QTimer, Qt, pyqtSignal
    from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QStyle,
        QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem, QTableView)
except ImportError:  # pragma: no cover
    from PyQt5.QtGui import QFont, QPainter, QPalette, QPen
    from PyQt5.QtCore import QAbstractTableModel, QEvent, QModelIndex, QRect, QTimer, Qt, pyqtSignal
    from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QStyle,
        QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem, QTableView)


@dataclass(eq=False)
class ResultEntry:
    result: object
    first_row: int
    row_count: int
    candidate_indices: tuple = ()
    error: str = ""
    stale: bool = False
    pending_index: int | None = None


class BirdIDResultsModel(QAbstractTableModel):
    HEADERS = ("照片预览", "状态", "操作", "候选", "中文鸟名", "置信度", "英文鸟名", "拼音", "学名",
               "稀有度", "保护等级", "说明", "地理筛选提示", "定位 / 检测信息", "分组")
    ACTION_COLUMN = 2
    GROUP_COLUMN = 14
    STATUS = {"success": "已确认", "candidate": "待确定", "skipped": "跳过", "failed": "失败", "cancelled": "取消"}

    def __init__(self, parent=None, *, thumbnails=None):
        super().__init__(parent)
        self.thumbnails = thumbnails
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
        candidates = result.response.get("results", [])
        order = tuple(sorted(range(len(candidates)), key=lambda i: -float(candidates[i]['confidence']))) or (0,)
        entry = ResultEntry(result, len(self.rows), count, order)
        self.beginInsertRows(QModelIndex(), entry.first_row, entry.first_row + count - 1)
        self.rows.extend((entry, i) for i in order)
        self.endInsertRows()
        return entry

    def can_adopt(self, row):
        entry, index = self.rows[row]
        result = entry.result
        return (not self.pending and not entry.stale and result.status in {"success", "candidate"}
                and result.saved_fingerprint is not None and result.source_fingerprint is not None
                and index < len(result.response.get("results", []))
                and (index != result.accepted_index or result.candidates_missing))

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
        if col == self.GROUP_COLUMN:
            if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
                count = len(candidates)
                return f"{result.source}\n同一张照片 · {count} 个候选" if count else result.source
            return None
        if role == Qt.ItemDataRole.FontRole and candidate and number == entry.candidate_indices[0] and col in (4, 5):
            font = QFont()
            font.setBold(True)
            return font
        if col == 0 and role == Qt.ItemDataRole.DecorationRole and self.thumbnails is not None:
            return self.thumbnails.image(result.source)
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 0:
                return result.source
            if col in (1, self.ACTION_COLUMN):
                return entry.error or result.message
            if col in (4, 5) and candidate and number == entry.candidate_indices[0]:
                return f"最高置信度候选：{candidate.get('cn_name') or candidate.get('en_name')} {float(candidate['confidence']):.1f}%"
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
                return "采纳并补存" if result.candidates_missing else "已采纳"
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


class PhotoPreviewDelegate(QStyledItemDelegate):
    """等比显示共享缩略图；文件名仍保留在预览下方。"""
    def paint(self, painter, option, index):
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""
        background.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)
        image = index.data(Qt.ItemDataRole.DecorationRole)
        box = option.rect.adjusted(6, 5, -6, -25)
        painter.save()
        painter.setClipRect(option.rect)
        role = (QPalette.ColorRole.HighlightedText if option.state & QStyle.StateFlag.State_Selected
                else QPalette.ColorRole.Text)
        painter.setPen(option.palette.color(role))
        if image is not None and not image.isNull():
            size = image.size().scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(0, 0, size.width(), size.height())
            target.moveCenter(box.center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, image)
        else:
            entry, _ = index.model().rows[index.row()]
            thumbnails = index.model().thumbnails
            text = "暂无预览" if thumbnails is None or thumbnails.failed(entry.result.source) else "加载中…"
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        name_box = QRect(option.rect.left() + 6, option.rect.bottom() - 22, option.rect.width() - 12, 20)
        text = option.fontMetrics.elidedText(index.data(), Qt.TextElideMode.ElideMiddle, name_box.width())
        painter.drawText(name_box, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class AdoptDelegate(QStyledItemDelegate):
    requested = pyqtSignal(int)

    @staticmethod
    def button_rect(rect):
        button = rect.adjusted(5, 4, -5, -4)
        button.setHeight(min(30, button.height()))
        button.moveCenter(rect.center())
        return button

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

    def __init__(self, parent=None, *, thumbnails=None):
        super().__init__(parent)
        self._thumbnails = thumbnails
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(0)
        self._preview_timer.timeout.connect(self._update_previews)
        self.results = BirdIDResultsModel(self, thumbnails=thumbnails)
        self.setModel(self.results)
        self.setAccessibleName("识鸟结果表格")
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setWordWrap(False)
        self.verticalHeader().setDefaultSectionSize(116)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((180, 100, 100, 60, 120, 90, 200, 150, 200, 100, 100, 300, 240, 280)):
            self.setColumnWidth(column, width)
        # 逻辑列追加、视觉移到最前，保留照片/采纳等既有列的索引和行为。
        group_column = self.results.GROUP_COLUMN
        self.setColumnWidth(group_column, 48)
        self.horizontalHeader().setSectionResizeMode(group_column, QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().moveSection(self.horizontalHeader().visualIndex(group_column), 0)
        self.setItemDelegateForColumn(0, PhotoPreviewDelegate(self))
        if thumbnails is not None:
            thumbnails.changed.connect(self._preview_ready)
        self.verticalScrollBar().valueChanged.connect(self._schedule_previews)
        self.horizontalScrollBar().valueChanged.connect(self._schedule_previews)
        self.horizontalHeader().sectionResized.connect(self._schedule_previews)
        delegate = AdoptDelegate(self)
        delegate.requested.connect(self._request)
        self.setItemDelegateForColumn(self.results.ACTION_COLUMN, delegate)

    def paintEvent(self, event):
        super().paintEvent(event)
        column = self.results.GROUP_COLUMN
        if self.isColumnHidden(column):
            return
        left = self.columnViewportPosition(column)
        width = self.columnWidth(column) - 1
        strip = QRect(left, 0, width, self.viewport().height())
        if not strip.intersects(self.viewport().rect()):
            return
        # 在表格网格线之后统一画括线，跨行不断线，选中某个候选也不割裂分组。
        painter = QPainter(self.viewport())
        painter.setClipRect(strip.intersected(self.viewport().rect()))
        painter.fillRect(strip, self.palette().brush(QPalette.ColorRole.Base))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Text), 2))
        spine, arm = left + 14, left + width - 10
        painted = set()
        for row in self._visible_rows():
            entry, _ = self.results.rows[row]
            if entry.first_row not in painted:
                painted.add(entry.first_row)
                last = entry.first_row + entry.row_count - 1
                top = self.rowViewportPosition(entry.first_row) + 12
                bottom = self.rowViewportPosition(last) + self.rowHeight(last) - 13
                painter.drawLine(spine, top, spine, bottom)
                painter.drawLine(spine, top, arm, top)
                painter.drawLine(spine, bottom, arm, bottom)
            center = self.rowViewportPosition(row) + self.rowHeight(row) // 2
            painter.drawLine(spine, center, arm, center)
        painter.end()

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
        if entry.first_row == 0:
            chosen = result.accepted_index if result.accepted_index is not None else entry.candidate_indices[0]
            self.setCurrentIndex(self.results.index(entry.first_row + entry.candidate_indices.index(chosen),
                                                    self.results.ACTION_COLUMN))
        self._schedule_previews()
        return entry

    def _schedule_previews(self, *_args):
        if self._thumbnails is not None and self.isVisible():
            self._preview_timer.start()

    def _visible_rows(self):
        if not self.results.rows or not self.isVisible():
            return range(0)
        top = self.rowAt(0)
        bottom = self.rowAt(self.viewport().height() - 1)
        return range(max(0, top), (bottom + 1) if bottom >= 0 else self.results.rowCount())

    def _update_previews(self):
        if self._thumbnails is None:
            return
        # 预览列已滚出可见区时不请求图片。
        visible = (self.columnViewportPosition(0) + self.columnWidth(0) > 0
                   and self.columnViewportPosition(0) < self.viewport().width())
        paths = [self.results.rows[row][0].result.source for row in self._visible_rows()] if visible else []
        self._thumbnails.set_visible(paths)

    def _preview_ready(self, path):
        # 只重绘当前可见的匹配候选，不扫描整批结果。
        for row in self._visible_rows():
            if self.results.rows[row][0].result.source == path:
                index = self.results.index(row, 0)
                self.results.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_previews()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_previews()

    def hideEvent(self, event):
        self._preview_timer.stop()
        if self._thumbnails is not None:
            self._thumbnails.set_visible([])
        super().hideEvent(event)
