"""Transport regression checks against decoded video frames and real Qt key events."""
import math

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLineEdit

from app_common.video import find_ffmpeg, run_video_tool
from SuperViewer.superviewer.video_playback import FrameClock
from SuperViewer.tests.test_video_preview import _APP, clip, panel, wait_until


def shown_frame(view):
    if view.player is None:
        return -1
    frame = view.video.videoSink().videoFrame()
    if not frame.isValid():
        return -1
    return round(frame.startTime() * view._fps / 1_000_000)


def load(panel, clip):
    panel.set_image(str(clip))
    wait_until(lambda: panel._video_worker is None)
    return panel.video_view


def move_held_mouse(slider, fraction):
    point = QPoint(slider._MARGIN + round(fraction * slider._span()), slider.height() // 2)
    # 不处理事件循环地模拟密集移动，验证定时合并而非逐事件解码。
    _APP.sendEvent(slider, QMouseEvent(
        QEvent.Type.MouseMove, QPointF(point), QPointF(slider.mapToGlobal(point)),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    return point


@pytest.mark.parametrize('fps', [24, 25, 30000 / 1001, 60000 / 1001])
def test_absolute_frame_positions_do_not_accumulate_rounding(fps):
    clock = FrameClock(3_600_000, fps)
    for index in (0, 1, 2, 29, 1000, 10000, clock.last_frame):
        position = clock.position(index)
        assert index <= position * fps / 1000 < index + 1
        assert clock.frame_at(position) == index
    assert clock.position(-1) == 0
    assert clock.position(clock.last_frame + 1) == clock.position(clock.last_frame)
    assert not FrameClock(0, fps).available
    assert not FrameClock(2000, math.nan).available


def test_step_before_play_both_directions_rapid_requests_and_edges(panel, clip):
    view = load(panel, clip)
    assert view.player is None
    QTest.keyClick(view.poster, Qt.Key.Key_Right)
    wait_until(lambda: shown_frame(view) == 1)
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    QTest.keyClick(view.video, Qt.Key.Key_Left)
    wait_until(lambda: shown_frame(view) == 0)
    QTest.mouseClick(view.previous_frame, Qt.MouseButton.LeftButton)
    assert view._step_frame == 0
    # 不处理事件就连续发按键，解码尚未返回时仍必须累计目标帧。
    for _ in range(5):
        QTest.keyClick(view.filmstrip, Qt.Key.Key_Right)
    wait_until(lambda: shown_frame(view) == 5)
    QTest.mouseClick(view.previous_frame, Qt.MouseButton.LeftButton)
    wait_until(lambda: shown_frame(view) == 4)
    QTest.mouseClick(view.next_frame, Qt.MouseButton.LeftButton)
    wait_until(lambda: shown_frame(view) == 5)
    for index in range(6, 10):
        view.step_frame(1)
        wait_until(lambda: shown_frame(view) == index)
    _APP.sendEvent(view.video, QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Right,
                                       Qt.KeyboardModifier.NoModifier, '', True))
    assert view._step_frame == 9
    _APP.sendEvent(view.video, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                                       Qt.KeyboardModifier.NoModifier, '', True))
    wait_until(lambda: shown_frame(view) == 10)
    view.step_frame(1000)
    wait_until(lambda: shown_frame(view) == 47)
    QTest.keyClick(view.seek, Qt.Key.Key_Right)
    assert view._step_frame == 47
    QTest.keyClick(view.seek, Qt.Key.Key_Left)
    wait_until(lambda: shown_frame(view) == 46)


def test_fractional_fps_steps_show_adjacent_decoded_frames(panel, tmp_path):
    path = tmp_path / '29.97 中文.mp4'
    code, _, error = run_video_tool([
        find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30000/1001',
        '-frames:v', '60', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path),
    ])
    assert code == 0, error.decode()
    view = load(panel, path)
    for index in range(1, 7):
        view.step_frame(1)
        wait_until(lambda: shown_frame(view) == index)
    for index in range(5, 1, -1):
        view.step_frame(-1)
        wait_until(lambda: shown_frame(view) == index)


def test_space_toggle_repeat_suppression_and_step_during_play(panel, clip):
    view = load(panel, clip)
    panel.activateWindow()
    _APP.processEvents()
    QTest.mouseClick(view.poster, Qt.MouseButton.LeftButton)
    assert _APP.focusWidget() is view.poster
    QTest.keyClick(_APP.focusWidget(), Qt.Key.Key_Space)
    wait_until(lambda: shown_frame(view) >= 3)
    focused = _APP.focusWidget()
    assert focused is view.video or view.video.isAncestorOf(focused)
    # 播放时按方向键以显示帧为基准，不能使用较慢的 positionChanged 缓存。
    current = shown_frame(view)
    QTest.keyClick(_APP.focusWidget(), Qt.Key.Key_Right)
    wait_until(lambda: shown_frame(view) == current + 1)
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    QTest.keyClick(view.play, Qt.Key.Key_Space)
    assert view.player.playbackState() == view.player.PlaybackState.PlayingState
    for kind in (QEvent.Type.KeyRelease, QEvent.Type.KeyPress):
        _APP.sendEvent(view.play, QKeyEvent(kind, Qt.Key.Key_Space,
                                          Qt.KeyboardModifier.NoModifier, ' ', True))
    assert view.player.playbackState() == view.player.PlaybackState.PlayingState
    QTest.keyClick(view.video, Qt.Key.Key_Space)
    assert view.player.playbackState() == view.player.PlaybackState.PausedState


def test_timeline_first_click_shows_selected_frame_without_playing(panel, clip):
    view = load(panel, clip)
    assert view._ensure_player()
    frames, states = [], []
    view.player.playbackStateChanged.connect(states.append)
    view.video.videoSink().videoFrameChanged.connect(
        lambda frame: frames.append(frame.startTime()) if frame.isValid() else None)
    strip = view.filmstrip
    point = QPoint(strip._MARGIN + round(.6 * strip._span()), strip.height() // 2)
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=point)
    wait_until(lambda: shown_frame(view) == 28)
    assert frames[0] >= 1_125_000  # 首帧来自点击处，加载时不能先从零播放。
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    assert view.player.PlaybackState.PlayingState not in states
    QTest.keyClick(strip, Qt.Key.Key_Space)
    wait_until(lambda: shown_frame(view) >= 29)
    assert view.player.playbackState() == view.player.PlaybackState.PlayingState


@pytest.mark.parametrize('timeline', ['filmstrip', 'seek'])
@pytest.mark.parametrize('playing', [False, True])
@pytest.mark.parametrize('drag', [False, True])
def test_timeline_click_and_drag_preserve_playback_state(panel, clip, timeline, playing, drag):
    view = load(panel, clip)
    view.step_frame(1)
    wait_until(lambda: shown_frame(view) == 1)
    if playing:
        view.toggle_play()
    state = view.player.playbackState()
    states = []
    view.player.playbackStateChanged.connect(states.append)
    slider = getattr(view, timeline)
    point = lambda part: QPoint(slider._MARGIN + round(part * slider._span()), slider.height() // 2)
    if drag:
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=point(.2))
        QTest.mouseMove(slider, point(.5))
        assert view.player.playbackState() == state
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=point(.5))
    else:
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=point(.5))
    wait_until(lambda: 23 <= shown_frame(view) <= 26)
    assert view.player.playbackState() == state
    assert all(value == state for value in states)


@pytest.mark.parametrize('timeline', ['filmstrip', 'seek'])
@pytest.mark.parametrize('initial', ['unloaded', 'paused', 'playing'])
def test_timeline_drag_refreshes_decoded_frames_before_release(panel, clip, timeline, initial):
    view = load(panel, clip)
    if initial != 'unloaded':
        view.step_frame(1)
        wait_until(lambda: shown_frame(view) == 1)
        if initial == 'playing':
            view.toggle_play()
    slider = getattr(view, timeline)
    point = lambda part: QPoint(slider._MARGIN + round(part * slider._span()), slider.height() // 2)
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=point(.25))
    try:
        # 始终按住鼠标：按下、向前拖动和向后拖动都必须收到真实解码帧。
        for part, target in ((.25, 12), (.75, 36), (.5, 24), (.125, 6)):
            if part != .25:
                QTest.mouseMove(slider, point(part))
            expected = FrameClock(view._duration, view._fps).frame_at(
                round(slider._value_at(point(part).x()) / 10000 * view._duration))
            assert abs(expected - target) <= 1
            wait_until(lambda: expected <= shown_frame(view) <= expected + (2 if initial == 'playing' else 0))
            assert slider.isSliderDown()
            assert view.filmstrip.value() == view.seek.value()
            state = view.player.PlaybackState
            assert view.player.playbackState() == (state.PlayingState if initial == 'playing' else state.PausedState)
    finally:
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=point(.125))


@pytest.mark.parametrize('timeline', ['filmstrip', 'seek'])
def test_drag_coalesces_moves_and_release_commits_latest_frame(panel, clip, monkeypatch, timeline):
    view = load(panel, clip)
    view.step_frame(1)
    wait_until(lambda: shown_frame(view) == 1)
    positions = []
    original = view._set_player_position

    def record(position):
        positions.append(position)
        original(position)

    monkeypatch.setattr(view, '_set_player_position', record)
    slider = getattr(view, timeline)
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider._MARGIN, slider.height() // 2))
    for part in (.2, .3, .4, .6, .75):
        move_held_mouse(slider, part)
    assert len(positions) == 1
    expected = FrameClock(view._duration, view._fps).frame_at(round(slider.value() / 10000 * view._duration))
    wait_until(lambda: shown_frame(view) == expected)
    assert len(positions) == 2 and slider.isSliderDown()
    before = len(positions)
    for part in (.6, .4, .2):
        end = move_held_mouse(slider, part)
    assert len(positions) <= before + 1
    before_release = len(positions)
    expected = FrameClock(view._duration, view._fps).frame_at(round(slider.value() / 10000 * view._duration))
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=end)
    wait_until(lambda: shown_frame(view) == expected)
    assert len(positions) == before_release + 1
    assert view._scrub_value is None and not view._scrub_timer.isActive()
    assert view.player.playbackState() == view.player.PlaybackState.PausedState


@pytest.mark.parametrize('action', ['switch', 'fast', 'shutdown'])
def test_queued_drag_does_not_seek_after_source_is_cancelled(panel, clip, action):
    view = load(panel, clip)
    view.step_frame(1)
    wait_until(lambda: shown_frame(view) == 1)
    slider = view.filmstrip
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider._MARGIN, slider.height() // 2))
    end = move_held_mouse(slider, .75)
    assert view._scrub_value is not None and view._scrub_timer.isActive()
    if action == 'shutdown':
        panel.request_shutdown()
    else:
        panel.set_image(str(clip), load_full=action != 'fast')
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=end)
    wait_until(lambda: panel._video_worker is None and panel._filmstrip_worker is None)
    assert view.player.source().isEmpty() and not view._source_set
    assert view._pending_seek is None and not view._pending_play
    assert view._scrub_value is None and not view._scrub_timer.isActive()
    assert not slider.isSliderDown()


@pytest.mark.parametrize('target', ['poster', 'filmstrip', 'seek', 'play'])
def test_home_end_before_play_show_boundary_frames_and_reset_step_target(panel, clip, target):
    view = load(panel, clip)
    QTest.keyClick(getattr(view, target), Qt.Key.Key_End)
    wait_until(lambda: shown_frame(view) == 47)
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    QTest.keyClick(view.video, Qt.Key.Key_Left)
    wait_until(lambda: shown_frame(view) == 46)
    QTest.keyClick(getattr(view, target), Qt.Key.Key_Home)
    wait_until(lambda: shown_frame(view) == 0)
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    QTest.keyClick(view.video, Qt.Key.Key_Right)
    wait_until(lambda: shown_frame(view) == 1)


def test_home_end_preserve_playing_state_and_seek_after_end_stays_paused(panel, clip):
    view = load(panel, clip)
    view.toggle_play()
    wait_until(lambda: shown_frame(view) >= 3)
    QTest.keyClick(view.video, Qt.Key.Key_Home)
    assert view.player.playbackState() == view.player.PlaybackState.PlayingState
    wait_until(lambda: shown_frame(view) <= 2)
    frames = []
    view.video.videoSink().videoFrameChanged.connect(
        lambda frame: frames.append(round(frame.startTime() * view._fps / 1_000_000))
        if frame.isValid() else None)
    QTest.keyClick(view.video, Qt.Key.Key_End)
    assert view.player.playbackState() == view.player.PlaybackState.PlayingState
    wait_until(lambda: view.player.mediaStatus() == view.player.MediaStatus.EndOfMedia)
    assert 47 in frames  # 正常播完后 Qt 可以清空 sink；End 必须先显示实际尾帧。
    QTest.keyClick(view.video, Qt.Key.Key_Home)
    wait_until(lambda: shown_frame(view) == 0)
    assert view.player.playbackState() == view.player.PlaybackState.PausedState


def test_fast_browse_and_stop_disable_transport(panel, clip):
    panel.set_image(str(clip), load_full=False)
    view = panel.video_view
    for key in (Qt.Key.Key_Space, Qt.Key.Key_Left, Qt.Key.Key_Right,
                Qt.Key.Key_Home, Qt.Key.Key_End):
        QTest.keyClick(view.poster, key)
    assert view.player is None and view._pending_seek is None
    assert not view.previous_frame.isEnabled() and not view.next_frame.isEnabled()
    load(panel, clip)
    view.step_frame(1)
    panel.clear_image()
    wait_until(lambda: panel._video_worker is None and panel._filmstrip_worker is None)
    assert view.player.source().isEmpty()
    assert view._pending_seek is None and not view._pending_play
    assert not view.previous_frame.isEnabled()


def test_unknown_fps_and_native_audio_keys(panel, clip):
    view = load(panel, clip)
    view.set_frame_rate(0)
    QTest.keyClick(view.poster, Qt.Key.Key_Right)
    assert view.player is None and not view.next_frame.isEnabled()
    view.set_frame_rate(24)
    before = view.volume.value()
    QTest.keyClick(view.volume, Qt.Key.Key_Right)
    assert view.volume.value() == before + 1 and view.player is None
    editor = QLineEdit()
    try:
        QTest.keyClicks(editor, 'a b')
        assert editor.text() == 'a b' and view.player is None
    finally:
        editor.deleteLater()


def test_space_cancels_explicit_play_while_seek_is_loading(panel, clip):
    view = load(panel, clip)
    view._filmstrip_seek(7500)
    assert not view._pending_play
    QTest.keyClick(view.filmstrip, Qt.Key.Key_Space)
    assert view._pending_play
    QTest.keyClick(view.filmstrip, Qt.Key.Key_Space)
    wait_until(lambda: shown_frame(view) == 36)
    assert not view._pending_play
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
