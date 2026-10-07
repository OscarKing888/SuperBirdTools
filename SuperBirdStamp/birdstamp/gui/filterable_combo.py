"""字段与字体选择器共用的带搜索条下拉列表。"""
from __future__ import annotations

from typing import Any
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget


class FilterableComboBox(QComboBox):
    """下拉列表顶部内置过滤框，适合长字段/字体列表。"""

    popupAboutToShow = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filter_placeholder_text = "过滤..."
        self._filter_popup: QFrame | None = None
        self._filter_popup_filter: QLineEdit | None = None
        self._filter_popup_list: QListWidget | None = None

    def setFilterPlaceholderText(self, text: str) -> None:
        self._filter_placeholder_text = str(text or "").strip() or "过滤..."

    def hidePopup(self) -> None:  # type: ignore[override]
        popup = self._filter_popup
        if popup is not None:
            self._filter_popup = None
            self._filter_popup_filter = None
            self._filter_popup_list = None
            popup.hide()
            popup.deleteLater()
            return
        super().hidePopup()

    def showPopup(self) -> None:  # type: ignore[override]
        self.popupAboutToShow.emit()
        self.hidePopup()

        popup = QFrame(self, Qt.WindowType.Popup)
        popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        popup.setObjectName("filterableComboPopup")

        layout = QVBoxLayout(popup)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        filter_edit = QLineEdit(popup)
        filter_edit.setClearButtonEnabled(True)
        filter_edit.setPlaceholderText(self._filter_placeholder_text)
        filter_edit.setObjectName("filterableComboFilterEdit")
        layout.addWidget(filter_edit)

        list_widget = QListWidget(popup)
        list_widget.setUniformItemSizes(True)
        list_widget.setObjectName("filterableComboList")
        layout.addWidget(list_widget)

        source_items = [
            (idx, str(self.itemText(idx) or ""), self.itemData(idx))
            for idx in range(self.count())
        ]

        def _matches(text: str, data: Any, query: str) -> bool:
            query_parts = [
                part for part in str(query or "").strip().lower().split()
                if part
            ]
            if not query_parts:
                return True
            haystack = f"{text} {data}".lower()
            return all(part in haystack for part in query_parts)

        def _refresh_list(query: str = "") -> None:
            current_combo_index = self.currentIndex()
            list_widget.blockSignals(True)
            try:
                list_widget.clear()
                selected_row = 0
                for combo_index, text, data in source_items:
                    if not _matches(text, data, query):
                        continue
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, combo_index)
                    if text:
                        item.setToolTip(text)
                    list_widget.addItem(item)
                    if combo_index == current_combo_index:
                        selected_row = list_widget.count() - 1

                if list_widget.count() == 0:
                    empty_item = QListWidgetItem("无匹配结果")
                    empty_item.setFlags(empty_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    list_widget.addItem(empty_item)
                    selected_row = -1

                if selected_row >= 0:
                    list_widget.setCurrentRow(selected_row)
                    current_item = list_widget.item(selected_row)
                    if current_item is not None:
                        list_widget.scrollToItem(current_item)
            finally:
                list_widget.blockSignals(False)

        def _choose_item(item: QListWidgetItem | None = None) -> None:
            chosen = item or list_widget.currentItem()
            if chosen is None:
                return
            combo_index = chosen.data(Qt.ItemDataRole.UserRole)
            if combo_index is None:
                return
            try:
                self.setCurrentIndex(int(combo_index))
            except Exception:
                return
            self.hidePopup()
            # 自定义 popup 与原生 QComboBox 一样发送用户选择信号。
            self.activated.emit(int(combo_index))
            self.textActivated.emit(self.itemText(int(combo_index)))

        filter_edit.textChanged.connect(_refresh_list)
        filter_edit.returnPressed.connect(lambda: _choose_item())
        list_widget.itemClicked.connect(_choose_item)
        list_widget.itemActivated.connect(_choose_item)
        popup.destroyed.connect(lambda *_args, owner=popup: self._clear_filter_popup_refs(owner))
        filter_edit.installEventFilter(self)
        list_widget.installEventFilter(self)

        _refresh_list("")
        self._filter_popup = popup
        self._filter_popup_filter = filter_edit
        self._filter_popup_list = list_widget

        text_width = max(
            (list_widget.fontMetrics().horizontalAdvance(text) for _idx, text, _data in source_items),
            default=0,
        )
        popup_width = max(self.width(), min(max(text_width + 72, 260), 760))
        visible_rows = max(6, min(max(self.maxVisibleItems(), 8), 24))
        row_height = max(list_widget.sizeHintForRow(0), list_widget.fontMetrics().height() + 8)
        popup_height = filter_edit.sizeHint().height() + row_height * visible_rows + 24
        popup.resize(popup_width, popup_height)
        popup.move(self.mapToGlobal(self.rect().bottomLeft()))
        popup.show()
        filter_edit.setFocus(Qt.FocusReason.PopupFocusReason)

    def _clear_filter_popup_refs(self, popup: QFrame) -> None:
        if self._filter_popup is not popup:
            return
        self._filter_popup = None
        self._filter_popup_filter = None
        self._filter_popup_list = None

    def eventFilter(self, watched: Any, event: Any) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.KeyPress and self._filter_popup is not None:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.hidePopup()
                return True
            if watched is self._filter_popup_list and key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                current = self._filter_popup_list.currentItem()
                if current is not None:
                    self._filter_popup_list.itemActivated.emit(current)
                return True
            if watched is self._filter_popup_filter and key in {
                Qt.Key.Key_Down,
                Qt.Key.Key_PageDown,
            }:
                if self._filter_popup_list is not None:
                    if self._filter_popup_list.currentRow() < 0 and self._filter_popup_list.count() > 0:
                        self._filter_popup_list.setCurrentRow(0)
                    self._filter_popup_list.setFocus(Qt.FocusReason.TabFocusReason)
                return True
            if (
                watched is self._filter_popup_list
                and key == Qt.Key.Key_Up
                and self._filter_popup_list.currentRow() <= 0
            ):
                if self._filter_popup_filter is not None:
                    self._filter_popup_filter.setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return super().eventFilter(watched, event)
