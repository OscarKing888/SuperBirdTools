"""Before/after reuses only a matching full original, independently per viewport."""
import json
import os

import pytest
from PIL import Image

from SuperViewer.tests.test_directory_selection_responsiveness import _APP, _wait_until
from SuperViewer.superviewer import preview_panel as module


def image(color, crop=None):
    result = module._qimage_from_pil_image(Image.new("RGB", (1600, 1000), color))
    if crop is not None:
        result.setText(module.RAW_FOCUS_CROP_KEY, json.dumps(crop))
    return result


def show_edited(panel, path):
    panel.set_edit_before(False)
    panel.set_preview_source_mode("edited")
    panel._full_preview_timer.stop()
    panel.apply_edited_preview(panel._preview_request_token, str(path), image("blue"), full=True)


@pytest.mark.parametrize("extension", [".png", ".HIF", ".ARW"])
def test_before_reuses_full_original_immediately_with_geometry_and_view(tmp_path, monkeypatch, extension):
    path = tmp_path / ("原照片" + extension)
    Image.new("RGB", (100, 60)).save(path, format="PNG")
    reads = []
    crop = [.05, .1, .95, .9] if extension == ".ARW" else None
    def load(source):
        reads.append(source)
        return image("red", crop)
    monkeypatch.setattr(module, "_load_full_preview_qimage", load)
    panel = module.PreviewPanel()
    try:
        panel.set_image(str(path))
        _wait_until(lambda: panel._full_preview_loaded and panel._full_preview_loader is None)
        show_edited(panel, path)
        panel.canvas.set_auto_focus_center(False)
        panel.set_display_scale_percent(100)
        view = panel.canvas.viewport_state()
        for _ in range(3):
            stale_token = panel._preview_request_token
            panel.set_edit_before(True)
            assert panel._full_preview_loaded and not panel._full_preview_timer.isActive()
            assert reads == [str(path)]
            assert panel.get_preview_image_size() == (1600, 1000)
            assert panel._raw_focus_crop_box == crop
            assert panel.canvas._source_pixmap.toImage().pixelColor(0, 0).red() == 255
            assert panel.canvas.viewport_state() == view
            assert not panel.apply_edited_preview(stale_token, str(path), image("blue"), full=True)
            show_edited(panel, path)
    finally:
        panel.shutdown()
        assert panel._original_preview is None
        panel.close()


@pytest.mark.parametrize("change_size", [False, True])
def test_before_reloads_changed_file_and_never_caches_a_decode_that_changed_mid_read(tmp_path, monkeypatch, change_size):
    path = tmp_path / "被更新的原图.HIF"
    path.write_bytes(b"first")
    reads = []
    def load(source):
        reads.append(source)
        if len(reads) == 2:
            path.write_bytes(b"changed again during decoding")
        return image("red" if len(reads) == 1 else "green")
    monkeypatch.setattr(module, "_load_full_preview_qimage", load)
    panel = module.PreviewPanel()
    try:
        panel.set_image(str(path))
        _wait_until(lambda: panel._full_preview_loaded)
        show_edited(panel, path)
        stat = path.stat()
        path.write_bytes(b"replacement" if change_size else b"later")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        panel.set_edit_before(True)
        assert not panel._full_preview_loaded and panel._original_preview is None
        _wait_until(lambda: panel._full_preview_loaded)
        assert len(reads) == 2 and panel._original_preview is None
        show_edited(panel, path)
        panel.set_edit_before(True)
        _wait_until(lambda: panel._full_preview_loaded)
        assert len(reads) == 3 and panel._original_preview is not None
        show_edited(panel, path)
        path.unlink()
        panel.set_edit_before(True)
        _wait_until(lambda: "原图预览失败" in panel._preview_note)
        assert panel._original_preview is None
    finally:
        panel.shutdown()
        panel.close()


@pytest.mark.parametrize("source_mode", ["raw", "denoised"])
def test_other_sources_are_never_reused_as_original(tmp_path, monkeypatch, source_mode):
    path = tmp_path / "原始.ARW"
    path.touch()
    reads = []
    def load(source):
        reads.append(source)
        return image("red")
    monkeypatch.setattr(module, "_load_full_preview_qimage", load)
    monkeypatch.setattr(module, "_load_sensor_raw_qimage", lambda *a, **k: image("blue"))
    monkeypatch.setattr(module, "_load_denoised_preview_qimage", lambda *a, **k: image("blue"))
    panel = module.PreviewPanel()
    try:
        panel.set_preview_source_mode(source_mode)
        panel.set_image(str(path))
        _wait_until(lambda: panel._full_preview_loaded)
        assert panel._original_preview is None
        panel.set_edit_before(True)
        panel.set_preview_source_mode("edited")
        _wait_until(lambda: panel._full_preview_loaded)
        assert reads == [str(path)]
        assert panel.canvas._source_pixmap.toImage().pixelColor(0, 0).red() == 255
    finally:
        panel.shutdown()
        panel.close()


def test_cache_is_per_viewport_bounded_and_does_no_io_during_held_navigation(tmp_path, monkeypatch):
    paths = [tmp_path / "A.png", tmp_path / "B.png"]
    for path, color in zip(paths, ("red", "green")):
        Image.new("RGB", (100, 60), color).save(path)
    panels = [module.PreviewPanel(), module.PreviewPanel()]
    try:
        for panel, path in zip(panels, paths):
            panel.set_image(str(path))
            show_edited(panel, path)
        panels[0].set_edit_before(True)
        assert panels[0].canvas._source_pixmap.toImage().pixelColor(0, 0).red() == 255
        panels[1].set_edit_before(True)
        assert panels[1].canvas._source_pixmap.toImage().pixelColor(0, 0).green() == 128
        panel = panels[0]
        panel.set_navigation_playback_active(True)
        def forbidden(*a):
            pytest.fail("held navigation must not query source fingerprints")
        monkeypatch.setattr(panel, "_original_preview_key", forbidden)
        panel.set_edit_before(False)
        panel.set_edit_before(True)
        panel.set_quick_pixmap(str(paths[1]), module.QPixmap.fromImage(image("blue")), quick_size=128)
        assert panel._original_preview is None
        assert not panel._full_preview_loaded
        assert panels[1]._original_preview is not None
        panels[1].clear_image()
        assert panels[1]._original_preview is None
    finally:
        for panel in panels:
            panel.shutdown()
            panel.close()
