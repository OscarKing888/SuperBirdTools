"""Focus follows actual RAW pixels and cached held-navigation frames in A/B."""
import json
import os

import pytest
from PIL import Image

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY
from SuperViewer.superviewer import preview_panel
from SuperViewer.superviewer.qt_compat import QPixmap
from SuperViewer.tests.test_directory_selection_responsiveness import window, _APP


def qimage(size=(1000, 800), crop=None):
    image = preview_panel._qimage_from_pil_image(Image.new('RGB', size, 'black'))
    if crop:
        image.setText(RAW_FOCUS_CROP_KEY, json.dumps(crop))
    return image


def test_raw_focus_survives_toggle_upgrade_export_and_late_result(tmp_path, monkeypatch):
    raw = tmp_path / 'bird.ARW'
    raw.touch()
    panel = preview_panel.PreviewPanel()
    box = (.25, .25, .75, .75)
    monkeypatch.setattr(preview_panel, '_load_full_preview_qimage_raw', lambda path: qimage((1600, 1000)))
    try:
        panel.set_quick_pixmap(str(raw), QPixmap.fromImage(qimage((256, 160))))
        panel.set_focus_box(box)
        panel.set_show_raw(True)
        assert panel.canvas._focus_box == box  # 开关已开启，实际仍是内嵌小图。
        token = panel._preview_request_token
        decoded = preview_panel._qimage_for_pixmap_upload(qimage(crop=(.1, .1, .7, .6)))
        panel._on_full_preview_loaded(token, str(raw), decoded, 0)
        assert panel.canvas._focus_box == pytest.approx((.25, .225, .55, .475))
        assert panel.get_preview_image_size() == (1000, 800)  # 不裁掉 RAW 边缘。
        rendered = panel.render_source_pixmap_with_overlays().toImage()
        assert max(rendered.pixelColor(x, 500).green() for x in range(400, 406)) > 200  # 导出用内嵌图坐标 x=.25。
        assert panel.canvas._focus_box == pytest.approx((.25, .225, .55, .475))
        panel.set_show_raw(False)
        assert panel.canvas._focus_box == box
        panel._on_full_preview_loaded(token, str(raw), decoded, 0)
        assert panel.canvas._focus_box == box
        panel.clear_image()
        assert panel.canvas._focus_box is None
    finally:
        panel.shutdown()
        panel.close()


@pytest.mark.parametrize('side', ['a', 'b'])
@pytest.mark.parametrize('memory_frame', [True, False])
def test_show_focus_alone_draws_cached_fast_frames_without_io(window, monkeypatch, side, memory_frame):
    window.ab_preview.enabled.setChecked(True)
    window.ab_preview.activate(side)
    panel = window.preview_a if side == 'a' else window.preview_panel
    window.ab_preview.a_panel.center.setChecked(False)
    window.check_auto_focus_center.setChecked(False)
    window.check_show_focus.setChecked(True)
    source = os.path.normpath('photos/鸟.ARW')
    box = (.25, .25, .75, .75)
    window._file_list._selected_display_path = source
    window._file_list._meta_cache[source] = {'focus_box': box}
    window._file_list._key_navigation_playback_active = True
    image = QPixmap.fromImage(qimage((256, 160)))
    panel.set_quick_preview_provider(lambda *_args: image)
    monkeypatch.setattr(window._file_list, 'preview_quick_size', lambda: 256)

    def forbidden(*args, **kwargs):
        raise AssertionError('fast focus must not read source metadata or launch a full decode')

    monkeypatch.setattr(window, '_resolve_focus_metadata_source_path', forbidden)
    monkeypatch.setattr(window, '_queue_focus_loader_request', forbidden)
    monkeypatch.setattr(preview_panel, '_load_full_preview_qimage', forbidden)
    monkeypatch.setattr(preview_panel, '_load_quick_preview_pixmap', forbidden)
    monkeypatch.setattr(os.path, 'isfile', forbidden)
    if memory_frame:
        window._on_file_fast_preview_pixmap_requested(source, image, 256)
    else:
        window._on_file_fast_preview_requested('cache/hashed-256.jpg')
    assert panel.canvas._focus_box == box
    assert not panel._full_preview_timer.isActive()
    rendered = panel.canvas.render_source_pixmap_with_overlays().toImage()
    assert max(rendered.pixelColor(x, 80).green() for x in range(64, 70)) > 200
    window.check_show_focus.setChecked(False)
    assert max(panel.canvas.render_source_pixmap_with_overlays().toImage().pixelColor(x, 80).green()
               for x in range(64, 70)) == 0
    # 不将上一张照片的框留给缺少焦点的下一帧。
    window.check_show_focus.setChecked(True)
    window._file_list._meta_cache.clear()
    window._on_file_fast_preview_pixmap_requested(source, image, 256)
    assert panel.canvas._focus_box is None
    # 选中项可能已经前进（该帧未就绪），迟到元数据只补当前显示帧。
    window._file_list._selected_display_path = 'photos/next.ARW'
    window._file_list._meta_cache[source] = {'focus_box': box}
    window._on_photo_metadata_cache_updated([source])
    assert panel.canvas._focus_box == box
    window._file_list._key_navigation_playback_active = False
