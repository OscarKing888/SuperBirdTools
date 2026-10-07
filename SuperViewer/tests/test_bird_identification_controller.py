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
    assert controller._dialog.details.model().index(0, 4).data() == '翠鸟'
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
    assert controller._dialog.details.model().index(1, 1).data() == '待确定'
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


@pytest.mark.parametrize('operation', ['bird_id', 'pinyin', 'location', 'rarity'])
@pytest.mark.parametrize('mode', ['list', 'thumbnail'])
def test_real_window_result_refresh_preserves_preview_and_comment_draft(tmp_path, monkeypatch, mode, operation):
    import importlib
    from app_common.bird_rarity import rarity_metadata
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
    tags.write_text('鸟类\n    白头鹎\n', encoding='utf-8')
    monkeypatch.setattr(main, 'SuperViewerTaggedFileListPanel', lambda: SuperViewerTaggedFileListPanel(tag_config_path=tags))
    photo = library / '实测.jpg'
    Image.new('RGB', (24, 18), 'blue').save(photo)
    source = str(photo)
    monkeypatch.setattr(BirdIDClient, 'health', lambda _: {})
    monkeypatch.setattr(BirdIDClient, 'recognize', lambda *_: {
        'success': True, 'results': [{'cn_name': '白头鹎', 'en_name': 'Light-vented Bulbul', 'confidence': 93, 'gbif_rarity_100': 80, 'iucn_category': 'NT'},
        {'cn_name': '红耳鹎', 'en_name': 'Red-whiskered Bulbul', 'confidence': 40, 'gbif_rarity_100': 10, 'iucn_category': 'LC'}]})
    if operation in ('pinyin', 'location', 'rarity'):
        assert PhotoMetaDataXMP().write_title(source, '白头鹎')
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
        if operation == 'bird_id':
            controller = window._bird_id
            assert controller.start_for_paths([source], options=BirdIDOptions())
        elif operation == 'rarity':
            from SuperViewer.superviewer.rarity_controller import QMenu
            controller = window._rarity_edit
            def choose(path, anchor):
                menu = QMenu(anchor)
                controller.populate_menu(menu, [path])
                menu.actions()[4].trigger()
            window.image_info_panel._rarity_edit_callback = choose
            window.image_info_panel.rarity_edit_button.click()
        elif operation == 'location':
            controller = window._shooting_location
            assert controller.start([source], '崇明东滩')
        else:
            controller = window._bird_pinyin
            panel = window.image_info_panel
            assert not panel.pinyin_update_button.isHidden()
            panel.pinyin_update_button.click()
        assert wait_for(lambda: not controller.busy)
        if operation == 'rarity':
            assert window.image_info_panel.basic_rows['稀有度'].text() == '传奇'
            assert rarity_metadata(PhotoMetaDataXMP().read(source))[0] == 75
            model = files._file_table_model
            if mode == 'list':
                assert model.index(0, model.rarity_column).data() == '传奇'
            assert rarity_metadata(files.cached_photo_metadata_for_path(source))[0] == 75
        if operation == 'location':
            assert window.image_info_panel.location_edit.text() == '崇明东滩'
            window.image_info_panel.location_edit.setText('云南高黎贡山')
            window.image_info_panel.location_edit.editingFinished.emit()
            assert PhotoMetaDataXMP().read(source)['shooting_location'] == '云南高黎贡山'
        if operation == 'pinyin':
            assert window.image_info_panel.basic_rows['拼音'].text() == 'bái tóu bēi'
            assert window.image_info_panel.pinyin_update_button.isHidden()
            assert PhotoMetaDataXMP().read(source)['pinyin_name'] == 'bái tóu bēi'
        if operation == 'bird_id':
            assert wait_for(lambda: controller._thumbnails.image(source) is not None)
            assert controller._thumbnails._context['thumb_cache'] is files._thumb_memory_cache
            assert controller._thumbnails._context['work_pool'] is files._browser_work_pool
            badge = window.image_info_panel.basic_rows['稀有度']
            assert badge.text() == '传奇' and '80/100' in badge.toolTip()
            assert window.image_info_panel.basic_rows['保护等级'].text() == 'NT · 近危'
            # 配置修改即时生效，并保留预览与未提交备注。
            superviewer_user_options.apply_runtime_user_options({
                **superviewer_user_options.get_runtime_user_options(),
                'rarity_badge_legendary_text': '传说',
                'rarity_badge_legendary_background': '#123456',
                'rarity_badge_legendary_foreground': '#ABCDEF',
            })
            window.image_info_panel.refresh_metadata_fields()
            assert badge.text() == '传说' and '#123456' in badge.styleSheet() and '#ABCDEF' in badge.styleSheet()
            assert controller.start_for_paths([source], saved_candidates=True)
            assert wait_for(lambda: not controller.busy)
            controller._dialog.details._request(1)
            assert wait_for(lambda: not controller.busy)
            assert PhotoMetaDataXMP().read(source)['Title'] == '红耳鹎'
            assert '10/100' in badge.toolTip()
            assert window.image_info_panel.basic_rows['保护等级'].text() == 'LC · 无危'
            controller._dialog.details._request(0)
            assert wait_for(lambda: not controller.busy)
            assert badge.text() == '传说'

        from app_common.bird_pinyin import bird_name
        assert bird_name(files.get_photo_metadata_for_path(source)) == '白头鹎'
        assert PhotoMetaDataXMP().read(source)['Title'] == '白头鹎'
        assert not reselections
        assert window.preview_panel.source_pixmap_for_path(source).cacheKey() == cache_key
        assert window.image_info_panel.comment_edit.toPlainText() == '用户未保存的备注'
        assert controller._dialog.grab().save(str(tmp_path / f'{operation}-{mode}.png'))
    finally:
        window.close()
        assert wait_for(lambda: window._shutdown_finalized)
        window.deleteLater()
        _APP.processEvents()


def multi_response():
    return {'success': True, 'results': [
        {'cn_name': '白头鹎', 'en_name': 'Light-vented Bulbul', 'confidence': 45,
         'scientific_name': 'Pycnonotus sinensis', 'pinyin_name': 'bái tóu bēi',
         'gbif_rarity_100': 0, 'iucn_category': 'LC', 'description': '常见留鸟'},
        {'cn_name': '红耳鹎', 'en_name': 'Red-whiskered Bulbul', 'confidence': 35},
        {'cn_name': '黑短脚鹎', 'confidence': 20}], 'warning': '地理筛选信息不足'}


@pytest.mark.parametrize("adoption_key", ["Key_Space", "Key_Return", "Key_Enter"])
def test_table_candidates_buttons_and_background_adoption(env, monkeypatch, adoption_key):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    controller, files, paths, state = env
    monkeypatch.setattr(BirdIDClient, 'recognize', lambda *_: multi_response())
    assert controller.start_for_paths(paths[:2], options=BirdIDOptions())
    assert wait_for(lambda: not controller.busy)
    table = controller._dialog.details
    model = table.model()
    assert model.rowCount() == 6
    assert model.index(0, 9).data() == '0 / 100'
    assert model.index(1, 9).data() == '—'
    assert model.index(0, 7).data() == 'bái tóu bēi'
    assert model.index(0, 12).data() == '地理筛选信息不足'
    assert model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole) == paths[0]
    assert table.horizontalScrollBar().maximum() > 0
    selected = []
    files.file_selected.connect(selected.append)
    target = model.index(1, model.ACTION_COLUMN)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(target).center())
    assert wait_for(lambda: not controller.busy)
    assert PhotoMetaDataXMP().read(paths[0])['Title'] == '红耳鹎'
    assert 'Title' not in PhotoMetaDataXMP().read(paths[1])
    assert model.index(1, model.ACTION_COLUMN).data() == '已采纳'
    assert not model.can_adopt(1) and model.can_adopt(0)
    assert controller._counts['success'] == 1 and controller._counts['candidate'] == 1
    assert not selected
    table.setCurrentIndex(model.index(0, model.ACTION_COLUMN))
    QTest.keyClick(table, getattr(Qt.Key, adoption_key))
    assert wait_for(lambda: not controller.busy)
    assert PhotoMetaDataXMP().read(paths[0])['Title'] == '白头鹎'
    assert controller._counts['success'] == 1


def test_stale_adoption_disables_photo_candidates(env, monkeypatch):
    controller, files, paths, state = env
    monkeypatch.setattr(BirdIDClient, 'recognize', lambda *_: multi_response())
    controller.start_for_paths(paths[:1], options=BirdIDOptions())
    assert wait_for(lambda: not controller.busy)
    assert PhotoMetaDataXMP().write_title(paths[0], '新的手动鸟名')
    table = controller._dialog.details
    table._request(2)
    assert wait_for(lambda: not controller.busy)
    assert '已变化' in controller._dialog.label.text()
    assert all(not table.results.can_adopt(row) for row in range(3))
    assert PhotoMetaDataXMP().read(paths[0])['Title'] == '新的手动鸟名'


def test_shutdown_waits_for_adoption_thread_and_ignores_late_completion(env, monkeypatch):
    controller, files, paths, state = env
    monkeypatch.setattr(BirdIDClient, 'recognize', lambda *_: multi_response())
    controller.start_for_paths(paths[:1], options=BirdIDOptions())
    assert wait_for(lambda: not controller.busy)
    gate, started = threading.Event(), threading.Event()
    original = ui.adopt_candidate
    def slow(*args, **kwargs):
        started.set()
        assert gate.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, 'adopt_candidate', slow)
    before = Path(paths[0]).with_suffix('.xmp').read_bytes()
    controller._dialog.details._request(1)
    assert started.wait(3)
    worker = controller._adopt_worker
    try:
        controller.request_shutdown()
        assert controller.busy and not controller.is_shutdown_done()
        controller._adopt_finished(object())
        assert controller._adopt_worker is worker
    finally:
        gate.set()
    assert wait_for(controller.is_shutdown_done)
    assert Path(paths[0]).with_suffix('.xmp').read_bytes() == before


def test_scroll_table_retains_all_results_without_per_row_widgets(tmp_path):
    from SuperViewer.superviewer.bird_identification import BirdIDResult
    dialog = ui.BirdIDProgressDialog(None, results_table=True)
    try:
        dialog.show()
        table = dialog.details
        for i in range(350):
            table.append_result(BirdIDResult(f'/照片/{i}.jpg', 'candidate', response=multi_response()))
        _APP.processEvents()
        assert table.model().rowCount() == 1050
        assert table.verticalScrollBar().maximum() > 0
        table.verticalScrollBar().setValue(0)
        table.append_result(BirdIDResult('/照片/失败.jpg', 'failed', '服务不可用'))
        _APP.processEvents()
        assert table.verticalScrollBar().value() == 0
        assert table.model().index(1050, 11).data() == '服务不可用'
        assert not table.results.can_adopt(1050)
        assert table.indexWidget(table.model().index(0, 2)) is None
        assert dialog.grab().save(str(tmp_path / 'bird-id-table.png'))
    finally:
        dialog.finish('完成')
        dialog.close()
        dialog.deleteLater()


def test_can_adopt_finished_photo_while_batch_continues(env, monkeypatch):
    controller, files, paths, state = env
    gate, waiting = threading.Event(), threading.Event()
    def recognize(client, path):
        if path == paths[1]:
            waiting.set()
            assert gate.wait(5)
        return multi_response()
    monkeypatch.setattr(BirdIDClient, 'recognize', recognize)
    controller.start_for_paths(paths[:2], options=BirdIDOptions())
    try:
        assert wait_for(lambda: waiting.is_set() and controller._dialog.details.model().rowCount() == 3)
        worker = controller._worker
        table = controller._dialog.details
        table._request(2)
        assert wait_for(lambda: controller._adopt_worker is None)
        assert controller._worker is worker and controller.busy
        assert PhotoMetaDataXMP().read(paths[0])['Title'] == '黑短脚鹎'
        assert table.model().index(2, 2).data() == '已采纳'
        assert table.model().index(0, 1).data() == '候选'
    finally:
        gate.set()
    assert wait_for(lambda: not controller.busy)
    assert controller._counts == {'candidate': 1, 'success': 1}


def test_adoption_write_failure_allows_retry(env, monkeypatch):
    controller, files, paths, state = env
    monkeypatch.setattr(BirdIDClient, 'recognize', lambda *_: multi_response())
    controller.start_for_paths(paths[:1], options=BirdIDOptions())
    assert wait_for(lambda: not controller.busy)
    table = controller._dialog.details
    with monkeypatch.context() as patch:
        patch.setattr(PhotoMetaDataXMP, '_write_tree_atomic', staticmethod(lambda *_: False))
        table._request(1)
        assert wait_for(lambda: not controller.busy)
    assert table.results.can_adopt(1)
    assert '失败' in controller._dialog.label.text()
    assert controller._counts['candidate'] == 1
    table._request(1)
    assert wait_for(lambda: not controller.busy)
    assert PhotoMetaDataXMP().read(paths[0])['Title'] == '红耳鹎'
    assert controller._counts['success'] == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_saved_candidates_menu_offline_selection_and_reopen(env, monkeypatch, legacy, tmp_path):
    import json
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    controller, files, paths, state = env
    # 返回顺序刻意与置信度相反，确保显示排序不会串采纳索引。
    candidates = [
        {'cn_name': '红耳鹎', 'en_name': 'Red-whiskered Bulbul', 'confidence': 20},
        {'cn_name': '白头鹎', 'en_name': 'Light-vented Bulbul', 'confidence': 95}]
    raw = json.dumps({'success': True, 'results': candidates}, ensure_ascii=False)
    fields = {'XMP-superpicky:birdid_response': raw, 'XMP-dc:Title': '红耳鹎',
              'XMP-superpicky:bird_species_cn': '红耳鹎'}
    if not legacy:
        fields['XMP-superpicky:birdid_candidates'] = json.dumps(candidates, ensure_ascii=False)
    assert PhotoMetaDataXMP().write(paths[0], fields)
    monkeypatch.setattr(BirdIDClient, 'health', lambda _: pytest.fail('查看已存候选不能访问服务'))
    monkeypatch.setattr(BirdIDClient, 'recognize', lambda *_: pytest.fail('改选候选不能重新识别'))
    menu = QMenu()
    controller.extend_file_menu(menu, paths[:1])
    next(a for a in menu.actions() if a.text() == '选择候选鸟名…').trigger()
    assert wait_for(lambda: not controller.busy)
    table = controller._dialog.details
    model = table.model()
    assert model.rowCount() == 2
    assert model.index(0, 4).data() == '白头鹎'
    assert model.index(1, 4).data() == '红耳鹎'
    assert model.index(0, 4).data(Qt.ItemDataRole.FontRole).bold()
    assert table.currentIndex().row() == 1
    assert model.index(1, 2).data() == ('采纳并补存' if legacy else '已采纳')
    assert controller._dialog.grab().save(str(tmp_path / 'saved-candidate-selection.png'))
    target = model.index(0, 2)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualRect(target).center())
    assert wait_for(lambda: not controller.busy)
    values = PhotoMetaDataXMP().read(paths[0])
    assert values['Title'] == '白头鹎'
    assert json.loads(values['birdid_candidates']) == candidates
    assert values['birdid_response'] == raw
    assert files.updates[-1][paths[0]]['title'] == '白头鹎'
    assert model.index(0, 2).data() == '已采纳'
    assert model.index(1, 2).data() == '采纳'
    assert controller.start_for_paths(paths[:1], saved_candidates=True)
    assert wait_for(lambda: not controller.busy)
    assert controller._dialog.details.currentIndex().row() == 0


def test_live_unsorted_candidates_default_to_highest_and_adopt_correct_row(env, monkeypatch):
    controller, files, paths, state = env
    candidates = multi_response()
    candidates['results'].reverse()
    monkeypatch.setattr(BirdIDClient, 'recognize', lambda *_: candidates)
    controller.start_for_paths(paths[:1], options=BirdIDOptions(threshold=40))
    assert wait_for(lambda: not controller.busy)
    table = controller._dialog.details
    assert table.model().index(0, 4).data() == '白头鹎'
    assert table.model().index(0, 2).data() == '已采纳'
    table._request(2)
    assert wait_for(lambda: not controller.busy)
    assert PhotoMetaDataXMP().read(paths[0])['Title'] == '黑短脚鹎'
    assert table.model().index(2, 2).data() == '已采纳'
