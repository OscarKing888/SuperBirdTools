"""真实 Qt 线程、菜单、逐张失败继续、取消和晚到回调。"""
import time
import threading
from pathlib import Path

from PIL import Image
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import bird_identification_controller as ui
from SuperViewer.superviewer.bird_identification import BirdIDClient, BirdIDOptions

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate(): return True
        time.sleep(.005)
    return predicate()


class Files(QObject):
    photo_metadata_cache_updated = pyqtSignal(object)
    file_selected = pyqtSignal(str)
    def __init__(self, paths):
        super().__init__()
        self._all_files = list(paths)
        self.extenders, self.updates, self.sources = [], [], {}
    def add_file_context_menu_extender(self, callback): self.extenders.append(callback)
    def _resolve_source_path_for_action(self, path): return self.sources.get(path, path)
    def sync_metadata_edits_for_paths(self, updates):
        self.updates.append(updates)
        self.file_selected.emit(next(iter(updates)))


@pytest.fixture
def env(tmp_path, monkeypatch):
    paths = []
    for i in range(3):
        photo = tmp_path / f'鸟片{i}.jpg'
        Image.new('RGB', (24, 16)).save(photo)
        paths.append(str(photo))
    files = Files(paths)
    main = QWidget()
    controller = ui.BirdIDController(main, files)
    state = {'gate': None, 'started': threading.Event(), 'calls': []}
    def recognize(client, path):
        state['calls'].append(path)
        state['started'].set()
        if state['gate'] is not None:
            assert state['gate'].wait(5)
        return {'success': True, 'results': [{'cn_name': '翠鸟', 'en_name': 'Common Kingfisher', 'confidence': 92.5}]}
    monkeypatch.setattr(BirdIDClient, 'health', lambda _: {})
    monkeypatch.setattr(BirdIDClient, 'recognize', recognize)
    yield controller, files, paths, state
    controller.request_shutdown()
    if state['gate']: state['gate'].set()
    assert wait_for(controller.is_shutdown_done)
    if controller._dialog: controller._dialog.close()
    main.close()
    main.deleteLater()
    _APP.processEvents()


def test_menus_resolved_source_and_cache_update_without_reselection(env):
    controller, files, paths, state = env
    menu = QMenu()
    controller.extend_file_menu(menu, paths[:1])
    assert menu.actions()[0].text() == '识别鸟种…'
    multi = QMenu()
    controller.extend_file_menu(multi, paths)
    assert '3 张' in multi.actions()[0].text()
    directory = QMenu()
    controller.extend_directory_menu(directory, str(Path(paths[0]).parent))
    assert [a.text() for a in directory.actions()[0].menu().actions()] == ['识别当前目录…', '识别目录及子目录…']
    files.sources['stale-report.jpg'] = paths[0]
    files._all_files = ['stale-report.jpg', *paths]
    selected, refreshed = [], []
    files.file_selected.connect(selected.append)
    files.photo_metadata_cache_updated.connect(refreshed.append)
    assert controller.start_for_paths(['stale-report.jpg'], options=BirdIDOptions())
    assert wait_for(lambda: not controller.busy)
    assert state['calls'] == paths[:1]
    assert files.updates[-1]['stale-report.jpg']['title'] == '翠鸟'
    assert files.updates[-1][paths[0]]['bird_species_cn'] == '翠鸟'
    assert refreshed and not selected
    assert '翠鸟' in controller._dialog.details.toPlainText()
    assert '已确认 1' in controller._dialog.summary.text()


def test_batch_continues_after_error_and_counts_candidates(env, monkeypatch):
    controller, files, paths, state = env
    def recognize(client, path):
        if path == paths[0]: raise RuntimeError('服务识别失败')
        return {'success': True, 'results': [{'cn_name': '候选翠鸟', 'confidence': 35}]}
    monkeypatch.setattr(BirdIDClient, 'recognize', recognize)
    assert controller.start(ui.BirdIDJob((str(Path(paths[0]).parent),)), options=BirdIDOptions())
    assert wait_for(lambda: not controller.busy)
    assert controller._counts == {'failed': 1, 'candidate': 2}
    assert '待确定' in controller._dialog.details.toPlainText()
    assert not Path(paths[0]).with_suffix('.xmp').exists()
    assert PhotoMetaDataXMP().read(paths[1])['alt_species_cn'] == '候选翠鸟'


def test_cancel_keeps_thread_owned_rejects_stale_callbacks_and_prevents_write(env):
    controller, files, paths, state = env
    state['gate'] = threading.Event()
    assert controller.start_for_paths(paths, options=BirdIDOptions())
    assert state['started'].wait(3)
    worker = controller._worker
    controller.request_shutdown()
    assert controller.busy and not controller.is_shutdown_done()
    assert not controller.start_for_paths(paths, options=BirdIDOptions())
    controller._finished(object())
    assert controller._worker is worker
    state['gate'].set()
    assert wait_for(controller.is_shutdown_done)
    assert all(not Path(path).with_suffix('.xmp').exists() for path in paths)
    assert not files.updates


def test_switch_directory_during_recognition_only_saves_original_photo(env):
    controller, files, paths, state = env
    state['gate'] = threading.Event()
    assert controller.start_for_paths(paths[:1], options=BirdIDOptions())
    assert state['started'].wait(3)
    files._all_files = [paths[1]]
    state['gate'].set()
    assert wait_for(lambda: not controller.busy)
    assert PhotoMetaDataXMP().read(paths[0])['bird_species_cn'] == '翠鸟'
    assert not files.updates


def test_service_unavailable_is_one_job_failure(env, monkeypatch):
    controller, files, paths, state = env
    def health(client): raise RuntimeError('请启动 SuperPicky')
    monkeypatch.setattr(BirdIDClient, 'health', health)
    controller.start_for_paths(paths, options=BirdIDOptions())
    assert wait_for(lambda: not controller.busy)
    assert not state['calls']
    assert '请启动 SuperPicky' in controller._dialog.label.text()


def test_saved_result_queued_before_new_edit_does_not_clobber_ui(env, monkeypatch):
    controller, files, paths, state = env
    saved = []
    original = controller._result
    def delayed(worker, result):
        saved.append((worker, result))
    monkeypatch.setattr(controller, '_result', delayed)
    controller.start_for_paths(paths[:1], options=BirdIDOptions())
    assert wait_for(lambda: bool(saved))
    worker, result = saved[0]
    assert PhotoMetaDataXMP().write_title(paths[0], '用户最新标题')
    controller._refresh_rows(result, worker.job)
    assert not files.updates
    assert wait_for(lambda: not controller.busy)


@pytest.mark.parametrize('mode', ['list', 'thumbnail'])
def test_real_window_result_refresh_preserves_preview_and_comment_draft(tmp_path, monkeypatch, mode):
    import importlib
    from app_common import superviewer_user_options
    from SuperViewer.superviewer import paths_settings
    from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
    main = importlib.import_module('SuperViewer.main')
    settings = tmp_path / 'settings'
    settings.mkdir()
    (settings / paths_settings.CONFIG_FILENAME).write_text('{}', encoding='utf-8')
    monkeypatch.setattr(paths_settings, '_get_app_dir', lambda: str(settings))
    monkeypatch.setattr(paths_settings, '_get_user_state_dir', lambda: str(settings / 'state'))
    monkeypatch.setattr(main, '_get_app_dir', lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, '_get_app_dir', lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, '_RUNTIME_OPTIONS', {
        **superviewer_user_options.get_runtime_user_options(), 'thumbnail_loader_workers': 1,
        'metadata_loader_workers': 1, 'persistent_thumb_workers': 1,
    })
    monkeypatch.setenv('LOCALAPPDATA', str(settings / 'cache'))
    library = tmp_path / 'library'
    (library / '.superpicky').mkdir(parents=True)
    tags = library / '.superpicky/tags.cfg'
    tags.write_text('鸟类\n    翠鸟\n', encoding='utf-8')
    monkeypatch.setattr(main, 'SuperViewerTaggedFileListPanel', lambda: SuperViewerTaggedFileListPanel(tag_config_path=tags))
    photo = library / '实测.jpg'
    Image.new('RGB', (24, 18), 'blue').save(photo)
    source = str(photo)
    monkeypatch.setattr(BirdIDClient, 'health', lambda _: {})
    monkeypatch.setattr(BirdIDClient, 'recognize', lambda *_: {
        'success': True, 'results': [{'cn_name': '翠鸟', 'en_name': 'Common Kingfisher', 'confidence': 93}]})
    window = main.MainWindow(initial_received_files=['skip-restore'])
    window.show()
    try:
        files = window._file_list
        files._set_view_mode(files._MODE_LIST if mode == 'list' else files._MODE_THUMB)
        files.load_directory(str(library))
        assert wait_for(lambda: source in files._all_files)
        files.set_pending_selection([source], source)
        assert wait_for(lambda: window.preview_panel.source_pixmap_for_path(source) is not None)
        assert wait_for(lambda: files._metadata_loader is None and files._photo_tag_loader is None and window._focus_loader is None)
        cache_key = window.preview_panel.source_pixmap_for_path(source).cacheKey()
        window.image_info_panel.comment_edit.setPlainText('用户未保存的备注')
        reselections = []
        files.file_selected.connect(reselections.append)
        assert window._bird_id.start_for_paths([source], options=BirdIDOptions())
        assert wait_for(lambda: not window._bird_id.busy)
        assert files.get_photo_metadata_for_path(source)['bird_species_cn'] == '翠鸟'
        assert PhotoMetaDataXMP().read(source)['Title'] == '翠鸟'
        assert not reselections
        assert window.preview_panel.source_pixmap_for_path(source).cacheKey() == cache_key
        assert window.image_info_panel.comment_edit.toPlainText() == '用户未保存的备注'
        assert window._bird_id._dialog.grab().save(str(tmp_path / f'birdid-{mode}.png'))
    finally:
        window.close()
        assert wait_for(lambda: window._shutdown_finalized)
        window.deleteLater()
        _APP.processEvents()
