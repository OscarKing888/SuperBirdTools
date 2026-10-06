import io
import os
import threading

import pytest
from PIL import Image
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest

from app_common.video import find_ffmpeg, run_video_tool
from SuperViewer.superviewer import video_frames as frames_core, video_preview
from SuperViewer.superviewer.filmstrip_slider import FilmstripSlider
from SuperViewer.superviewer.video_frames import VideoFrame
from SuperViewer.tests.test_video_preview import _APP, clip, panel, wait_until


def point_at(slider, fraction):
    return QPoint(slider._MARGIN + round(fraction * slider._span()), slider.height() // 2)


def frame(color='red', width=96, height=54):
    return VideoFrame(0, Image.new('RGB', (width, height), color).tobytes(), width, height)


def test_real_timeline_is_bounded_changing_and_cached(clip, monkeypatch):
    progress = []
    samples = frames_core.video_frames(clip, 2, fps=24, on_progress=progress.append)
    assert len(samples) == frames_core.FRAME_COUNT
    # 2 秒片段只有一个关键帧：先快速铺满，再一次顺序解码得到各格精确画面。
    assert len({item.rgb for item in samples}) == frames_core.FRAME_COUNT
    assert progress[0][0] is not None and progress[0][1:].count(None) == len(samples) - 1
    assert all(max(item.width, item.height) <= frames_core.FRAME_SIZE for item in samples)
    assert samples[0].seconds < .1 and samples[-1].seconds > 1.9
    assert samples[0].rgb != samples[-1].rgb
    assert all(a.seconds < b.seconds for a, b in zip(samples, samples[1:]))
    assert progress[-1] == samples
    monkeypatch.setattr(frames_core, 'run_video_tool', lambda *a, **kw: pytest.fail('cache miss'))
    assert frames_core.video_frames(clip, 2, fps=24) is samples
    with pytest.raises(RuntimeError, match='取消'):
        frames_core.video_frames(clip, 2, fps=24, cancelled=lambda: True)


def jpeg(color, size=(20, 10)):
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='JPEG')
    return buffer.getvalue()


def test_cache_invalidation_eviction_and_cancellation(tmp_path, monkeypatch):
    calls = []

    def decode(args, **kwargs):
        assert args.index('-ss') < args.index('-i')  # 长视频采用输入定位，不顺序解完整段。
        assert '-skip_frame' in args  # 各格画面不同时只解关键帧，不做精确定位。
        calls.append(args)
        return 0, jpeg((len(calls) * 7 % 256, 90, 40)), b''

    monkeypatch.setattr(frames_core, 'run_video_tool', decode)
    monkeypatch.setattr(frames_core, '_CACHE', frames_core.OrderedDict())
    path = tmp_path / '中文 素材.mp4'
    path.write_bytes(b'video')
    samples = frames_core.video_frames(path, 1, fps=2)
    assert len(calls) == 2
    assert frames_core.video_frames(path, 1, fps=2) is samples
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert frames_core.video_frames(path, 1, fps=2) is not samples
    assert len(calls) == 4
    cancelled = []
    with pytest.raises(RuntimeError, match='取消'):
        frames_core.video_frames(path, 2, fps=2, cancelled=lambda: bool(cancelled),
                                 on_progress=lambda samples: cancelled.append(True))
    assert len(calls) == 5  # 第一帧后取消，不再启动下一个进程或缓存半成品。
    assert len(frames_core._CACHE) == 2
    for index in range(frames_core._CACHE_LIMIT + 2):
        frames_core.video_frames(path, (index + 1) / 1000, fps=1)
    assert len(frames_core._CACHE) == frames_core._CACHE_LIMIT


def test_keyframes_fill_coarse_to_fine_then_long_video_refines_shared(tmp_path, monkeypatch):
    calls = []

    def decode(args, **kwargs):
        seconds = float(args[args.index('-ss') + 1])
        keyframe = '-skip_frame' in args
        calls.append((keyframe, seconds))
        # 关键帧每 20 秒一个：60 秒内的 32 格只解出 3 种画面，需精确定位。
        value = int(seconds // 20) if keyframe else int(seconds * 4)
        return 0, jpeg((value % 256, value // 256, 9)), b''

    monkeypatch.setattr(frames_core, 'run_video_tool', decode)
    monkeypatch.setattr(frames_core, '_CACHE', frames_core.OrderedDict())
    path = tmp_path / '长 GOP.mp4'
    path.write_bytes(b'video')
    progress = []
    samples = frames_core.video_frames(path, 64, fps=25, on_progress=progress.append)
    first = [index for keyframe, index in
             ((k, round(s / 2 - .5)) for k, s in calls[:4]) if keyframe]
    assert first == [0, 16, 8, 24]
    assert sum(keyframe for keyframe, _ in calls) == frames_core.FRAME_COUNT
    assert sum(not keyframe for keyframe, _ in calls) == frames_core.FRAME_COUNT
    assert len({item.rgb for item in samples}) == frames_core.FRAME_COUNT
    assert progress[-1] == samples
    assert frames_core._coarse_to_fine(5) == [0, 4, 2, 1, 3]


def test_keyframe_failure_falls_back_to_exact_and_refine_failure_keeps_strip(tmp_path,
                                                                            monkeypatch):
    calls = []

    def decode(args, **kwargs):
        calls.append(args)
        if '-skip_frame' in args:
            return 1, b'', b'no keyframe flags'
        if '-ss' not in args:
            return 1, b'', b'sequential failed'
        return 0, jpeg('green'), b''

    monkeypatch.setattr(frames_core, 'run_video_tool', decode)
    monkeypatch.setattr(frames_core, '_CACHE', frames_core.OrderedDict())
    path = tmp_path / '无关键帧.ts'
    path.write_bytes(b'video')
    samples = frames_core.video_frames(path, 1, fps=2)
    assert len(samples) == 2 and all(samples)
    assert [('-skip_frame' in args, '-ss' in args) for args in calls] == [
        (True, True), (False, True), (True, True), (False, True), (False, False)]


def test_strip_borrows_nearest_frame_until_samples_arrive():
    strip = FilmstripSlider()
    try:
        strip.set_frames((frame('red'), None, None, frame('blue'), None))
        assert strip._images[1] is strip._images[0]
        assert strip._images[2] is strip._images[3]
        assert strip._images[4] is strip._images[3]
        strip.set_frames((None, None))
        assert strip._images == (None, None)
    finally:
        strip.deleteLater()


def test_one_frame_portrait_clip_without_audio(panel, tmp_path):
    path = tmp_path / '竖屏 单帧.mp4'
    code, _, error = run_video_tool([
        find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-f', 'lavfi', '-i', 'color=c=blue:size=90x160:rate=25',
        '-frames:v', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path),
    ])
    assert code == 0, error.decode()
    samples = frames_core.video_frames(path, .04, fps=25)
    assert len(samples) == 1 and samples[0].seconds == 0
    assert samples[0].height > samples[0].width
    panel.set_image(str(path))
    wait_until(lambda: panel._video_worker is None and panel._filmstrip_worker is None)
    assert panel.video_view.filmstrip._frames
    assert panel.video_view.seek._status == '无音轨'
    assert panel.video_view.filmstrip.isEnabled()


def test_filmstrip_above_aligned_waveform_and_paused_seek(panel, clip, tmp_path):
    panel.set_image(str(clip))
    wait_until(lambda: panel._video_worker is None and panel._filmstrip_worker is None, timeout=30)
    view = panel.video_view
    strip = view.filmstrip
    assert all(strip._frames) and view.player is None
    assert strip.width() == view.seek.width()
    assert strip.geometry().bottom() < view.seek.geometry().top()
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=point_at(strip, .5))
    assert view._pending_seek == pytest.approx(1000, abs=10)
    assert view.seek.value() == strip.value()
    assert view.player is None
    view.toggle_play()
    wait_until(lambda: view._pending_seek is None and view.player.position() >= 1000)
    view.toggle_play()
    QTest.mousePress(strip, Qt.MouseButton.LeftButton, pos=point_at(strip, .2))
    QTest.mouseMove(strip, point_at(strip, .7))
    value = strip.value()
    view._position_changed(500)
    assert strip.value() == view.seek.value() == value
    QTest.mouseRelease(strip, Qt.MouseButton.LeftButton, pos=point_at(strip, .7))
    wait_until(lambda: abs(view.player.position() - 1400) < 30)
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    QTest.mouseClick(view.seek, Qt.MouseButton.LeftButton, pos=point_at(view.seek, .25))
    assert strip.value() == view.seek.value()
    view._restart()
    wait_until(lambda: view.player.position() < 500)
    assert strip.value() == view.seek.value()
    assert panel.grab().save(str(tmp_path / 'filmstrip-preview.png'))


@pytest.mark.parametrize('width,height', [(96, 54), (54, 96)])
def test_resize_keyboard_endpoints_and_theme_paint(width, height):
    from PyQt6.QtGui import QColor, QPalette
    strip = FilmstripSlider()
    try:
        strip.set_frames(tuple(frame(color, width, height) for color in ('red', 'green', 'blue')))
        strip.set_duration(10000)
        strip.resize(640, 64)
        strip.show()
        selected = []
        strip.position_selected.connect(selected.append)
        for size in (640, 300, 1000):
            strip.resize(size, 64)
            QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=QPoint(0, 32))
            QTest.mouseClick(strip, Qt.MouseButton.LeftButton, pos=QPoint(size - 1, 32))
            assert selected[-2:] == [0, 10000]
            image = strip.grab().toImage()
            assert image.pixelColor(size // 2, 25).green() > 80
        original = strip._frames
        for background in ('#15171a', '#f8f8f8'):
            palette = strip.palette()
            palette.setColor(QPalette.ColorRole.Base, QColor(background))
            strip.setPalette(palette)
            assert strip.grab().toImage().pixelColor(4, 30).name() == background
            assert strip._frames is original
        QTest.keyClick(strip, Qt.Key.Key_Home)
        assert selected[-1] == 0
        QTest.keyClick(strip, Qt.Key.Key_End)
        assert selected[-1] == 10000
    finally:
        strip.close()
        strip.deleteLater()


def test_fast_browse_and_switch_during_drag(panel, clip, monkeypatch):
    monkeypatch.setattr(frames_core, 'video_frames', lambda *a, **kw: pytest.fail('fast decoded'))
    panel.set_image(str(clip), load_full=False)
    view = panel.video_view
    assert panel._filmstrip_worker is None and not view.filmstrip.isEnabled()
    view.set_duration_hint(2000)
    QTest.mousePress(view.filmstrip, Qt.MouseButton.LeftButton, pos=point_at(view.filmstrip, .8))
    panel.set_image(str(clip), load_full=False)
    QTest.mouseRelease(view.filmstrip, Qt.MouseButton.LeftButton, pos=point_at(view.filmstrip, .8))
    assert view._pending_seek is None and view.seek.value() == view.filmstrip.value() == 0
    assert not view.filmstrip.isSliderDown()


def test_finished_ownership_latest_request_and_shutdown(panel, clip, monkeypatch):
    release = threading.Event()
    calls = []

    class HeldProbe(video_preview._FilmstripProbe):
        def run(self):
            calls.append(self.token)
            self.result.emit(self.token, self.path, (frame(),), '')
            release.wait(5)

    monkeypatch.setattr(video_preview, '_FilmstripProbe', HeldProbe)
    try:
        panel.set_image(str(clip))
        wait_until(lambda: bool(panel.video_view.filmstrip._frames))
        owner, token = panel._filmstrip_worker, panel._video_token
        panel.set_image(str(clip))
        panel.set_image(str(clip))
        wait_until(lambda: panel._filmstrip_pending is not None)
        assert owner.isInterruptionRequested() and panel._filmstrip_worker is owner
        panel._filmstrip_result(token, str(clip), (frame('blue'),), '')
        assert not panel.video_view.filmstrip._frames
        release.set()
        wait_until(lambda: panel._filmstrip_worker is None)
        assert calls == [token, panel._video_token]
        release.clear()
        panel.set_image(str(clip))
        wait_until(lambda: bool(panel.video_view.filmstrip._frames))
        owner = panel._filmstrip_worker
        panel.request_shutdown()
        assert not panel.shutdown(wait_timeout_ms=0)
        assert panel._filmstrip_worker is owner
        release.set()
        wait_until(lambda: panel.shutdown(wait_timeout_ms=0))
        assert not panel.video_view.filmstrip._frames
    finally:
        release.set()


def test_failed_filmstrip_keeps_waveform_and_seek(panel, clip, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('decoder failed')

    monkeypatch.setattr(frames_core, 'video_frames', fail)
    panel.set_image(str(clip))
    wait_until(lambda: panel._video_worker is None and panel._filmstrip_worker is None)
    view = panel.video_view
    assert view.filmstrip._status == '序列帧不可用'
    assert view.filmstrip.isEnabled() and view.seek.isEnabled() and view.seek._peaks
    assert view.play.isEnabled()
