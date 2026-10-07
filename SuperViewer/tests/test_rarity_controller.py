"""单张/多张快捷菜单、同侧车去重、部分失败和真实线程生命周期。"""
import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu, QWidget
from app_common.bird_rarity import rarity_metadata
from app_common.exif_io.photo_meta import PhotoMetaDataXMP, PhotoMetaDataEXIFEmbeded
from SuperViewer.superviewer import rarity_controller as ui

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
        self._all_files, self.updates, self.sources = paths, [], {}
    def add_file_context_menu_extender(self, callback): self.extender = callback
    def _file_writes_allowed(self, *args, **kwargs): return True
    def _resolve_source_path_for_action(self, path): return self.sources.get(path, path)
    def cached_photo_metadata_for_path(self, path): return {'gbif_rarity_100': 72.25}
    def sync_metadata_edits_for_paths(self, updates):
        self.updates.append(updates)
        self.file_selected.emit(next(iter(updates)))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(PhotoMetaDataEXIFEmbeded, 'read', lambda *_: {})
    paths = []
    for i in range(3):
        path = tmp_path / f'鸟{i}.jpg'
        Image.new('RGB', (16, 16)).save(path)
        assert PhotoMetaDataXMP().write_title(str(path), '白头鹎')
        paths.append(str(path))
    files, window = Files(paths), QWidget()
    controller = ui.RarityController(window, files)
    yield controller, files, paths
    controller.request_shutdown()
    assert wait_for(controller.is_shutdown_done)
    if controller._dialog: controller._dialog.close()
    window.close()
    window.deleteLater()
    _APP.processEvents()


@pytest.mark.parametrize('count', [1, 3])
def test_quick_menu_single_and_batch_no_reselection(env, count):
    controller, files, paths = env
    selected = []
    files.file_selected.connect(selected.append)
    menu = QMenu()
    controller.extend_file_menu(menu, paths[:count])
    actions = menu.actions()[0].menu().actions()
    assert actions[0].text() == '普通（0）'
    actions[3].trigger()
    assert wait_for(lambda: not controller.busy)
    assert controller._done == count and controller._failed == 0
    assert all(rarity_metadata(PhotoMetaDataXMP().read(p))[0] == 50 for p in paths[:count])
    assert files.updates and not selected


def test_deduplicates_sidecars_resolves_aliases_and_continues_after_failure(env):
    controller, files, paths = env
    raw = str(Path(paths[0]).with_suffix('.ARW'))
    Path(raw).write_bytes(b'raw')
    Path(paths[1]).with_suffix('.xmp').write_bytes(b'<broken')
    files._all_files = [*paths, raw, 'old-path.jpg']
    files.sources['old-path.jpg'] = paths[0]
    assert controller.start(['old-path.jpg', raw, *paths], 0)
    assert wait_for(lambda: not controller.busy)
    assert controller._done == 3 and controller._failed == 1
    merged = {p: m for batch in files.updates for p, m in batch.items()}
    assert all(merged[p]['gbif_rarity_100'] == 0 for p in [paths[0], paths[2], raw, 'old-path.jpg'])


def test_custom_cancel_and_write_disabled(env, monkeypatch):
    controller, files, paths = env
    monkeypatch.setattr(ui.QInputDialog, 'getDouble', lambda *_: (72.25, False))
    controller.edit(paths[:1])
    assert not controller.busy
    monkeypatch.setattr(ui.QInputDialog, 'getDouble', lambda *_: (72.25, True))
    controller.edit(paths[:1])
    assert wait_for(lambda: not controller.busy)
    assert rarity_metadata(PhotoMetaDataXMP().read(paths[0]))[0] == 72.25
    monkeypatch.setattr(files, '_file_writes_allowed', lambda *a, **k: False)
    assert not controller.start(paths, 8)
    menu = QMenu()
    controller.populate_menu(menu, paths)
    assert all(not a.isEnabled() for a in menu.actions() if not a.isSeparator())


@pytest.mark.parametrize('shutdown', [False, True])
def test_stop_retains_worker_until_finished_and_saved_results(env, monkeypatch, shutdown):
    controller, files, paths = env
    started, gate = threading.Event(), threading.Event()
    original = ui.save_rarity
    def save(path, score, **kwargs):
        result = original(path, score, **kwargs)
        started.set()
        assert gate.wait(5)
        return result
    monkeypatch.setattr(ui, 'save_rarity', save)
    assert controller.start(paths, 75)
    assert started.wait(5)
    worker = controller._worker
    try:
        controller.request_shutdown() if shutdown else controller.stop()
        assert controller.busy
        controller._finished(object())
        assert controller._worker is worker
        assert not controller.start(paths, 0)
    finally:
        gate.set()
    assert wait_for(lambda: not controller.busy)
    assert controller._done == 1
    assert bool(files.updates) != shutdown
    assert rarity_metadata(PhotoMetaDataXMP().read(paths[0]))[0] == 75
    assert rarity_metadata(PhotoMetaDataXMP().read(paths[1]))[0] is None
