"""Initialization/directory first-frame fit without changing later view retention."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PIL import Image

from SuperViewer.superviewer import preview_panel
from SuperViewer.superviewer.qt_compat import QPixmap
from SuperViewer.tests.test_directory_selection_responsiveness import window, _APP


def image(width, height):
    return preview_panel._qimage_from_pil_image(Image.new('RGB', (width, height), 'black'))


@pytest.mark.parametrize('auto_center', [False, True])
@pytest.mark.parametrize('kind', ['small', 'large', 'raw', 'heif'])
def test_first_frame_and_async_upgrade_fit_but_next_photo_keeps_view(tmp_path, monkeypatch, auto_center, kind):
    suffix = {'small': '.jpg', 'large': '.jpg', 'raw': '.ARW', 'heif': '.HIF'}[kind]
    source = tmp_path / ('first' + suffix)
    source.touch()
    panel = preview_panel.PreviewPanel()
    panel.resize(640, 480)
    panel.show()
    _APP.processEvents()
    panel.set_keep_view_on_switch(True)
    panel.set_auto_focus_center(auto_center)
    monkeypatch.setattr(preview_panel, '_should_load_full_preview_sync', lambda path: kind == 'small')
    monkeypatch.setattr(preview_panel, '_load_full_preview_qimage', lambda path: image(1200, 800))
    panel.set_quick_preview_provider(lambda *_args: QPixmap.fromImage(image(256, 170)))
    try:
        panel.set_image(str(source), quick_size=256)
        panel._full_preview_timer.stop()
        assert panel.canvas._zoom == pytest.approx(1)
        if kind != 'small':
            # 模拟后台原生尺寸到达，宽高比也可与小图略有差异。
            panel.resize(800, 600)
            _APP.processEvents()
            panel._on_full_preview_loaded(panel._preview_request_token, str(source), image(3200, 2000), 0)
            assert panel.canvas._zoom == pytest.approx(1)
        fitted = panel.current_display_scale_percent()
        assert fitted < 100
        panel.set_display_scale_percent(fitted * 2)
        before = panel.canvas._zoom
        panel.set_quick_pixmap(str(tmp_path / ('next' + suffix)), QPixmap.fromImage(image(256, 160)), quick_size=256)
        assert panel.canvas._zoom == pytest.approx(before, rel=.08)
        panel.clear_image()
        panel.set_image(str(source), quick_size=256)
        panel._full_preview_timer.stop()
        assert panel.canvas._zoom == pytest.approx(1)
    finally:
        panel.shutdown()
        panel.close()


def test_manual_zoom_on_first_thumbnail_is_not_overwritten_by_upgrade(tmp_path):
    source = tmp_path / 'first.ARW'
    source.touch()
    panel = preview_panel.PreviewPanel()
    panel.resize(640, 480)
    panel.show()
    _APP.processEvents()
    panel.set_keep_view_on_switch(True)
    panel.set_quick_preview_provider(lambda *_args: QPixmap.fromImage(image(256, 160)))
    try:
        panel.set_image(str(source), quick_size=256)
        panel._full_preview_timer.stop()
        panel.set_display_scale_percent(panel.current_display_scale_percent() * 2)
        panel._on_full_preview_loaded(panel._preview_request_token, str(source), image(3200, 2000), 0)
        assert panel.canvas._zoom == pytest.approx(2)
    finally:
        panel.shutdown()
        panel.close()


def test_directory_switch_resets_each_ab_first_image(window, tmp_path, monkeypatch):
    import importlib
    main = importlib.import_module("SuperViewer.main")
    window.show()
    _APP.processEvents()
    first = tmp_path / 'first.jpg'
    second = tmp_path / 'second.jpg'
    Image.new('RGB', (1600, 1000)).save(first)
    Image.new('RGB', (1600, 1000)).save(second)
    monkeypatch.setattr(main, 'save_last_selected_directory_to_settings', lambda path: None)
    directories = []
    def load_directory(path, *, force_reload=False):
        assert force_reload is False
        directories.append(path)
    monkeypatch.setattr(window._file_list, 'load_directory', load_directory)
    window._on_file_selected_from_list(str(first))
    window.ab_preview.enabled.setChecked(True)
    for panel in (window.preview_a, window.preview_panel):
        panel.set_auto_focus_center(True)
        panel.set_display_scale_percent(panel.current_display_scale_percent() * 3)
        assert panel.canvas._zoom > 1
    window._on_directory_selected(str(tmp_path))
    assert directories == [str(tmp_path)]
    for side, panel in [('a', window.preview_a), ('b', window.preview_panel)]:
        window.ab_preview.activate(side)
        window._on_file_selected_from_list(str(second))
        assert panel.canvas._zoom == pytest.approx(1)
        rect = panel.canvas._display_rect()
        assert rect.width() <= panel.canvas.contentsRect().width() + 1
        assert rect.height() <= panel.canvas.contentsRect().height() + 1


def test_first_uncached_raw_waits_for_pixels_before_fitting(tmp_path):
    source = tmp_path / 'uncached.ARW'
    source.touch()
    panel = preview_panel.PreviewPanel()
    panel.resize(640, 480)
    panel.show()
    _APP.processEvents()
    panel.set_auto_focus_center(True)
    try:
        panel.set_quick_pixmap('previous.jpg', QPixmap.fromImage(image(256, 160)))
        panel.set_display_scale_percent(panel.current_display_scale_percent() * 3)
        panel.clear_image()
        panel.set_image(str(source), quick_size=256)
        panel._full_preview_timer.stop()
        assert not panel._has_canvas_pixmap()
        assert panel._fit_next_image
        panel._on_full_preview_loaded(panel._preview_request_token, str(source), image(3200, 2000), 0)
        assert panel.canvas._zoom == pytest.approx(1)
        assert not panel._fit_next_image
    finally:
        panel.shutdown()
        panel.close()
