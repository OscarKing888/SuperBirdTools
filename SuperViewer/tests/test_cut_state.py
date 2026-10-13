"""Viewer 剪切反馈：原生快捷键、菜单、双视图与真实离屏绘制。"""
import pytest
from PyQt6.QtCore import QRect, Qt, QItemSelectionModel
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QStyle, QStyleOptionViewItem

from app_common import superviewer_user_options
from app_common.file_browser import _panel as panel_module
from app_common.file_browser._browser_core import _platform_cut_key_sequence, _platform_copy_key_sequence
from app_common.file_browser._models import _CutPendingRole, _ThumbPixmapRole
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setattr(SuperViewerTaggedFileListPanel, "_schedule_visible_thumbnail_update", lambda *a, **kw: None)
    monkeypatch.setattr(SuperViewerTaggedFileListPanel, "_emit_file_selected_for_path", lambda *a, **kw: None)
    monkeypatch.setattr(panel_module, "_shutdown_thumb_disk_writer", lambda **kw: None)
    _APP.clipboard().clear()
    panel = SuperViewerTaggedFileListPanel(tag_config_path=tmp_path / "tags.cfg")
    paths = [str(tmp_path / f"白鹭_{i}.jpg") for i in range(3)]
    for path in paths:
        with open(path, "wb") as photo:
            photo.write(b"test photo")
    panel._current_dir = str(tmp_path)
    panel._all_files = list(paths)
    panel._filtered_files = list(paths)
    for model in (panel._file_table_model, panel._thumb_list_model):
        model.rebuild(paths, meta_cache={p: {"title": "白鹭", "rating": 4} for p in paths},
                      tooltip_fn=None, mismatch_fn=None)
    yield panel, paths
    panel.request_shutdown()
    panel.shutdown()
    panel.close()
    panel.deleteLater()
    _APP.processEvents()
    _APP.clipboard().clear()


@pytest.mark.parametrize("mode", ["thumb", "tree"])
def test_native_cut_and_copy_shortcuts_and_context_menu(browser, mode):
    panel, paths = browser
    panel._set_view_mode(panel._MODE_THUMB if mode == "thumb" else panel._MODE_LIST)
    # 视图切换可能重填模型；等待已有的定时填充完成。
    while panel._thumb_model_dirty:
        panel._populate_thumb_model_batch()
    view = panel._list_widget if mode == "thumb" else panel._tree_widget
    panel.resize(720, 520)
    panel.show()
    panel.activateWindow()
    view.setFocus()
    _APP.processEvents()
    selection = view.selectionModel()
    first, second = view.model().index(0, 0), view.model().index(1, 0)
    selection.setCurrentIndex(first, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
    selection.select(second, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    before = [index.row() for index in selection.selectedRows()]
    QTest.keySequence(view, _platform_cut_key_sequence())
    assert first.data(_CutPendingRole) and second.data(_CutPendingRole)
    assert not view.model().index(2, 0).data(_CutPendingRole)
    assert [index.row() for index in selection.selectedRows()] == before
    assert view.currentIndex() == first
    QTest.keySequence(view, _platform_copy_key_sequence())
    assert not first.data(_CutPendingRole)
    menu = QMenu(panel)
    panel._add_file_clipboard_menu_actions(menu, [paths[2]])
    next(action for action in menu.actions() if action.text().split("\t")[0] == "剪切").trigger()
    assert view.model().index(2, 0).data(_CutPendingRole)
    assert not first.data(_CutPendingRole)


def render_thumbnail(panel, path, *, cut, light=False, selected=False, size=128, font_size=10):
    model = panel._thumb_list_model
    if cut:
        panel._cut_paths_to_clipboard([path])
    else:
        _APP.clipboard().clear()
    pixmap = QPixmap(300, 200)
    pixmap.fill(QColor("#88bb66"))
    model.set_pixmap_for_path(path, pixmap, size)
    delegate = panel._list_widget.itemDelegate()
    image = QImage(delegate.grid_size(size), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#f5f5f5" if light else "#292929"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, image.width(), image.height())
    option.font = QFont("Arial", font_size)
    option.palette.setColor(QPalette.ColorRole.Text, QColor("#202124" if light else "#eeeeee"))
    option.palette.setColor(QPalette.ColorRole.Highlight, QColor("#245fa8"))
    option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    option.state = QStyle.StateFlag.State_Enabled
    if selected:
        option.state |= QStyle.StateFlag.State_Selected
    painter = QPainter(image)
    painter.setFont(option.font)
    try:
        delegate.paint(painter, option, model.index_for_path(path))
    finally:
        painter.end()
    return image


@pytest.mark.parametrize("light,selected,size,font_size", [
    (False, False, 128, 10), (False, True, 128, 10), (True, False, 128, 16),
    (True, True, 256, 16), (False, False, 256, 16),
])
def test_cut_render_dims_only_pixels_and_keeps_footer_selection_and_cache(browser, light, selected, size, font_size):
    panel, paths = browser
    options = dict(light=light, selected=selected, size=size, font_size=font_size)
    normal = render_thumbnail(panel, paths[0], cut=False, **options)
    cut = render_thumbnail(panel, paths[0], cut=True, **options)
    # 图片中央变淡；外侧选中背景和底部元数据/文件名完全不变。
    assert cut.pixelColor(cut.width() // 2, 40) != normal.pixelColor(normal.width() // 2, 40)
    assert cut.pixelColor(1, 1) == normal.pixelColor(1, 1)
    for y in range(cut.height() - 65, cut.height()):
        for x in range(cut.width()):
            assert cut.pixelColor(x, y) == normal.pixelColor(x, y)
    cached = panel._thumb_list_model.index_for_path(paths[0]).data(_ThumbPixmapRole)
    assert cached.toImage().pixelColor(100, 100).name() == "#88bb66"
    restored = render_thumbnail(panel, paths[0], cut=False, **options)
    assert restored == normal
