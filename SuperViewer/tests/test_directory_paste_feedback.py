"""目录粘贴与拖放光标：真实 Qt 菜单、照片/XMP、后台完成与剪贴板竞态。"""
import os
from pathlib import Path
import shutil
import threading

import pytest
from PyQt6.QtCore import QMimeData, QPoint, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QFont, QFontMetricsF
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QTreeWidgetItem

from SuperViewer.superviewer import directory_file_controller as ui
from SuperViewer.superviewer import file_drag_views as views
from SuperViewer.superviewer.file_drag_feedback import file_drag_cursor
from SuperViewer.superviewer.ui_theme import build_palette
from test_directory_file_transfer import photos, select_photos, target_item, wait_until, window

_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clipboard():
    _APP.clipboard().clear()
    yield _APP.clipboard()
    _APP.clipboard().clear()


def paste_action(controller, destination):
    menu = QMenu()
    controller.extend_directory_menu(menu, str(destination))
    action = next(a for a in menu.actions() if a.text() == "粘贴到此文件夹")
    return menu, action


@pytest.mark.parametrize("mode,action", [("list", "copy"), ("thumbnail", "cut"), ("list", "external")])
def test_directory_paste_keeps_browsed_folder_and_pairs_xmp(window, photos, monkeypatch, clipboard, mode, action):
    paths, destination = photos
    select_photos(window, paths, mode)
    files, controller = window._file_list, window._directory_files
    current_dir = files.get_current_dir()
    target, _ = target_item(window, destination)
    tree = window._dir_browser._tree
    current_item = tree.currentItem()
    if action == "external":
        mime = QMimeData()
        # 外部只有照片 URL 时，也要发现磁盘上的同名 XMP。
        mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        clipboard.setMimeData(mime)
    else:
        files._set_file_clipboard(files._collect_file_clipboard_entries(paths), action=action)

    calls = []
    def transfer(batch, directory, *, copy, owner):
        assert threading.current_thread() is not threading.main_thread()
        calls.append((directory, copy, len(batch)))
        for source in batch:
            (shutil.copy2 if copy else shutil.move)(source, Path(directory) / Path(source).name)
        return ui.TransferResult()
    monkeypatch.setattr(ui, "native_transfer", transfer)
    menu, paste = paste_action(controller, destination)
    assert paste.isEnabled()
    menu.popup(QPoint(20, 20))
    menu.setActiveAction(paste)
    QTest.keyClick(menu, Qt.Key.Key_Return)
    wait_until(controller.is_shutdown_done)
    assert calls == [(str(destination), action != "cut", 4)]
    assert files.get_current_dir() == current_dir and tree.currentItem() is current_item
    assert not target.isExpanded()
    for path in paths:
        assert (destination / Path(path).name).exists()
        assert (destination / Path(path).with_suffix(".XMP").name).read_text(encoding="utf-8") == "中文元数据：保留所有内容"
        assert Path(path).exists() == (action != "cut")
    assert bool(clipboard.mimeData() and clipboard.mimeData().hasUrls()) == (action != "cut")
    if action == "cut":
        wait_until(lambda: not any(path in files._all_files for path in paths))
        assert controller.can_undo
        controller.undo()
        wait_until(controller.is_shutdown_done)
        assert all(Path(path).exists() and Path(path).with_suffix(".XMP").exists() for path in paths)


@pytest.mark.parametrize("outcome", ["failure", "partial", "sidecar_skipped", "new_clipboard", "guard_clipboard", "shutdown"])
def test_paste_completion_does_not_lose_pending_or_new_clipboard(window, photos, monkeypatch, clipboard, outcome):
    paths, destination = photos
    select_photos(window, paths, "list")
    files, controller = window._file_list, window._directory_files
    files._cut_paths_to_clipboard(paths)
    entered, release = threading.Event(), threading.Event()
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args))
    if outcome == "guard_clipboard":
        def guard(paths, warn=False):
            if warn:
                clipboard.setText("保存编辑期间复制的新内容")
            return True
        monkeypatch.setattr(files, "file_operation_guard", guard)
    def transfer(batch, directory, **kwargs):
        entered.set()
        assert release.wait(10)
        if outcome == "failure":
            return ui.TransferResult(error="测试失败，未移动")
        for path in (batch[:2] if outcome == "partial" else batch):
            if outcome == "sidecar_skipped" and Path(path).suffix.lower() == ".xmp":
                continue
            shutil.move(path, Path(directory) / Path(path).name)
        return ui.TransferResult(cancelled=outcome == "partial")
    monkeypatch.setattr(ui, "native_transfer", transfer)
    assert controller.paste_to_directory(str(destination))
    wait_until(entered.is_set)
    try:
        assert not controller.can_paste_to_directory(str(destination))
        assert not controller.paste_to_directory(str(destination))
        if outcome == "new_clipboard":
            clipboard.setText("操作期间复制的新内容")
        elif outcome == "shutdown":
            controller.request_shutdown()
            assert not controller.is_shutdown_done()
    finally:
        release.set()
    wait_until(controller.is_shutdown_done)
    if outcome == "new_clipboard":
        assert clipboard.text() == "操作期间复制的新内容"
    elif outcome == "guard_clipboard":
        assert clipboard.text() == "保存编辑期间复制的新内容"
    elif outcome == "shutdown":
        assert not (clipboard.mimeData() and clipboard.mimeData().hasUrls())
    else:
        assert clipboard.mimeData().hasUrls()
        assert Path(paths[-1]).exists() == (outcome != "sidecar_skipped")
        assert Path(paths[-1]).with_suffix(".XMP").exists()
        assert bool(warnings) == (outcome == "failure")


def test_paste_menu_rechecks_clipboard_destination_and_edit_guard(window, photos, monkeypatch, clipboard):
    paths, destination = photos
    controller, files = window._directory_files, window._file_list
    menu, action = paste_action(controller, destination)
    assert not action.isEnabled()
    clipboard.setText("文字不能粘贴为文件")
    assert not controller.can_paste_to_directory(str(destination))
    files._copy_paths_to_clipboard(paths)
    menu, action = paste_action(controller, destination)
    assert action.isEnabled()
    clipboard.setText("菜单打开后剪贴板已改变")
    action.trigger()
    assert controller._worker is None
    files._copy_paths_to_clipboard(paths)
    monkeypatch.setattr(files, "file_operation_guard", lambda paths, warn=False: False)
    assert not controller.can_paste_to_directory(str(destination))
    action.trigger()  # 菜单创建后守卫改变，不能绕过正在编辑照片的保护。
    assert controller._worker is None
    monkeypatch.setattr(files, "file_operation_guard", None)
    destination.rmdir()
    action.trigger()
    assert controller._worker is None
    assert all(Path(path).exists() for path in paths)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_drag_action_switches_with_ctrl_and_rejects_invalid_targets(monkeypatch, platform):
    monkeypatch.setattr(views.sys, "platform", platform)
    tree = views.DirectoryDropTree()
    tree.resize(240, 160)
    tree.show()
    item = QTreeWidgetItem(["照片目标"])
    item.setData(0, Qt.ItemDataRole.UserRole, "/target")
    placeholder = QTreeWidgetItem(["载入中…"])
    tree.addTopLevelItems([item, placeholder])
    _APP.processEvents()
    mime = QMimeData()
    tree.accepts_file_drag = lambda data: data is mime
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    ctrl = Qt.KeyboardModifier.MetaModifier if platform == "darwin" else Qt.KeyboardModifier.ControlModifier
    position = tree.visualItemRect(item).center()
    enter = QDragEnterEvent(position, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(tree.viewport(), enter)
    assert enter.isAccepted() and enter.dropAction() == Qt.DropAction.MoveAction
    for modifiers, expected in [(ctrl, Qt.DropAction.CopyAction), (Qt.KeyboardModifier.NoModifier, Qt.DropAction.MoveAction)]:
        move = QDragMoveEvent(position, actions, mime, Qt.MouseButton.LeftButton, modifiers)
        QApplication.sendEvent(tree.viewport(), move)
        assert move.isAccepted() and move.dropAction() == expected
    for pos in (tree.visualItemRect(placeholder).center(), QPoint(10, 130)):
        QApplication.sendEvent(tree.viewport(), QDragLeaveEvent())
        event = QDragEnterEvent(pos, actions, mime, Qt.MouseButton.LeftButton, ctrl)
        QApplication.sendEvent(tree.viewport(), event)
        assert event.isAccepted() and event.dropAction() == Qt.DropAction.IgnoreAction
        assert not tree.drop_target.isValid()
        invalid_move = QDragMoveEvent(pos, actions, mime, Qt.MouseButton.LeftButton, ctrl)
        QApplication.sendEvent(tree.viewport(), invalid_move)
        assert not invalid_move.isAccepted()
        # 从空白进入后再移到真实目录，仍必须收到 DragMove 并切成复制光标。
        move = QDragMoveEvent(position, actions, mime, Qt.MouseButton.LeftButton, ctrl)
        QApplication.sendEvent(tree.viewport(), move)
        assert move.isAccepted() and move.dropAction() == Qt.DropAction.CopyAction
    QApplication.sendEvent(tree.viewport(), QDragLeaveEvent())
    assert not tree._hover_timer.isActive() and not tree.drop_target.isValid()
    tree.close()
    tree.deleteLater()


@pytest.mark.parametrize("theme,point_size,ratio", [("light", 10, 1), ("dark", 16, 2)])
def test_cursor_and_directory_menu_render(window, photos, clipboard, theme, point_size, ratio):
    paths, destination = photos
    window._file_list._copy_paths_to_clipboard(paths)
    palette = build_palette(theme)
    font = QFont(window.font())
    font.setPointSize(point_size)
    qa = os.environ.get("SUPERBIRD_DIRECTORY_DRAG_QA")
    images = []
    for action in (Qt.DropAction.CopyAction, Qt.DropAction.MoveAction):
        pixmap = file_drag_cursor(action, palette, font, ratio)
        assert pixmap.devicePixelRatio() == ratio
        assert pixmap.width() / ratio >= QFontMetricsF(font).horizontalAdvance("复制") + 48
        assert pixmap.height() / ratio >= QFontMetricsF(font).height() + 16
        images.append(pixmap.toImage())
        if qa:
            Path(qa).mkdir(parents=True, exist_ok=True)
            assert pixmap.save(str(Path(qa) / f"cursor-{theme}-{action.name}.png"))
    assert images[0] != images[1]
    menu, action = paste_action(window._directory_files, destination)
    menu.setFont(font)
    menu.setPalette(palette)
    menu.popup(QPoint(20, 20))
    menu.setActiveAction(action)
    _APP.processEvents()
    assert menu.actionGeometry(action).height() >= QFontMetricsF(font).height()
    assert menu.rect().contains(menu.actionGeometry(action))
    if qa:
        assert menu.grab().save(str(Path(qa) / f"directory-paste-{theme}.png"))
    menu.close()
