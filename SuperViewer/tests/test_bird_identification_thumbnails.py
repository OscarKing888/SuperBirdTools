"""识鸟表格复用列表缩略图：缓存、可见区、原图身份与线程退出。"""
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from app_common.file_browser import _thumbnail as shared
from app_common.file_browser import _browser_core as core
from app_common.file_browser._work_pool import BrowserWorkPool
from SuperViewer.superviewer.bird_identification import BirdIDResult
from SuperViewer.superviewer.bird_identification_controller import BirdIDProgressDialog
from SuperViewer.superviewer.bird_identification_thumbnails import BirdIDThumbnails

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


def qimage(color='green'):
    image = QImage(256, 128, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


@pytest.fixture
def env(tmp_path):
    (tmp_path / '.superpicky').mkdir()
    pool = BrowserWorkPool(3, 2)
    cache = shared.ThumbnailMemoryCache(max_bytes=16 * 1024 * 1024)
    files = SimpleNamespace(_thumb_memory_cache=cache, _get_browser_work_pool=lambda: pool,
                            _current_dir=str(tmp_path), _report_cache={}, _use_preview_cache=True)
    previews = BirdIDThumbnails()
    previews.configure(files)
    yield previews, files, pool
    previews.request_shutdown()
    assert wait_for(previews.is_shutdown_done)
    assert pool.shutdown(timeout=3)
    previews.deleteLater()
    _APP.processEvents()


def test_uses_list_memory_cache_without_decoding(env, tmp_path, monkeypatch):
    previews, files, pool = env
    photo = str(tmp_path / '缓存鸟片.jpg')
    Image.new('RGB', (512, 256), 'red').save(photo)
    files._thumb_memory_cache.put(photo, 256, qimage('blue'))
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('命中列表内存缓存后不应解码')
    monkeypatch.setattr(shared.thumb_stream, 'iter_thumbnail_rgb_progressive', forbidden)
    monkeypatch.setattr(shared, '_load_thumbnail_image', forbidden)
    previews.set_visible([photo, photo, photo])
    assert wait_for(lambda: previews.image(photo) is not None and previews._worker is None)
    assert previews.image(photo).pixelColor(0, 0) == QColor('blue')
    assert not calls
    assert previews._context['thumb_cache'] is files._thumb_memory_cache
    assert previews._context['work_pool'] is pool


@pytest.mark.parametrize('extension', ['.jpg', '.ARW', '.HIF'])
def test_uses_real_persistent_list_cache_for_all_sources(env, tmp_path, monkeypatch, extension):
    previews, files, pool = env
    photo = tmp_path / ('同名鸟片' + extension)
    if extension == '.jpg':
        Image.new('RGB', (512, 256), 'red').save(photo)
    else:
        photo.write_bytes(b'raw/heif must not be decoded')
    cached = core._persistent_thumb_cache_path_for_file(str(photo), str(tmp_path), 256)
    assert core._write_persistent_thumb_cache_image(cached, qimage('blue'),
                                                   source_stamp=core._thumb_source_stamp(str(photo)))
    targets = []
    original = shared.ThumbnailLoader._resolve_load_target_path
    def resolve(loader, path):
        target = original(loader, path)
        targets.append(target)
        return target
    monkeypatch.setattr(shared.ThumbnailLoader, '_resolve_load_target_path', resolve)
    previews.set_visible([str(photo)])
    assert wait_for(lambda: previews.image(str(photo)) is not None and previews._worker is None)
    assert targets == [cached]
    color = previews.image(str(photo)).pixelColor(0, 0)
    assert color.blue() > 240 and color.red() < 10


@pytest.mark.parametrize('extension', ['.jpg', '.ARW', '.HIF'])
def test_uncached_decoding_runs_in_shared_pool_and_returns_source_identity(env, tmp_path, monkeypatch, extension):
    previews, files, pool = env
    source = str(tmp_path / ('未缓存' + extension))
    calls = []
    main_thread = threading.get_ident()
    def progressive(path, size, cancelled):
        calls.append((path, size, threading.get_ident()))
        image = Image.new('RGB', (256, 128), 'green')
        yield image.tobytes(), 256, 128
    def single(path, size, *args, **kwargs):
        calls.append((path, size, threading.get_ident()))
        return qimage()
    monkeypatch.setattr(shared.thumb_stream, 'iter_thumbnail_rgb_progressive', progressive)
    monkeypatch.setattr(shared, '_load_thumbnail_image', single)
    monkeypatch.setattr(shared, '_schedule_thumb_disk_cache_write', lambda *args: None)
    previews.set_visible([source])
    assert wait_for(lambda: previews.image(source) is not None and previews._worker is None)
    assert len(calls) == 1
    assert calls[0][:2] == (source, 256) and calls[0][2] != main_thread


def test_report_preview_resolution_keeps_original_source_identity(env, tmp_path):
    previews, files, pool = env
    source = str(tmp_path / '原图.ARW')
    Path(source).write_bytes(b'not a decodable raw')
    preview = tmp_path / '相机预览.jpg'
    Image.new('RGB', (512, 256), 'blue').save(preview)
    files._report_cache = core._index_report_cache({'原图': {
        'filename': '原图', 'original_path': source, 'temp_jpeg_path': preview.name,
        '_report_root_dir': str(tmp_path)}})
    previews.configure(files)
    # 批次开始后的目录和报告缓存变化不改变本批快照。
    files._current_dir = str(tmp_path / '另一个目录')
    files._report_cache = {}
    previews.set_visible([source])
    assert wait_for(lambda: previews.image(source) is not None and previews._worker is None)
    assert previews.image(str(preview)) is None
    assert previews.image(source).pixelColor(0, 0).blue() > 240


def test_viewport_loading_deduplicates_candidates_and_releases_hidden_requests(env, tmp_path, monkeypatch):
    previews, files, pool = env
    calls = []
    def load(loader, path, emit, **kwargs):
        calls.append(path)
        emit(loader._request_token, path, qimage())
    monkeypatch.setattr(shared.ThumbnailLoader, '_load_single', load)
    dialog = BirdIDProgressDialog(None, results_table=True, thumbnails=previews)
    table = dialog.details
    try:
        for i in range(20):
            table.append_result(BirdIDResult(str(tmp_path / f'鸟片{i}.jpg'), 'candidate', response={
                'results': [{'cn_name': '翠鸟', 'confidence': 30}] * 3}))
        dialog.show()
        table.scrollToTop()
        assert wait_for(lambda: bool(calls) and previews._worker is None)
        assert len(calls) == len(set(calls)) <= 3
        assert all(int(Path(p).stem[2:]) < 3 for p in calls)
        assert table.model().index(0, 0).data(Qt.ItemDataRole.DecorationRole) is not None
        assert table.model().index(1, 0).data(Qt.ItemDataRole.DecorationRole) is not None
        assert dialog.grab().save(str(tmp_path / '识鸟照片预览.png'))
        table.scrollToBottom()
        assert wait_for(lambda: previews.image(str(tmp_path / '鸟片19.jpg')) is not None and previews._worker is None)
        assert len(calls) < 8
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        assert wait_for(lambda: not previews._visible)
        dialog.hide()
        assert not previews._visible
    finally:
        dialog.finish('完成')
        dialog.close()
        dialog.deleteLater()


def test_old_decode_cannot_replace_new_batch_and_shutdown_waits_for_finished(env, tmp_path, monkeypatch):
    previews, files, pool = env
    started, gate = threading.Event(), threading.Event()
    old, new = str(tmp_path / '旧照片.jpg'), str(tmp_path / '新照片.jpg')
    def load(loader, path, emit, **kwargs):
        if path == old:
            started.set()
            assert gate.wait(5)
        emit(loader._request_token, path, qimage('red' if path == old else 'blue'))
    monkeypatch.setattr(shared.ThumbnailLoader, '_load_single', load)
    previews.set_visible([old])
    assert wait_for(started.is_set)
    worker = previews._worker
    previews.configure(files)
    previews.set_visible([new])
    try:
        assert previews._worker is worker
        previews._finished(object(), -1, ())
        assert previews._worker is worker
    finally:
        gate.set()
    assert wait_for(lambda: previews.image(new) is not None and previews._worker is None)
    assert previews.image(old) is None
    assert previews.image(new).pixelColor(0, 0) == QColor('blue')
    started.clear()
    gate.clear()
    previews.set_visible([old])
    assert wait_for(started.is_set)
    try:
        previews.request_shutdown()
        assert not previews.is_shutdown_done()
    finally:
        gate.set()
    assert wait_for(previews.is_shutdown_done)
    assert previews.image(old) is None


def test_failed_preview_is_bounded_and_not_retried_on_each_paint(env, tmp_path, monkeypatch):
    previews, files, pool = env
    calls = []
    monkeypatch.setattr(shared.ThumbnailLoader, '_load_single', lambda loader, path, emit, **kw: calls.append(path))
    path = str(tmp_path / '损坏.jpg')
    previews.set_visible([path])
    assert wait_for(lambda: previews.failed(path) and previews._worker is None)
    for _ in range(5):
        previews.set_visible([path])
        previews.image(path)
        _APP.processEvents()
    assert calls == [path]
    for i in range(100):
        previews._remember(str(tmp_path / f'{i}.jpg'), qimage())
    assert len(previews._images) == previews.MAX_IMAGES


def test_interrupted_progressive_frame_is_upgraded_when_still_visible(env, tmp_path, monkeypatch):
    previews, files, pool = env
    first, second = str(tmp_path / '第一张.jpg'), str(tmp_path / '第二张.jpg')
    gate = threading.Event()
    calls = []
    def load(loader, path, emit, **kwargs):
        calls.append(path)
        if path == first and calls.count(first) == 1:
            emit(loader._request_token, path, qimage('red'))
            assert gate.wait(5)
        else:
            emit(loader._request_token, path, qimage('blue'))
    monkeypatch.setattr(shared.ThumbnailLoader, '_load_single', load)
    previews.set_visible([first])
    assert wait_for(lambda: previews.image(first) is not None)
    try:
        # 新结果进入视口中止旧加载，但已显示的渐进帧不能被误当成最终缓存。
        previews.set_visible([first, second])
    finally:
        gate.set()
    assert wait_for(lambda: previews.image(second) is not None and previews._worker is None)
    assert calls.count(first) == 2
    assert previews.image(first).pixelColor(0, 0) == QColor('blue')
    assert not previews._incomplete
