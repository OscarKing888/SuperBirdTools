"""Viewer integration for the selected browsing/UI backports."""
import pytest
from PIL import Image
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QDialogButtonBox, QMenu, QStyle

from app_common import superviewer_user_options
from app_common.collapsible_section import CollapsibleSection
from SuperViewer.superviewer import preview_panel
from SuperViewer.superviewer.file_context_menu import build_file_context_menu
from SuperViewer.superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
from SuperViewer.tests.test_preview_info_sync import window, _APP


def pixels(width, height):
    result = preview_panel.QImage(width, height, preview_panel._qimage_rgb888_format())
    result.fill(30)
    return result


@pytest.mark.parametrize("quick_first", [False, True])
def test_first_fit_and_upgrade_then_preserve_user_view(tmp_path, monkeypatch, quick_first):
    photo = tmp_path / "首图.jpg"
    Image.new("RGB", (1200, 800)).save(photo)
    monkeypatch.setattr(preview_panel, "_should_load_original_immediately", lambda p: not quick_first)
    monkeypatch.setattr(preview_panel, "_load_full_preview_qimage", lambda p: pixels(1200, 800))
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *a: QPixmap.fromImage(pixels(120, 80)))
    panel = preview_panel.PreviewPanel()
    panel.resize(640, 480)
    panel.show()
    _APP.processEvents()
    panel.set_keep_view_on_switch(True)
    try:
        panel.set_image(str(photo))
        panel._full_preview_timer.stop()
        assert panel.canvas._zoom == pytest.approx(1)
        if quick_first:
            panel.resize(800, 600)
            _APP.processEvents()
            panel._on_full_preview_loaded(panel._preview_request_token, str(photo), pixels(1200, 800), 0)
            assert panel.canvas._zoom == pytest.approx(1)
        panel.set_display_scale_percent(100)
        zoom = panel.canvas._zoom
        panel.set_quick_pixmap(str(tmp_path / "next.jpg"), QPixmap.fromImage(pixels(120, 80)))
        assert panel.canvas._zoom == pytest.approx(zoom)
        panel.clear_image()
        panel.set_image(str(photo))
        panel._full_preview_timer.stop()
        assert panel.canvas._zoom == pytest.approx(1)
    finally:
        panel.shutdown()
        panel.close()


def test_manual_first_frame_zoom_wins_over_async_fit(tmp_path):
    panel = preview_panel.PreviewPanel()
    panel.resize(640, 480)
    panel.show()
    _APP.processEvents()
    path = str(tmp_path / "first.ARW")
    try:
        panel.set_keep_view_on_switch(True)
        panel.set_quick_pixmap(path, QPixmap.fromImage(pixels(120, 80)))
        panel.set_display_scale_percent(75)
        zoom = panel.canvas._zoom
        panel._on_full_preview_loaded(panel._preview_request_token, path, pixels(1200, 800), 0)
        assert panel.canvas._zoom == pytest.approx(zoom)
    finally:
        panel.shutdown()
        panel.close()


def test_pending_directory_fit_does_not_refit_old_decoder_result(tmp_path):
    panel = preview_panel.PreviewPanel()
    panel.resize(640, 480)
    panel.show()
    _APP.processEvents()
    path = str(tmp_path / "old.ARW")
    try:
        panel.set_keep_view_on_switch(True)
        panel.set_quick_pixmap(path, QPixmap.fromImage(pixels(120, 80)))
        panel.set_display_scale_percent(75)
        zoom = panel.canvas._zoom
        panel.fit_next_image()
        panel._on_full_preview_loaded(panel._preview_request_token, path, pixels(1200, 800), 0)
        assert panel.canvas._zoom == pytest.approx(zoom)
        panel.set_image(path)
        assert panel.canvas._zoom == pytest.approx(1)
    finally:
        panel.shutdown()
        panel.close()


def test_committed_selection_in_both_ab_sides_skips_quick_image(window, tmp_path, monkeypatch):
    paths = []
    for index in range(3):
        path = tmp_path / f"中文{index}.jpg"
        Image.new("RGB", (800, 600)).save(path)
        paths.append(str(path))
    monkeypatch.setattr(preview_panel, "_load_quick_preview_pixmap", lambda *a: pytest.fail("committed small photo used thumbnail"))
    window._on_file_selected_from_list(paths[0])
    window.preview_compare.set_enabled(True)
    for side, path in zip(("A", "B"), paths[1:]):
        window.preview_compare.set_active_side(side)
        window._on_file_selected_from_list(path)
        panel = window.preview_compare.preview_for_side(side)
        assert panel._full_preview_loaded and not panel._full_preview_timer.isActive()
        assert panel.get_preview_image_size() == (800, 600)


def test_directory_first_fit_leaves_pinned_ab_view_untouched(window, tmp_path, monkeypatch):
    window.show()
    _APP.processEvents()
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (1200, 800)).save(photo)
    window._on_file_selected_from_list(str(photo))
    window.preview_compare.set_enabled(True)
    pinned = window.preview_compare.preview_for_side("A")
    pinned.set_display_scale_percent(150)
    monkeypatch.setattr(window._file_list, "load_directory", lambda *a, **k: None)
    window._on_directory_selected(str(tmp_path / "next"))
    assert pinned.current_display_scale_percent() == pytest.approx(150, abs=1)
    next_photo = tmp_path / "second.jpg"
    Image.new("RGB", (1800, 1200)).save(next_photo)
    window.preview_compare.set_active_side("B")
    window._on_file_selected_from_list(str(next_photo))
    assert window.preview_compare.active_preview.canvas._zoom == pytest.approx(1)


def test_settings_drafts_survive_navigation_collapse_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    opts = superviewer_user_options.normalize_user_options(None)
    dialog = SuperViewerUserOptionsDialog(options=opts)
    dialog.show()
    _APP.processEvents()
    try:
        dialog._spin_metadata_loader_workers.setValue(3)
        dialog._combo_persistent_thumb_size.setCurrentIndex(2)
        for group in dialog.findChildren(CollapsibleSection):
            group.set_expanded(False)
            group.set_expanded(True)
        dialog.tabs.setCurrentIndex(1)
        dialog.resize(640, 420)
        _APP.processEvents()
        assert dialog.buttons.isVisible()
        assert dialog.buttons.geometry().top() > dialog.tabs.geometry().bottom()
        values = dialog.selected_options()
        assert values["metadata_loader_workers"] == 3
        assert values["persistent_thumb_max_size"] == 512
        assert len(values) == 7
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        assert not list(tmp_path.iterdir())
    finally:
        dialog.close()


def test_menu_grouping_feedback_and_source_copy(window, tmp_path, monkeypatch):
    photo = tmp_path / "原始 文件.jpg"
    Image.new("RGB", (8, 6)).save(photo)
    panel = window._file_list
    monkeypatch.setattr(panel, "_file_writes_allowed", lambda *a, **k: False)
    monkeypatch.setattr(panel, "rating_writes_allowed", lambda *a, **k: False)
    previous = _APP.clipboard().text()
    menu = build_file_context_menu(panel, [str(photo)], str(photo), log_prefix="test")
    try:
        actions = [a for a in menu.actions() if not a.isSeparator() and a.isVisible()]
        assert all(a.text().startswith(label) for a, label in zip(actions[:3], ("复制", "粘贴", "剪切")))
        assert actions[0].isEnabled() and not actions[1].isEnabled() and not actions[2].isEnabled()
        names = [a.text() for a in actions]
        assert "定位文件" in names and "发送到" in names
        assert "鸟种信息" not in names and "分析与处理" not in names
        locate = next(a.menu() for a in actions if a.text() == "定位文件")
        next(a for a in locate.actions() if a.text() == "复制文件全路径").trigger()
        assert _APP.clipboard().text() == str(photo)
        for child in [menu, *menu.findChildren(QMenu)]:
            assert child.style().styleHint(QStyle.StyleHint.SH_Menu_SubMenuPopupDelay) == 100
        assert "item:selected:enabled" in menu.styleSheet()
    finally:
        _APP.clipboard().setText(previous)
        menu.deleteLater()
