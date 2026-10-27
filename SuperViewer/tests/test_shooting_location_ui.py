"""地点编辑、批量剪贴板、局部刷新及线程关闭的真实 Qt 回归。"""
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QWidget
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.shooting_location import LOCATION_FIELD, LOCATION_TAG, shooting_location
from SuperViewer.superviewer import shooting_location_controller as ui
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
from SuperViewer.superviewer.metadata_edit_sync import sync_saved_xmp_edit
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app_common import superviewer_user_options
    from SuperViewer.superviewer import paths_settings
    monkeypatch.setattr(paths_settings, '_get_app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(paths_settings, '_get_user_state_dir', lambda: str(tmp_path / 'state'))
    monkeypatch.setattr(superviewer_user_options, '_get_app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(superviewer_user_options, 'get_user_config_dir', lambda: str(tmp_path))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'cache'))
    paths = []
    for name in ('白鹭.jpg', '白鹭.png', '苍鹭.jpg'):
        photo = tmp_path / name
        Image.new('RGB', (16, 16)).save(photo)
        paths.append(str(photo))
    config = tmp_path / 'tags.cfg'
    config.write_text('飞行\n', encoding='utf-8')
    files, window = SuperViewerTaggedFileListPanel(tag_config_path=config), QWidget()
    files._all_files = paths
    files._resolve_source_path_for_action = lambda path: path
    controller = ui.ShootingLocationController(window, files)
    yield controller, files, paths
    controller.request_shutdown()
    assert wait_for(controller.is_shutdown_done)
    if controller._dialog:
        controller._dialog.close()
    files.shutdown()
    files.deleteLater()
    window.close()
    window.deleteLater()
    _APP.processEvents()


@pytest.mark.parametrize('mode', ['list', 'thumb'])
def test_edit_copy_paste_batch_and_stale_metadata(env, monkeypatch, mode):
    controller, files, paths = env
    files._view_mode = files._MODE_LIST if mode == 'list' else files._MODE_THUMB
    selected = []
    files.file_selected.connect(selected.append)
    menu = QMenu()
    controller.extend_file_menu(menu, paths[:1])
    actions = menu.actions()
    assert [a.text() for a in actions] == ['修改拍摄地点…', '复制拍摄地点', '粘贴拍摄地点']
    monkeypatch.setattr(ui.QInputDialog, 'getText', lambda *a, **k: ('上海东滩', True))
    actions[0].trigger()
    assert wait_for(controller.is_shutdown_done)
    controller.copy(paths[0])
    assert wait_for(lambda: not controller._copy.busy)
    assert QApplication.clipboard().text() == '上海东滩'
    multi = QMenu()
    controller.extend_file_menu(multi, paths)
    multi_actions = multi.actions()
    assert not multi_actions[1].isEnabled()
    multi_actions[2].trigger()
    assert wait_for(controller.is_shutdown_done)
    assert controller._done == 2  # 同侧车只写一次。
    for path in paths:
        assert shooting_location(PhotoMetaDataXMP().read(path)) == '上海东滩'
        files._on_metadata_batch_ready({path: {LOCATION_FIELD: '旧地点', 'iso': '800'}})
        assert shooting_location(files.cached_photo_metadata_for_path(path)) == '上海东滩'
        assert files._meta_cache[path]['iso'] == '800'
    assert not selected
    assert controller.start(paths, '')
    assert wait_for(controller.is_shutdown_done)
    assert all(shooting_location(PhotoMetaDataXMP().read(p)) == '' for p in paths)


def test_information_editor_preserves_other_drafts_and_failed_location(env, monkeypatch):
    controller, files, paths = env
    panel = ImageInfoTabPanel_ImageInfo(
        lambda: [], lambda p: set(), lambda *a: None, lambda *a: None,
        metadata_provider=files.cached_photo_metadata_for_path, location_save_callback=controller.save_one,
    )
    try:
        panel.on_photo_selected(paths[0])
        panel.comment_edit.setPlainText('尚未提交的备注')
        panel.filename_edit.setText('尚未提交的文件名')
        panel.location_edit.setText('云南·高黎贡山')
        panel.location_edit.editingFinished.emit()
        assert shooting_location(PhotoMetaDataXMP().read(paths[0])) == '云南·高黎贡山'
        assert panel.comment_edit.toPlainText() == '尚未提交的备注'
        assert panel.filename_edit.text() == '尚未提交的文件名'
        panel.location_edit.setText('地点草稿')
        files._meta_cache[paths[0]][LOCATION_TAG] = '后台地点'
        panel.refresh_metadata_fields()
        assert panel.location_edit.text() == '地点草稿'
        errors = []
        monkeypatch.setattr('SuperViewer.superviewer.image_info_tab_image_info.QMessageBox.warning', lambda *a: errors.append(a))
        panel._location_save_callback = lambda *a: False
        panel._commit_location_edit()
        assert errors and panel.location_edit.text() == '地点草稿'
        panel._current_photo_path = paths[-1]
        panel._location_save_callback = lambda *a: pytest.fail('old draft written to another photo')
        panel._commit_location_edit()
    finally:
        panel.deleteLater()
        _APP.processEvents()


def test_partial_failure_keeps_success_and_reports_path(env):
    controller, files, paths = env
    Path(paths[-1]).with_suffix('.xmp').write_text('<broken>', encoding='utf-8')
    assert controller.start(paths, '江苏盐城')
    assert wait_for(controller.is_shutdown_done)
    assert controller._failed == 1
    assert shooting_location(PhotoMetaDataXMP().read(paths[0])) == '江苏盐城'
    assert paths[-1] in controller._dialog.details.toPlainText()
    assert Path(paths[-1]).with_suffix('.xmp').read_text() == '<broken>'


def test_shutdown_waits_for_real_finished_and_rejects_stale_callback(env, monkeypatch):
    controller, files, paths = env
    started, gate = threading.Event(), threading.Event()
    original = ui.save_location
    def save(path, text):
        result = original(path, text)
        started.set()
        assert gate.wait(5)
        return result
    monkeypatch.setattr(ui, 'save_location', save)
    assert controller.start(paths, '北京')
    assert started.wait(5)
    worker = controller._worker
    controller.request_shutdown()
    controller._finished(object())
    assert controller._worker is worker
    assert not controller.is_shutdown_done()
    assert not controller.start(paths, '不应写入')
    gate.set()
    assert wait_for(controller.is_shutdown_done)
    assert shooting_location(PhotoMetaDataXMP().read(paths[0])) == '北京'
    # 已开始的并行写入允许完成，取消只阻止尚未执行的 action。
    assert shooting_location(PhotoMetaDataXMP().read(paths[-1])) in ('', '北京')


def test_exif_location_clear_updates_canonical_cache(env):
    controller, files, paths = env
    controller.save_one(paths[0], '旧地点')
    assert PhotoMetaDataXMP().write(paths[0], {LOCATION_TAG: ''})
    sync_saved_xmp_edit(files, paths[0], LOCATION_TAG)
    assert shooting_location(files.cached_photo_metadata_for_path(paths[0])) == ''


def test_location_actions_share_browser_pool_and_run_concurrently(env, monkeypatch):
    controller, files, paths = env
    original = ui.LocationWriteAction.execute
    barrier, gate = threading.Barrier(2), threading.Event()
    threads = []

    def execute(action):
        threads.append(threading.get_ident())
        barrier.wait(5)
        assert gate.wait(5)
        return original(action)

    monkeypatch.setattr(ui.LocationWriteAction, 'execute', execute)
    assert controller.start(paths, '上海东滩')
    try:
        assert controller._worker.pool is files._browser_work_pool
        assert wait_for(lambda: len(threads) == 2)
        assert len(set(threads)) == 2 and threading.get_ident() not in threads
    finally:
        gate.set()
    assert wait_for(controller.is_shutdown_done)
    assert controller._done == 2
    assert all(shooting_location(PhotoMetaDataXMP().read(path)) == '上海东滩' for path in paths)


def test_immediate_location_paste_waits_for_copy(env, monkeypatch):
    controller, files, paths = env
    controller.save_one(paths[0], '云南·高黎贡山')
    QApplication.clipboard().setText('旧地点')
    original = ui.LocationCopyAction.execute
    gate = threading.Event()

    def execute(action):
        assert gate.wait(5)
        return original(action)

    monkeypatch.setattr(ui.LocationCopyAction, 'execute', execute)
    controller.copy(paths[0])
    controller.paste(paths[-1:])
    assert not Path(paths[-1]).with_suffix('.xmp').exists()
    gate.set()
    assert wait_for(controller.is_shutdown_done)
    assert shooting_location(PhotoMetaDataXMP().read(paths[-1])) == '云南·高黎贡山'


@pytest.mark.parametrize('mode', ['list', 'thumb'])
@pytest.mark.parametrize('recursive', [False, True])
def test_directory_location_scope_ignores_selection_and_filter(env, tmp_path, monkeypatch, mode, recursive):
    controller, files, unselected = env
    files._view_mode = files._MODE_LIST if mode == 'list' else files._MODE_THUMB
    files._current_dir = str(tmp_path)
    files._all_files = unselected[:1]  # Only an unrelated, filtered photo is listed.
    clicked = tmp_path / '右击目录'
    nested = clicked / '子目录'
    nested.mkdir(parents=True)
    photos = [clicked / '白鹭.jpg', clicked / '白鹭.png', nested / '苍鹭.jpg']
    for photo in photos:
        Image.new('RGB', (16, 16)).save(photo)
    (clicked / '视频.mp4').write_bytes(b'video')
    (clicked / '说明.txt').write_text('非照片', encoding='utf-8')
    prompts, selected = [], []
    files.file_selected.connect(selected.append)
    monkeypatch.setattr(ui.QInputDialog, 'getText',
                        lambda *a, **k: (prompts.append(a[2]) or '云南·高黎贡山', True))
    menu = QMenu()
    controller.extend_directory_menu(menu, str(clicked))
    assert menu.actions()[0].text() == '批量修改拍摄地点'
    sub = menu.actions()[0].menu()
    assert [a.text() for a in sub.actions()] == ['仅当前目录…', '目录及子目录…']
    sub.popup(QPoint(20, 20))
    sub.setActiveAction(sub.actions()[int(recursive)])
    QTest.keyClick(sub, Qt.Key.Key_Return)
    switched = str(tmp_path / '切换后的目录')
    files._current_dir = switched
    assert wait_for(lambda: not controller.busy)
    assert len(prompts) == 1
    assert controller._done == (2 if recursive else 1)
    assert all(shooting_location(PhotoMetaDataXMP().read(str(p))) == '云南·高黎贡山' for p in photos[:2])
    assert shooting_location(PhotoMetaDataXMP().read(str(photos[-1]))) == ('云南·高黎贡山' if recursive else '')
    assert not any(Path(p).with_suffix('.xmp').exists() for p in unselected)
    assert not (clicked / '视频.xmp').exists() and not (clicked / '说明.xmp').exists()
    assert files._current_dir == switched and files._all_files == unselected[:1]
    assert not selected


@pytest.mark.parametrize('accepted', [False, True])
def test_directory_location_input_cancel_or_clear(env, tmp_path, monkeypatch, accepted):
    controller, files, paths = env
    controller.save_one(paths[0], '上海东滩')
    old_xmp = Path(paths[0]).with_suffix('.xmp').read_bytes()
    monkeypatch.setattr(ui.QInputDialog, 'getText', lambda *a, **k: ('', accepted))
    assert controller.edit_directory(str(tmp_path))
    assert wait_for(lambda: not controller.busy)
    assert shooting_location(PhotoMetaDataXMP().read(paths[0])) == ('' if accepted else '上海东滩')
    if not accepted:
        assert Path(paths[0]).with_suffix('.xmp').read_bytes() == old_xmp
        assert not Path(paths[-1]).with_suffix('.xmp').exists()
    else:
        assert all(shooting_location(files.cached_photo_metadata_for_path(p)) == '' for p in paths)


@pytest.mark.parametrize('outcome', ['empty', 'scan_error', 'submit_error'])
def test_directory_location_empty_and_scan_failures(env, tmp_path, monkeypatch, outcome):
    controller, files, _ = env
    empty = tmp_path / '空目录'
    empty.mkdir()
    messages = []
    monkeypatch.setattr(ui.QMessageBox, 'information', lambda *a: messages.append(a[-1]))
    monkeypatch.setattr(ui.QMessageBox, 'warning', lambda *a: messages.append(a[-1]))
    monkeypatch.setattr(ui.QInputDialog, 'getText', lambda *a, **k: pytest.fail('Unexpected location input'))
    def fail(*a, **k):
        raise OSError('测试扫描失败')
    if outcome == 'scan_error':
        monkeypatch.setattr(ui, 'collect_paths', fail)
    elif outcome == 'submit_error':
        monkeypatch.setattr(files, 'metadata_work_pool', fail)
    assert controller.edit_directory(str(empty)) == (outcome != 'submit_error')
    assert wait_for(lambda: not controller.busy)
    assert messages == ['没有找到受支持的照片' if outcome == 'empty' else '测试扫描失败']


@pytest.mark.parametrize('shutdown', [False, True])
def test_directory_scan_cancel_waits_and_never_opens_editor(env, tmp_path, monkeypatch, shutdown):
    controller, files, paths = env
    entered, release = threading.Event(), threading.Event()
    original = ui.collect_paths
    scan_threads = []
    def scan(*args, **kwargs):
        scan_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        assert kwargs['cancelled']()
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, 'collect_paths', scan)
    monkeypatch.setattr(ui.QInputDialog, 'getText', lambda *a, **k: pytest.fail('Cancelled scan opened editor'))
    assert controller.edit_directory(str(tmp_path), recursive=True)
    try:
        assert entered.wait(5)
        assert scan_threads != [threading.get_ident()]
        future = controller._scan_future
        assert not controller.edit_directory(str(tmp_path))
        assert not controller.start(paths, '不应写入')
        with pytest.raises(RuntimeError):
            controller.save_one(paths[0], '不应写入')
        controller.paste(paths)
        controller.edit(paths)
        for extend, target in ((controller.extend_directory_menu, str(tmp_path)), (controller.extend_file_menu, paths)):
            menu = QMenu()
            extend(menu, target)
            actions = menu.actions()[0].menu().actions() if isinstance(target, str) else menu.actions()[::2]
            assert all(not action.isEnabled() for action in actions)
        if shutdown:
            # Browser shutdown may already reject new pool lookups.
            monkeypatch.setattr(files, 'metadata_work_pool', lambda: pytest.fail('Pool reacquired during shutdown'))
            controller.request_shutdown()
        else:
            controller._scan_dialog.reject()
        controller._finish_scan()
        assert controller._scan_future is future and controller.busy
        assert not controller.is_shutdown_done()
    finally:
        release.set()
    assert wait_for(controller.is_shutdown_done)
    assert not any(Path(p).with_suffix('.xmp').exists() for p in paths)


def test_directory_extender_registration_and_writes_disabled(env, tmp_path, monkeypatch):
    controller, files, _ = env
    class DirectoryBrowser:
        def add_context_menu_extender(self, callback):
            self.extender = callback
    browser = DirectoryBrowser()
    extra = ui.ShootingLocationController(controller._main, files, browser)
    try:
        assert browser.extender == extra.extend_directory_menu
        monkeypatch.setattr(files, '_file_writes_allowed', lambda *a, **k: False)
        assert not extra.edit_directory(str(tmp_path))
        assert not extra.busy and extra._scan_dialog is None
    finally:
        extra.request_shutdown()


@pytest.mark.parametrize('theme,point_size', [('light', 10), ('dark', 18)])
def test_directory_menu_rendered_geometry(env, tmp_path, theme, point_size):
    from SuperViewer.superviewer.ui_theme import build_palette
    controller, _, _ = env
    menu = QMenu()
    controller.extend_directory_menu(menu, str(tmp_path))
    sub = menu.actions()[0].menu()
    for widget in (menu, sub):
        font = widget.font()
        font.setPointSize(point_size)
        widget.setFont(font)
        widget.setPalette(build_palette(theme))
        widget.popup(QPoint(20, 20))
        _APP.processEvents()
        for action in widget.actions():
            geometry = widget.actionGeometry(action)
            assert widget.rect().contains(geometry)
            assert geometry.width() >= widget.fontMetrics().horizontalAdvance(action.text())
            assert geometry.height() >= widget.fontMetrics().height()
        assert widget.grab().save(str(tmp_path / f'location-menu-{theme}-{point_size}-{widget is sub}.png'))
        widget.close()
