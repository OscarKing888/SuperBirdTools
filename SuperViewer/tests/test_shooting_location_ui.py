"""地点编辑、批量剪贴板、局部刷新及线程关闭的真实 Qt 回归。"""
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
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
def env(tmp_path):
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
    actions = menu.actions()[0].menu().actions()
    assert [a.text() for a in actions] == ['修改拍摄地点…', '复制拍摄地点', '粘贴拍摄地点']
    monkeypatch.setattr(ui.QInputDialog, 'getText', lambda *a, **k: ('上海东滩', True))
    actions[0].trigger()
    assert wait_for(controller.is_shutdown_done)
    controller.copy(paths[0])
    assert QApplication.clipboard().text() == '上海东滩'
    multi = QMenu()
    controller.extend_file_menu(multi, paths)
    multi_actions = multi.actions()[0].menu().actions()
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
    assert shooting_location(PhotoMetaDataXMP().read(paths[-1])) == ''


def test_exif_location_clear_updates_canonical_cache(env):
    controller, files, paths = env
    controller.save_one(paths[0], '旧地点')
    assert PhotoMetaDataXMP().write(paths[0], {LOCATION_TAG: ''})
    sync_saved_xmp_edit(files, paths[0], LOCATION_TAG)
    assert shooting_location(files.cached_photo_metadata_for_path(paths[0])) == ''
