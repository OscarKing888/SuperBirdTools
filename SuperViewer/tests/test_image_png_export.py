"""图片 PNG 导出：真实解码/落盘、RAW 来源、右键及后台生命周期。"""
import hashlib
import io
import json
import os
from pathlib import Path
import threading
import time

import pytest
from PIL import Image, ImageOps
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from SuperViewer.superviewer import image_png_export as core
from SuperViewer.superviewer import image_png_export_controller as ui

_APP = QApplication.instance() or QApplication([])


def wait_until(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def photo(tmp_path):
    source = tmp_path / "中文 鸟.jpg"
    image = Image.new("RGB", (80, 40), "orange")
    image.paste("blue", (0, 0, 30, 20))
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif, icc_profile=b"test-color-profile")
    return source


def test_original_pixels_orientation_profile_and_source_retention(photo, tmp_path):
    sidecar = photo.with_suffix(".xmp")
    sidecar.write_text("中文侧车保持原样", encoding="utf-8")
    before = {p: hashlib.sha256(p.read_bytes()).digest() for p in (photo, sidecar)}
    result = core.export_image_png(photo, tmp_path / "输出")
    assert not result.error and (result.width, result.height) == (40, 80)
    with Image.open(photo) as source, Image.open(result.output_path) as output:
        assert output.format == "PNG"
        assert output.tobytes() == ImageOps.exif_transpose(source).tobytes()
        assert output.info["icc_profile"] == b"test-color-profile"
        assert output.getexif().get(274, 1) == 1
    assert all(hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in before.items())


@pytest.mark.parametrize("extension", [".HIF", ".heif", ".heic"])
def test_real_heif_full_resolution(photo, tmp_path, extension):
    import pillow_heif
    pillow_heif.register_heif_opener()
    source = tmp_path / ("相机照片" + extension)
    with Image.open(photo) as image:
        image.save(source, format="HEIF", quality=95)
    result = core.export_image_png(source, tmp_path / "heif-output")
    assert not result.error
    with Image.open(source) as original, Image.open(result.output_path) as exported:
        expected = ImageOps.exif_transpose(original)
        assert exported.size == expected.size
        assert exported.tobytes() == expected.tobytes()


def jpeg_bytes(size, color, orientation=1):
    data = io.BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    Image.new("RGB", size, color).save(data, format="JPEG", exif=exif)
    return data.getvalue()


def stub_raw(monkeypatch, inprocess=None, tags=None):
    from app_common import thumb_stream
    monkeypatch.setattr(thumb_stream, "_extract_raw_embedded_jpeg_inprocess", lambda _: inprocess)
    monkeypatch.setattr(thumb_stream, "_run_exiftool_binary_tag", lambda _, tag: (tags or {}).get(tag))
    monkeypatch.setattr(thumb_stream, "_get_piexif_thumbnail_bytes", lambda _: None)
    import rawpy
    monkeypatch.setattr(rawpy, "imread", lambda *_: pytest.fail("PNG export must not decode RAW pixels"))


def test_raw_uses_largest_jpeg_with_its_orientation(monkeypatch, tmp_path):
    source = tmp_path / "RAW 中文.ARW"
    source.write_bytes(b"RAW remains untouched")
    largest = jpeg_bytes((300, 200), "green", 6)
    stub_raw(monkeypatch, jpeg_bytes((100, 60), "red"),
             {"PreviewImage": largest, "JpgFromRaw": jpeg_bytes((160, 100), "blue")})
    result = core.export_image_png(source)
    assert not result.error and (result.width, result.height) == (200, 300)
    with Image.open(io.BytesIO(largest)) as original, Image.open(result.output_path) as output:
        assert output.tobytes() == ImageOps.exif_transpose(original).tobytes()
    assert source.read_bytes() == b"RAW remains untouched"


@pytest.mark.parametrize("data", [None, b"broken jpeg"])
def test_raw_missing_or_corrupt_jpeg_never_falls_back_to_raw(monkeypatch, tmp_path, data):
    source = tmp_path / "missing.nef"
    source.write_bytes(b"RAW")
    stub_raw(monkeypatch, data)
    result = core.export_image_png(source)
    assert "内嵌 JPG" in result.error and not result.output_path
    assert list(tmp_path.iterdir()) == [source]


def test_raw_corrupt_candidate_keeps_valid_small_jpeg(monkeypatch, tmp_path):
    source = tmp_path / "small.cr3"
    source.write_bytes(b"RAW")
    stub_raw(monkeypatch, jpeg_bytes((16, 12), "red"), {"JpgFromRaw": b"broken"})
    result = core.export_image_png(source)
    assert not result.error and (result.width, result.height) == (16, 12)


def test_png_alpha_and_case_unicode_collisions(tmp_path):
    source = tmp_path / "Café.png"
    Image.new("RGBA", (20, 10), (1, 2, 3, 40)).save(source)
    directory = tmp_path / "out"
    directory.mkdir()
    existing = directory / "CAFÉ.PNG"
    existing.write_bytes(b"do not overwrite")
    result = core.export_image_png(source, directory)
    assert Path(result.output_path).name == "Café (2).png"
    again = core.export_image_png(source, directory)
    assert Path(again.output_path).name == "Café (3).png"
    same_directory = core.export_image_png(source)
    assert Path(same_directory.output_path) != source
    assert existing.read_bytes() == b"do not overwrite"
    with Image.open(result.output_path) as image:
        assert image.mode == "RGBA" and image.getpixel((0, 0)) == (1, 2, 3, 40)


@pytest.mark.parametrize("stage", ["encode", "commit", "cancel"])
def test_failed_or_cancelled_export_removes_partial_only(photo, tmp_path, monkeypatch, stage):
    directory = tmp_path / "output"
    directory.mkdir()
    existing = directory / (photo.stem + ".png")
    existing.write_bytes(b"previous complete PNG")
    stopped = threading.Event()
    save = Image.Image.save

    def save_then_fail(image, stream, **kwargs):
        save(image, stream, **kwargs)
        if stage == "encode":
            raise OSError("injected write failure")
        stopped.set()

    if stage == "commit":
        def fail_replace(*_):
            raise OSError("injected replace failure")
        monkeypatch.setattr(core.os, "replace", fail_replace)
    else:
        monkeypatch.setattr(Image.Image, "save", save_then_fail)
    result = core.export_image_png(photo, directory, cancelled=stopped.is_set)
    assert result.cancelled if stage == "cancel" else result.error
    assert not result.output_path
    assert list(directory.iterdir()) == [existing]
    assert existing.read_bytes() == b"previous complete PNG"


def test_cli_failure_and_precancelled_no_output(photo, tmp_path, capsys):
    directory = tmp_path / "cli"
    assert core.main([str(tmp_path / "missing.hif"), str(photo), "--output", str(directory)]) == 1
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert results[0]["error"] and Path(results[1]["output_path"]).exists()
    result = core.export_image_png(photo, tmp_path / "cancel", cancelled=lambda: True)
    assert result.cancelled and not (tmp_path / "cancel").exists()


def test_raw_cancellation_stops_before_next_extraction(monkeypatch, tmp_path):
    from app_common import thumb_stream
    source = tmp_path / "cancel.dng"
    source.write_bytes(b"RAW")
    stub_raw(monkeypatch)
    stopped = threading.Event()
    calls = []

    def extract(path, tag):
        calls.append(tag)
        stopped.set()
        return jpeg_bytes((10, 10), "red")

    monkeypatch.setattr(thumb_stream, "_run_exiftool_binary_tag", extract)
    result = core.export_image_png(source, cancelled=stopped.is_set)
    assert result.cancelled and not result.error and len(calls) == 1
    assert list(tmp_path.iterdir()) == [source]


def test_big_endian_grayscale_tiff_retains_sample_values(tmp_path):
    import numpy as np
    values = np.array([[0, 400, 12345, 65535]], dtype=">u2")
    source = tmp_path / "gray.tiff"
    Image.fromarray(values).save(source)
    result = core.export_image_png(source)
    assert not result.error
    with Image.open(result.output_path) as output:
        assert [output.getpixel((x, 0)) for x in range(4)] == [0, 400, 12345, 65535]


def test_cleanup_failure_reports_retained_path(photo, tmp_path, monkeypatch):
    unlink = Path.unlink
    save = Image.Image.save

    def fail_after_save(image, stream, **kwargs):
        save(image, stream, **kwargs)
        raise OSError("write failed")

    def fail_unlink(path, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError("locked")
        return unlink(path, **kwargs)

    monkeypatch.setattr(Image.Image, "save", fail_after_save)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    result = core.export_image_png(photo, tmp_path / "out")
    leftovers = list((tmp_path / "out").iterdir())
    assert len(leftovers) == 1 and str(leftovers[0]) in result.error
    assert "write failed" in result.error


class Files:
    def add_file_context_menu_extender(self, callback):
        self.extender = callback

    def _resolve_source_path_for_action(self, path):
        return path


@pytest.fixture
def controller():
    window = QWidget()
    controller = ui.ImagePngExportController(window, Files())
    yield controller
    controller.request_shutdown()
    wait_until(controller.is_shutdown_done)
    if controller._dialog:
        controller._dialog.close()
    window.close()
    window.deleteLater()
    _APP.processEvents()


def test_menu_filters_cancel_resolved_sources_and_real_batch(controller, photo, tmp_path, monkeypatch):
    menu = QMenu()
    controller.extend_file_menu(menu, ["video.mp4", "metadata.xmp"])
    assert not menu.actions()
    controller.extend_file_menu(menu, [str(photo), "video.mp4"])
    assert menu.actions()[0].text() == "导出为 PNG…"
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *_: "")
    menu.actions()[0].trigger()
    assert controller._worker is None
    stale = str(tmp_path / "stale.jpg")
    monkeypatch.setattr(controller._files, "_resolve_source_path_for_action",
                        lambda path: str(photo) if path == stale else path)
    assert controller.start([stale, str(photo), "video.mp4", str(tmp_path / "missing.hif")], tmp_path / "out")
    assert not controller.start([str(photo)])
    busy_menu = QMenu()
    controller.extend_file_menu(busy_menu, [str(photo)])
    assert not busy_menu.actions()[0].isEnabled()
    assert busy_menu.actions()[1].text() == "查看 PNG 导出进度…"
    wait_until(controller.is_shutdown_done)
    assert (controller._done, controller._saved, controller._failed) == (2, 1, 1)
    assert not controller._dialog.running
    assert str(photo.with_suffix(".png").name) in controller._dialog.details.toPlainText()


@pytest.mark.parametrize("theme,point_size", [("light", 10), ("dark", 16)])
def test_progress_render_escape_and_owned_shutdown(controller, photo, tmp_path, monkeypatch, theme, point_size):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from SuperViewer.superviewer.ui_theme import build_palette
    entered, release = threading.Event(), threading.Event()

    def delayed(source, destination, **kwargs):
        entered.set()
        assert release.wait(10)
        return core.PngExportResult(source, cancelled=True)

    monkeypatch.setattr(ui, "export_image_png", delayed)
    assert controller.start([str(photo)], tmp_path / "out")
    wait_until(entered.is_set)
    worker = controller._worker
    dialog = controller._dialog
    try:
        dialog.setPalette(build_palette(theme))
        font = dialog.font()
        font.setPointSize(point_size)
        dialog.setFont(font)
        dialog.resize(600, 380)
        controller._drain()
        _APP.processEvents()
        for widget in (dialog.label, dialog.bar, dialog.summary, dialog.details, dialog.button):
            assert dialog.rect().contains(widget.geometry())
        assert dialog.details.height() >= 50
        assert dialog.label.height() >= dialog.label.heightForWidth(dialog.label.width())
        qa = os.environ.get("SUPERBIRD_PNG_EXPORT_QA")
        if qa:
            Path(qa).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(qa) / f"png-progress-{theme}.png"))
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert worker.cancelled.is_set()
        controller.request_shutdown()
        assert not controller.is_shutdown_done()
        assert not controller.start([str(photo)])
    finally:
        release.set()
    wait_until(controller.is_shutdown_done)
    assert not dialog.isVisible()


def test_shutdown_drains_full_result_queue(controller, tmp_path, monkeypatch):
    full = threading.Event()
    calls = []

    def export(source, destination, **kwargs):
        calls.append(source)
        if len(calls) == 33:
            full.set()
        return core.PngExportResult(source, str(Path(source).with_suffix(".png")), 1, 1)

    monkeypatch.setattr(ui, "export_image_png", export)
    assert controller.start([str(tmp_path / f"{n}.jpg") for n in range(100)])
    assert full.wait(5)
    worker = controller._worker
    controller.request_shutdown()
    assert worker.results.full() and not controller.is_shutdown_done()
    wait_until(controller.is_shutdown_done)
    assert controller._saved == len(calls) == 33


@pytest.mark.parametrize("mode", ["list", "thumbnail"])
def test_main_window_image_menu_exports_and_closes(photo, tmp_path, monkeypatch, mode):
    import importlib
    from app_common import superviewer_user_options
    from app_common.file_browser import FileListPanel
    from SuperViewer.superviewer import paths_settings
    from SuperViewer.superviewer.file_context_menu import build_file_context_menu
    from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
    main = importlib.import_module("SuperViewer.main")
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / paths_settings.CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(settings / "state"))
    monkeypatch.setattr(main, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, "get_user_config_dir", lambda: str(settings))
    monkeypatch.setenv("LOCALAPPDATA", str(settings / "cache"))
    tags = settings / "tags.cfg"
    tags.write_text("", encoding="utf-8")
    monkeypatch.setattr(main, "SuperViewerTaggedFileListPanel",
                        lambda: SuperViewerTaggedFileListPanel(tag_config_path=tags))
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path / "output"))
    monkeypatch.setattr(FileListPanel, "_prompt_create_superpicky_cache_scope", lambda self, count: False)
    window = main.MainWindow(initial_received_files=["skip-restore"])
    menu = None
    window.show()
    try:
        files = window._file_list
        files._set_view_mode(files._MODE_LIST if mode == "list" else files._MODE_THUMB)
        files.load_directory(str(photo.parent))
        wait_until(lambda: str(photo) in files._all_files)
        menu = build_file_context_menu(files, [str(photo)], str(photo), log_prefix="png-export-test")
        action = next(a for a in menu.actions() if a.text() == "导出为 PNG…")
        qa = os.environ.get("SUPERBIRD_PNG_EXPORT_QA")
        if qa:
            menu.show()
            _APP.processEvents()
            Path(qa).mkdir(parents=True, exist_ok=True)
            assert menu.grab().save(str(Path(qa) / f"png-menu-{mode}.png"))
            menu.hide()
        action.trigger()
        wait_until(window._image_png_export.is_shutdown_done)
        assert window._image_png_export._saved == 1
        assert window._image_png_export._failed == 0
    finally:
        if menu is not None:
            menu.deleteLater()
        window.close()
        wait_until(lambda: window._shutdown_finalized)
        window.deleteLater()
        _APP.processEvents()
        from app_common.exif_io.exiftool_path import _is_usable_exiftool
        _is_usable_exiftool.cache_clear()
