"""B 侧预览来源的后台查找、原图身份及长按松键回归。"""
from __future__ import annotations

import threading
import xml.etree.ElementTree as ET

from PIL import Image
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
import pytest

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY, map_camera_focus_box
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.decoders import preview_source
from birdstamp.gui.editor_core import extract_focus_box_for_display
from birdstamp.gui.editor_utils import path_key
from image_denoise import preview as denoise_preview
from test_editor_dejitter import window, _APP
from test_reference_tracking import wait_until
from test_sequence_transport import populate


def _source(tmp_path, name='白鹭.png', color='green'):
    path = tmp_path / name
    Image.new('RGB', (160, 80), color).save(path)
    return path


def _output(source, *, color='red', crop=None):
    path = source.parent / 'denoised' / f'{source.stem}_denoised.tif'
    path.parent.mkdir(exist_ok=True)
    Image.new('RGB', (200, 100), color).save(path)
    root = ET.Element('{adobe:ns:meta/}xmpmeta')
    rdf = ET.SubElement(root, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF')
    description = ET.SubElement(rdf, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
    for key, value in denoise_preview.source_provenance(source, crop).items():
        ET.SubElement(description, '{' + denoise_preview.NAMESPACE + '}' + key).text = value
    ET.ElementTree(root).write(path.with_suffix('.xmp'), encoding='utf-8', xml_declaration=True)
    return path


def _prepare(window, monkeypatch, paths):
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *_args: None)
    populate(window, paths)
    window.export_tabs.setCurrentIndex(0)
    window._on_photo_selected(window._find_photo_item_by_path(paths[0]), None, target_view='b')
    _finish(window)


def _finish(window):
    wait_until(lambda: window._preview_decode_worker is None and window.current_source_image is not None)


def _select_again(window, path):
    window._on_photo_selected(window._find_photo_item_by_path(path), None, target_view='b')
    _finish(window)


def _send(target, kind, *, repeat=False):
    _APP.sendEvent(target, QKeyEvent(kind, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, '', repeat))


@pytest.mark.parametrize('compare', [False, True])
def test_b_denoised_uses_worker_and_preserves_source_metadata_export_and_focus(window, monkeypatch, tmp_path, compare):
    source = _source(tmp_path)
    crop = (.1, .2, .9, .8)
    output = _output(source, crop=crop)
    metadata = {'SourceFile': str(source), 'Title': '白鹭', 'Make': 'SONY',
                'ExifImageWidth': 160, 'ExifImageHeight': 80, 'SubjectArea': [40, 20, 16, 8]}
    window.raw_metadata_cache[path_key(source)] = dict(metadata)
    bird = (.2, .25, .7, .75)
    window._bird_box_cache[window._source_signature(source)] = bird
    _prepare(window, monkeypatch, [source])
    window.ab_preview.enabled.setChecked(compare)
    if compare:
        wait_until(lambda: window.ab_preview.worker is None and not window.ab_preview.pending)

    # 已缓存的普通小图不能短路“显示降噪”，查找及读图均不得回到 GUI 线程。
    assert window._cached_preview_image(source) is not None
    monkeypatch.setattr(window, '_decode_image_for_preview', lambda *_args: pytest.fail('降噪模式不得同步解码'))
    original_find = denoise_preview.find_denoised_preview
    lookup_threads = []
    def find(*args, **kwargs):
        lookup_threads.append(threading.get_ident())
        return original_find(*args, **kwargs)
    monkeypatch.setattr(preview_source, 'find_denoised_preview', find)
    window.ab_preview.b_panel.set_source_mode('denoised')
    _finish(window)

    assert lookup_threads and all(value != threading.get_ident() for value in lookup_threads)
    assert window.current_path == source and window.current_photo_info.path == source
    assert window.current_raw_metadata['Title'] == '白鹭'
    assert window.current_source_image.getpixel((0, 0)) == (255, 0, 0)
    assert window.current_source_image.info[RAW_FOCUS_CROP_KEY] == crop
    expected = extract_focus_box_for_display(metadata, 200, 100, camera_crop_box=crop)
    assert window.preview_label.canvas._focus_box == pytest.approx(expected)
    assert window.preview_label.canvas._bird_box == pytest.approx(map_camera_focus_box(bird, crop))
    assert '显示降噪' in window.preview_label._status_label.text()
    assert str(output) not in str(window._collect_workspace_payload(tmp_path / 'test.birdstamp-workspace.json'))
    with decode_image(source) as exported:
        assert exported.size == (160, 80) and exported.getpixel((0, 0)) == (0, 128, 0)


def test_b_denoised_rechecks_created_replaced_and_removed_output(window, monkeypatch, tmp_path):
    source = _source(tmp_path)
    _prepare(window, monkeypatch, [source])
    window.ab_preview.b_panel.set_source_mode('denoised')
    _finish(window)
    assert window.current_source_image.getpixel((0, 0)) == (0, 128, 0)
    assert '降噪' in window.preview_label._status_label.text()
    output = _output(source)
    _select_again(window, source)
    assert window.current_source_image.getpixel((0, 0)) == (255, 0, 0)
    _output(source, color='blue')
    _select_again(window, source)
    assert window.current_source_image.getpixel((0, 0)) == (0, 0, 255)
    output.unlink()
    _select_again(window, source)
    assert window.current_source_image.getpixel((0, 0)) == (0, 128, 0)
    assert window.ab_preview.b_panel.source_mode() == 'denoised'


def test_b_denoised_late_worker_cannot_replace_new_default_mode(window, monkeypatch, tmp_path):
    source = _source(tmp_path)
    _output(source)
    _prepare(window, monkeypatch, [source])
    entered, release = threading.Event(), threading.Event()
    original_find = denoise_preview.find_denoised_preview
    def find(*args, **kwargs):
        entered.set()
        assert release.wait(5), '测试未释放降噪查询 worker'
        return original_find(*args, **kwargs)
    monkeypatch.setattr(preview_source, 'find_denoised_preview', find)
    try:
        window.ab_preview.b_panel.set_source_mode('denoised')
        wait_until(entered.is_set)
        window.ab_preview.b_panel.set_source_mode('default')
        release.set()
        _finish(window)
        assert window.current_source_image.getpixel((0, 0)) == (0, 128, 0)
        assert window.ab_preview.b_panel.source_mode() == 'default'
        assert '显示降噪' not in window.preview_label._status_label.text()
    finally:
        release.set()


def test_b_held_navigation_uses_quick_frames_and_resolves_once_on_release(window, monkeypatch, tmp_path):
    paths = [_source(tmp_path, f'连拍{index}.png') for index in range(4)]
    for path in paths:
        _output(path)
    _prepare(window, monkeypatch, paths)
    transport = window.sequence_transport
    transport._source_scan_timer.stop()
    monkeypatch.setattr(transport, '_request_source_frames', lambda: None)
    for path in paths:
        transport._source_cache[window._source_signature(path)] = (Image.new('RGB', (160, 80), 'green'), (160, 80))
    window.ab_preview.b_panel.set_source_mode('denoised')
    _finish(window)
    original_find = denoise_preview.find_denoised_preview
    lookups = []
    def find(source, *args, **kwargs):
        lookups.append(source)
        return original_find(source, *args, **kwargs)
    monkeypatch.setattr(preview_source, 'find_denoised_preview', find)
    target = window.photo_list._tree_widget
    _send(target, QEvent.Type.KeyPress)
    _finish(window)
    assert len(lookups) == 1 and window.current_path == paths[1]
    _send(target, QEvent.Type.KeyPress, repeat=True)
    transport.timer.stop()
    assert window.current_path == paths[2] and window._preview_is_quick
    assert window.current_source_image.getpixel((0, 0)) == (0, 128, 0)
    _send(target, QEvent.Type.KeyRelease, repeat=True)
    _APP.processEvents()
    assert transport.active and len(lookups) == 1 and window._preview_decode_worker is None
    _send(target, QEvent.Type.KeyRelease)
    _finish(window)
    assert len(lookups) == 2 and not transport.active
    assert window.current_path == paths[2]
    assert window.current_source_image.getpixel((0, 0)) == (255, 0, 0)


def test_first_repeat_cancels_slow_denoised_upgrade_even_if_next_quick_frame_missing(window, monkeypatch, tmp_path):
    paths = [_source(tmp_path, f'缺缓存{index}.png') for index in range(3)]
    for path in paths:
        _output(path)
    _prepare(window, monkeypatch, paths)
    transport = window.sequence_transport
    transport._source_scan_timer.stop()
    monkeypatch.setattr(transport, '_request_source_frames', lambda: None)
    transport._source_cache[window._source_signature(paths[1])] = (Image.new('RGB', (160, 80), 'green'), (160, 80))
    window.ab_preview.b_panel.set_source_mode('denoised')
    _finish(window)
    entered, release = threading.Event(), threading.Event()
    original_find = denoise_preview.find_denoised_preview
    def find(*args, **kwargs):
        entered.set()
        assert release.wait(5), '测试未释放首帧降噪查询 worker'
        return original_find(*args, **kwargs)
    monkeypatch.setattr(preview_source, 'find_denoised_preview', find)
    target = window.photo_list._tree_widget
    try:
        _send(target, QEvent.Type.KeyPress)
        wait_until(entered.is_set)
        assert window.current_path == paths[1]
        assert window._preview_is_quick
        _send(target, QEvent.Type.KeyPress, repeat=True)
        transport.timer.stop()
        release.set()
        wait_until(lambda: window._preview_decode_worker is None)
        assert window.current_path == paths[1] and transport.active
        assert window._preview_is_quick
        assert window.current_source_image.getpixel((0, 0)) == (0, 128, 0)
        _send(target, QEvent.Type.KeyRelease)
        _finish(window)
        assert window.current_source_image.getpixel((0, 0)) == (255, 0, 0)
    finally:
        release.set()
        transport.stop(commit=False)


@pytest.mark.parametrize('camera_box', [(.2, .25, .7, .75), (0, 0, 1, 1)])
def test_bird_detect_worker_normalizes_raw_pixels_to_shared_camera_box(monkeypatch, camera_box):
    from birdstamp.gui import bird_detect_worker
    crop = (.1, .2, .9, .8)
    image = Image.new('RGB', (200, 100))
    image.info[RAW_FOCUS_CROP_KEY] = crop
    monkeypatch.setattr(bird_detect_worker, 'detect_primary_bird_box',
                        lambda _image: map_camera_focus_box(camera_box, crop))
    worker = bird_detect_worker.BirdDetectWorker('source-signature', image)
    results = []
    worker.result_ready.connect(lambda signature, box: results.append((signature, box)))
    worker.start()
    wait_until(lambda: not worker.isRunning() and bool(results))
    assert results[0][0] == 'source-signature'
    assert results[0][1] == pytest.approx(camera_box)
    with pytest.raises(ValueError):
        image.getpixel((0, 0))
