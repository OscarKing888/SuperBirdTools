import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

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


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


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
