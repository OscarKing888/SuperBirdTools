"""窗口接线验证：鸟体缓存跟随已显示帧，缺帧播放同样暂停分析。"""
import os
import importlib

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent

from SuperViewer.superviewer.bird_body import BirdBodyResult
from SuperViewer.superviewer.bird_body_controller import _source_key
from SuperViewer.superviewer.qt_compat import QPixmap
from SuperViewer.tests.test_directory_selection_responsiveness import window, _APP


@pytest.mark.parametrize("side", ["a", "b"])
@pytest.mark.parametrize("memory_frame", [True, False])
def test_bird_body_fast_frame_uses_display_identity_without_io(window, monkeypatch, side, memory_frame):
    window.ab_preview.enabled.setChecked(True)
    window.ab_preview.activate(side)
    (window.ab_preview.a_panel if side == 'a' else window.ab_preview.b_panel).overlays.focus.setChecked(False)
    (window.ab_preview.a_panel if side == 'a' else window.ab_preview.b_panel).overlays.bird.setChecked(True)
    panel = window._active_preview_panel()
    source = os.path.normpath("photos/鸟.ARW")
    box = (.2, .25, .7, .8)
    window._bird_body._cache[_source_key(source)] = BirdBodyResult(box, "cached")
    window._file_list._selected_display_path = source
    window._file_list._key_navigation_playback_active = True
    pixels = QPixmap(256, 160)
    pixels.fill(Qt.GlobalColor.black)
    panel.set_quick_preview_provider(lambda *_args: pixels)
    monkeypatch.setattr(window._file_list, "preview_quick_size", lambda: 256)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("长按期间不得读文件或提交鸟体任务")

    monkeypatch.setattr(window._bird_body, "_pool_provider", forbidden)
    monkeypatch.setattr(os.path, "isfile", forbidden)
    if memory_frame:
        window._on_file_fast_preview_pixmap_requested(source, pixels, 256)
    else:
        window._on_file_fast_preview_requested("cache/hash.jpg")
    assert panel.canvas._bird_box == box
    assert not window._bird_body._timer.isActive()
    assert not panel._full_preview_timer.isActive()
    # 缺少下一帧时保持旧画面，开关不能错误使用列表中已前进的身份。
    window._file_list._selected_display_path = "photos/skipped.ARW"
    (window.ab_preview.a_panel if side == 'a' else window.ab_preview.b_panel).overlays.bird.setChecked(False)
    (window.ab_preview.a_panel if side == 'a' else window.ab_preview.b_panel).overlays.bird.setChecked(True)
    assert panel.canvas._bird_box == box
    window._on_file_fast_preview_pixmap_requested("photos/uncached.ARW", pixels, 256)
    assert panel.canvas._bird_box is None
    window._file_list._key_navigation_playback_active = False


def test_skipped_first_playback_frame_pauses_pending_analysis(window, monkeypatch):
    window.check_show_bird.setChecked(True)
    window._bird_body.show_source(window.preview_panel, "photos/prior.jpg")
    assert window._bird_body._timer.isActive()
    file_list = window._file_list
    states = []
    file_list.playback_state_changed.connect(states.append)
    monkeypatch.setattr(file_list, "_advance_key_navigation_step", lambda *_args, **_kwargs: "photos/skipped.ARW")
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, "", True)
    file_list._start_key_navigation_playback(event, view_name="list")
    assert states == [True]
    assert window._bird_body._playback_active
    assert not window._bird_body._timer.isActive()
    file_list._on_key_navigation_playback_tick()
    assert states == [True]
    file_list.stop_key_navigation_playback(commit=False)
    assert states == [True, False]


def test_new_denoised_output_refreshes_both_viewports(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.preview_a, "refresh_denoised_preview", lambda path: calls.append(("a", path)))
    monkeypatch.setattr(window.preview_panel, "refresh_denoised_preview", lambda path: calls.append(("b", path)))
    window._denoise.output_ready.emit("原图.ARW", "成片.tif")
    assert calls == [("b", "原图.ARW"), ("a", "原图.ARW")]


@pytest.mark.parametrize("memory_frame", [True, False])
def test_fast_body_identity_reuses_cached_actual_path_repair(window, monkeypatch, memory_frame):
    main = importlib.import_module("SuperViewer.main")
    old, actual = "old/鸟.ARW", "repaired/鸟.ARW"
    monkeypatch.setattr(main, "_get_cached_actual_path", lambda path: actual if path == old else None)
    window.check_show_focus.setChecked(False)
    window.check_show_bird.setChecked(True)
    window._file_list._selected_display_path = old
    window._file_list._key_navigation_playback_active = True
    box = (.2, .2, .8, .8)
    window._bird_body._cache[_source_key(actual)] = BirdBodyResult(box, "valid")
    pixels = QPixmap(256, 160)
    pixels.fill(Qt.GlobalColor.black)
    window.preview_panel.set_quick_preview_provider(lambda *_args: pixels)
    monkeypatch.setattr(window._file_list, "preview_quick_size", lambda: 256)
    if memory_frame:
        window._on_file_fast_preview_pixmap_requested(old, pixels, 256)
    else:
        window._on_file_fast_preview_requested("cache/hash.jpg")
    assert window.preview_panel.canvas._bird_box == box
    assert window.preview_panel.source_identity_path() == actual
    window.ab_preview.b_panel.source_button.click()
    assert window.preview_panel.preview_source_mode() == "raw"
    assert not window.preview_panel._full_preview_timer.isActive()
    window._file_list._key_navigation_playback_active = False
