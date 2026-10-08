"""Viewer A/B selection, independent media and viewport ownership."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from SuperViewer.tests.test_directory_selection_responsiveness import window
from SuperViewer.tests.test_video_preview import clip


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def _image(path, color):
    Image.new("RGB", (96, 72), color).save(path)
    return str(path)


def test_activation_routes_selection_and_information_without_reloading_other_side(window, tmp_path, monkeypatch):
    first = _image(tmp_path / "甲.jpg", "red")
    second = _image(tmp_path / "乙.jpg", "blue")
    window.show()
    window._on_file_selected_from_list(first)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    assert ab.active_side == "b"
    assert window.preview_a.current_path() == first
    assert window.preview_panel.current_path() == first
    ab.activate("a")
    window._on_file_selected_from_list(second)
    assert window.preview_a.current_path() == second

    from PyQt6.QtGui import QPixmap
    quick = QPixmap(48, 36)
    quick.fill(Qt.GlobalColor.green)
    window._on_file_fast_preview_pixmap_requested(first, quick, 128)
    assert window.preview_a._fast_preview_only
    assert window.preview_panel.current_path() == first
    window._on_file_selected_from_list(second)
    assert window.preview_a._full_preview_loaded
    assert window.preview_panel.current_path() == first
    assert window._current_exif_path == second
    assert window.image_info_panel.current_photo_path() == second
    selections = []
    monkeypatch.setattr(window._file_list, "select_display_path_silently", lambda path: selections.append(path))
    ab.activate("b")
    assert selections == [first]
    assert window._current_exif_path == first
    assert window.image_info_panel.current_photo_path() == first
    assert window.preview_a.current_path() == second


def test_splitter_contracts_and_restores_and_directory_clear(window, tmp_path, monkeypatch):
    source = _image(tmp_path / "one.jpg", "green")
    window.resize(1800, 820)
    window.show()
    _APP.processEvents()
    window._on_file_selected_from_list(source)
    before = window._main_splitter.sizes()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    _APP.processEvents()
    assert window._file_list.minimumWidth() == 260
    assert window._main_splitter.sizes()[1] <= before[1]
    assert ab.a_panel.isVisible()
    sizes = window._main_splitter.sizes()
    sizes[1] += 80
    sizes[2] -= 80
    window._main_splitter.setSizes(sizes)
    assert window._main_splitter.sizes()[1] > 260
    monkeypatch.setattr(window._file_list, "load_directory", lambda _path, **kwargs: None)
    window._on_directory_selected(str(tmp_path))
    assert window.preview_a.current_path() is None
    assert window.preview_panel.current_path() is None
    ab.enabled.setChecked(False)
    _APP.processEvents()
    assert window._file_list.minimumWidth() == 520
    assert not ab.a_panel.isVisible()
    assert window._main_splitter.sizes()[1] == pytest.approx(before[1], abs=20)


def test_linked_zoom_and_focus_mutual_exclusion(window):
    window.resize(1800, 820)
    window.show()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    from PyQt6.QtGui import QPixmap
    pixmap = QPixmap(1000, 700)
    pixmap.fill(Qt.GlobalColor.black)
    for side, panel in (("a", window.preview_a), ("b", window.preview_panel)):
        panel.set_quick_pixmap(f"{side}.jpg", pixmap, quick_size=2048)
        ab.set_side_path(side, f"{side}.jpg")
    ab.linked.setChecked(True)
    a_before = window.preview_a.canvas.viewport_state()
    b_before = window.preview_panel.canvas.viewport_state()
    assert a_before is not None and b_before is not None
    window.preview_a.canvas.set_display_scale_percent(180)
    a_after = window.preview_a.canvas.viewport_state()
    b_after = window.preview_panel.canvas.viewport_state()
    assert b_after[0] / b_before[0] == pytest.approx(a_after[0] / a_before[0], rel=0.01)
    ab.a_panel.center.setChecked(True)
    assert not ab.linked.isChecked()


def test_two_video_viewports_keep_independent_players_and_active_info(window, clip, tmp_path):
    other = tmp_path / "另一个.mp4"
    other.write_bytes(clip.read_bytes())
    window.resize(1500, 850)
    window.show()
    window._on_file_selected_from_list(str(clip))
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    ab.activate("a")
    window._on_file_selected_from_list(str(other))
    _wait_until(lambda: window.preview_a._video_worker is None and window.preview_panel._video_worker is None)
    assert window.width() == 1500
    assert ab.a_panel.width() >= 320 and ab.b_panel.width() >= 320
    assert window.video_info_panel.path == str(other)
    assert ab.video_info["a"][0] == str(other)
    assert ab.video_info["b"][0] == str(clip)
    window.preview_a.video_view.toggle_play()
    window.preview_panel.video_view.toggle_play()
    _wait_until(lambda: window.preview_a.video_view.player is not None
                and window.preview_panel.video_view.player is not None
                and window.preview_a.video_view._position > 100
                and window.preview_panel.video_view._position > 100)
    assert window.preview_a.video_view.audio is not window.preview_panel.video_view.audio
    window.preview_a.video_view.mute.setChecked(True)
    assert window.preview_a.video_view.audio.isMuted()
    assert not window.preview_panel.video_view.audio.isMuted()
    ab.activate("b")
    assert window.video_info_panel.path == str(clip)
    assert window.preview_a.video_view.player.playbackState() == window.preview_a.video_view.player.PlaybackState.PlayingState
    assert window.preview_panel.video_view.player.playbackState() == window.preview_panel.video_view.player.PlaybackState.PlayingState
    from PyQt6.QtTest import QTest
    QTest.mouseClick(window.preview_a.video_view.video, Qt.MouseButton.LeftButton)
    assert ab.active_side == "a"


def test_real_list_highlight_switches_without_emitting_selection(window, tmp_path):
    first = _image(tmp_path / "first.jpg", "red")
    second = _image(tmp_path / "second.jpg", "blue")
    (tmp_path / ".superpicky").mkdir()
    window.show()
    window._file_list.load_directory(str(tmp_path))
    _wait_until(lambda: len(window._file_list.get_display_file_paths()) == 2)
    events = []
    window._file_list.file_selected.connect(events.append)
    assert window._file_list.select_display_path_silently(first)
    assert window._file_list.get_selected_display_path() == first
    assert window._file_list.select_display_path_silently(second)
    assert window._file_list.get_selected_display_path() == second
    assert events == []


def test_focus_boxes_remain_bound_to_each_photo(window, tmp_path):
    first = _image(tmp_path / "focus-a.jpg", "red")
    second = _image(tmp_path / "focus-b.jpg", "blue")
    window._file_list._meta_cache[first] = {"focus_box": (0.1, 0.2, 0.3, 0.4)}
    window._file_list._meta_cache[second] = {"focus_box": (0.6, 0.5, 0.8, 0.7)}
    window._on_file_selected_from_list(first)
    window.ab_preview.enabled.setChecked(True)
    window.ab_preview.activate("a")
    window._on_file_selected_from_list(second)
    assert window.preview_a.canvas._focus_box == (0.6, 0.5, 0.8, 0.7)
    assert window.preview_panel.canvas._focus_box == (0.1, 0.2, 0.3, 0.4)


def test_late_focus_and_video_results_cannot_update_another_side(window, tmp_path):
    first = _image(tmp_path / "a.jpg", "red")
    second = _image(tmp_path / "b.jpg", "blue")
    window._on_file_selected_from_list(first)
    window.ab_preview.enabled.setChecked(True)
    window.ab_preview.activate("a")
    window._on_file_selected_from_list(second)
    window._focus_display_request_id = 41
    window._focus_display_panel = window.preview_panel
    window._on_focus_box_loaded(41, (0.1, 0.1, 0.2, 0.2), first)
    assert window.preview_a.canvas._focus_box != (0.1, 0.1, 0.2, 0.2)
    window._focus_display_panel = window.preview_a
    window._on_focus_box_loaded(40, (0.3, 0.3, 0.4, 0.4), second)
    assert window.preview_a.canvas._focus_box != (0.3, 0.3, 0.4, 0.4)
    window._on_focus_box_loaded(41, (0.5, 0.5, 0.6, 0.6), second)
    assert window.preview_a.canvas._focus_box == (0.5, 0.5, 0.6, 0.6)
    window._on_video_info_ready("a", str(tmp_path / "old.mp4"), {"duration": 1}, "")
    assert window.ab_preview.video_info["a"] is None


def test_photo_viewports_keep_matching_toolbar_and_canvas_rows(window, tmp_path):
    source = _image(tmp_path / "preview.jpg", "green")
    window.resize(1600, 900)
    window.show()
    window._on_file_selected_from_list(source)
    window.ab_preview.enabled.setChecked(True)
    _APP.processEvents()
    ab = window.ab_preview
    assert ab.a_panel.toolbar.height() == ab.b_panel.toolbar.height()
    a_y = ab.a_panel.viewport_frame.mapToGlobal(ab.a_panel.viewport_frame.rect().topLeft()).y()
    b_y = ab.b_panel.viewport_frame.mapToGlobal(ab.b_panel.viewport_frame.rect().topLeft()).y()
    assert a_y == b_y


@pytest.mark.parametrize("kind", ("small", "large", "raw"))
def test_active_a_uses_normal_loading_policy_without_touching_b(window, tmp_path, monkeypatch, kind):
    first = _image(tmp_path / "first.jpg", "red")
    source = tmp_path / ("second.ARW" if kind == "raw" else "second.jpg")
    if kind == "raw":
        source.write_bytes(b"raw fixture")
    else:
        Image.new("RGB", (1200 if kind == "large" else 96, 800 if kind == "large" else 72)).save(source)
    if kind == "large":
        from SuperViewer.superviewer import preview_panel
        monkeypatch.setattr(preview_panel, "_SYNC_FULL_PREVIEW_MAX_PIXELS", 1)
    window._on_file_selected_from_list(first)
    window.ab_preview.enabled.setChecked(True)
    window.ab_preview.activate("a")
    window._on_file_selected_from_list(str(source))
    assert window.preview_panel.current_path() == first
    assert window.preview_a.current_path() == str(source)
    if kind == "small":
        assert window.preview_a._full_preview_loaded
    else:
        assert not window.preview_a._full_preview_loaded
        assert window.preview_a._full_preview_timer.isActive()
        window.preview_a._full_preview_timer.stop()
