"""系统文件拖放：临时照片/侧车、真实 Qt 事件及原生协议边界。"""
import ctypes
import os
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image
from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, QTimer, Qt
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QTreeWidgetItem

from SuperViewer.superviewer import directory_file_controller as ui
from SuperViewer.superviewer import file_drag_views as views
from SuperViewer.superviewer import native_file_transfer as native

_APP = QApplication.instance() or QApplication([])


def wait_until(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def photos(tmp_path):
    source, destination = tmp_path / "照片来源", tmp_path / "目标目录"
    source.mkdir(); destination.mkdir()
    paths = []
    for name in ("鸟 一.jpg", "鸟 二.jpg"):
        photo = source / name
        Image.new("RGB", (60, 40), "green").save(photo)
        photo.with_suffix(".XMP").write_text("中文元数据：保留所有内容", encoding="utf-8")
        paths.append(str(photo))
    return paths, destination


@pytest.mark.parametrize("name", ["", ".", "..", "../outside", "a/b", "a\\b", "CON", "con.txt", "LPT1", "a:", "尾部.", "尾部 ", "\0bad"])
def test_new_directory_rejects_paths_and_windows_invalid_names(tmp_path, name):
    with pytest.raises(ValueError):
        native.create_child_directory(tmp_path, name)
    assert list(tmp_path.iterdir()) == []


def test_create_unicode_directory_no_overwrite(tmp_path):
    created = native.create_child_directory(tmp_path, "自定义 鸟类目录")
    assert Path(created).is_dir()
    with pytest.raises(FileExistsError):
        native.create_child_directory(tmp_path, "自定义 鸟类目录")


def test_collect_strict_sidecars_deduplicate_raw_jpeg_and_same_folder(photos):
    paths, destination = photos
    raw = Path(paths[0]).with_suffix(".ARW")
    raw.write_bytes(b"RAW fixture")
    Path(paths[0]).parent.joinpath("鸟.XMP").write_text("unrelated", encoding="utf-8")
    batch = native.transfer_paths_with_sidecars([*paths, str(raw), paths[0]], destination)
    assert len(batch) == 5
    assert sum(Path(p).suffix.lower() == ".xmp" for p in batch) == 2
    assert native.transfer_paths_with_sidecars(paths, Path(paths[0]).parent) == ()
    assert len(native.transfer_paths_with_sidecars(paths, Path(paths[0]).parent, copy=True)) == 4
    with pytest.raises(FileNotFoundError):
        native.transfer_paths_with_sidecars([str(destination / "missing.jpg")], destination)


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


@pytest.mark.parametrize("copy,aborted,code", [(False, False, 0), (True, False, 0), (False, True, 0), (False, False, 5)])
def test_windows_shell_batch_flags_and_cancel(monkeypatch, photos, copy, aborted, code):
    paths, destination = photos
    expanded = native.transfer_paths_with_sidecars(paths, destination)
    calls = []

    def operation(pointer):
        value = pointer._obj
        assert value.wFunc == (2 if copy else 1)
        assert value.fFlags == 0x40  # 系统撤销；不使用静默、自动覆盖、自动改名。
        # 读取包含双 NUL 的完整路径列表，而非 LPCWSTR 自动截到第一项的值。
        address = ctypes.c_void_p.from_address(ctypes.addressof(value) + type(value).pFrom.offset).value
        raw = ctypes.wstring_at(address, sum(len(p) + 1 for p in expanded) + 1)
        assert raw == "\0".join(expanded) + "\0\0"
        value.fAnyOperationsAborted = aborted
        calls.append(value.pTo)
        return code

    com = []
    library = SimpleNamespace(SHFileOperationW=Function(operation),
                              CoInitializeEx=Function(lambda *a: com.append("init") or 0),
                              CoUninitialize=Function(lambda: com.append("close")))
    monkeypatch.setattr(native.ctypes, "WinDLL", lambda *a, **k: library, raising=False)
    result = native._windows_transfer(expanded, str(destination), copy, 0)
    assert calls == [str(destination)]
    assert result.cancelled == aborted
    assert bool(result.error) == bool(code)
    assert com == ["init", "close"]


@pytest.mark.parametrize("reply_code", [0, -128, -1743, -43])
def test_finder_event_flags_destination_and_cleanup(monkeypatch, photos, reply_code):
    paths, destination = photos
    calls, allocated, disposed = [], [], []

    class FakeEvents:
        check = staticmethod(native._FinderEvents.check)
        def __init__(self):
            self.api = SimpleNamespace(
                AECreateList=lambda *a: 0,
                AEPutDesc=lambda *a: 0,
                AECreateAppleEvent=lambda *a: calls.append(("create", a[0], a[1])) or 0,
                AEPutParamDesc=lambda event, key, desc: calls.append(("parameter", key)) or 0,
                AESendMessage=lambda event, reply, flags, timeout: calls.append(("send", flags, timeout)) or 0,
                AEGetParamPtr=self.get_error)
        def data(self, kind, data):
            calls.append((kind, data))
            return self.new()
        def new(self):
            desc = native._AEDesc()
            allocated.append(desc)
            return desc
        def close(self):
            disposed.extend(allocated)
        def get_error(self, event, key, kind, actual, value, size, actual_size):
            value._obj.value = reply_code
            return 0

    monkeypatch.setattr(native, "_FinderEvents", FakeEvents)
    result = native._macos_transfer(paths, str(destination), False, 0)
    assert ("create", native._fourcc("core"), native._fourcc("move")) in calls
    assert ("parameter", native._fourcc("insh")) in calls
    assert ("send", 0x73, -2) in calls
    assert ("furl", destination.as_uri().encode()) in calls
    assert len(disposed) == len(allocated)
    assert result.cancelled == (reply_code == -128)
    assert bool(result.error) == (reply_code not in (0, -128))
    if reply_code == -1743:
        assert "自动化" in result.error


@pytest.fixture
def window(tmp_path, monkeypatch):
    import importlib
    from app_common import superviewer_user_options
    from app_common.file_browser import FileListPanel
    from SuperViewer.superviewer import paths_settings
    from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
    main = importlib.import_module("SuperViewer.main")
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / paths_settings.CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(settings / "state"))
    monkeypatch.setattr(main, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, "get_user_config_dir", lambda: str(settings))
    monkeypatch.setenv("LOCALAPPDATA", str(settings / "cache"))
    tags = settings / "tags.cfg"
    tags.write_text("", encoding="utf-8")
    monkeypatch.setattr(main, "SuperViewerTaggedFileListPanel", lambda: SuperViewerTaggedFileListPanel(tag_config_path=tags))
    monkeypatch.setattr(FileListPanel, "_prompt_create_superpicky_cache_scope", lambda self, count: False)
    window = main.MainWindow(initial_received_files=["skip-restore"])
    window.show()
    yield window
    window.close()
    wait_until(lambda: window._shutdown_finalized)
    window.deleteLater()
    _APP.processEvents()
    from app_common.exif_io.exiftool_path import _is_usable_exiftool
    _is_usable_exiftool.cache_clear()
    assert not warnings, warnings


def select_photos(window, paths, mode):
    files = window._file_list
    files._set_view_mode(files._MODE_LIST if mode == "list" else files._MODE_THUMB)
    files.load_directory(str(Path(paths[0]).parent))
    wait_until(lambda: all(path in files._all_files for path in paths))
    view = files._tree_widget if mode == "list" else files._list_widget
    wait_until(lambda: view.model().rowCount() == len(paths))
    view.selectAll()
    wait_until(lambda: len(files._active_view_selected_paths()) == len(paths))
    return view


def target_item(window, path):
    tree = window._dir_browser._tree
    item = window._dir_browser._make_item(str(path), path.name)
    tree.addTopLevelItem(item)
    tree.scrollToItem(item)
    _APP.processEvents()
    return item, tree.visualItemRect(item).center()


@pytest.mark.parametrize("mode,copy", [("list", False), ("list", True), ("thumbnail", False), ("thumbnail", True)])
def test_real_qt_drop_moves_or_copies_entire_selection_and_xmp(window, photos, monkeypatch, mode, copy):
    paths, destination = photos
    select_photos(window, paths, mode)
    controller = window._directory_files
    calls = []

    def system_transfer(batch, directory, *, copy, owner):
        assert threading.current_thread() is not threading.main_thread()
        calls.append((batch, directory, copy))
        for source in batch:
            (shutil.copy2 if copy else shutil.move)(source, Path(directory) / Path(source).name)
        return native.TransferResult()

    monkeypatch.setattr(ui, "native_transfer", system_transfer)
    mime = controller.create_drag_mime()
    assert len(mime.urls()) == 4
    item, position = target_item(window, destination)
    tree = window._dir_browser._tree
    original = tree.currentItem()
    modifier = (Qt.KeyboardModifier.MetaModifier if os.sys.platform == "darwin" else Qt.KeyboardModifier.ControlModifier) if copy else Qt.KeyboardModifier.NoModifier
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    enter = QDragEnterEvent(position, actions, mime, Qt.MouseButton.LeftButton, modifier)
    QApplication.sendEvent(tree.viewport(), enter)
    assert enter.isAccepted()
    move = QDragMoveEvent(position, actions, mime, Qt.MouseButton.LeftButton, modifier)
    QApplication.sendEvent(tree.viewport(), move)
    assert move.isAccepted() and tree.drop_target.isValid()
    assert tree.currentItem() is original
    drop = QDropEvent(QPointF(position), actions, mime, Qt.MouseButton.LeftButton, modifier)
    QApplication.sendEvent(tree.viewport(), drop)
    assert drop.isAccepted() and drop.dropAction() == (Qt.DropAction.CopyAction if copy else Qt.DropAction.MoveAction)
    controller.clear_drag()
    wait_until(controller.is_shutdown_done)
    assert calls[0][2] == copy and len(calls[0][0]) == 4
    for path in paths:
        assert (destination / Path(path).name).exists()
        assert (destination / Path(path).with_suffix(".XMP").name).read_text(encoding="utf-8") == "中文元数据：保留所有内容"
        assert Path(path).exists() == copy
    if not copy:
        wait_until(lambda: not any(path in window._file_list._all_files for path in paths))


def test_create_directory_menu_preserves_current_selection(window, tmp_path, monkeypatch):
    parent = tmp_path / "右键目录"
    parent.mkdir()
    item, _ = target_item(window, parent)
    original = window._dir_browser._tree.currentItem()
    selected = []
    window._dir_browser.directory_selected.connect(selected.append)
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: ("我的鸟类", True))
    menu = QMenu()
    window._directory_files.extend_directory_menu(menu, str(parent))
    menu.actions()[0].trigger()
    assert (parent / "我的鸟类").is_dir()
    assert item.child(0).text(0) == "我的鸟类"
    assert window._dir_browser._tree.currentItem() is original and not selected
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: ("取消", False))
    menu.actions()[0].trigger()
    assert not (parent / "取消").exists()


@pytest.mark.parametrize("mode", ["list", "thumbnail"])
def test_mouse_drag_preserves_multiple_selection(window, photos, monkeypatch, mode):
    paths, _ = photos
    view = select_photos(window, paths, mode)
    payloads = []
    class Drag:
        def __init__(self, parent):
            self.cursors = {}
        def setMimeData(self, mime):
            self.mime = mime
        def setDragCursor(self, pixmap, action):
            self.cursors[action] = pixmap
        def exec(self, actions, default):
            payloads.append([u.toLocalFile() for u in self.mime.urls()])
            assert default == Qt.DropAction.MoveAction
            assert set(self.cursors) == {Qt.DropAction.CopyAction, Qt.DropAction.MoveAction}
            assert all(not pixmap.isNull() for pixmap in self.cursors.values())
            return Qt.DropAction.IgnoreAction
        def deleteLater(self):
            pass
    monkeypatch.setattr(views, "QDrag", Drag)
    index = view.model().index(0, 0)
    view.scrollTo(index)
    _APP.processEvents()
    pos = view.visualRect(index).center()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    moved = pos + QPoint(45, 0)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(moved), QPointF(view.viewport().mapToGlobal(moved)),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), event)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=moved)
    assert len(payloads) == 1 and len(payloads[0]) == 4


@pytest.mark.parametrize("theme,point_size", [("light", 10), ("dark", 16)])
def test_real_new_directory_dialog_and_drop_target_render(window, photos, monkeypatch, theme, point_size):
    from SuperViewer.superviewer.ui_theme import build_palette
    paths, destination = photos
    select_photos(window, paths, "list")
    controller = window._directory_files
    tree = window._dir_browser._tree
    item, position = target_item(window, destination)
    window.setPalette(build_palette(theme))
    window._dir_browser.apply_theme(theme)
    mime = controller.create_drag_mime()
    tree._update_target(position)
    original = tree.currentItem()
    qa = os.environ.get("SUPERBIRD_DIRECTORY_DRAG_QA")
    if qa:
        Path(qa).mkdir(parents=True, exist_ok=True)
        assert window._dir_browser.grab().save(str(Path(qa) / f"directory-drop-{theme}.png"))
    tree.clear_drop_target()
    errors = []
    def fill_dialog():
        try:
            dialog = _APP.activeModalWidget()
            assert isinstance(dialog, ui.QInputDialog)
            dialog.setPalette(build_palette(theme))
            font = dialog.font(); font.setPointSize(point_size); dialog.setFont(font)
            dialog.setTextValue("自定义 中文子目录")
            dialog.adjustSize()
            _APP.processEvents()
            if qa:
                assert dialog.grab().save(str(Path(qa) / f"directory-create-{theme}.png"))
            dialog.accept()
        except Exception as exc:
            errors.append(exc)
            if _APP.activeModalWidget():
                _APP.activeModalWidget().reject()
    QTimer.singleShot(0, fill_dialog)
    controller.create_directory(str(destination))
    assert not errors
    assert (destination / "自定义 中文子目录").is_dir()
    assert tree.currentItem() is original


def test_cancel_partial_completion_refreshes_only_gone_sources(window, photos, monkeypatch):
    paths, destination = photos
    select_photos(window, paths, "list")
    controller = window._directory_files
    moved = []
    controller.photos_moved.connect(moved.extend)
    def partial(batch, directory, **kwargs):
        for source in batch[:2]:
            shutil.move(source, Path(directory) / Path(source).name)
        return native.TransferResult(cancelled=True)
    monkeypatch.setattr(ui, "native_transfer", partial)
    mime = controller.create_drag_mime()
    assert controller.submit_drop(mime, str(destination), False)
    wait_until(controller.is_shutdown_done)
    assert moved == [paths[0]]
    assert not Path(paths[0]).exists() and Path(paths[1]).exists()
    assert Path(paths[1]).with_suffix(".XMP").exists()


def test_shutdown_retains_worker_and_rejects_stale_external_drag(window, photos, monkeypatch):
    paths, destination = photos
    select_photos(window, paths, "thumbnail")
    controller = window._directory_files
    entered, release = threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return native.TransferResult(cancelled=True)
    monkeypatch.setattr(ui, "native_transfer", delayed)
    mime = controller.create_drag_mime()
    assert not controller.accepts_drag(QMimeData())
    assert controller.submit_drop(mime, str(destination), False)
    wait_until(entered.is_set)
    worker = controller._worker
    try:
        controller.request_shutdown()
        assert not controller.is_shutdown_done()
        assert not controller.submit_drop(mime, str(destination), True)
        assert worker.isInterruptionRequested()
    finally:
        release.set()
    wait_until(controller.is_shutdown_done)


def test_resolved_source_and_report_scope_survive_directory_change(window, photos, monkeypatch, tmp_path):
    paths, destination = photos
    select_photos(window, paths, "list")
    files, controller = window._file_list, window._directory_files
    stale = str(tmp_path / "stale-report-path.jpg")
    monkeypatch.setattr(files, "_active_view_selected_paths", lambda: [stale])
    monkeypatch.setattr(files, "_resolve_source_path_for_action", lambda p: paths[0] if p == stale else p)
    monkeypatch.setattr(files, "get_report_row_for_path", lambda p: {"scope": "original-report"})
    monkeypatch.setattr(files, "_report_scope_path_key", lambda row: row["scope"])
    recorded = []
    monkeypatch.setattr(files, "_delete_report_rows_for_paths", lambda *a, **k: recorded.append((a, k)))
    mime = controller.create_drag_mime()
    # 发起拖动后修改侧车：worker 必须操作当前侧车，不复制内存中的旧元数据。
    sidecar = Path(paths[0]).with_suffix(".XMP")
    sidecar.write_text("拖放前最新中文备注", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    def delayed(batch, directory, **kwargs):
        entered.set()
        assert release.wait(10)
        for path in batch:
            shutil.move(path, Path(directory) / Path(path).name)
        return native.TransferResult()
    monkeypatch.setattr(ui, "native_transfer", delayed)
    assert controller.submit_drop(mime, str(destination), False)
    wait_until(entered.is_set)
    try:
        monkeypatch.setattr(files, "get_report_row_for_path", lambda p: {"scope": "later-report"})
        monkeypatch.setattr(files, "get_current_dir", lambda: str(tmp_path / "unrelated"))
    finally:
        release.set()
    wait_until(controller.is_shutdown_done)
    assert recorded == [(([stale],), {"resolved_paths": [paths[0]], "scope_keys": ["original-report"]})]
    assert (destination / sidecar.name).read_text(encoding="utf-8") == "拖放前最新中文备注"


def test_default_shared_browser_does_not_enable_dragging():
    from app_common.file_browser._panel import FileListPanel
    from app_common.file_browser._directory_browser import DirectoryBrowserWidget
    from app_common.file_browser._models import FileTableView
    from PyQt6.QtWidgets import QListView, QTreeWidget
    assert FileListPanel.file_table_view_class is FileTableView
    assert FileListPanel.thumbnail_view_class is QListView
    assert DirectoryBrowserWidget.directory_tree_class is QTreeWidget


@pytest.mark.parametrize("platform,modifier,copy", [
    ("darwin", Qt.KeyboardModifier.MetaModifier, True),
    ("darwin", Qt.KeyboardModifier.ControlModifier, False),
    ("win32", Qt.KeyboardModifier.ControlModifier, True),
    ("win32", Qt.KeyboardModifier.NoModifier, False)])
def test_physical_ctrl_means_copy(monkeypatch, platform, modifier, copy):
    monkeypatch.setattr(views.sys, "platform", platform)
    assert views.physical_ctrl_pressed(modifier) == copy
