"""A/B interaction and relative viewport regression tests."""
import os

import pytest
from PIL import Image
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox

from SuperViewer.tests.test_preview_info_sync import window
from SuperViewer.tests.test_ab_preview import _wait_until
from SuperViewer.superviewer.ab_preview import ABPreviewPanel
from app_common import superviewer_user_options


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS", superviewer_user_options.normalize_user_options(None))
    panel = ABPreviewPanel()
    panel.resize(1100, 600)
    panel.show()
    panel.set_enabled(True)
    _APP.processEvents()
    pixmap = QPixmap(1200, 800)
    pixmap.fill(Qt.GlobalColor.gray)
    for side in ("A", "B"):
        panel.preview_for_side(side).set_quick_pixmap(side + ".jpg", pixmap)
    try:
        yield panel
    finally:
        assert panel.shutdown(wait_timeout_ms=3000)
        panel.close()
        panel.deleteLater()
        _APP.processEvents()


def test_link_retains_relative_zoom_pan_and_resumes_after_content_change(comparison):
    a, b = [comparison.preview_for_side(side).canvas for side in ("A", "B")]
    a.apply_viewport_state((3, (.45, .4)))
    b.apply_viewport_state((4, (.6, .55)))
    before_a, before_b = a.viewport_state(), b.viewport_state()
    comparison.link_button.setChecked(True)
    assert a.viewport_state() == before_a
    assert b.viewport_state() == before_b
    a.set_display_scale_percent(a.current_display_scale_percent() * 1.25)
    assert b.viewport_state()[0] == pytest.approx(before_b[0] * 1.25)
    a.apply_viewport_state((a.viewport_state()[0], (.48, .42)))
    a.viewport_interacted.emit()
    assert b.viewport_state()[1] == pytest.approx((.63, .57), abs=.002)
    remembered = b.viewport_state()
    b.set_source_pixmap(None)
    replacement = QPixmap(2400, 1600)
    replacement.fill(Qt.GlobalColor.blue)
    b.set_source_pixmap(replacement, reset_view=True)
    assert b.viewport_state()[0] == pytest.approx(remembered[0])
    assert b.viewport_state()[1] == pytest.approx(remembered[1], abs=.002)
    comparison.link_button.setChecked(False)
    a.set_display_scale_percent(50)
    assert b.viewport_state()[0] == pytest.approx(remembered[0])


def test_toolbar_activation_and_fit_are_independent(comparison):
    a, b = [comparison.preview_for_side(side).canvas for side in ("A", "B")]
    b.set_display_scale_percent(150)
    b_before = b.viewport_state()
    QTest.mouseClick(comparison._fits["A"], Qt.MouseButton.LeftButton)
    assert comparison.active_side() == "A"
    assert b.viewport_state() == b_before
    assert a.viewport_state()[0] == pytest.approx(1)
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert comparison.active_side() == "B"


def test_each_side_grid_options_remain_independent(comparison):
    from PyQt6.QtWidgets import QToolButton
    a, b = [comparison.preview_for_side(side) for side in ("A", "B")]
    grid = next(button for button in comparison._toolbars["A"].findChildren(QToolButton)
                if button.text() == "构图线")
    grid.menu().actions()[1].trigger()
    assert a._composition_grid_mode == "thirds"
    assert b._composition_grid_mode == "none"
    assert a.canvas._composition_grid_mode == "thirds"
    widths = grid.menu().actions()[-1].menu()
    widths.actions()[2].trigger()
    assert a._composition_grid_line_width == 3
    assert b._composition_grid_line_width == 1


def test_headers_show_filenames_without_directory_choice_widgets(comparison):
    paths = [os.path.normpath(f"photos/{index}.jpg") for index in range(10000)]
    comparison.set_display_paths(paths)
    assert not any(header.findChildren(QComboBox) for header in comparison._headers.values())
    assert set(comparison.findChildren(QComboBox)) == set(comparison._scales.values())
    pixmap = QPixmap(32, 24)
    pixmap.fill()
    for path in (paths[10], "outside.jpg", paths[9999]):
        comparison.set_quick_pixmap_for_list(path, pixmap)
        assert comparison._filenames[comparison.active_side()].toolTip() == os.path.normpath(path)
    assert set(comparison.findChildren(QComboBox)) == set(comparison._scales.values())


def test_active_side_updates_cached_info_and_list_without_reloading_other_side(window, tmp_path, monkeypatch):
    paths = [str(tmp_path / f"photo-{index}.png") for index in range(3)]
    for path in paths:
        Image.new("RGB", (100, 80)).save(path)
    window._file_list._all_files = paths
    window._file_list._rebuild_views()
    window._on_file_selected_from_list(paths[0])
    ab = window.preview_compare
    ab.set_enabled(True)
    ab.set_active_side("A")
    window._on_file_selected_from_list(paths[1])
    assert window.image_info_panel.current_photo_path() == paths[1]
    events = []
    window._file_list.file_selected.connect(events.append)
    monkeypatch.setattr(ab.preview_for_side("A"), "set_image", lambda *args, **kwargs: pytest.fail("inactive reload"))
    ab.set_active_side("B")
    assert window._current_exif_path == paths[0]
    assert window.image_info_panel.current_photo_path() == paths[0]
    assert window._file_list.get_selected_display_path() == paths[0]
    assert not events
    window._file_list.select_display_path_silently(paths[2])
    window._on_file_selected_from_list(paths[2])
    assert window.image_info_panel.current_photo_path() == paths[2]
    assert window._file_list.get_selected_display_path() == paths[2]


def test_ab_quick_navigation_commits_only_active_side(window, tmp_path):
    paths = [str(tmp_path / f"photo-{index}.png") for index in range(2)]
    for path in paths:
        Image.new("RGB", (100, 80)).save(path)
    window._on_file_selected_from_list(paths[0])
    ab = window.preview_compare
    ab.set_enabled(True)
    ab.set_active_side("A")
    quick = QPixmap(40, 30)
    quick.fill()
    superviewer_user_options.apply_runtime_user_options({"direct_preview_max_width": 0, "direct_preview_max_file_mb": 0})
    window._on_file_fast_preview_pixmap_requested(paths[1], quick, 128)
    a, b = [ab.preview_for_side(side) for side in ("A", "B")]
    assert a._fast_preview_only and not a._full_preview_timer.isActive()
    assert b.current_path() == paths[0]
    superviewer_user_options.apply_runtime_user_options({})
    window._on_file_selected_from_list(paths[1])
    assert a._full_preview_loaded and not a._fast_preview_only
    assert b.current_path() == paths[0]
    assert window._current_exif_path == paths[1]


@pytest.mark.parametrize("mode", ["list", "thumb"])
def test_canvas_focus_routes_native_list_selection_and_keys(window, tmp_path, mode):
    paths = [str(tmp_path / f"photo-{i}.png") for i in range(3)]
    for path in paths:
        Image.new("RGB", (100, 80)).save(path)
    files = window._file_list
    files._all_files = paths
    files._set_view_mode(files._MODE_LIST if mode == "list" else files._MODE_THUMB)
    files._rebuild_views()
    window.resize(1700, 800)
    window.show()
    window.activateWindow()
    _APP.processEvents()
    window._on_file_selected_from_list(paths[0])
    ab = window.preview_compare
    ab.set_enabled(True)
    a, b = [ab.preview_for_side(side).canvas for side in ("A", "B")]
    _APP.processEvents()

    QTest.mouseClick(a, Qt.MouseButton.LeftButton)
    assert _APP.focusWidget() is a and ab.active_side() == "A"
    view = files._tree_widget if mode == "list" else files._list_widget
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                     pos=view.visualRect(view.model().index(2, 0)).center())
    assert ab.path_for_side("A") == paths[2]
    assert ab.path_for_side("B") == paths[0]

    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert _APP.focusWidget() is b and ab.active_side() == "B"
    assert window._current_exif_path == paths[0]
    QTest.keyClick(b, Qt.Key.Key_Down if mode == "list" else Qt.Key.Key_Right)
    _wait_until(lambda: ab.path_for_side("B") == paths[1])
    assert ab.path_for_side("B") == paths[1]
    assert ab.path_for_side("A") == paths[2]
    assert _APP.focusWidget() is b
    assert window._current_exif_path == paths[1]

    # Focus-only activation of a toolbar child must also retarget selection.
    ab._fits["A"].setFocus()
    _APP.processEvents()
    assert ab.active_side() == "A"
    assert window._current_exif_path == paths[2]
    assert files.get_selected_display_path() == paths[2]


def test_canvas_repeat_stops_on_side_focus_change(window, tmp_path):
    paths = [str(tmp_path / f"photo-{i}.png") for i in range(4)]
    for path in paths:
        Image.new("RGB", (80, 60)).save(path)
    files = window._file_list
    files._all_files = paths
    files._set_view_mode(files._MODE_LIST)
    files._rebuild_views()
    window.show()
    window.activateWindow()
    _APP.processEvents()
    window._on_file_selected_from_list(paths[0])
    ab = window.preview_compare
    ab.set_enabled(True)
    a, b = [ab.preview_for_side(side).canvas for side in ("A", "B")]
    _APP.processEvents()
    QTest.mouseClick(a, Qt.MouseButton.LeftButton)
    _APP.sendEvent(a, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    _APP.sendEvent(a, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier, "", True))
    assert files._key_navigation_playback_active
    a_path = ab.path_for_side("A")
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert not files._key_navigation_playback_active
    assert ab.active_side() == "B"
    assert ab.path_for_side("A") == a_path
    assert ab.path_for_side("B") == paths[0]


def test_preview_keyboard_reuses_clipboard_action_and_leaves_edits_alone(window, tmp_path, monkeypatch):
    photo = str(tmp_path / "photo.png")
    Image.new("RGB", (80, 60)).save(photo)
    files = window._file_list
    files._all_files = [photo]
    files._rebuild_views()
    window.show()
    window.activateWindow()
    _APP.processEvents()
    window._on_file_selected_from_list(photo)
    files.select_display_path_silently(photo)
    canvas = window.preview_compare.active_preview.canvas
    copied = []
    monkeypatch.setattr(files, "_copy_paths_to_clipboard", lambda paths: copied.append(paths))
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton)
    QTest.keyClick(canvas, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert copied == [[photo]]
    edit = window.image_info_panel.filename_edit
    edit.setFocus()
    edit.setCursorPosition(2)
    QTest.keyClick(edit, Qt.Key.Key_Left)
    assert edit.cursorPosition() == 1
    assert files.get_selected_display_path() == photo
    assert copied == [[photo]]


def test_pending_scroll_does_not_undo_side_activation(window, tmp_path, monkeypatch):
    from app_common.file_browser import _panel
    paths = [str(tmp_path / f"image-{index}.png") for index in range(2)]
    for path in paths:
        Image.new("RGB", (80, 60)).save(path)
    files = window._file_list
    files._all_files = paths
    files._rebuild_views()
    window._on_file_selected_from_list(paths[0])
    window.preview_compare.set_enabled(True)
    window.preview_compare.set_side_path("A", paths[1])
    callbacks = []
    monkeypatch.setattr(_panel.QTimer, "singleShot", lambda delay, callback: callbacks.append(callback))
    files._schedule_selection_visibility_restore(paths[0])
    window.preview_compare.set_active_side("A")
    for callback in callbacks:
        callback()
    assert files.get_selected_display_path() == paths[1]
    assert files._list_widget.currentIndex() == files._thumb_index_for_path(paths[1])
    assert window._current_exif_path == paths[1]


def test_toggle_restores_splitter_and_same_photo_drafts(window, tmp_path):
    photo = tmp_path / "same.png"
    Image.new("RGB", (80, 60)).save(photo)
    window.resize(1800, 900)
    window.show()
    window._on_file_selected_from_list(str(photo))
    _APP.processEvents()
    before = window._main_splitter.sizes()
    window.image_info_panel.comment_edit.insert("draft")
    draft = window.image_info_panel.comment_edit.text()
    ab = window.preview_compare
    ab.set_enabled(True)
    ab.set_active_side("A")
    assert window.image_info_panel.comment_edit.text() == draft
    assert window._main_splitter.sizes()[1] <= before[1]
    ab.set_enabled(False)
    _APP.processEvents()
    assert window._main_splitter.sizes()[1] == pytest.approx(before[1], abs=20)
    assert not ab.link_button.isVisible()
