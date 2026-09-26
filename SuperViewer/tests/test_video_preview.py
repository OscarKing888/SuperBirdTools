import os
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication
from app_common.video import find_ffmpeg, probe_video, run_video_tool, video_thumbnail_rgb
from app_common.file_browser._browser_core import _load_thumbnail_image
from SuperViewer.superviewer.video_preview import MediaPreviewPanel, VideoInfoPanel

_APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), 'Qt operation timed out'


@pytest.fixture(scope='module')
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp('video') / '中文 视频.mp4'
    code, _, err = run_video_tool([
        find_ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
        '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path),
    ])
    assert code == 0, err.decode()
    return path


@pytest.fixture
def panel():
    result = MediaPreviewPanel()
    result.resize(700, 500)
    result.show()
    yield result
    result.request_shutdown()
    wait_until(lambda: result.shutdown(wait_timeout_ms=10))
    result.close()
    result.deleteLater()
    _APP.processEvents()


def test_real_video_probe_and_thumbnail_tiers(clip, tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    info = probe_video(str(clip))
    assert info['duration'] == pytest.approx(2, abs=.1)
    assert (info['width'], info['height'], info['fps']) == (320, 180, 24)
    assert info['audio_tracks'] == 1
    for tier in (128, 256, 512, 1024, 2048):
        image = _load_thumbnail_image(str(clip), tier)
        assert image is not None and not image.isNull()
        assert max(image.width(), image.height()) <= tier
    assert 'aac' in info['audio']


def test_real_play_pause_seek_speed_and_switch_to_photo(panel, clip, tmp_path):
    info = []
    panel.video_info_ready.connect(lambda *args: info.append(args))
    panel.set_image(str(clip))
    wait_until(lambda: bool(info) and panel._video_worker is None)
    assert panel.video_view.player is None  # browsing must not start playback
    assert panel.video_view._poster is not None
    view = panel.video_view
    assert view._ensure_player()
    frames = []
    view.video.videoSink().videoFrameChanged.connect(lambda frame: frames.append(frame.isValid()))
    view.toggle_play()
    wait_until(lambda: view._position > 150 and any(frames))
    view.toggle_play()
    assert view.player.playbackState() == view.player.PlaybackState.PausedState
    view.seek.setValue(5000)
    view._seek()
    wait_until(lambda: abs(view.player.position() - 1000) < 200)
    view.speed.setCurrentIndex(view.speed.findData(1.5))
    assert view.player.playbackRate() == 1.5
    view.mute.setChecked(True)
    assert view.audio.isMuted()
    photo = tmp_path / '照片.jpg'
    Image.new('RGB', (50, 40), 'blue').save(photo)
    panel.set_image(str(photo))
    assert view.player.source().isEmpty()
    assert panel.current_path() == str(photo)
    assert panel._full_preview_loaded
    assert not panel.video_view.isVisible()


def test_fast_browsing_does_not_probe_and_release_commits(panel, clip, monkeypatch):
    requests = []
    original = panel._start_video_probe
    monkeypatch.setattr(panel, '_start_video_probe', lambda request: requests.append(request))
    panel.set_image(str(clip), load_full=False, quick_size=256)
    assert not requests and panel.video_view.player is None
    assert not panel.video_view.play.isEnabled()
    monkeypatch.setattr(panel, '_start_video_probe', original)
    panel.set_image(str(clip), quick_size=256)
    wait_until(lambda: panel._video_worker is None)
    assert panel.video_view._poster is not None
    assert panel.video_view.play.isEnabled()


def test_stale_info_is_ignored_and_shutdown_retains_worker(panel, clip):
    infos = []
    panel.video_info_ready.connect(lambda *args: infos.append(args))
    panel.set_image(str(clip))
    token = panel._video_token
    panel.clear_image()
    panel._video_result(token, str(clip), {'duration': 999}, None, '')
    assert infos == []
    wait_until(lambda: panel._video_worker is None)
    assert panel.current_path() is None


def test_corrupt_file_and_info_panel(panel, tmp_path):
    path = tmp_path / '损坏.mp4'
    path.write_bytes(b'not a video')
    info_panel = VideoInfoPanel()
    info_panel.set_path(str(path))
    panel.video_info_ready.connect(info_panel.update_info)
    panel.set_image(str(path))
    wait_until(lambda: panel._video_worker is None)
    assert '正在读取' not in info_panel.status.text()
    assert '损坏' in info_panel.status.text()
    old = info_panel.status.text()
    info_panel.update_info('wrong.mp4', {'duration': 50}, '')
    assert info_panel.status.text() == old
    info_panel.deleteLater()


def test_missing_decoder_error_is_displayed_once(panel, tmp_path, monkeypatch):
    from SuperViewer.superviewer import video_preview
    path = tmp_path / '中文视频.mp4'
    path.touch()
    error = '无法生成视频封面：预览组件缺失'
    def missing(*args, **kwargs):
        raise RuntimeError(error)
    monkeypatch.setattr(video_preview, 'probe_video', missing)
    monkeypatch.setattr(video_preview, 'video_thumbnail_rgb', missing)
    panel.set_image(str(path))
    wait_until(lambda: panel._video_worker is None)
    assert panel.video_view.message.text().count(error) == 1
    assert panel.video_view.play.isEnabled()


def test_read_only_video_diagnostic(clip, tmp_path):
    import json
    from SuperViewer.superviewer.video_diagnostics import main
    output = tmp_path / '诊断.json'
    assert main([str(clip), '--output', str(output)]) == 0
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['ok'] and result['poster'] == {'width': 320, 'height': 180}
    assert main([str(tmp_path / 'missing.mp4'), '--output', str(output)]) == 1
    assert not json.loads(output.read_text(encoding='utf-8'))['ok']


# Use the existing pre-construction settings isolation and shutdown fixture.
from SuperViewer.tests.test_directory_selection_responsiveness import window


def test_main_window_video_info_directory_switch_and_screenshot(window, clip, tmp_path, monkeypatch):
    window.resize(1550, 820)
    window.show()
    window._on_file_selected_from_list(str(clip))
    wait_until(lambda: window.preview_panel._video_worker is None)
    assert window.media_info_stack.currentWidget() is window.video_info_panel
    assert window.video_info_panel.path == str(clip)
    assert not window.combo_preview_grid.isEnabled()
    assert window.image_info_panel.current_photo_path() == ''
    window.preview_panel.video_view.toggle_play()
    wait_until(lambda: window.preview_panel.video_view._position > 100)
    monkeypatch.setattr(window._file_list, 'load_directory', lambda path: None)
    window._on_directory_selected(str(tmp_path))
    assert window.preview_panel.video_view.player.source().isEmpty()
    assert window.preview_panel.current_path() is None
    assert window.video_info_panel.path == ''
    photo = tmp_path / 'still.jpg'
    Image.new('RGB', (60, 40), 'green').save(photo)
    window._on_file_selected_from_list(str(photo))
    assert window.media_info_stack.currentWidget() is window.image_info_tabs
    assert window.combo_preview_grid.isEnabled()


def test_pending_video_only_latest_and_fast_pixmap_stops_audio(panel, clip):
    from SuperViewer.superviewer.qt_compat import QPixmap, QColor
    results = []
    panel.video_info_ready.connect(lambda path, *_: results.append(path))
    for _ in range(4):
        panel.set_image(str(clip))
    wait_until(lambda: panel._video_worker is None)
    assert results == [str(clip)]
    panel.video_view.toggle_play()
    wait_until(lambda: panel.video_view._position > 100)
    pix = QPixmap(128, 72)
    pix.fill(QColor('blue'))
    panel.set_quick_pixmap(str(clip), pix, quick_size=128)
    assert panel.video_view.player.source().isEmpty()
    assert not panel.video_view.isVisible()
    panel.set_image(str(clip))
    wait_until(lambda: panel._video_worker is None)
    assert panel.video_view.play.isEnabled()


def test_mixed_directory_thumbnails_metadata_and_visual(window, clip, tmp_path):
    import shutil
    library = tmp_path / '混合媒体'
    (library / '.superpicky').mkdir(parents=True)
    movie = library / '视频样例.mp4'
    shutil.copyfile(clip, movie)
    Image.new('RGB', (240, 160), '#367a87').save(library / '照片样例.jpg')
    window.resize(1580, 850)
    window.show()
    window._file_list.set_pending_selection([str(movie)])
    window._file_list.load_directory(str(library))
    wait_until(lambda: len(window._file_list._all_files) == 2)
    wait_until(lambda: window._file_list._meta_cache.get(str(movie), {}).get('video_info'))
    wait_until(lambda: window._file_list._thumb_list_model.has_current_pixmap(str(movie), window._file_list._thumb_size))
    window._on_file_selected_from_list(str(movie))
    wait_until(lambda: window.preview_panel._video_worker is None)
    assert window.video_info_panel.table.item(2, 1).text() == '00:02'
    assert window.grab().save(str(tmp_path / 'video-preview.png'))
