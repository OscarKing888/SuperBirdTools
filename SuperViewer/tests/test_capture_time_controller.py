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
    photo_tags_cache_updated = pyqtSignal(object)
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

    def replace_photo_metadata_for_paths(self, updates):
        self.sync_metadata_edits_for_paths(updates)

    def clear_tag_history(self):
        pass


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
    files.extender(empty, ['file.mp4', 'file.txt'])
    assert not empty.actions()
    menu = QMenu()
    files.extender(menu, paths + ['file.ARW'])
    assert len(menu.actions()) == 2
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


@pytest.mark.parametrize('all_information', [False, True])
def test_shutdown_preserves_commits_and_waits_for_actual_finished(env, monkeypatch, all_information):
    controller, files, paths, raws = env
    started, gate = threading.Event(), threading.Event()
    update_name = 'update_all_information' if all_information else 'update_capture_times'
    if all_information:
        for raw in raws.glob('*.ARW'):
            assert PhotoMetaDataXMP().write(str(raw), {'XMP-superpicky:date_time_original': '2025-08-19T09:10:11.045+08:00'})
    original = getattr(ui, update_name)
    def update(*args, **kwargs):
        for result in original(*args, **kwargs):
            started.set()
            assert gate.wait(5)
            yield result
    monkeypatch.setattr(ui, update_name, update)
    assert controller.start(paths, str(raws), all_information=all_information)
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


def test_all_information_menu_copies_xmp_and_reports_missing_sidecar(env, monkeypatch):
    controller, files, paths, raws = env
    store = PhotoMetaDataXMP()
    raw = raws / (Path(paths[0]).stem + '.ARW')
    assert store.write(str(raw), {'XMP-dc:Title': '更新后的鸟名', 'XMP-dc:Subject': ['观鸟'],
                                  'XMP-xmp:Rating': 5, 'XMP-dc:Description': '新备注'})
    (raws / (Path(paths[2]).stem + '.ARW')).unlink()
    monkeypatch.setattr(ui.QFileDialog, 'getExistingDirectory', lambda *a: str(raws))
    menu = QMenu()
    files.extender(menu, paths)
    assert menu.actions()[1].text() == '从同名 RAW 更新所有信息…'
    menu.actions()[1].trigger()
    busy_menu = QMenu()
    files.extender(busy_menu, paths)
    assert all(not a.isEnabled() for a in busy_menu.actions())
    assert wait_for(lambda: not controller.busy)
    assert controller._succeeded == 1 and len(controller._failures) == 2
    assert files.selected == [paths[2]]
    assert '更新所有信息' in controller._report.windowTitle()
    assert '缺少 XMP' in controller._report.details.toPlainText()
    assert Path(paths[0]).with_suffix('.xmp').read_bytes() == raw.with_suffix('.xmp').read_bytes()
    meta = files.updates[-1][paths[0]]
    assert meta['title'] == '更新后的鸟名' and meta['tags'] == ['观鸟'] and meta['rating'] == 5


def test_all_information_supports_other_image_formats_but_not_video(env):
    controller, files, paths, raws = env
    for path in ('file.psd', 'file.webp', 'file.hif', 'file.ARW'):
        menu = QMenu()
        files.extender(menu, [path])
        assert len(menu.actions()) == 1 and '更新所有信息' in menu.actions()[0].text()


def test_full_copy_result_cannot_overwrite_newer_sidecar_cache(env, monkeypatch):
    controller, files, paths, raws = env
    raw = raws / (Path(paths[0]).stem + '.ARW')
    store = PhotoMetaDataXMP()
    assert store.write_title(str(raw), '来自 RAW')
    original = ui.update_all_information
    def update(*args, **kwargs):
        for result in original(*args, **kwargs):
            assert store.write_title(result.source, '复制后最新编辑')
            yield result
    monkeypatch.setattr(ui, 'update_all_information', update)
    assert controller.start(paths[:1], str(raws), all_information=True)
    assert wait_for(lambda: not controller.busy)
    assert not files.updates
    assert store.read(paths[0])['Title'] == '复制后最新编辑'


def test_shared_sidecar_refresh_preserves_each_images_dimensions(env):
    controller, files, paths, raws = env
    photo = Path(paths[0])
    sibling = photo.with_suffix('.jpg')
    Image.new('RGB', (80, 60)).save(sibling)
    files._all_files = [*paths, str(sibling)]
    raw = raws / (photo.stem + '.ARW')
    assert PhotoMetaDataXMP().write_title(str(raw), '共同标题')
    # 未选中的同侧车图像也刷新，并保留自己的内嵌宽高。
    assert controller.start(paths[:1], str(raws), all_information=True)
    assert wait_for(lambda: not controller.busy)
    updates = files.updates[-1]
    assert updates[str(photo)]['PNG:ImageWidth'] == 24
    assert updates[str(sibling)]['File:ImageWidth'] == 80
    assert updates[str(photo)]['title'] == updates[str(sibling)]['title'] == '共同标题'
