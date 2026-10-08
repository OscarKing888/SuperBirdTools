"""归档菜单、配置持久化、真实后台线程及关闭交接。"""
from pathlib import Path
import threading
import time

from PIL import Image
import pytest
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import bird_archive as core, bird_archive_ui as ui

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class Files:
    def __init__(self, directory):
        self.directory = str(directory)
        self.extenders = []
        self.sources = {}
        self.tombstones = []
        self.reloads = []
        self.history_cleared = False

    def add_file_context_menu_extender(self, callback):
        self.extenders.append(callback)

    def _resolve_source_path_for_action(self, path):
        return self.sources.get(path, path)

    def get_report_row_for_path(self, path):
        return None

    def _report_scope_path_key(self, row):
        return self.directory

    def _delete_report_rows_for_paths(self, paths, *, resolved_paths, scope_keys):
        self.last_scopes = scope_keys
        self.tombstones.append((paths, resolved_paths))

    def clear_tag_history(self):
        self.history_cleared = True

    def get_current_dir(self):
        return self.directory

    def load_directory(self, directory, **kwargs):
        self.reloads.append((directory, kwargs))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.paths_settings, "_get_user_state_dir", lambda: str(tmp_path / "settings"))
    monkeypatch.setattr(core.PhotoMetaDataEXIFEmbeded, "read", lambda *_: {})
    source = tmp_path / "bird.jpg"
    Image.new("RGB", (12, 8)).save(source)
    assert PhotoMetaDataXMP().write_title(str(source), "白鹭")
    window = QWidget()
    files = Files(tmp_path)
    controller = ui.BirdArchiveController(window, files)
    yield controller, files, source, core.ArchiveOptions(str(tmp_path / "名册"))
    controller.request_shutdown()
    assert wait_for(controller.is_shutdown_done)
    window.close()
    window.deleteLater()
    _APP.processEvents()


def test_top_bold_bird_icon_menu_and_settings_round_trip(env):
    controller, files, source, options = env
    menu = QMenu()
    menu.addAction("复制")
    controller.extend_file_menu(menu, [str(source)])
    action = menu.actions()[0]
    assert action.text() == "珍禽入册…（1 张）" and action.font().bold()
    assert not action.icon().isNull() and action.isIconVisibleInMenu()
    assert menu.actions()[1].isSeparator()
    options = core.ArchiveOptions(options.directory, "copy", False)
    dialog = ui.ArchiveDialog(count=1, options=options)
    assert dialog.selected_options() == options
    dialog.accept()
    assert ui.load_archive_options() == options
    assert "名册" in ui.settings_path().read_text(encoding="utf-8")
    dialog.deleteLater()


def test_resolved_source_move_tombstones_and_refreshes_current_view(env):
    controller, files, source, options = env
    files.sources["stale-report-path.jpg"] = str(source)
    assert controller.start_for_paths(["stale-report-path.jpg"], options=options)
    worker = controller._worker
    assert wait_for(lambda: not controller.busy)
    assert not source.exists()
    assert files.tombstones == [(["stale-report-path.jpg"], [str(source)])]
    assert files.history_cleared and files.reloads == [(files.directory, {"force_reload": True})]
    assert "入册 1 张" in controller._dialog.label.text()
    before = controller._dialog.details.toPlainText()
    controller._on_result(worker, core.ArchiveResult((str(source),), status="failed", message="late"))
    assert controller._dialog.details.toPlainText() == before


def test_copy_does_not_tombstone_or_clear_history(env):
    controller, files, source, options = env
    options = core.ArchiveOptions(options.directory, "copy")
    assert controller.start_for_paths([str(source)], options=options)
    assert wait_for(lambda: not controller.busy)
    assert source.exists() and not files.tombstones and not files.history_cleared


def test_shutdown_retains_real_worker_until_finished_and_rejects_new_work(env, monkeypatch):
    controller, files, source, options = env
    entered, release = threading.Event(), threading.Event()
    original = core.read_archive_metadata

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(core, "read_archive_metadata", blocked)
    assert controller.start_for_paths([str(source)], options=options)
    assert wait_for(entered.is_set)
    worker = controller._worker
    try:
        assert not controller.start_for_paths([str(source)], options=options)
        controller.request_shutdown()
        assert controller._worker is worker and not controller.is_shutdown_done()
        assert not controller.start_for_paths([str(source)], options=options)
    finally:
        release.set()
    assert wait_for(controller.is_shutdown_done)
    assert not files.reloads and not files.tombstones
    assert not controller._dialog.isVisible()


def test_moved_photo_clears_only_its_own_viewport_and_active_info():
    from types import SimpleNamespace
    from SuperViewer.main import MainWindow
    paths, info = [], []
    class Panel:
        def __init__(self, path):
            self.path = path
        def source_identity_path(self):
            return self.path
        def set_image(self, path):
            self.path = path
    a, b = Panel('/library/a.jpg'), Panel('/library/b.jpg')
    window = SimpleNamespace(_shutdown_requested=False, preview_a=a, preview_panel=b,
        _active_preview_panel=lambda: b, on_image_loaded=info.append,
        ab_preview=SimpleNamespace(set_side_path=lambda *args, **kw: paths.append((args, kw))))
    MainWindow._on_archive_photos_moved(window, ['/library/a.jpg'])
    assert a.path == '' and b.path == '/library/b.jpg' and info == []
    MainWindow._on_archive_photos_moved(window, ['/library/b.jpg'])
    assert b.path == '' and info == ['']


@pytest.mark.parametrize("view", ["list", "thumb"])
def test_real_window_archive_updates_list_and_clears_moved_preview(tmp_path, monkeypatch, view):
    import importlib
    from app_common import superviewer_user_options
    from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
    main = importlib.import_module('SuperViewer.main')
    settings = tmp_path / 'settings'
    settings.mkdir()
    (settings / 'super_viewer.cfg').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(ui.paths_settings, '_get_app_dir', lambda: str(settings))
    monkeypatch.setattr(ui.paths_settings, '_get_user_state_dir', lambda: str(settings / 'state'))
    monkeypatch.setattr(main, '_get_app_dir', lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, '_get_app_dir', lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, 'get_user_config_dir', lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, '_RUNTIME_OPTIONS', {
        **superviewer_user_options.get_runtime_user_options(),
        'thumbnail_loader_workers': 1, 'metadata_loader_workers': 1, 'persistent_thumb_workers': 1})
    monkeypatch.setenv('LOCALAPPDATA', str(settings / 'cache'))
    library = tmp_path / 'library'
    (library / '.superpicky').mkdir(parents=True)
    tags = library / '.superpicky/tags.cfg'
    tags.write_text('珍藏\n', encoding='utf-8')
    monkeypatch.setattr(main, 'SuperViewerTaggedFileListPanel', lambda: SuperViewerTaggedFileListPanel(tag_config_path=tags))
    photo = library / '白鹭.jpg'
    Image.new('RGB', (24, 18)).save(photo)
    assert PhotoMetaDataXMP().write_title(str(photo), '白鹭')
    window = main.MainWindow(initial_received_files=['skip-restore'])
    try:
        panel = window._file_list
        panel._set_view_mode(panel._MODE_LIST if view == 'list' else panel._MODE_THUMB)
        window._on_directory_selected(str(library))
        assert wait_for(lambda: panel.get_display_file_paths() == [str(photo)])
        window._on_file_selected_from_list(str(photo))
        assert window._current_exif_path == str(photo)
        assert window._bird_archive.start_for_paths([str(photo)], options=core.ArchiveOptions(str(tmp_path / 'archive')))
        assert wait_for(lambda: not window._bird_archive.busy and not panel.get_display_file_paths())
        assert not photo.exists()
        assert window._current_exif_path == ''
        assert not window.preview_panel.source_identity_path()
    finally:
        window.close()
        assert wait_for(lambda: window._shutdown_finalized)


def test_switching_directory_during_archive_keeps_original_report_scope(env, monkeypatch):
    controller, files, source, options = env
    entered, release = threading.Event(), threading.Event()
    original = core.read_archive_metadata
    previous = files.directory
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(core, 'read_archive_metadata', blocked)
    assert controller.start_for_paths([str(source)], options=options)
    assert wait_for(entered.is_set)
    files.directory = str(source.parent / 'other-directory')
    release.set()
    assert wait_for(lambda: not controller.busy)
    assert files.last_scopes == [previous]
    assert not files.reloads and not files.history_cleared
