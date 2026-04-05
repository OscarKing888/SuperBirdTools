"""editor_photo_list.py – QTreeWidget-compatible adapter on top of FileListPanel."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QProgressBar,
    QSlider,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
)

from app_common.file_browser import FileListPanel
from birdstamp.constants import SUPPORTED_EXTENSIONS
from birdstamp.discover import discover_inputs
from birdstamp.gui import template_context as _template_context
from birdstamp.gui.editor_utils import path_key as _path_key

# Column indices for editor photo list (must match header and editor.py usage)
PHOTO_COL_SEQ = 0
PHOTO_COL_NAME = 1
PHOTO_COL_CAPTURE_TIME = 2
PHOTO_COL_TITLE = 3
PHOTO_COL_RATIO = 4
PHOTO_COL_RATING = 5
PHOTO_COL_SHUTTER = 6
PHOTO_COL_ISO = 7
PHOTO_COL_APERTURE = 8
PHOTO_COL_ROW = 9

# Custom item data roles (for path, sequence, sort key)
_UserRole = int(Qt.ItemDataRole.UserRole)
PHOTO_LIST_PATH_ROLE = _UserRole + 1
PHOTO_LIST_SEQUENCE_ROLE = _UserRole + 2
PHOTO_LIST_SORT_ROLE = _UserRole + 3
PHOTO_LIST_PHOTO_INFO_ROLE = _UserRole + 4
PHOTO_LIST_DISPLAY_ROW_ROLE = _UserRole + 5


class PhotoListItem(QTreeWidgetItem):
    """Tree item for editor photo list; constructor accepts list of column texts (e.g. 7 empty strings)."""

    def __init__(self, texts: list[str]) -> None:
        super().__init__(texts)

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        """优先按业务排序键比较，缺省时再退回文本与添加顺序。"""
        tree_widget = self.treeWidget()
        column = tree_widget.sortColumn() if tree_widget is not None else PHOTO_COL_SEQ

        self_sort_value = self._column_sort_value(column)
        other_sort_value = self._column_sort_value_for_item(other, column)
        if self_sort_value is not None and other_sort_value is not None and self_sort_value != other_sort_value:
            try:
                return self_sort_value < other_sort_value
            except TypeError:
                return str(self_sort_value) < str(other_sort_value)

        self_text = (self.text(column) or "").casefold()
        other_text = (other.text(column) or "").casefold()
        if self_text != other_text:
            return self_text < other_text

        self_sequence = self._sequence_value()
        other_sequence = self._sequence_value_for_item(other)
        if self_sequence is not None and other_sequence is not None and self_sequence != other_sequence:
            return self_sequence < other_sequence

        return super().__lt__(other)

    def _column_sort_value(self, column: int) -> Any:
        value = self.data(column, PHOTO_LIST_SORT_ROLE)
        if value is not None:
            return value
        if column == PHOTO_COL_SEQ:
            sequence = self._sequence_value()
            if sequence is not None:
                return (0, sequence)
        return None

    def _sequence_value(self) -> int | None:
        raw_value = self.data(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE)
        try:
            return int(raw_value)
        except Exception:
            return None

    @staticmethod
    def _column_sort_value_for_item(item: QTreeWidgetItem, column: int) -> Any:
        if isinstance(item, PhotoListItem):
            return item._column_sort_value(column)
        value = item.data(column, PHOTO_LIST_SORT_ROLE)
        if value is not None:
            return value
        if column == PHOTO_COL_SEQ:
            try:
                return (0, int(item.data(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE)))
            except Exception:
                return None
        return None

    @staticmethod
    def _sequence_value_for_item(item: QTreeWidgetItem) -> int | None:
        if isinstance(item, PhotoListItem):
            return item._sequence_value()
        try:
            return int(item.data(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE))
        except Exception:
            return None


class PhotoListWidget(FileListPanel):
    """
    兼容旧版 `QTreeWidget` 调用方式的适配层。

    设计目的：
    - 主编辑器仍按 `QTreeWidget` API 使用（`addTopLevelItem` / `selectedItems` / `currentItemChanged` 等）
    - 底层实际使用 `FileListPanel` 的列表视图与样式实现
    - 隐藏 `FileListPanel` 的目录浏览专用增强 UI（缩略图切换、过滤栏、进度条）
    """

    create_filter_bar = False

    pathsDropped = pyqtSignal(list)
    currentItemChanged = pyqtSignal(object, object)  # (QTreeWidgetItem | None, QTreeWidgetItem | None)
    itemSelectionChanged = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._configure_editor_compat_view()
        self._install_drop_event_filters()
        self._tree_widget.currentItemChanged.connect(self._emit_current_item_changed)
        self._tree_widget.itemSelectionChanged.connect(self.itemSelectionChanged.emit)

    # ------------------------------------------------------------------
    # Compatibility setup
    # ------------------------------------------------------------------

    def _configure_editor_compat_view(self) -> None:
        # FileListPanel now uses FileTableView (QTreeView + model). PhotoListWidget
        # needs the QTreeWidget item API, so replace it in the stacked widget.
        old_view = self._tree_widget
        idx = self._stack.indexOf(old_view)
        new_tw = QTreeWidget()
        if idx >= 0:
            self._stack.insertWidget(idx, new_tw)
            self._stack.removeWidget(old_view)
            old_view.deleteLater()
        else:
            self._stack.addWidget(new_tw)
        self._stack.setCurrentWidget(new_tw)
        self._tree_widget = new_tw

        # 强制使用列表模式，避免主编辑器使用 QTreeWidget API 时与缩略图模式语义冲突。
        self._set_view_mode(self._MODE_LIST)

        # 隐藏 FileListPanel 扩展 UI（仍保留底层树控件）。
        self._hide_non_tree_ui()

        # 主编辑器沿用通用文件列表的编号列，并扩展出快门 / ISO / 光圈列。
        self._tree_widget.setColumnCount(10)
        self._tree_widget.setHeaderLabels(["#", "文件名", "拍摄时间", "鸟名", "裁切比例", "标星", "快门", "ISO", "光圈", ""])
        self._tree_widget.setAcceptDrops(True)
        self._tree_widget.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

        header = self._tree_widget.header()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(PHOTO_COL_SEQ, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(PHOTO_COL_NAME, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PHOTO_COL_CAPTURE_TIME, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PHOTO_COL_TITLE, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PHOTO_COL_RATIO, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PHOTO_COL_RATING, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PHOTO_COL_SHUTTER, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PHOTO_COL_ISO, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PHOTO_COL_APERTURE, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(PHOTO_COL_ROW, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(PHOTO_COL_SEQ, 44)
        header.resizeSection(PHOTO_COL_NAME, 260)
        header.resizeSection(PHOTO_COL_CAPTURE_TIME, 96)
        header.resizeSection(PHOTO_COL_TITLE, 160)
        header.resizeSection(PHOTO_COL_RATIO, 96)
        header.resizeSection(PHOTO_COL_RATING, 88)
        header.resizeSection(PHOTO_COL_SHUTTER, 84)
        header.resizeSection(PHOTO_COL_ISO, 72)
        header.resizeSection(PHOTO_COL_APERTURE, 72)
        header.resizeSection(PHOTO_COL_ROW, 0)  # ROW 数据列隐藏

        # 兼容旧控件的默认行为
        self._tree_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree_widget.setRootIsDecorated(False)
        self._tree_widget.setUniformRowHeights(True)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        header.setSortIndicator(PHOTO_COL_SEQ, Qt.SortOrder.AscendingOrder)
        self._tree_widget.setSortingEnabled(True)
        self._tree_widget.sortByColumn(PHOTO_COL_SEQ, Qt.SortOrder.AscendingOrder)

    def _hide_non_tree_ui(self) -> None:
        # 隐藏已知控件
        for widget in (
            getattr(self, "_btn_list", None),
            getattr(self, "_btn_thumb", None),
            getattr(self, "_size_slider", None),
            getattr(self, "_size_label", None),
            getattr(self, "_filter_edit", None),
            getattr(self, "_btn_filter_pick", None),
            getattr(self, "_meta_progress", None),
            getattr(self, "_list_widget", None),  # 锁定列表模式，不暴露缩略图
        ):
            if widget is not None:
                widget.hide()
        for btn in getattr(self, "_star_btns", []) or []:
            try:
                btn.hide()
            except Exception:
                pass

        # 隐藏 `FileListPanel` 顶部布局中未缓存引用的标签（如“大小:”）。
        for label in self.findChildren(QLabel):
            if label is None:
                continue
            if label is getattr(self, "_size_label", None):
                continue
            text = (label.text() or "").strip()
            if text in {"大小:"}:
                label.hide()

        # 隐藏进度条后也禁止后台加载器误触发时闪烁
        if isinstance(getattr(self, "_meta_progress", None), QProgressBar):
            self._meta_progress.hide()

        # 兜底：若尺寸滑块仍占位，收起其高度
        if isinstance(getattr(self, "_size_slider", None), QSlider):
            self._size_slider.setFixedHeight(0)
        if isinstance(getattr(self, "_btn_list", None), QToolButton):
            self._btn_list.setFixedHeight(0)
        if isinstance(getattr(self, "_btn_thumb", None), QToolButton):
            self._btn_thumb.setFixedHeight(0)

    def _install_drop_event_filters(self) -> None:
        self.setAcceptDrops(True)
        self._tree_widget.setAcceptDrops(True)
        try:
            self._tree_widget.viewport().setAcceptDrops(True)
        except Exception:
            pass
        self.installEventFilter(self)
        self._tree_widget.installEventFilter(self)
        try:
            self._tree_widget.viewport().installEventFilter(self)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Signal bridge
    # ------------------------------------------------------------------

    def _emit_current_item_changed(self, current: object, previous: object) -> None:
        self.currentItemChanged.emit(current, previous)

    # ------------------------------------------------------------------
    # Drag-and-drop (compat with old PhotoListWidget)
    # ------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        event_type = event.type()
        viewport = getattr(self._tree_widget, "viewport", lambda: None)()
        is_drop_target = watched in {self, self._tree_widget} or watched is viewport
        if not is_drop_target:
            return super().eventFilter(watched, event)

        if event_type == QEvent.Type.DragEnter:
            if getattr(event, "mimeData", None) and event.mimeData().hasUrls():  # type: ignore[attr-defined]
                event.acceptProposedAction()  # type: ignore[attr-defined]
                return True
        elif event_type == QEvent.Type.DragMove:
            if getattr(event, "mimeData", None) and event.mimeData().hasUrls():  # type: ignore[attr-defined]
                event.acceptProposedAction()  # type: ignore[attr-defined]
                return True
        elif event_type == QEvent.Type.Drop:
            if getattr(event, "mimeData", None) and event.mimeData().hasUrls():  # type: ignore[attr-defined]
                deduped = self._collect_dropped_paths(event)
                if deduped:
                    self.pathsDropped.emit(deduped)
                    event.acceptProposedAction()  # type: ignore[attr-defined]
                    return True
        return super().eventFilter(watched, event)

    def _collect_dropped_paths(self, event: QEvent) -> list[Path]:
        urls = event.mimeData().urls()  # type: ignore[attr-defined]
        incoming: list[Path] = []
        for url in urls:
            local = url.toLocalFile()
            if not local:
                continue
            path = Path(local)
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                incoming.append(path)
            elif path.is_dir():
                incoming.extend(discover_inputs(path, recursive=True))

        deduped: list[Path] = []
        seen: set[str] = set()
        for path in incoming:
            key = _path_key(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    # ------------------------------------------------------------------
    # QTreeWidget-compatible surface used by birdstamp.gui.editor
    # ------------------------------------------------------------------

    def setSelectionMode(self, mode: Any) -> None:  # type: ignore[override]
        self._tree_widget.setSelectionMode(mode)
        try:
            self._list_widget.setSelectionMode(mode)
        except Exception:
            pass

    def setSortingEnabled(self, enabled: bool) -> None:  # type: ignore[override]
        self._tree_widget.setSortingEnabled(bool(enabled))

    def header(self):  # noqa: ANN201
        return self._tree_widget.header()

    def topLevelItemCount(self) -> int:
        return int(self._tree_widget.topLevelItemCount())

    def topLevelItem(self, index: int) -> QTreeWidgetItem | None:
        return self._tree_widget.topLevelItem(index)

    def addTopLevelItem(self, item: QTreeWidgetItem) -> None:
        self._tree_widget.addTopLevelItem(item)

    def indexOfTopLevelItem(self, item: QTreeWidgetItem) -> int:
        return int(self._tree_widget.indexOfTopLevelItem(item))

    def takeTopLevelItem(self, index: int) -> QTreeWidgetItem | None:
        return self._tree_widget.takeTopLevelItem(index)

    def clear(self) -> None:  # type: ignore[override]
        try:
            self._stop_all_loaders()
        except Exception:
            pass
        self._tree_widget.clear()
        try:
            self._list_widget.clear()
        except Exception:
            pass
        self._tree_item_map = {}
        self._item_map = {}
        self._all_files = []
        self._meta_cache = {}
        self._current_dir = ""

    def currentItem(self) -> QTreeWidgetItem | None:
        return self._tree_widget.currentItem()

    def setCurrentItem(self, item: QTreeWidgetItem) -> None:
        self._tree_widget.setCurrentItem(item)

    def selectedItems(self) -> list[QTreeWidgetItem]:
        return list(self._tree_widget.selectedItems())

    def _on_sort_indicator_changed(self, column: int, _order: Qt.SortOrder) -> None:
        if column == PHOTO_COL_ROW:
            header = self._tree_widget.header()
            try:
                header.blockSignals(True)
                header.setSortIndicator(PHOTO_COL_SEQ, Qt.SortOrder.AscendingOrder)
            finally:
                header.blockSignals(False)
            self._tree_widget.sortByColumn(PHOTO_COL_SEQ, Qt.SortOrder.AscendingOrder)
        self.refresh_row_numbers()

    def resort(self) -> None:
        """按当前表头排序规则重排；首次默认按编号列升序。"""
        header = self._tree_widget.header()
        column = header.sortIndicatorSection()
        order = header.sortIndicatorOrder()
        if column < 0 or column >= self._tree_widget.columnCount() or column == PHOTO_COL_ROW:
            column = PHOTO_COL_SEQ
            order = Qt.SortOrder.AscendingOrder
            try:
                header.blockSignals(True)
                header.setSortIndicator(column, order)
            finally:
                header.blockSignals(False)
        self._tree_widget.sortByColumn(column, order)

    def refresh_row_numbers(self) -> None:
        """刷新首列显示编号，并同步回编辑器 photo info。"""
        for row in range(self._tree_widget.topLevelItemCount()):
            item = self._tree_widget.topLevelItem(row)
            if item is None:
                continue
            display_row_number = row + 1
            item.setText(PHOTO_COL_SEQ, str(display_row_number))
            item.setTextAlignment(PHOTO_COL_SEQ, int(Qt.AlignmentFlag.AlignCenter))
            item.setToolTip(PHOTO_COL_SEQ, str(display_row_number))
            item.setData(PHOTO_COL_ROW, PHOTO_LIST_DISPLAY_ROW_ROLE, display_row_number)

            photo_info = item.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE)
            if isinstance(photo_info, _template_context.PhotoInfo):
                item.setData(
                    PHOTO_COL_ROW,
                    PHOTO_LIST_PHOTO_INFO_ROLE,
                    _template_context.ensure_editor_photo_info(
                        photo_info,
                        editor_row_number=display_row_number,
                    ),
                )
