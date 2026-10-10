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


@pytest.mark.parametrize("pixels,expected", [(0, False), (25_000_000, True), (25_000_001, False)])
def test_custom_pixel_boundary(monkeypatch, pixels, expected):
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_MAX_PIXELS: 25_000_000})
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda path: pixels)
    assert preview_panel._should_load_original_immediately("image.jpg") is expected


@pytest.mark.parametrize("size,expected", [(0, False), (1024 * 1024, True), (1024 * 1024 + 1, False)])
def test_file_size_boundary_does_not_probe_resolution(tmp_path, monkeypatch, size, expected):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"x" * size)
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_LIMIT_MODE: options.DIRECT_PREVIEW_BY_FILE_SIZE,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 1})
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda p: pytest.fail("byte mode read image header"))
    assert preview_panel._should_load_original_immediately(str(path)) is expected


def test_missing_unreadable_directory_and_raw_never_direct_decode(tmp_path, monkeypatch):
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_LIMIT_MODE: options.DIRECT_PREVIEW_BY_FILE_SIZE})
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


@pytest.mark.parametrize("mode", [options.DIRECT_PREVIEW_BY_PIXELS, options.DIRECT_PREVIEW_BY_FILE_SIZE])
def test_zero_limit_does_not_probe_any_image(monkeypatch, mode):
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_LIMIT_MODE: mode,
                                       options.KEY_DIRECT_PREVIEW_MAX_PIXELS: 0,
                                       options.KEY_DIRECT_PREVIEW_MAX_FILE_MB: 0})
    monkeypatch.setattr(preview_panel, "_preview_source_pixel_count", lambda p: pytest.fail("disabled policy read header"))
    assert not preview_panel._should_load_original_immediately("image.jpg")


def test_settings_default_exactness_mode_switch_and_cancel(tmp_path):
    before = options.get_runtime_user_options()
    dialog = SuperViewerUserOptionsDialog(options=before)
    try:
        assert dialog.selected_options()[options.KEY_DIRECT_PREVIEW_MAX_PIXELS] == 41_943_040
        assert dialog._spin_direct_preview_mp.isEnabled()
        assert not dialog._spin_direct_preview_file_mb.isEnabled()
        dialog._spin_direct_preview_mp.setValue(50.123456)
        dialog._combo_direct_preview_mode.setCurrentIndex(1)
        dialog._spin_direct_preview_file_mb.setValue(16)
        assert not dialog._spin_direct_preview_mp.isEnabled()
        assert dialog._spin_direct_preview_file_mb.isEnabled()
        selected = dialog.selected_options()
        assert selected[options.KEY_DIRECT_PREVIEW_MAX_PIXELS] == 50_123_456
        assert selected[options.KEY_DIRECT_PREVIEW_MAX_FILE_MB] == 16
        dialog._combo_direct_preview_mode.setCurrentIndex(0)
        assert dialog._spin_direct_preview_mp.value() == 50.123456
        dialog.reject()
        assert options.get_runtime_user_options() == before
        assert not list(tmp_path.iterdir())
    finally:
        dialog.close()


@pytest.mark.parametrize("mode", [options.DIRECT_PREVIEW_BY_PIXELS, options.DIRECT_PREVIEW_BY_FILE_SIZE])
def test_save_from_window_changes_next_selection_for_both_ab_sides(window, tmp_path, monkeypatch, mode):
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
        dialog._combo_direct_preview_mode.setCurrentIndex(mode)
        dialog._spin_direct_preview_mp.setValue(0.000100)
        dialog._spin_direct_preview_file_mb.setValue(1)
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
        assert panel._full_preview_loaded is (mode == options.DIRECT_PREVIEW_BY_FILE_SIZE)
        panel._full_preview_timer.stop()


def test_byte_mode_does_not_bypass_owned_worker_or_fast_navigation(tmp_path, monkeypatch):
    path = tmp_path / "image.jpg"
    Image.new("RGB", (40, 30)).save(path)
    options.apply_runtime_user_options({options.KEY_DIRECT_PREVIEW_LIMIT_MODE: options.DIRECT_PREVIEW_BY_FILE_SIZE})
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
