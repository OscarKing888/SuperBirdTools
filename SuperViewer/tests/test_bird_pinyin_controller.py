"""拼音菜单、目录补全、有界结果队列和真实线程取消。"""
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu, QWidget
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import bird_pinyin_controller as ui

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        _APP.processEvents()
        if predicate(): return True
        time.sleep(.005)
    return predicate()


class Files(QObject):
    photo_metadata_cache_updated = pyqtSignal(object)
    file_selected = pyqtSignal(str)
    def __init__(self, paths):
        super().__init__()
        self._all_files, self.updates = paths, []
    def add_file_context_menu_extender(self, callback): self.extender = callback
    def sync_metadata_edits_for_paths(self, updates):
        self.updates.append(updates)
        self.file_selected.emit(next(iter(updates)))


@pytest.fixture
def env(tmp_path):
    paths = []
    for i, name in enumerate(['白头鹎', '家燕', '词表未知鸟名']):
        path = tmp_path / f'鸟{i}.jpg'
        Image.new('RGB', (16, 16)).save(path)
        assert PhotoMetaDataXMP().write_title(str(path), name)
        paths.append(str(path))
    files, window = Files(paths), QWidget()
    controller = ui.PinyinController(window, files)
    yield controller, files, paths
    controller.request_shutdown()
    assert wait_for(controller.is_shutdown_done)
    if controller._dialog: controller._dialog.close()
    window.close()
    window.deleteLater()
    _APP.processEvents()


def test_directory_menu_updates_and_skips_unknown_without_reselection(env):
    controller, files, paths = env
    selected = []
    files.file_selected.connect(selected.append)
    menu = QMenu()
    controller.extend_file_menu(menu, paths[:1])
    assert menu.actions()[0].text() == '更新拼音'
    directory = QMenu()
    controller.extend_directory_menu(directory, str(Path(paths[0]).parent))
    actions = directory.actions()[0].menu().actions()
    assert [a.text() for a in actions] == ['更新当前目录拼音', '更新目录及子目录拼音']
    actions[0].trigger()
    assert wait_for(lambda: not controller.busy)
    assert controller._counts == {'success': 2, 'skipped': 1}
    assert PhotoMetaDataXMP().read(paths[0])['pinyin_name'] == 'bái tóu bēi'
    assert files.updates and not selected


def test_cancel_waits_for_worker_and_keeps_saved_result(env, monkeypatch):
    controller, files, paths = env
    started, gate = threading.Event(), threading.Event()
    original = ui.PinyinUpdater.update
    def update(updater, path, **kwargs):
        result = original(updater, path, **kwargs)
        started.set()
        assert gate.wait(5)
        return result
    monkeypatch.setattr(ui.PinyinUpdater, 'update', update)
    assert controller.start_for_paths(paths)
    assert started.wait(5)
    worker = controller._worker
    controller.stop()
    assert controller.busy
    controller._finished(object())
    assert controller._worker is worker
    gate.set()
    assert wait_for(lambda: not controller.busy)
    assert controller._counts == {'success': 1}
    assert files.updates[-1][paths[0]]['pinyin_name'] == 'bái tóu bēi'
    assert 'pinyin_name' not in PhotoMetaDataXMP().read(paths[1])


def test_shutdown_discards_ui_callbacks_and_disallows_new_jobs(env, monkeypatch):
    controller, files, paths = env
    started, gate = threading.Event(), threading.Event()
    original = ui.PinyinUpdater.update
    def update(updater, path, **kwargs):
        started.set()
        assert gate.wait(5)
        return original(updater, path, **kwargs)
    monkeypatch.setattr(ui.PinyinUpdater, 'update', update)
    assert controller.start_for_paths(paths)
    assert started.wait(5)
    controller.request_shutdown()
    assert not controller.is_shutdown_done()
    assert not controller.start_for_paths(paths)
    gate.set()
    assert wait_for(controller.is_shutdown_done)
    assert not files.updates
    assert 'pinyin_name' not in PhotoMetaDataXMP().read(paths[0])
