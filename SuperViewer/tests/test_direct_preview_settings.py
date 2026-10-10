"""User-selected thresholds must preserve quick navigation and decoder ownership."""
import pytest
from PIL import Image
from PyQt6.QtWidgets import QDialog, QMessageBox

from app_common import superviewer_user_options as options
from SuperViewer.superviewer import preview_panel
from SuperViewer.superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
from SuperViewer.tests.test_preview_info_sync import window, _APP


@pytest.fixture(autouse=True)
def isolate_options(tmp_path, monkeypatch):
    monkeypatch.setattr(options, "_RUNTIME_OPTIONS", options.normalize_user_options(None))
    monkeypatch.setattr(options, "_get_app_dir", lambda: str(tmp_path))


@pytest.mark.parametrize("size,expected", [(None, False), ((0, 100), False), ((2048, 2048), True),
    ((2049, 100), False), ((100, 2049), False), ((4096, 128), False), ((1200, 1800), True)])
def test_dimension_boundary_is_per_axis(monkeypatch, size, expected):
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 0})
    monkeypatch.setattr(preview_panel, "_preview_source_dimensions", lambda path: size)
    assert preview_panel._should_load_original_immediately("image.jpg") is expected


@pytest.mark.parametrize("size,expected", [(0, False), (1024 * 1024, True), (1024 * 1024 + 1, False)])
def test_file_size_boundary_does_not_probe_resolution(tmp_path, monkeypatch, size, expected):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"x" * size)
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_WIDTH: 0,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 1})
    monkeypatch.setattr(preview_panel, "_preview_source_dimensions", lambda p: pytest.fail("disabled dimensions read header"))
    assert preview_panel._should_load_original_immediately(str(path)) is expected


@pytest.mark.parametrize("width,height,file_mb,expected", [(64, 32, 1, True), (63, 32, 2, True),
    (64, 31, 2, True), (63, 32, 1, False), (64, 31, 1, False), (0, 32, 1, False), (64, 0, 2, True)])
def test_resolution_or_file_size_with_real_source(tmp_path, width, height, file_mb, expected):
    path = tmp_path / "图片.jpg"
    Image.new("RGB", (64, 32)).save(path)
    with path.open("ab") as output:
        output.write(b"\0" * (1024 * 1024 + 1 - path.stat().st_size))
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_WIDTH: width,
                                       options.KEY_DIRECT_PREVIEW_MAX_HEIGHT: height,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: file_mb})
    assert preview_panel._should_load_original_immediately(str(path)) is expected


def test_file_size_success_skips_header_even_with_dimensions_enabled(tmp_path, monkeypatch):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"small file")
    monkeypatch.setattr(preview_panel, "_preview_source_dimensions", lambda p: pytest.fail("file success read header"))
    assert preview_panel._should_load_original_immediately(str(path))


def test_source_dimensions_read_header_without_loading_pixels(tmp_path, monkeypatch):
    path = tmp_path / "wide.png"
    Image.new("RGB", (4096, 128)).save(path)
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 0})
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda p: pytest.fail("header check decoded pixels"))
    assert preview_panel._preview_source_dimensions(str(path)) == (4096, 128)
    assert not preview_panel._should_load_original_immediately(str(path))


def test_missing_unreadable_directory_and_raw_never_direct_decode(tmp_path, monkeypatch):
    assert not preview_panel._should_load_original_immediately(str(tmp_path / "missing.jpg"))
    assert not preview_panel._should_load_original_immediately(str(tmp_path))
    raw = tmp_path / "source.ARW"
    raw.write_bytes(b"small RAW")
    assert not preview_panel._should_load_original_immediately(str(raw))
    with monkeypatch.context() as patch:
        def denied(path):
            raise PermissionError("unreadable")
        patch.setattr(preview_panel.os, "stat", denied)
        assert not preview_panel._should_load_original_immediately(str(tmp_path / "locked.jpg"))


@pytest.mark.parametrize("width,height", [(0, 2048), (2048, 0), (0, 0)])
def test_zero_limits_do_not_probe_any_image(monkeypatch, width, height):
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_WIDTH: width,
                                       options.KEY_DIRECT_PREVIEW_MAX_HEIGHT: height,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 0})
    monkeypatch.setattr(preview_panel, "_preview_source_dimensions", lambda p: pytest.fail("disabled policy read header"))
    assert not preview_panel._should_load_original_immediately("image.jpg")


def test_settings_dimensions_and_file_size_are_enabled_together_and_cancel(tmp_path):
    before = options.get_runtime_user_options()
    dialog = SuperViewerUserOptionsDialog(options=before)
    try:
        assert dialog._spin_direct_preview_width.value() == 2048
        assert dialog._spin_direct_preview_height.value() == 2048
        assert dialog._spin_direct_preview_width.isEnabled()
        assert dialog._spin_direct_preview_height.isEnabled()
        assert dialog._spin_direct_preview_file_mb.isEnabled()
        dialog._spin_direct_preview_width.setValue(3000)
        dialog._spin_direct_preview_height.setValue(2000)
        dialog._spin_direct_preview_file_mb.setValue(16)
        selected = dialog.selected_options()
        assert selected[options.KEY_DIRECT_PREVIEW_MAX_WIDTH] == 3000
        assert selected[options.KEY_DIRECT_PREVIEW_MAX_HEIGHT] == 2000
        assert selected[options.KEY_DIRECT_PREVIEW_MAX_FILE_MB] == 16
        assert "direct_preview_limit_mode" not in selected
        dialog.reject()
        assert options.get_runtime_user_options() == before
        assert not list(tmp_path.iterdir())
    finally:
        dialog.close()


@pytest.mark.parametrize("width,height,file_mb,direct", [(40, 30, 0, True), (10, 10, 1, True), (10, 10, 0, False)])
def test_save_from_window_changes_next_selection_for_both_ab_sides(window, tmp_path, monkeypatch, width, height, file_mb, direct):
    import importlib
    main = importlib.import_module("SuperViewer.main")
    saved_paths = []
    def save(data):
        path = tmp_path / "saved-options.cfg"
        saved_paths.append(path)
        return options.save_user_options(data, str(path))
    monkeypatch.setattr(main, "save_user_options", save)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    def accept(dialog):
        dialog._spin_direct_preview_width.setValue(width)
        dialog._spin_direct_preview_height.setValue(height)
        dialog._spin_direct_preview_file_mb.setValue(file_mb)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(SuperViewerUserOptionsDialog, "exec", accept)
    window._open_user_options_dialog()
    assert saved_paths and options.load_user_options(str(saved_paths[0])) == options.get_runtime_user_options()
    window.preview_compare.set_enabled(True)
    for side in ("A", "B"):
        path = tmp_path / f"{side}.png"
        Image.new("RGB", (40, 30)).save(path)
        window.preview_compare.set_active_side(side)
        window._on_file_selected_from_list(str(path))
        panel = window.preview_compare.active_preview
        assert panel._full_preview_loaded is direct
        panel._full_preview_timer.stop()


def test_byte_mode_does_not_bypass_owned_worker_or_fast_navigation(tmp_path, monkeypatch):
    path = tmp_path / "image.jpg"
    Image.new("RGB", (40, 30)).save(path)
    panel = preview_panel.PreviewPanel()
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda p: pytest.fail("unexpected sync decode"))
    try:
        panel._full_preview_loader = object()
        assert not panel._try_set_direct_original_preview(str(path))
        panel._full_preview_loader = None
        panel.set_image(str(path), load_full=False)
        assert panel._fast_preview_only
        assert not panel._full_preview_timer.isActive()
        assert panel._full_preview_loader is None
    finally:
        panel._full_preview_loader = None
        panel.shutdown()
        panel.close()
