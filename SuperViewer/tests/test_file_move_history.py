"""移动历史在真实临时文件和 Qt 动作上的往返、部分完成与安全边界。"""
from pathlib import Path
import os
import shutil
import threading

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMenu

from SuperViewer.superviewer import directory_file_controller as ui
from SuperViewer.superviewer import file_move_history as journal
from SuperViewer.tests.test_directory_file_transfer import (
    _APP, photos, window as transfer_window, select_photos, wait_until,
)


@pytest.fixture
def window(transfer_window, monkeypatch):
    # The Python native-backend substitute uses immediate rename/unlink.
    # A Windows image-read handle can make shutil.move fall back to copy/unlink
    # and change a same-volume file ID. Keep these history-focused tests clear
    # of unrelated preview/metadata reads while retaining real Qt selection,
    # directory loading, metadata file-operation barriers and filesystem moves.
    monkeypatch.setattr(transfer_window._file_list, "_start_metadata_loader", lambda *_: None)
    transfer_window._file_list.file_selected.disconnect()
    return transfer_window


@pytest.fixture(autouse=True)
def unexpected_warnings_fail_instead_of_opening_a_modal(monkeypatch):
    """An unexpected replay error must fail headless tests, never hang them."""
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    yield
    assert not warnings, warnings


def system_transfer(paths, destination, *, copy=False, owner=0):
    for path in paths:
        (shutil.copy2 if copy else shutil.move)(path, Path(destination) / Path(path).name)
    return ui.TransferResult()


def move(window, paths, destination, monkeypatch, backend=system_transfer):
    monkeypatch.setattr(ui, "native_transfer", backend)
    select_photos(window, paths, "list")
    controller = window._directory_files
    assert controller.submit_drop(controller.create_drag_mime(), str(destination), False)
    wait_until(controller.is_shutdown_done)
    controller.clear_drag()
    return controller


@pytest.mark.parametrize("mode", ["list", "thumbnail"])
def test_menu_round_trip_chinese_xmp_and_directory_switch(window, photos, monkeypatch, tmp_path, mode):
    paths, destination = photos
    contents = {path: Path(path).read_bytes() for path in paths}
    controller = move(window, paths, destination, monkeypatch)
    window._file_list._set_view_mode(window._file_list._MODE_THUMB if mode == "thumbnail" else window._file_list._MODE_LIST)
    window._file_list.load_directory(str(destination))
    wait_until(lambda: window._file_list.get_current_dir() == str(destination))
    actions = window._move_history_actions
    assert actions.undo_action.isEnabled() and not actions.redo_action.isEnabled()
    menu = QMenu(window)
    controller.extend_directory_menu(menu, str(destination))
    assert menu.actions()[-2:] == [actions.undo_action, actions.redo_action]
    window.menuBar().clear(); window._init_menu_bar()
    edit = next(a.menu() for a in window.menuBar().actions() if a.text() == "编辑")
    assert edit.actions()[-2:] == menu.actions()[-2:]
    actions.undo_action.trigger()
    assert not actions.undo_action.isEnabled() and not actions.redo_action.isEnabled()
    wait_until(controller.is_shutdown_done)
    assert not controller.can_undo and controller.can_redo
    for path in paths:
        assert Path(path).read_bytes() == contents[path]
        assert Path(path).with_suffix(".XMP").read_text(encoding="utf-8") == "中文元数据：保留所有内容"
        assert not (destination / Path(path).name).exists()
    actions.redo_action.trigger()
    wait_until(controller.is_shutdown_done)
    for path in paths:
        assert not Path(path).exists()
        assert (destination / Path(path).name).read_bytes() == contents[path]
        assert (destination / Path(path).with_suffix(".XMP").name).is_file()
    assert controller.can_undo and not controller.can_redo


def test_multiple_batches_undo_redo_and_new_move_clears_redo(window, photos, monkeypatch, tmp_path):
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    paths, destination = photos
    controller = move(window, paths, destination, monkeypatch)
    target2 = tmp_path / "第三目录"; target2.mkdir()
    paths2 = [str(destination / Path(path).name) for path in paths]
    move(window, paths2, target2, monkeypatch)
    assert len(controller._history.undo) == 2
    controller.undo(); wait_until(controller.is_shutdown_done)
    controller.undo(); wait_until(controller.is_shutdown_done)
    assert all(Path(path).exists() for path in paths)
    assert len(controller._history.redo) == 2
    controller.redo(); wait_until(controller.is_shutdown_done)
    assert all(Path(path).exists() for path in paths2)
    assert len(controller._history.redo) == 1
    move(window, paths2, target2, monkeypatch)
    assert not controller.can_redo
    assert not warnings


def test_cross_volume_round_trips_rebind_older_history_file_ids(window, photos, monkeypatch, tmp_path):
    from dataclasses import replace
    paths, destination = photos
    original_read = journal.FileIdentity.read
    def cross_volume_identity(cls, path, **kwargs):
        identity = original_read(path, **kwargs)
        return replace(identity, digest=journal._digest(path)) if kwargs.get("for_directory") else identity
    monkeypatch.setattr(journal.FileIdentity, "read", classmethod(cross_volume_identity))
    def cross_volume_transfer(batch, directory, **kwargs):
        for path in batch:
            shutil.copy2(path, Path(directory) / Path(path).name)
            Path(path).unlink()
        return ui.TransferResult()
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    controller = move(window, paths, destination, monkeypatch, backend=cross_volume_transfer)
    destination2 = tmp_path / "第二磁盘"; destination2.mkdir()
    paths2 = [str(destination / Path(path).name) for path in paths]
    move(window, paths2, destination2, monkeypatch, backend=cross_volume_transfer)
    for _ in range(2):
        controller.undo(); wait_until(controller.is_shutdown_done)
    assert not warnings and all(Path(path).exists() for path in paths)
    for _ in range(2):
        controller.redo(); wait_until(controller.is_shutdown_done)
    assert not warnings and all((destination2 / Path(path).name).exists() for path in paths)


def test_partial_cancel_retains_retry_and_redo_completed_subset(window, photos, monkeypatch):
    paths, destination = photos
    controller = move(window, paths, destination, monkeypatch)
    calls = []
    def partial(batch, directory, **kwargs):
        calls.append(batch)
        system_transfer(batch[:2], directory, **kwargs)
        return ui.TransferResult(cancelled=True)
    monkeypatch.setattr(ui, "native_transfer", partial)
    controller.undo(); wait_until(controller.is_shutdown_done)
    assert Path(paths[0]).exists() and Path(paths[0]).with_suffix(".XMP").exists()
    assert not Path(paths[1]).exists()
    assert controller.can_undo and controller.can_redo
    monkeypatch.setattr(ui, "native_transfer", system_transfer)
    controller.undo(); wait_until(controller.is_shutdown_done)
    assert all(Path(path).exists() for path in paths)
    assert not controller.can_undo
    controller.redo(); wait_until(controller.is_shutdown_done)
    controller.redo(); wait_until(controller.is_shutdown_done)
    assert all((destination / Path(path).name).exists() for path in paths)


@pytest.mark.parametrize("change", ["occupied", "missing", "replaced", "directory_missing"])
def test_replay_validation_never_moves_unrelated_files_and_is_retryable(window, photos, monkeypatch, change):
    paths, destination = photos
    controller = move(window, paths, destination, monkeypatch)
    warnings, calls = [], []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    monkeypatch.setattr(ui, "native_transfer", lambda *a, **kw: calls.append(a))
    target = destination / Path(paths[0]).name
    if change == "occupied":
        Path(paths[0]).write_bytes(b"unrelated")
    elif change == "directory_missing":
        Path(paths[0]).parent.rmdir()
    else:
        target.rename(target.with_suffix(".backup"))
        if change == "replaced":
            target.write_bytes(b"unrelated")
    controller.undo(); wait_until(controller.is_shutdown_done)
    assert not calls and warnings and controller.can_undo and not controller.can_redo
    if change == "occupied":
        assert Path(paths[0]).read_bytes() == b"unrelated"
        Path(paths[0]).unlink()
        monkeypatch.setattr(ui, "native_transfer", system_transfer)
        controller.undo(); wait_until(controller.is_shutdown_done)
        assert not controller.can_undo and controller.can_redo


def test_xmp_updates_and_new_sidecar_follow_photo_without_restoring_old_bytes(window, photos, monkeypatch):
    paths, destination = photos
    Path(paths[1]).with_suffix(".XMP").unlink()
    controller = move(window, paths, destination, monkeypatch)
    first_xmp = destination / Path(paths[0]).with_suffix(".XMP").name
    # 模拟元数据写入的原子替换，文件 ID 会改变。
    temporary = destination / "updated.tmp"
    temporary.write_text("更新中文元数据", encoding="utf-8"); temporary.replace(first_xmp)
    (destination / Path(paths[1]).with_suffix(".xmp").name).write_text("新建侧车", encoding="utf-8")
    controller.undo(); wait_until(controller.is_shutdown_done)
    assert Path(paths[0]).with_suffix(".XMP").read_text(encoding="utf-8") == "更新中文元数据"
    assert Path(paths[1]).with_suffix(".xmp").read_text(encoding="utf-8") == "新建侧车"
    controller.redo(); wait_until(controller.is_shutdown_done)
    assert first_xmp.read_text(encoding="utf-8") == "更新中文元数据"


def test_copy_noop_and_failed_move_do_not_clear_redo(window, photos, monkeypatch):
    paths, destination = photos
    controller = move(window, paths, destination, monkeypatch)
    controller.undo(); wait_until(controller.is_shutdown_done)
    select_photos(window, paths, "list")
    assert controller.submit_drop(controller.create_drag_mime(), str(Path(paths[0]).parent), False)
    wait_until(controller.is_shutdown_done)
    assert controller.can_redo and not controller.can_undo
    monkeypatch.setattr(ui, "native_transfer", lambda *a, **kw: ui.TransferResult(cancelled=True))
    assert controller.submit_drop(controller.create_drag_mime(), str(destination), False)
    wait_until(controller.is_shutdown_done)
    assert controller.can_redo
    monkeypatch.setattr(ui, "native_transfer", system_transfer)
    select_photos(window, paths, "list")
    assert controller.submit_drop(controller.create_drag_mime(), str(destination), True)
    wait_until(controller.is_shutdown_done)
    assert controller.can_redo and not controller.can_undo


@pytest.mark.parametrize("conflict", ["overwrite", "rename"])
def test_conflict_results_are_not_presented_as_fully_reversible(tmp_path, conflict):
    source, dest = tmp_path / "src", tmp_path / "dst"
    source.mkdir(); dest.mkdir()
    photo = source / "鸟.jpg"; photo.write_bytes(b"moved")
    target = dest / photo.name
    if conflict == "overwrite":
        target.write_bytes(b"previous destination")
    snapshots = journal.snapshot_move([str(photo)], str(dest))
    photo.replace(target if conflict == "overwrite" else dest / "鸟 2.jpg")
    inverse, untracked = journal.completed_moves(snapshots)
    assert not inverse and untracked == (str(photo),)


def test_cross_volume_identity_uses_content_and_rejects_wrong_destination(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"original")
    # 用独立文件模拟跨卷 copy + delete；在跨卷预快照中具有摘要。
    identity = journal.FileIdentity(0, 0, journal._digest(source))
    shutil.copy2(source, target)
    assert identity.matches(target)
    target.write_bytes(b"unrelated")
    assert not identity.matches(target)


def test_overwrite_never_leaves_only_xmp_as_an_undoable_group(window, photos, monkeypatch):
    paths, destination = photos
    (destination / Path(paths[0]).name).write_bytes(b"old destination")
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    controller = move(window, paths, destination, monkeypatch)
    assert warnings and "2 个" in warnings[0] and "未加入撤销历史" in warnings[0]
    assert len(controller._history.undo[-1]) == 2  # 另一张照片及其 XMP。
    controller.undo(); wait_until(controller.is_shutdown_done)
    assert not Path(paths[0]).exists() and not Path(paths[0]).with_suffix(".XMP").exists()
    assert Path(paths[1]).exists() and Path(paths[1]).with_suffix(".XMP").exists()


def test_shutdown_cancels_preparation_before_any_native_operation(tmp_path, monkeypatch):
    source, destination = tmp_path / "photo.jpg", tmp_path / "target"
    source.write_bytes(b"photo"); destination.mkdir()
    calls = []
    monkeypatch.setattr(ui, "native_transfer", lambda *a, **kw: calls.append(a))
    worker = ui.NativeTransferWorker([str(source)], str(destination), False, 0)
    monkeypatch.setattr(worker, "isInterruptionRequested", lambda: True)
    worker.run()
    assert worker.result.cancelled and not calls and source.exists() and not worker.inverse


def test_hash_preparation_can_be_cancelled(tmp_path):
    source = tmp_path / "photo.raw"; source.write_bytes(b"pixels")
    with pytest.raises(InterruptedError):
        journal._digest(source, lambda: True)


def test_same_destination_name_in_one_batch_never_guesses_which_source_survived(tmp_path):
    destination = tmp_path / "destination"; destination.mkdir()
    paths = []
    for name in ("A", "B"):
        directory = tmp_path / name; directory.mkdir()
        photo = directory / "same.jpg"; photo.write_bytes(b"identical pixels")
        paths.append(str(photo))
    snapshots = journal.snapshot_move(paths, str(destination))
    for path in paths:
        Path(path).replace(destination / "same.jpg")
    inverse, untracked = journal.completed_moves(snapshots)
    assert not inverse and set(untracked) == set(paths)


def test_shared_raw_jpeg_xmp_and_multiple_source_directories(tmp_path, monkeypatch):
    dest = tmp_path / "target"; dest.mkdir()
    paths = []
    for name in ("A", "B"):
        directory = tmp_path / name; directory.mkdir()
        for extension in (".arw", ".jpg", ".xmp"):
            photo = directory / (name + extension); photo.write_bytes((name + extension).encode())
            if extension != ".xmp":
                paths.append(str(photo))
    monkeypatch.setattr(ui, "native_transfer", system_transfer)
    worker = ui.NativeTransferWorker(paths, str(dest), False, 0); worker.run()
    assert len(worker.inverse) == 6
    undo = ui.MoveReplayWorker(worker.inverse, "undo"); undo.run()
    assert not undo.result.error and not undo.remaining
    assert all(Path(p).exists() for p in paths)
    assert len(undo.inverse) == 6


def test_exception_after_partial_system_move_keeps_both_history_halves(tmp_path, monkeypatch):
    source, destination = tmp_path / "src", tmp_path / "dst"
    source.mkdir(); destination.mkdir()
    paths = [str(source / f"bird{i}.jpg") for i in range(2)]
    for path in paths:
        Path(path).write_bytes(b"photo")
    monkeypatch.setattr(ui, "native_transfer", system_transfer)
    worker = ui.NativeTransferWorker(paths, str(destination), False, 0); worker.run()
    def interrupted(batch, directory, **kwargs):
        system_transfer(batch[:1], directory, **kwargs)
        raise OSError("模拟系统调用部分完成后失败")
    monkeypatch.setattr(ui, "native_transfer", interrupted)
    undo = ui.MoveReplayWorker(worker.inverse, "undo"); undo.run()
    assert undo.result.error
    assert len(undo.inverse) == len(undo.remaining) == 1
    assert Path(paths[0]).exists() and not Path(paths[1]).exists()


def test_undo_restores_report_cache_without_writing_database(window, photos, monkeypatch, tmp_path):
    from app_common.report_db import ReportDB
    paths, destination = photos
    root = Path(paths[0]).parent
    db = ReportDB(str(root))
    try:
        for path in paths:
            db.insert_photo({"filename": Path(path).stem,
                             "original_path": "DCIM/" + Path(path).name,
                             "current_path": Path(path).name,
                             "bird_species_cn": "白鹭"})
    finally:
        db.close()
    database = root / ".superpicky/report.db"
    before = database.read_bytes()
    controller = move(window, paths, destination, monkeypatch)
    files = window._file_list
    source_identity = (journal.path_key(root), journal.path_key(paths[0]))
    unrelated = (journal.path_key(root), journal.path_key(root / "另一个目录" / Path(paths[0]).name))
    files._report_deleted_path_tombstones.add(unrelated)
    assert source_identity in files._report_deleted_path_tombstones
    controller.undo(); wait_until(controller.is_shutdown_done)
    wait_until(lambda: files.get_report_row_for_path(paths[0]) is not None)
    assert source_identity not in files._report_deleted_path_tombstones
    assert unrelated in files._report_deleted_path_tombstones
    assert files.get_report_row_for_path(paths[0])["bird_species_cn"] == "白鹭"
    assert database.read_bytes() == before
    controller.redo(); wait_until(controller.is_shutdown_done)
    assert source_identity in files._report_deleted_path_tombstones
    assert database.read_bytes() == before


def test_replay_shutdown_retains_worker_and_disables_actions(window, photos, monkeypatch):
    paths, destination = photos
    controller = move(window, paths, destination, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        entered.set(); release.wait(10)
        return ui.TransferResult(cancelled=True)
    monkeypatch.setattr(ui, "native_transfer", delayed)
    controller.undo(); wait_until(entered.is_set)
    try:
        controller.request_shutdown()
        assert not controller.is_shutdown_done()
        assert not controller.can_undo and not controller.can_redo
        assert controller.create_drag_mime() is None
    finally:
        release.set(); wait_until(controller.is_shutdown_done)


def test_move_history_is_bounded_and_noop_preserves_redo():
    history = journal.MoveHistory(limit=100)
    def entry(index):
        return journal.MoveEntry(f"source{index}", f"target{index}", journal.FileIdentity(1, index))
    for i in range(105):
        history.record((entry(i),))
    assert len(history.undo) == 100 and history.undo[0] == (entry(5),)
    history.finish("undo", (entry(104),), ())
    history.record(())
    assert history.redo[-1] == (entry(104),)


@pytest.mark.parametrize("theme,font_size", [("light", 10), ("dark", 16)])
def test_history_menu_render_and_keyboard_activation(window, photos, monkeypatch, theme, font_size):
    from SuperViewer.superviewer.ui_theme import build_palette
    paths, destination = photos
    controller = move(window, paths, destination, monkeypatch)
    menu = QMenu(window)
    menu.setPalette(build_palette(theme))
    font = QFont(menu.font()); font.setPointSize(font_size); menu.setFont(font)
    controller.extend_directory_menu(menu, str(destination))
    menu.popup(QPoint(10, 10)); _APP.processEvents()
    actions = window._move_history_actions
    for action in (actions.undo_action, actions.redo_action):
        rect = menu.actionGeometry(action)
        assert rect.height() >= menu.fontMetrics().height()
        assert rect.width() > menu.fontMetrics().horizontalAdvance(action.text())
    qa = os.environ.get("SUPERBIRD_MOVE_HISTORY_QA")
    if qa:
        Path(qa).mkdir(parents=True, exist_ok=True)
        assert menu.grab().save(str(Path(qa) / f"move-history-{theme}.png"))
    menu.setActiveAction(actions.undo_action)
    QTest.keyClick(menu, Qt.Key.Key_Return)
    wait_until(controller.is_shutdown_done)
    assert controller.can_redo and all(Path(p).exists() for p in paths)
    menu.close()
