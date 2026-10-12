# -*- coding: utf-8 -*-
"""离屏渲染文件锁 UI 设计；状态为演示数据，不接入文件系统或用户配置。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor, QFont, QImageReader, QPainter, QPainterPath, QPalette, QPen,
    QPixmap, QStandardItem, QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QApplication, QAbstractItemView, QHeaderView, QLabel, QListView, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QVBoxLayout, QWidget,
)

from app_common.bird_rarity import IUCN_FIELD, RARITY_FIELD
from SuperViewer.superviewer import thumbnail_metadata as thumbnails
from SuperViewer.superviewer.ui_theme import build_palette

OUT = Path(__file__).resolve().parent
STATES = ("", "只读", "已锁定", "只读、已锁定")
NAMES = ("DSC05561.ARW", "DSC05562.ARW", "DSC05563.ARW", "湿地观鸟_黑脸琵鹭_05564.ARW")
LOCK_ROLE = int(Qt.ItemDataRole.UserRole) + 101


def draw_lock(painter, rect, color):
    """逻辑像素矢量锁；不用 emoji，避免平台字形差异。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 16, rect.height() / 16)
    painter.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    shackle = QPainterPath()
    shackle.moveTo(4.5, 7)
    shackle.lineTo(4.5, 4.5)
    shackle.cubicTo(4.5, .4, 11.5, .4, 11.5, 4.5)
    shackle.lineTo(11.5, 7)
    painter.drawPath(shackle)
    painter.drawRoundedRect(QRectF(2.5, 7, 11, 7.5), 1.6, 1.6)
    painter.drawLine(8, 10, 8, 12)
    painter.restore()


class NameDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, option.widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        color = opt.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        painter.save()
        painter.setPen(color)
        painter.setFont(opt.font)
        if index.data(LOCK_ROLE):
            draw_lock(painter, QRectF(opt.rect.x() + 5, opt.rect.center().y() - 7, 14, 14), color)
        text_rect = opt.rect.adjusted(25, 0, -4, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                         opt.fontMetrics.elidedText(text, Qt.TextElideMode.ElideMiddle, text_rect.width()))
        painter.restore()


class LockedThumbnailDelegate(thumbnails.BirdThumbnailDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if not index.data(LOCK_ROLE):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 真实 delegate 卡片边距为 6；角标在卡片内再留 6 DIP。
        plate = QRectF(option.rect.right() - 33, option.rect.top() + 12, 22, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 185))
        painter.drawRoundedRect(plate, 5, 5)
        draw_lock(painter, plate.adjusted(4, 4, -4, -4), QColor("white"))
        painter.restore()


class DemoModel(thumbnails.BirdThumbnailModel):
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == LOCK_ROLE:
            return STATES[index.row()]
        if index.isValid() and role == Qt.ItemDataRole.ToolTipRole:
            base = str(super().data(index, role) or "")
            state = STATES[index.row()]
            return thumbnails._tooltip_with_lines(base, [f"原始文件：{state}"] if state else [])
        return super().data(index, role)


def board(app, scheme, width=716, font_size=13, thumb_size=128):
    app.setPalette(build_palette(scheme))
    font = QFont(app.font())
    font.setPixelSize(font_size)
    app.setFont(font)
    root = QWidget()
    root.setAutoFillBackground(True)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    title = QLabel("SuperViewer · 文件保护标识")
    title_font = QFont(font)
    title_font.setPixelSize(18)
    title_font.setBold(True)
    title.setFont(title_font)
    layout.addWidget(title)
    layout.addWidget(QLabel("UI 设计稿 · 示例状态"))
    layout.addSpacing(8)
    layout.addWidget(QLabel("文件列表 · 文件名前的固定状态位"))
    table = QTreeView()
    table.setRootIsDecorated(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    model = QStandardItemModel(table)
    model.setHorizontalHeaderLabels(["#", "文件名", "连拍", "鸟名", "评级"])
    for row, (name, state) in enumerate(zip(NAMES, STATES)):
        cells = [QStandardItem(text) for text in (str(row + 1), name, f"({row+1}/1)", "黑脸琵鹭", "★★★★★")]
        for cell in cells:
            cell.setSizeHint(QSize(20, font_size + 17))
        cells[1].setData(state, LOCK_ROLE)
        cells[1].setToolTip(name + (f"\n原始文件：{state}" if state else ""))
        model.appendRow(cells)
    table.setModel(model)
    table.setItemDelegateForColumn(1, NameDelegate(table))
    for col, size in enumerate((32, 260, 64, 112, 95)):
        table.setColumnWidth(col, size)
    # 窄窗口保留可读的文件名宽度，由已有横向滚动承载其余列。
    table.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch if width >= 600
                                       else QHeaderView.ResizeMode.Interactive)
    table.header().setStretchLastSection(False)
    table.setCurrentIndex(model.index(2, 1))
    table.setFixedHeight((font_size + 17) * 4 + 38 + (20 if width < 600 else 0))
    layout.addWidget(table)
    layout.addSpacing(8)
    layout.addWidget(QLabel("缩略图 · 右上角白锁，深色半透明底板"))
    view = QListView()
    view.setViewMode(QListView.ViewMode.IconMode)
    view.setResizeMode(QListView.ResizeMode.Adjust)
    view.setMovement(QListView.Movement.Static)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setSpacing(3)
    delegate = LockedThumbnailDelegate(view)
    grid = delegate.grid_size(thumb_size)
    view.setGridSize(grid)
    view.setItemDelegate(delegate)
    thumb_model = DemoModel(view)
    meta = {name: {"title": "黑脸琵鹭", RARITY_FIELD: 30, IUCN_FIELD: "EN", "rating": 5, "pick": 1} for name in NAMES}
    thumb_model.append_paths(list(NAMES), meta_cache=meta, tooltip_fn=None, mismatch_fn=None)
    reader = QImageReader(str(ROOT / "SuperBirdStamp/images/default.jpg"))
    reader.setScaledSize(QSize(384, 256))
    pixmap = QPixmap.fromImage(reader.read())
    assert not pixmap.isNull()
    for name in NAMES:
        thumb_model.set_pixmap_for_path(name, pixmap, thumb_size)
    view.setModel(thumb_model)
    view.setCurrentIndex(thumb_model.index(2, 0))
    columns = max(1, (width - 44) // grid.width())
    rows = (4 + columns - 1) // columns
    view.setFixedHeight(rows * grid.height() + 8)
    layout.addWidget(view)
    note = QLabel("从左到右：普通文件 / 只读 / 已锁定（选中）/ 只读且锁定")
    note.setWordWrap(True)
    layout.addWidget(note)
    layout.addSpacing(8)
    tip = QLabel("悬停提示示例\nDSC05563.ARW\n原始文件：已锁定")
    tip.setMargin(10)
    tip.setAutoFillBackground(True)
    palette = tip.palette()
    palette.setColor(QPalette.ColorRole.Window, palette.color(QPalette.ColorRole.Base))
    tip.setPalette(palette)
    layout.addWidget(tip)
    root.resize(width, root.sizeHint().height())
    root.show()
    table.setFocus()
    app.processEvents()
    view.doItemsLayout()
    app.processEvents()
    # 几何检查保证最小字号、重排与角标区域可见；不检查业务实现。
    for row in range(4):
        rect = view.visualRect(thumb_model.index(row, 0))
        assert rect.width() >= 128 and rect.top() >= 0
        assert rect.bottom() < view.viewport().height(), (width, font_size, rect)
    for widget in (title, table, view, note, tip):
        assert root.rect().contains(widget.geometry())
    assert table.columnWidth(1) >= 160
    assert "只读、已锁定" in thumb_model.index(3, 0).data(Qt.ItemDataRole.ToolTipRole)
    return root


def main():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    # 使用默认徽章样式，不加载用户配置。仅此离屏演示进程生效。
    thumbnails.get_runtime_user_options = lambda: {}
    for scheme, width, size, thumb, filename in (
        ("dark", 716, 13, 128, "dark.png"),
        ("light", 716, 13, 128, "light.png"),
        ("dark", 390, 18, 128, "compact-large-font.png"),
        ("light", 650, 13, 256, "large-thumbnails.png"),
    ):
        root = board(app, scheme, width, size, thumb)
        assert root.grab().save(str(OUT / filename))
        root.close()
        root.deleteLater()
        app.processEvents()
        print(f"Rendered {filename}: {width}px, font {size}px, thumbnail {thumb}px")


if __name__ == "__main__":
    main()
