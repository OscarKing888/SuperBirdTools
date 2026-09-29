"""原图播放的整组预取、磁盘缓存与缺帧保护。"""
from pathlib import Path
import threading

from PIL import Image

from test_editor_dejitter import window  # noqa: F401
from test_dejitter_tab import setup_tab
from test_reference_tracking import wait_until
from test_sequence_transport import populate
from birdstamp.gui import editor_sequence_transport as transport_module
from birdstamp.gui import editor_source_quick_loader as loader_module
from birdstamp.gui.editor_source_quick_loader import SourceQuickAction


def test_source_quick_action_reuses_disk_and_invalidates_signature(tmp_path, monkeypatch):
    path = tmp_path / '中文预览.jpg'
    cache = tmp_path / 'cache'
    Image.new('RGB', (800, 600), 'red').save(path)
    first, size = SourceQuickAction('first', path, cache, cancelled=lambda: False).execute()
    assert size == (800, 600) and max(first.size) == 512
    first.close()

    original = loader_module.decode_image_for_preview
    monkeypatch.setattr(loader_module, 'decode_image_for_preview',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('重复解码')))
    cached, size = SourceQuickAction('first', path, cache, cancelled=lambda: False).execute()
    assert size == (800, 600)
    cached.close()

    monkeypatch.setattr(loader_module, 'decode_image_for_preview', original)
    Image.new('RGB', (900, 600), 'blue').save(path)
    changed, size = SourceQuickAction('second', path, cache, cancelled=lambda: False).execute()
    assert size == (900, 600) and changed.getpixel((0, 0))[2] > 150
    changed.close()


def test_corrupt_source_cache_is_rebuilt(tmp_path):
    path = tmp_path / 'photo.png'
    Image.new('RGB', (32, 24), 'green').save(path)
    action = SourceQuickAction('stamp', path, tmp_path / 'cache', cancelled=lambda: False)
    action.cache_path.parent.mkdir(parents=True)
    action.cache_path.write_bytes(b'broken')
    image, size = action.execute()
    assert image.size == size == (32, 24)
    image.close()
    again, _ = action.execute()
    again.close()


def test_full_list_prepared_before_play_and_unreadable_photo_skipped(window, monkeypatch, tmp_path):
    paths, _, _ = setup_tab(window, monkeypatch)
    for index in range(9):
        path = tmp_path / f'frame-{index}.png'
        Image.new('RGB', (200, 160), (index * 20, 30, 40)).save(path)
        paths.append(path)
    broken = tmp_path / 'broken.png'
    broken.write_bytes(b'not an image')
    paths.append(broken)
    populate(window, paths)
    window.export_tabs.setCurrentIndex(0)
    transport = window.sequence_transport
    started = threading.Event()
    release = threading.Event()
    original = loader_module.SourceQuickAction.execute

    def delayed(self):
        if self.path == paths[1]:
            started.set()
            release.wait(5)
        return original(self)

    monkeypatch.setattr(loader_module.SourceQuickAction, 'execute', delayed)
    try:
        transport._scan_source_list()
        wait_until(started.is_set)
        transport.toggle()
        assert transport._pending_source_play and transport.mode is None
    finally:
        release.set()
    wait_until(lambda: transport.mode == 'source_play')
    transport.timer.stop()
    assert len(transport._source_ready) == len(paths) - 1
    assert len(transport._source_failed) == 1
    assert broken not in transport._active_paths()
    assert '跳过 1' in transport.preparation.text()
    assert all(SourceQuickAction(signature, path, transport._source_loader._cache_dir,
                                 cancelled=lambda: False).cache_path.is_file()
               for signature, path in transport._source_entries if path != broken)


def test_evicted_next_frame_waits_without_black_canvas(window, monkeypatch, tmp_path):
    paths, _, _ = setup_tab(window, monkeypatch)
    for index in range(9):
        path = tmp_path / f'frame-{index}.png'
        Image.new('RGB', (200, 160), (index * 20, 30, 40)).save(path)
        paths.append(path)
    populate(window, paths)
    window.export_tabs.setCurrentIndex(0)
    monkeypatch.setattr(transport_module, '_SOURCE_QUICK_CACHE_BYTES', 200 * 160 * 4 + 1)
    transport = window.sequence_transport
    transport._scan_source_list()
    wait_until(lambda: len(transport._source_ready) == len(paths))
    assert len(transport._source_cache) == 1
    transport.start('source_play', 1)
    transport.timer.stop()
    before = window.preview_label.canvas._source_pixmap
    transport._tick()
    assert window.preview_label.canvas._source_pixmap is not None
    if window.current_path != paths[1]:
        assert window.preview_label.canvas._source_pixmap is before
    wait_until(lambda: window.current_path == paths[1])
    assert window.preview_label.canvas._source_pixmap is not None


def test_pending_play_cancel_and_list_change_discard_old_results(window, monkeypatch, tmp_path):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    window.export_tabs.setCurrentIndex(0)
    transport = window.sequence_transport
    started = threading.Event()
    release = threading.Event()
    original = loader_module.SourceQuickAction.execute

    def delayed(self):
        if self.path == paths[1]:
            started.set()
            release.wait(5)
        return original(self)

    monkeypatch.setattr(loader_module.SourceQuickAction, 'execute', delayed)
    try:
        transport._scan_source_list()
        wait_until(started.is_set)
        transport.toggle()
        assert transport._pending_source_play
        transport.toggle()
        assert not transport._pending_source_play
        extra = tmp_path / '新增.png'
        Image.new('RGB', (40, 30), 'blue').save(extra)
        paths.append(extra)
        transport._scan_source_list()
        assert len(transport._source_entries) == 3
    finally:
        release.set()
    wait_until(lambda: len(transport._source_ready) == 3)
    assert transport.mode is None
