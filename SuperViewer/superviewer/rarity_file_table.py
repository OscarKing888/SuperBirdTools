# -*- coding: utf-8 -*-
"""Viewer 稀有度列表列：仅消费已有元数据，徽章绘制不创建逐行控件。"""
from app_common.bird_rarity import rarity_metadata
from app_common.file_browser._models import FileTableModel, _file_sort_tiebreaker
from .rarity_badge import rarity_badge_style, rarity_badge_tooltip

try:
    from PyQt6.QtCore import Qt, QModelIndex, QRectF, QSize
    from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
    from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import Qt, QModelIndex, QRectF, QSize
    from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
    from PyQt5.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication

_Role = getattr(Qt, "ItemDataRole", Qt)
_RarityScoreRole = int(_Role.UserRole) + 40
_Horizontal = getattr(Qt, "Orientation", Qt).Horizontal


def _rarity_sort_value(score):
    # 缺失值独立于有效的 0 分，升序时排在有效分数之后。
    return (score is None, score if score is not None else 0.0)


class RarityFileTableModel(FileTableModel):
    @property
    def rarity_column(self):
        # 逻辑列追加在末尾，保留共享相机/视频列索引；视觉位置由表头调整。
        return super().columnCount()

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.rarity_column + 1

    def headerData(self, section, orientation, role=int(_Role.DisplayRole)):
        if section == self.rarity_column and orientation == _Horizontal and role == _Role.DisplayRole:
            return "稀有度"
        return super().headerData(section, orientation, role)

    def _apply_meta_to_entry(self, entry, meta):
        super()._apply_meta_to_entry(entry, meta)
        # 缓存在当前行上，随原模型重建/清空释放，并沿用增量元数据刷新。
        entry.rarity_score = rarity_metadata(meta or {})[0]

    def _display_value(self, entry, row, column):
        if column == self.rarity_column:
            return "" if entry.rarity_score is None else rarity_badge_style(entry.rarity_score)[0]
        return super()._display_value(entry, row, column)

    def _sort_value(self, entry, column):
        if column == self.rarity_column:
            return _rarity_sort_value(entry.rarity_score)
        return super()._sort_value(entry, column)

    def sort_key_for_path(self, path, meta, column):
        if column == self.rarity_column:
            return (_rarity_sort_value(rarity_metadata(meta or {})[0]), *_file_sort_tiebreaker(path))
        return super().sort_key_for_path(path, meta, column)

    def data(self, index, role=int(_Role.DisplayRole)):
        if index.isValid() and 0 <= index.row() < len(self._entries) and index.column() == self.rarity_column:
            score = self._entries[index.row()].rarity_score
            if role == _RarityScoreRole:
                return score
            if role == _Role.ToolTipRole:
                return "" if score is None else f"{rarity_badge_style(score)[0]}\n{rarity_badge_tooltip(score)}"
        return super().data(index, role)

    def refresh_badges(self):
        if self.rowCount():
            self.dataChanged.emit(self.index(0, self.rarity_column),
                                  self.index(self.rowCount() - 1, self.rarity_column),
                                  [_Role.DisplayRole, _Role.ToolTipRole])


class RarityBadgeDelegate(QStyledItemDelegate):
    """先绘制原有选中/连拍背景，再绘制紧凑圆角徽章。"""
    @staticmethod
    def badge_font(base):
        font = QFont(base)
        font.setPixelSize(13)
        font.setWeight(getattr(QFont, "Weight", QFont).DemiBold)
        return font

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        font_metrics = QFontMetrics(self.badge_font(option.font))
        return QSize(max(size.width(), font_metrics.horizontalAdvance(index.data() or "") + 26),
                     max(size.height(), font_metrics.height() + 10))

    def paint(self, painter, option, index):
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        cell.text = ""
        style = cell.widget.style() if cell.widget else QApplication.style()
        style.drawControl(getattr(QStyle, "ControlElement", QStyle).CE_ItemViewItem, cell, painter, cell.widget)
        score = index.data(_RarityScoreRole)
        if score is None:
            return
        text, background, foreground = rarity_badge_style(score)
        painter.save()
        try:
            painter.setClipRect(option.rect)
            painter.setRenderHint(getattr(QPainter, "RenderHint", QPainter).Antialiasing)
            painter.setFont(self.badge_font(option.font))
            metrics = painter.fontMetrics()
            area = QRectF(option.rect).adjusted(4, 2, -4, -2)
            width = min(area.width(), max(60, metrics.horizontalAdvance(text) + 18))
            height = min(area.height(), metrics.height() + 6)
            if width <= 0 or height <= 0:
                return
            badge = QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)
            painter.setPen(getattr(Qt, "PenStyle", Qt).NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(badge, 6, 6)
            painter.setPen(QColor(foreground))
            label = metrics.elidedText(text, getattr(Qt, "TextElideMode", Qt).ElideRight, max(0, int(width) - 18))
            painter.drawText(badge, int(getattr(Qt, "AlignmentFlag", Qt).AlignCenter), label)
        finally:
            painter.restore()
