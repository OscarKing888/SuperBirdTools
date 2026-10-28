"""目录重命名：真实文件/XMP、目录树状态及编辑保护。"""
import os
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QInputDialog, QMenu

from SuperViewer.superviewer import directory_file_controller as ui
from SuperViewer.superviewer import native_file_transfer as native
from SuperViewer.superviewer.photo_edit_controller import source_key
from test_directory_file_transfer import photos, select_photos, target_item, wait_until, window

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("name", ["", ".", "..", "../other", "a/b", "a\\b", "CON", "bad.", "bad ", "a:b"])
def test_rename_rejects_invalid_names(tmp_path, name):
    source = tmp_path / "原目录"
    source.mkdir()
    with pytest.raises(ValueError):
        native.rename_directory(source, name)
    assert source.is_dir()


def test_rename_conflict_noop_and_case_only(tmp_path):
    source = tmp_path / "Birds"
    source.mkdir()
    (source / "鸟.xmp").write_text("中文元数据", encoding="utf-8")
    (tmp_path / "已有目录").mkdir()
    (tmp_path / "已有文件").write_bytes(b"untouched")
    for name in ("已有目录", "已有文件"):
        with pytest.raises(FileExistsError):
            native.rename_directory(source, name)
    assert native.rename_directory(source, source.name) == str(source)
    renamed = Path(native.rename_directory(source, "BIRDS"))
    assert renamed.name in [p.name for p in tmp_path.iterdir()]
    assert (renamed / "鸟.xmp").read_text(encoding="utf-8") == "中文元数据"


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive rename rollback")
def test_case_only_failure_restores_original(tmp_path, monkeypatch):
    source = tmp_path / "Birds"
    source.mkdir()
    real_rename = native.os.rename
    def rename(old, new):
        if Path(new).name == "BIRDS":
            raise PermissionError("目标不可写")
        real_rename(old, new)
    monkeypatch.setattr(native.os, "rename", rename)
    with pytest.raises(PermissionError):
        native.rename_directory(source, "BIRDS")
    assert [p.name for p in tmp_path.iterdir()] == ["Birds"]


def test_permission_failure_preserves_photo_and_sidecar(tmp_path, monkeypatch):
    source = tmp_path / "照片目录"
    source.mkdir()
    (source / "鸟.jpg").write_bytes(b"original")
    (source / "鸟.xmp").write_text("中文元数据", encoding="utf-8")
    def denied(*args):
        raise PermissionError("目录不可写")
    monkeypatch.setattr(native.os, "rename", denied)
    with pytest.raises(PermissionError):
        native.rename_directory(source, "新目录")
    assert (source / "鸟.jpg").read_bytes() == b"original"
    assert (source / "鸟.xmp").read_text(encoding="utf-8") == "中文元数据"


@pytest.mark.parametrize("mode,scope", [("list", "self"), ("thumbnail", "parent"), ("list", "descendant"), ("thumbnail", "unrelated")])
def test_menu_rename_preserves_tree_and_reloads_affected_directory(window, photos, monkeypatch, mode, scope):
    paths, destination = photos
    source = Path(paths[0]).parent
    if scope == "descendant":
        parent = source.parent / "父文件夹"
        parent.mkdir()
        source.rename(parent / source.name)
        paths = [str(parent / source.name / Path(p).name) for p in paths]
        source = parent
    select_photos(window, paths, mode)
    files, browser = window._file_list, window._dir_browser
    item, _ = target_item(window, source)
    browser._ensure_children_loaded(item)
    item.setExpanded(True)
    current = item
    if scope == "descendant":
        current = item.child(0)
        browser._ensure_children_loaded(current)
        current.setExpanded(True)
    if scope == "unrelated":
        item, _ = target_item(window, destination)
        source = destination
    browser._tree.setCurrentItem(current)
    original_dir = files.get_current_dir()
    if scope == "parent":
        window._on_directory_selected(str(source.parent), force_reload=True)
        wait_until(lambda: all(p in files._all_files for p in paths))
        original_dir = files.get_current_dir()
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: ("重命名 鸟类", True))
    menu = QMenu()
    window._directory_files.extend_directory_menu(menu, str(source))
    action = next(a for a in menu.actions() if a.text() == "重命名文件夹…")
    assert action.isEnabled()
    menu.popup(QPoint(20, 20))
    menu.setActiveAction(action)
    QTest.keyClick(menu, Qt.Key.Key_Return)
    target = source.with_name("重命名 鸟类")
    wait_until(lambda: not window._directory_files._rename_pending)
    assert target.is_dir() and not source.exists()
    assert item.text(0) == target.name
    assert item.data(0, Qt.ItemDataRole.UserRole) == str(target)
    assert browser._tree.currentItem() is current
    if scope != "unrelated":
        assert item.isExpanded()
        new_paths = [str(target / Path(p).relative_to(source)) for p in paths]
        wait_until(lambda: all(p in files._all_files for p in new_paths))
        for path in new_paths:
            assert Path(path).with_suffix(".XMP").read_text(encoding="utf-8") == "中文元数据：保留所有内容"
        expected_dir = str(target / Path(original_dir).relative_to(source)) if scope != "parent" else original_dir
        assert files.get_current_dir() == expected_dir
        if scope == "descendant":
            assert current.data(0, Qt.ItemDataRole.UserRole) == expected_dir
            assert current.isExpanded()
    else:
        assert files.get_current_dir() == original_dir
        assert all(Path(p).exists() for p in paths)


def test_cancel_retry_busy_shutdown_and_edit_guard(window, photos, monkeypatch):
    paths, _ = photos
    source = Path(paths[0]).parent
    controller = window._directory_files
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: warnings.append(a))
    answers = iter([("已有", True), ("../非法", True), ("取消", False)])
    (source.parent / "已有").mkdir()
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: next(answers))
    controller.rename_directory(str(source))
    wait_until(lambda: not controller._rename_pending)
    assert source.is_dir() and len(warnings) == 2
    editor = window._photo_edit
    editor._loading.add(source_key(paths[0]))  # 不在当前目录列表内的后台编辑也要保护。
    assert source_key(paths[0]) in editor.directory_operation_paths(str(source))
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: ("新名字", True))
    controller.rename_directory(str(source))
    assert source.is_dir() and not (source.parent / "新名字").exists()
    editor._loading.clear()
    controller._worker = object()
    assert not controller.can_rename_directory(str(source))
    controller._worker = None
    controller.request_shutdown()
    assert not controller.can_rename_directory(str(source))
    assert not controller.can_rename_directory(source.anchor)


@pytest.mark.parametrize("report_in_folder", [True, False])
def test_renamed_folder_keeps_report_metadata_readonly(window, photos, monkeypatch, report_in_folder):
    from app_common.report_db import ReportDB
    from app_common.file_browser import _browser_core
    # 临时目录很深；模拟允许读取上级报告的照片库位置。
    monkeypatch.setattr(_browser_core, "_is_within_volume_root_depth", lambda *a: True)
    paths, _ = photos
    source = Path(paths[0]).parent
    root = source if report_in_folder else source.parent
    db = ReportDB(str(root))
    try:
        for path in paths:
            db.insert_photo({"filename": Path(path).stem,
                             "original_path": "DCIM/" + Path(path).name,
                             "current_path": Path(path).relative_to(root).as_posix(),
                             "bird_species_cn": "白鹭"})
    finally:
        db.close()
    before = (root / ".superpicky/report.db").read_bytes()
    select_photos(window, paths, "list")
    assert window._file_list.get_report_row_for_path(paths[0])["bird_species_cn"] == "白鹭"
    target_item(window, source)
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: ("改名目录", True))
    window._directory_files.rename_directory(str(source))
    target = source.with_name("改名目录")
    new_path = str(target / Path(paths[0]).name)
    files = window._file_list
    wait_until(lambda: new_path in files._all_files)
    wait_until(lambda: files.get_report_row_for_path(new_path) is not None)
    assert files.get_report_row_for_path(new_path)["bird_species_cn"] == "白鹭"
    database = (target if report_in_folder else root) / ".superpicky/report.db"
    assert database.read_bytes() == before


@pytest.mark.parametrize("scheme,size", [("light", 10), ("dark", 16)])
def test_native_menu_and_name_dialog_render_and_keyboard(window, photos, tmp_path, scheme, size):
    from SuperViewer.superviewer.ui_theme import build_palette
    paths, _ = photos
    source = Path(paths[0]).parent
    old_palette, old_font = _APP.palette(), _APP.font()
    font = _APP.font()
    font.setPointSize(size)
    _APP.setPalette(build_palette(scheme))
    _APP.setFont(font)
    try:
        window.resize(900, 600)
        menu = QMenu(window)
        window._directory_files.extend_directory_menu(menu, str(source))
        action = next(a for a in menu.actions() if a.text() == "重命名文件夹…")
        menu.popup(QPoint(20, 20))
        menu.setActiveAction(action)
        _APP.processEvents()
        assert menu.rect().contains(menu.actionGeometry(action))
        menu.grab().save(str(tmp_path / f"rename-menu-{scheme}.png"))
        captured = []
        def accept_dialog():
            dialog = _APP.activeModalWidget()
            assert isinstance(dialog, QInputDialog)
            assert dialog.textValue() == source.name
            dialog.grab().save(str(tmp_path / f"rename-dialog-{scheme}.png"))
            captured.append(dialog.size())
            dialog.setTextValue("键盘重命名")
            QTest.keyClick(dialog, Qt.Key.Key_Return)
        QTimer.singleShot(0, accept_dialog)
        QTest.keyClick(menu, Qt.Key.Key_Return)
        wait_until(lambda: not window._directory_files._rename_pending)
        assert captured and source.with_name("键盘重命名").is_dir()
    finally:
        _APP.setFont(old_font)
        _APP.setPalette(old_palette)
