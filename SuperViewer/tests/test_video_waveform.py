import threading

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest

from app_common.video import find_ffmpeg, run_video_tool
from SuperViewer.superviewer import video_preview
from SuperViewer.superviewer.waveform_slider import WaveformSlider
from SuperViewer.tests.test_video_preview import _APP, clip, panel, wait_until


def point_at(slider, fraction):
    return QPoint(slider._MARGIN + round(fraction * slider._span()), slider.height() // 2)


def test_real_waveform_preplay_position_and_drag_while_paused(panel, clip):
    panel.set_image(str(clip))
    wait_until(lambda: panel._video_worker is None)
    view = panel.video_view
    assert view.player is None
    assert view.seek.isEnabled() and max(view.seek._peaks) > 0
    QTest.mouseClick(view.seek, Qt.MouseButton.LeftButton, pos=point_at(view.seek, .5))
    assert view._pending_seek == pytest.approx(1000, abs=10)
    assert view.player is None
    view.toggle_play()
    wait_until(lambda: view._pending_seek is None and view.player.position() >= 1000)
    view.toggle_play()
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    QTest.mousePress(view.seek, Qt.MouseButton.LeftButton, pos=point_at(view.seek, .2))
    QTest.mouseMove(view.seek, point_at(view.seek, .75))
    assert view.seek.isSliderDown()
    assert view.time_label.text().startswith('00:01')
    QTest.mouseRelease(view.seek, Qt.MouseButton.LeftButton, pos=point_at(view.seek, .75))
    wait_until(lambda: abs(view.player.position() - 1500) < 50)
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    view._restart()
    wait_until(lambda: view.player.position() < 500)
    assert view.player.playbackState() == view.player.PlaybackState.PlayingState


def test_multitrack_playback_matches_first_track_waveform(panel, clip, tmp_path):
    path = tmp_path / '双音轨.mp4'
    code, _, error = run_video_tool([
        find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-i', str(clip), '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono:d=2',
        '-map', '0:v:0', '-map', '0:a:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
        '-disposition:a:0', '0', '-disposition:a:1', 'default', str(path),
    ])
    assert code == 0, error.decode()
    panel.set_image(str(path))
    wait_until(lambda: panel._video_worker is None)
    view = panel.video_view
    assert max(view.seek._peaks) > 0
    view.toggle_play()
    wait_until(lambda: view._position > 100)
    assert len(view.player.audioTracks()) == 2
    assert view.player.activeAudioTrack() == 0


def test_slider_edges_resize_keyboard_and_paint(tmp_path):
    slider = WaveformSlider()
    slider.resize(600, 56)
    slider.set_duration(10000)
    slider.set_waveform((0, .1, .8, .3, 0, .6, .2, 0))
    slider.show()
    selected = []
    slider.position_selected.connect(selected.append)
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(0, 28))
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(599, 28))
    assert selected == [0, 10000]
    slider.resize(320, 56)
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=point_at(slider, .5))
    assert selected[-1] == pytest.approx(5000, abs=20)
    QTest.keyClick(slider, Qt.Key.Key_Home)
    assert selected[-1] == 0
    QTest.keyClick(slider, Qt.Key.Key_End)
    assert selected[-1] == 10000
    slider.clearFocus()
    image = slider.grab().toImage()
    assert image.pixelColor(100, 27) != image.pixelColor(100, 3)
    slider.setFocus()
    _APP.processEvents()
    focused = slider.grab().toImage()
    assert focused.pixelColor(100, 27) != focused.pixelColor(100, 3)
    assert image.save(str(tmp_path / 'waveform-slider.png'))
    slider.close()
    slider.deleteLater()


def test_switch_during_drag_discards_old_position(panel, clip):
    panel.set_image(str(clip))
    wait_until(lambda: panel._video_worker is None)
    view = panel.video_view
    QTest.mousePress(view.seek, Qt.MouseButton.LeftButton, pos=point_at(view.seek, .75))
    panel.set_image(str(clip))
    QTest.mouseRelease(view.seek, Qt.MouseButton.LeftButton, pos=point_at(view.seek, .75))
    wait_until(lambda: panel._video_worker is None)
    assert not view.seek.isSliderDown()
    assert view.seek.value() == 0
    assert view._pending_seek is None


@pytest.mark.parametrize('tracks, fail, status', [(0, False, '无音轨'), (1, True, '音频波形不可用')])
def test_missing_or_failed_audio_keeps_seek(panel, clip, monkeypatch, tracks, fail, status):
    monkeypatch.setattr(video_preview, 'probe_video', lambda *a, **kw: {'duration': 2, 'audio_tracks': tracks})
    calls = []
    def extract(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('test decoder failure')
    monkeypatch.setattr(video_preview, 'audio_waveform', extract)
    panel.set_image(str(clip))
    wait_until(lambda: panel._video_worker is None)
    assert bool(calls) == fail
    assert panel.video_view.seek._status == status
    assert panel.video_view.seek.isEnabled()
    assert panel.video_view.play.isEnabled()


def test_fast_browse_does_not_extract_waveform(panel, clip, monkeypatch):
    monkeypatch.setattr(video_preview, 'audio_waveform', lambda *a, **kw: pytest.fail('fast waveform decoded'))
    panel.set_image(str(clip), load_full=False)
    assert panel._video_worker is None
    assert not panel.video_view.seek.isEnabled()
    assert not panel.video_view.seek._peaks


def test_worker_result_is_not_finished_and_latest_selection_wins(panel, clip, monkeypatch):
    release = threading.Event()
    entered = threading.Event()
    calls = []
    class HeldProbe(video_preview._VideoProbe):
        def run(self):
            calls.append(self.token)
            self.result.emit(self.token, self.path, {'duration': 2}, None, '')
            self.waveform_ready.emit(self.token, self.path, (.2, .8), '')
            entered.set()
            release.wait(4)
    monkeypatch.setattr(video_preview, '_VideoProbe', HeldProbe)
    try:
        panel.set_image(str(clip))
        owner = panel._video_worker
        token = panel._video_token
        wait_until(lambda: entered.is_set() and bool(panel.video_view.seek._peaks))
        assert panel._video_worker is owner and owner.isRunning()
        panel.set_image(str(clip))
        panel.set_image(str(clip))
        assert panel._video_worker is owner
        assert owner.isInterruptionRequested()
        panel._waveform_result(token, str(clip), (.9,), '')
        assert not panel.video_view.seek._peaks
        release.set()
        wait_until(lambda: panel._video_worker is None)
        assert calls == [token, panel._video_token]
        assert panel.video_view.seek._peaks == (.2, .8)
    finally:
        release.set()


def test_shutdown_cancels_audio_and_ignores_late_result(panel, clip, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    cancelled = []
    def extract(*args, **kwargs):
        entered.set()
        release.wait(4)
        cancelled.append(kwargs['cancelled']())
        return (.5,)
    monkeypatch.setattr(video_preview, 'audio_waveform', extract)
    try:
        panel.set_image(str(clip))
        wait_until(entered.is_set)
        owner = panel._video_worker
        panel.request_shutdown()
        assert not panel.shutdown(wait_timeout_ms=0)
        assert panel._video_worker is owner
        assert not panel.video_view.seek._peaks
        release.set()
        wait_until(lambda: panel.shutdown(wait_timeout_ms=0))
        assert cancelled == [True]
        assert not panel.video_view.seek._peaks
    finally:
        release.set()
