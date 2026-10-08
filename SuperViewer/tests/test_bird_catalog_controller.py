"""真实 Qt 线程：搜索/选择、分页、过期请求、批量写入和退出。"""
from pathlib import Path
import threading
import time

from PIL import Image
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import bird_catalog_controller as ui

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


class Files(QObject):
    photo_metadata_cache_updated = pyqtSignal(object)
    file_selected = pyqtSignal(str)

    def __init__(self, paths):
        super().__init__()
        self._all_files, self.updates = paths, []
        self.allowed = True

    def add_file_context_menu_extender(self, callback):
        self.extender = callback

    def _file_writes_allowed(self, *args, **kwargs):
        return self.allowed

    def _resolve_source_path_for_action(self, path):
        return path

    def sync_metadata_edits_for_paths(self, updates):
        self.updates.append(updates)
        self.file_selected.emit(next(iter(updates)))


@pytest.fixture
def env(tmp_path, monkeypatch):
    paths = []
    for i in range(3):
        path = tmp_path / f'照片{i}.jpg'
        Image.new('RGB', (24, 16)).save(path)
        paths.append(str(path))
    bird = dict(bird_id=2, version_id=10, version_name='IOC 14.2', cn_name='白头鹎',
                en_name='Light-vented Bulbul', scientific_name='Pycnonotus sinensis',
                pinyin_name='bái tóu bēi', pinyin_plain='bai tou bei', abbreviation='BTB',
                gbif_rarity_100=0, iucn_category='LC', china_protection_level=None, description='中文简介')
    state = dict(gate=None, started=threading.Event(), calls=[])
    def search(client, query='', *, offset=0, limit=100, version_id=None):
        state['calls'].append((query, offset, version_id))
        state['started'].set()
        if state['gate']:
            assert state['gate'].wait(5)
        return dict(success=True, results=[bird], total=101, offset=offset, limit=limit,
                    version_id=10, version_name='IOC 14.2')
    monkeypatch.setattr(ui.BirdCatalogClient, 'search', search)
    monkeypatch.setattr(ui.BirdCatalogClient, 'detail', lambda self, bird_id, version_id: dict(bird))
    window, files = QWidget(), Files(paths)
    controller = ui.BirdCatalogController(window, files)
    yield controller, files, paths, bird, state
    controller.request_shutdown()
    if state['gate']:
        state['gate'].set()
    assert wait_for(controller.is_shutdown_done)
    window.close()
    window.deleteLater()
    _APP.processEvents()


def choose(controller, paths):
    assert controller.open(paths)
    assert wait_for(lambda: controller._query_worker is None)
    controller._dialog.list.setCurrentRow(0)
    assert wait_for(lambda: controller._selected is not None)


def test_menu_search_detail_batch_save_and_no_reselection(env):
    controller, files, paths, bird, state = env
    selected = []
    files.file_selected.connect(selected.append)
    menu = QMenu()
    files.extender(menu, paths)
    assert menu.actions()[0].text() == '手动指定鸟名…'
    menu.actions()[0].trigger()
    assert wait_for(lambda: controller._query_worker is None)
    dialog = controller._dialog
    assert dialog.list.count() == 1 and not dialog.apply.isEnabled()
    dialog.query.setText('BTB')
    assert wait_for(lambda: state['calls'][-1][0] == 'BTB' and controller._query_worker is None)
    dialog.list.setCurrentRow(0)
    assert wait_for(lambda: controller._selected is not None)
    assert 'bái tóu bēi' in dialog.detail.toPlainText()
    assert 'LC 无危' in dialog.detail.toPlainText()
    dialog.apply.click()
    assert wait_for(lambda: controller._apply_worker is None)
    assert controller._counts['success'] == 3
    assert not dialog.isVisible()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert files.updates and not selected
    for path in paths:
        assert PhotoMetaDataXMP().read(path)['Title'] == bird['cn_name']


def test_paging_pins_version_and_query_change_invalidates_detail(env):
    controller, files, paths, bird, state = env
    choose(controller, paths)
    controller._dialog.next.click()
    assert wait_for(lambda: controller._query_worker is None)
    assert state['calls'][-1] == ('', 100, 10)
    assert controller._selected is None and not controller._dialog.apply.isEnabled()
    controller._dialog.previous.click()
    assert wait_for(lambda: controller._query_worker is None)
    assert state['calls'][-1] == ('', 0, 10)
    controller._dialog.query.setText('new')
    assert not controller._dialog.apply.isEnabled()


@pytest.mark.parametrize('shutdown', [False, True])
def test_inflight_search_cancel_stale_result_and_real_finished(env, shutdown):
    controller, files, paths, bird, state = env
    state['gate'] = threading.Event()
    assert controller.open(paths)
    assert state['started'].wait(3)
    worker = controller._query_worker
    if shutdown:
        controller.request_shutdown()
    else:
        controller._dialog.query.setText('latest')
        controller.search()
    assert controller._query_worker is worker
    controller._query_finished(object())
    assert controller._query_worker is worker
    state['gate'].set()
    assert wait_for(controller.is_shutdown_done)
    if shutdown:
        assert controller._dialog.list.count() == 0
    else:
        assert state['calls'][-1][0] == 'latest'
        assert controller._dialog.list.count() == 1


def test_detail_late_result_cannot_apply_other_bird(env, monkeypatch):
    controller, files, paths, bird, state = env
    assert controller.open(paths)
    assert wait_for(lambda: controller._query_worker is None)
    gate, started = threading.Event(), threading.Event()
    def detail(*args, **kwargs):
        started.set()
        assert gate.wait(5)
        return bird
    monkeypatch.setattr(ui.BirdCatalogClient, 'detail', detail)
    controller._dialog.list.setCurrentRow(0)
    assert started.wait(3)
    try:
        controller._dialog.query.setText('another bird')
        assert not controller.apply()
    finally:
        gate.set()
    assert wait_for(lambda: controller._query_worker is None)
    assert controller._selected is None
    assert not controller._dialog.apply.isEnabled()


def test_partial_failure_shared_sidecar_and_write_gate(env):
    controller, files, paths, bird, state = env
    raw = str(Path(paths[0]).with_suffix('.ARW'))
    Path(raw).write_bytes(b'raw')
    Path(paths[1]).with_suffix('.xmp').write_bytes(b'<broken')
    files._all_files = [*paths, raw]
    choose(controller, [*paths, raw])
    files.allowed = False
    assert not controller.apply()
    files.allowed = True
    assert controller.apply()
    assert wait_for(lambda: controller._apply_worker is None)
    assert controller._counts['success'] == 2 and controller._counts['failed'] == 1
    assert controller._dialog.isVisible()
    assert '失败 1' in controller._dialog.status.text()
    merged = {p: m for batch in files.updates for p, m in batch.items()}
    assert raw in merged and paths[0] in merged


def test_apply_rechecks_details_and_preserves_edit_during_request(env, monkeypatch):
    controller, files, paths, bird, state = env
    choose(controller, paths[:1])
    def detail(*args, **kwargs):
        PhotoMetaDataXMP().write_title(paths[0], '刚刚编辑')
        return bird
    monkeypatch.setattr(ui.BirdCatalogClient, 'detail', detail)
    assert controller.apply()
    assert wait_for(lambda: controller._apply_worker is None)
    assert controller._counts['skipped'] == 1
    assert PhotoMetaDataXMP().read(paths[0])['Title'] == '刚刚编辑'


def test_shutdown_during_save_keeps_committed_results(env, monkeypatch):
    controller, files, paths, bird, state = env
    choose(controller, paths)
    gate, started = threading.Event(), threading.Event()
    real_apply = ui.apply_bird
    def apply(*args, **kwargs):
        result = real_apply(*args, **kwargs)
        started.set()
        assert gate.wait(5)
        return result
    monkeypatch.setattr(ui, 'apply_bird', apply)
    assert controller.apply()
    assert started.wait(3)
    try:
        worker = controller._apply_worker
        controller.request_shutdown()
        assert not controller.is_shutdown_done()
        controller._apply_finished(object())
        assert controller._apply_worker is worker
    finally:
        gate.set()
    assert wait_for(controller.is_shutdown_done)
    assert controller._counts['success'] == 1
    assert PhotoMetaDataXMP().read(paths[0])['Title'] == bird['cn_name']
    assert not files.updates


def test_filter_initial_focus_and_url_on_right(env):
    controller, files, paths, bird, state = env
    assert controller.open(paths)
    dialog = controller._dialog
    assert wait_for(lambda: dialog.query.hasFocus())
    assert dialog.query.geometry().top() == dialog.url.geometry().top()
    assert dialog.query.geometry().right() < dialog.url.geometry().left()


@pytest.mark.parametrize('details_ready', [False, True])
def test_double_click_saves_then_closes_without_progress_popup(env, monkeypatch, details_ready):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    controller, files, paths, bird, state = env
    assert controller.open(paths)
    assert wait_for(lambda: controller._query_worker is None)
    dialog = controller._dialog
    gate, started = threading.Event(), threading.Event()
    def detail(*args, **kwargs):
        started.set()
        assert gate.wait(5)
        return dict(bird)
    if details_ready:
        gate.set()
    monkeypatch.setattr(ui.BirdCatalogClient, 'detail', detail)
    point = dialog.list.visualItemRect(dialog.list.item(0)).center()
    QTest.mouseClick(dialog.list.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert started.wait(3)
    if details_ready:
        assert wait_for(lambda: controller._selected is not None)
    try:
        QTest.mouseDClick(dialog.list.viewport(), Qt.MouseButton.LeftButton, pos=point)
        if not details_ready:
            assert controller._apply_after_detail is not None
            assert not Path(paths[0]).with_suffix('.xmp').exists()
    finally:
        gate.set()
    assert wait_for(lambda: not controller.busy and not dialog.isVisible())
    assert controller._counts['success'] == 3
    assert not any(w.isVisible() and w.windowTitle() == '保存手动鸟名' for w in _APP.topLevelWidgets())
    assert PhotoMetaDataXMP().read(paths[0])['Title'] == bird['cn_name']


@pytest.mark.parametrize('dismiss', [False, True])
def test_double_click_waiting_for_details_cannot_apply_after_invalidation(env, monkeypatch, dismiss):
    controller, files, paths, bird, state = env
    assert controller.open(paths)
    assert wait_for(lambda: controller._query_worker is None)
    gate, started = threading.Event(), threading.Event()
    def detail(*args, **kwargs):
        started.set()
        assert gate.wait(5)
        return dict(bird)
    monkeypatch.setattr(ui.BirdCatalogClient, 'detail', detail)
    controller.activate(bird)
    assert started.wait(3)
    try:
        if dismiss:
            controller._dialog.close()
        else:
            controller._dialog.query.setText('another bird')
        assert controller._apply_after_detail is None
    finally:
        gate.set()
    assert wait_for(lambda: not controller.busy)
    assert not any(Path(path).with_suffix('.xmp').exists() for path in paths)
