import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QApplication
import pytest

from birdstamp.photo_order import moved_photo_rows

from birdstamp.gui.editor_photo_list import (
    PHOTO_COL_CAPTURE_TIME,
    PHOTO_COL_NAME,
    PHOTO_COL_ROW,
    PHOTO_COL_SEQ,
    PHOTO_LIST_PATH_ROLE,
    PHOTO_LIST_SEQUENCE_ROLE,
    PHOTO_LIST_SORT_ROLE,
    PhotoListItem,
    PhotoListWidget,
)


_APP = QApplication.instance() or QApplication([])


def _app() -> QApplication:
    return _APP


@pytest.mark.parametrize("selected,direction,expected", [
    ([], -1, [0, 1, 2, 3, 4]),
    ([0], -1, [0, 1, 2, 3, 4]),
    ([4], 1, [0, 1, 2, 3, 4]),
    ([1, 2], -1, [1, 2, 0, 3, 4]),
    ([1, 2], 1, [0, 3, 1, 2, 4]),
    ([1, 3], -1, [1, 0, 3, 2, 4]),
    ([0, 2], -1, [0, 2, 1, 3, 4]),
    ([1, 3], 1, [0, 2, 1, 4, 3]),
    ([0, 1, 2, 3, 4], 1, [0, 1, 2, 3, 4]),
])
def test_move_photo_rows(selected, direction, expected):
    assert moved_photo_rows(5, selected, direction) == expected


@pytest.mark.parametrize("sorting_enabled", [True, False])
def test_manual_order_preserves_selection_and_survives_metadata_sort(sorting_enabled):
    app = _app()
    widget = PhotoListWidget()
    try:
        widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        items = [_make_item(seq, name, (0, seq)) for seq, name in enumerate(("c", "a", "b"), 1)]
        for item in items:
            widget.addTopLevelItem(item)
        widget.header().setSortIndicator(PHOTO_COL_NAME, Qt.SortOrder.AscendingOrder)
        widget.setCurrentItem(items[2])
        items[0].setSelected(True)
        widget.setSortingEnabled(sorting_enabled)
        changed, reordered = [], []
        widget.currentItemChanged.connect(lambda *args: changed.append(args))
        widget.manualOrderChanged.connect(lambda: reordered.append(True))

        assert widget.move_selected(-1)
        assert [widget.topLevelItem(i).text(PHOTO_COL_NAME) for i in range(3)] == ["b", "c", "a"]
        assert widget.currentItem() is items[2]
        assert {id(item) for item in widget.selectedItems()} == {id(items[0]), id(items[2])}
        assert not changed
        assert reordered == [True]
        assert widget.isSortingEnabled() == sorting_enabled
        # 模拟迟到元数据及批处理结束，不能把手动顺序恢复为文件名排序。
        items[0].setData(PHOTO_COL_NAME, PHOTO_LIST_SORT_ROLE, (0, "0"))
        widget.setSortingEnabled(True)
        widget.resort()
        assert [widget.topLevelItem(i).text(PHOTO_COL_NAME) for i in range(3)] == ["b", "c", "a"]
        assert [widget.topLevelItem(i).text(PHOTO_COL_SEQ) for i in range(3)] == ["1", "2", "3"]
        assert not widget.can_move_selected(-1)
        assert not widget.move_selected(-1)
        assert reordered == [True]
    finally:
        widget.deleteLater()
        app.processEvents()


def _make_item(seq: int, name: str, capture_sort: tuple[int, int]) -> PhotoListItem:
    item = PhotoListItem(["", name, "", "", "", "", ""])
    item.setData(PHOTO_COL_SEQ, PHOTO_LIST_SORT_ROLE, (0, seq))
    item.setData(PHOTO_COL_NAME, PHOTO_LIST_SORT_ROLE, (0, name.casefold()))
    item.setData(PHOTO_COL_CAPTURE_TIME, PHOTO_LIST_SORT_ROLE, capture_sort)
    item.setData(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE, seq)
    return item


def test_photo_list_context_menu_collects_selected_paths(tmp_path) -> None:
    app = _app()
    widget = PhotoListWidget()
    try:
        first = tmp_path / "first.jpg"
        second = tmp_path / "second.jpg"
        first.write_bytes(b"a")
        second.write_bytes(b"b")

        for seq, path in ((1, first), (2, second)):
            item = _make_item(seq, path.name, (0, seq))
            item.setData(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE, str(path))
            widget.addTopLevelItem(item)

        widget.setCurrentItem(widget.topLevelItem(0))
        widget.topLevelItem(0).setSelected(True)
        widget.topLevelItem(1).setSelected(True)

        assert widget._photo_selected_paths() == [str(first), str(second)]
        assert widget._photo_path_from_item(widget.topLevelItem(0)) == str(first)
    finally:
        widget.deleteLater()
        app.processEvents()


def test_photo_list_header_sorting_keeps_active_column() -> None:
    app = _app()
    widget = PhotoListWidget()
    try:
        for item in (
            _make_item(2, "beta", (0, 20)),
            _make_item(1, "gamma", (0, 10)),
            _make_item(3, "alpha", (0, 30)),
        ):
            widget.addTopLevelItem(item)

        widget.resort()
        app.processEvents()
        default_order = [
            widget.topLevelItem(i).data(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE)
            for i in range(widget.topLevelItemCount())
        ]
        assert default_order == [1, 2, 3]

        header = widget.header()
        header.setSortIndicator(PHOTO_COL_NAME, Qt.SortOrder.AscendingOrder)
        widget.resort()
        app.processEvents()
        name_order = [
            widget.topLevelItem(i).text(PHOTO_COL_NAME)
            for i in range(widget.topLevelItemCount())
        ]
        assert name_order == ["alpha", "beta", "gamma"]

        header.setSortIndicator(PHOTO_COL_CAPTURE_TIME, Qt.SortOrder.DescendingOrder)
        widget.resort()
        app.processEvents()
        capture_order = [
            widget.topLevelItem(i).data(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE)
            for i in range(widget.topLevelItemCount())
        ]
        assert capture_order == [3, 2, 1]
    finally:
        widget.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("clicked_row,expected", [(0, ["a.jpg", "b.jpg"]), (2, ["c.jpg"])])
def test_remove_context_menu_preserves_or_replaces_selection(tmp_path, monkeypatch, clicked_row, expected):
    from birdstamp.gui import editor_photo_list

    widget = PhotoListWidget()
    widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    requests = []
    widget.removeSelectedRequested.connect(
        lambda: requests.append([item.text(PHOTO_COL_NAME) for item in widget.selectedItems()])
    )
    try:
        for seq, name in enumerate(("a.jpg", "b.jpg", "c.jpg"), 1):
            item = _make_item(seq, name, (0, seq))
            item.setData(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE, str(tmp_path / name))
            widget.addTopLevelItem(item)
        widget.resize(600, 300)
        widget.show()
        _APP.processEvents()
        widget.setCurrentItem(widget.topLevelItem(0))
        widget.topLevelItem(1).setSelected(True)

        def choose_remove(menu, pos):
            action = next(action for action in menu.actions() if action.text() == "删除所选")
            assert action.shortcuts()
            action.trigger()

        monkeypatch.setattr(editor_photo_list, "_exec_menu", choose_remove)
        pos = widget._tree_widget.visualItemRect(widget.topLevelItem(clicked_row)).center()
        widget._on_photo_context_menu(pos)
        assert requests == [expected]
        widget._tree_widget.clearSelection()
        widget.remove_selected_action.trigger()
        assert requests == [expected]
    finally:
        widget.close()
        widget.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_remove_shortcuts_only_affect_focused_photo_list(monkeypatch, platform):
    from types import SimpleNamespace
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QLineEdit, QVBoxLayout, QWidget
    from birdstamp.gui import editor_photo_list

    monkeypatch.setattr(editor_photo_list, "sys", SimpleNamespace(platform=platform))
    host = QWidget()
    layout = QVBoxLayout(host)
    widget = PhotoListWidget()
    edit = QLineEdit("abc")
    layout.addWidget(widget)
    layout.addWidget(edit)
    item = _make_item(1, "a.jpg", (0, 1))
    widget.addTopLevelItem(item)
    widget.setCurrentItem(item)
    requests = []
    widget.removeSelectedRequested.connect(lambda: requests.append(True))
    try:
        host.show()
        host.activateWindow()
        widget._tree_widget.setFocus()
        _APP.processEvents()
        assert widget._tree_widget.hasFocus()
        QTest.keyClick(widget._tree_widget, Qt.Key.Key_Delete)
        assert len(requests) == 1
        QTest.keyClick(widget._tree_widget, Qt.Key.Key_Backspace)
        expected_count = 2 if platform == "darwin" else 1
        assert len(requests) == expected_count
        _APP.sendEvent(widget._tree_widget, QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier, "", True,
        ))
        assert len(requests) == expected_count
        edit.setFocus()
        edit.setCursorPosition(1)
        QTest.keyClick(edit, Qt.Key.Key_Delete)
        assert edit.text() == "ac"
        QTest.keyClick(edit, Qt.Key.Key_Backspace)
        assert edit.text() == "c"
        assert len(requests) == expected_count
        widget._tree_widget.setFocus()
        widget._tree_widget.clearSelection()
        QTest.keyClick(widget._tree_widget, Qt.Key.Key_Delete)
        assert len(requests) == expected_count
    finally:
        host.close()
        host.deleteLater()
        _APP.processEvents()
