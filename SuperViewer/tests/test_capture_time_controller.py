"""目录选择、格式限制、失败报告及后台线程关闭。"""
from pathlib import Path
import threading
import time

from PIL import Image
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import capture_time_controller as ui
from SuperViewer.superviewer import capture_time_update as core

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    end = time.monotonic() + 8
    while time.monotonic() < end:
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
        self._all_files, self.updates, self.sources = paths, [], {}
        self.selected = list(paths)
        self.allowed = True

    def add_file_context_menu_extender(self, callback):
        self.extender = callback

    def _file_writes_allowed(self, *args, **kwargs):
        return self.allowed

    def _resolve_source_path_for_action(self, path):
        return self.sources.get(path, path)

    def sync_metadata_edits_for_paths(self, updates):
        self.updates.append(updates)
        self.file_selected.emit(next(iter(updates)))

    def deselect_display_paths_silently(self, paths):
        self.selected = [path for path in self.selected if path not in paths]


@pytest.fixture
def env(tmp_path, monkeypatch):
    paths = []
    raws = tmp_path / '原片'
    raws.mkdir()
    for i, ext in enumerate(('.png', '.JPG', '.jpeg')):
        path = tmp_path / f'鸟片{i}{ext}'
        Image.new('RGB', (24, 16)).save(path)
        paths.append(str(path))
        (raws / f'{path.stem}.ARW').write_bytes(b'raw')
    monkeypatch.setattr(core, 'read_raw_capture_time', lambda *a, **k: '2025-08-19T09:10:11.045+08:00')
    window, files = QWidget(), Files(paths)
    controller = ui.CaptureTimeController(window, files)
    yield controller, files, paths, raws
    controller.request_shutdown()
    assert wait_for(controller.is_shutdown_done)
    if controller._report:
        controller._report.close()
    window.close()
    window.deleteLater()
    _APP.processEvents()


def test_menu_formats_choose_directory_all_success_without_popup(env, monkeypatch):
    controller, files, paths, raws = env
    # 从共同根目录选择，RAW 分散在不同层级及隐藏目录时也应全部更新。
    for path, relative in zip(paths, ('2025/观鸟', '.归档/原片', '2026/十月/鸟片')):
        nested = raws / relative
        nested.mkdir(parents=True)
        raw = raws / (Path(path).stem + '.ARW')
        raw.rename(nested / raw.name)
    empty = QMenu()
    files.extender(empty, ['file.ARW', 'file.webp', 'file.hif'])
    assert not empty.actions()
    menu = QMenu()
    files.extender(menu, paths + ['file.ARW'])
    assert len(menu.actions()) == 1
    asked = []
    def choose(*args):
        asked.append(args)
        return str(raws)
    monkeypatch.setattr(ui.QFileDialog, 'getExistingDirectory', choose)
    selected = []
    files.file_selected.connect(selected.append)
    menu.actions()[0].trigger()
    assert wait_for(lambda: not controller.busy)
    assert asked and controller._succeeded == 3 and not controller._failures
    assert controller._report is None and files.updates and not selected
    assert files.selected == []
    assert all(PhotoMetaDataXMP().read(p)['date_time_original'] == '2025-08-19T09:10:11.045+08:00' for p in paths)


def test_failure_report_lists_missing_raw_and_corrupt_sidecar(env):
    controller, files, paths, raws = env
    (raws / (Path(paths[0]).stem + '.ARW')).unlink()
    Path(paths[1]).with_suffix('.xmp').write_bytes(b'<broken')
    assert controller.start(paths, str(raws))
    assert wait_for(lambda: not controller.busy)
    assert controller._succeeded == 1 and len(controller._failures) == 2
    assert controller._report.isVisible()
    text = controller._report.details.toPlainText()
    assert paths[0] in text and '未找到同名 RAW' in text
    assert paths[1] in text and 'XMP 损坏' in text
    assert paths[2] not in text
    assert files.selected == [paths[0]]


def test_missing_raw_selection_can_retry_with_another_directory_and_resolved_alias(env, tmp_path):
    controller, files, paths, raws = env
    display = str(tmp_path / '旧路径' / Path(paths[0]).name)
    files.sources[display] = paths[0]
    files._all_files = files.selected = [display, *paths[1:]]
    raw = raws / (Path(paths[0]).stem + '.ARW')
    retry = tmp_path / '另一个原片目录'
    retry.mkdir()
    raw.rename(retry / raw.name)
    assert controller.start(files.selected, str(raws))
    assert wait_for(lambda: not controller.busy)
    assert files.selected == [display]
    assert controller.start(files.selected, str(retry))
    assert wait_for(lambda: not controller.busy)
    assert files.selected == []
    assert controller._succeeded == 1 and controller._report is None


def test_cancel_directory_and_disabled_writes(env, monkeypatch):
    controller, files, paths, raws = env
    monkeypatch.setattr(ui.QFileDialog, 'getExistingDirectory', lambda *a: '')
    controller.choose_directory(paths)
    assert not controller.busy and controller._report is None
    assert files.selected == paths
    files.allowed = False
    menu = QMenu()
    files.extender(menu, paths)
    assert not menu.actions()[0].isEnabled()
    assert not controller.start(paths, str(raws))


def test_unresolved_source_reported_without_aborting_other_photos(env):
    controller, files, paths, raws = env
    files.sources[paths[0]] = None
    assert controller.start(paths, str(raws))
    assert wait_for(lambda: not controller.busy)
    assert controller._succeeded == 2
    assert paths[0] in controller._report.details.toPlainText()
    assert files.selected == []


def test_shutdown_preserves_commits_and_waits_for_actual_finished(env, monkeypatch):
    controller, files, paths, raws = env
    started, gate = threading.Event(), threading.Event()
    original = ui.update_capture_times
    def update(*args, **kwargs):
        for result in original(*args, **kwargs):
            started.set()
            assert gate.wait(5)
            yield result
    monkeypatch.setattr(ui, 'update_capture_times', update)
    assert controller.start(paths, str(raws))
    assert started.wait(3)
    try:
        worker = controller._worker
        controller.request_shutdown()
        controller._finished(object())
        assert controller._worker is worker and not controller.is_shutdown_done()
        assert not controller.start(paths, str(raws))
    finally:
        gate.set()
    assert wait_for(controller.is_shutdown_done)
    assert controller._succeeded == 1 and controller._report is None
    assert not files.updates
    assert files.selected == paths
    assert PhotoMetaDataXMP().read(paths[0])['date_time_original'] == '2025-08-19T09:10:11.045+08:00'
