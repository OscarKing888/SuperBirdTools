"""安全区参考图：后台解码、实际比例、遮罩绘制、路径持久化和取消/关闭。"""
from copy import deepcopy
from pathlib import Path
import threading
import time

from PIL import Image
from PyQt6.QtCore import QThread
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog

from test_platform_safe_area import _APP, isolated_options
from test_safe_area_user_options import select_group
from birdstamp.gui.user_options_dialog import UserOptionsDialog
from birdstamp.gui.safe_area_reference import ReferenceImageLoader
from birdstamp.overlays import safe_area_options as options
from birdstamp.export_stage import VideoFrameJob, source_frame_signature_for_job


def wait_loaded(loader):
    deadline = time.monotonic()+8
    while loader.worker is not None or loader.pending:
        assert time.monotonic() < deadline, 'reference decode timed out'
        QTest.qWait(10)
    _APP.processEvents()


def make_image(path, size=(300, 650), color='#EED090'):
    with Image.new('RGB', size, color) as image:
        image.save(path)
    return str(path)


def test_reference_image_picker_mask_ratio_and_cached_paths(tmp_path, monkeypatch):
    portrait = make_image(tmp_path/'小红书 竖屏参考.png')
    landscape = make_image(tmp_path/'横屏参考.png', (700, 400), '#80B0D0')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (portrait, ''))
    dialog = UserOptionsDialog()
    try:
        select_group(dialog, 'xiaohongshu')
        dialog._choose_reference('portrait')
        dialog._set_reference('landscape', landscape)
        wait_loaded(dialog.reference_loader)
        assert dialog.reference_path_edits['portrait'].text() == portrait
        assert dialog.preview.images['portrait'].size().width() == 300
        dialog.show(); _APP.processEvents()
        frame = dialog.preview.frame_rect(0)
        assert abs(frame.width()/frame.height()-300/650) < 1e-9
        preview = dialog.preview.grab().toImage()
        center = preview.pixelColor(round(frame.center().x()), round(frame.center().y()))
        shaded = preview.pixelColor(round(frame.center().x()), round(frame.bottom()-frame.height()*.1))
        assert center == QColor('#EED090')
        assert shaded.red() < center.red()*.6  # 框外遮罩，框内保留图像。
        dialog.reference_visible_check.setChecked(False)
        plain = dialog.preview.grab().toImage()
        assert plain.pixelColor(round(frame.center().x()), round(frame.center().y())) == QColor('#D9EDE6')
        dialog.reference_visible_check.setChecked(True)
        dialog.safe_area_scroll.ensureWidgetVisible(dialog.preview)
        _APP.processEvents()
        assert dialog.grab().save(str(tmp_path/'reference-options.png'))
        assert dialog.preview.grab().save(str(tmp_path/'reference-preview.png'))
        dialog.accept()
    finally:
        dialog.close(); dialog.deleteLater(); _APP.processEvents()
    saved = options.reload_options()
    assert saved['reference_images']['xiaohongshu'] == {'portrait': portrait, 'landscape': landscape}
    assert '小红书 竖屏参考' in options.options_path().read_text(encoding='utf-8')
    reopened = UserOptionsDialog()
    try:
        select_group(reopened, 'xiaohongshu')
        wait_loaded(reopened.reference_loader)
        assert reopened.reference_path_edits['portrait'].text() == portrait
        assert not reopened.preview.images['landscape'].isNull()
    finally:
        reopened.close(); reopened.deleteLater(); _APP.processEvents()


def test_per_group_copy_clear_cancel_and_missing_reference(tmp_path):
    portrait = make_image(tmp_path/'参考.png')
    values = options.default_options()
    values['reference_images'] = {'xiaohongshu': {'portrait': portrait},
                                 'douyin': {'portrait': str(tmp_path/'不存在.png')}}
    options.save_options(values)
    dialog = UserOptionsDialog()
    try:
        select_group(dialog, 'xiaohongshu')
        wait_loaded(dialog.reference_loader)
        dialog.copy_group()
        duplicate = dialog.current_id
        assert dialog.options['reference_images'][duplicate]['portrait'] == portrait
        dialog._set_reference('portrait', '')
        assert not dialog.options['reference_images'][duplicate]
        assert dialog.options['reference_images']['xiaohongshu']['portrait'] == portrait
        select_group(dialog, 'douyin')
        wait_loaded(dialog.reference_loader)
        assert '读取失败' in dialog.reference_status['portrait'].text()
        assert dialog.preview.images['portrait'].isNull()
        assert dialog.reference_path_edits['portrait'].text().endswith('不存在.png')
        select_group(dialog, 'xiaohongshu')
        dialog._set_reference('portrait', '')
        dialog.reject()
        assert options.current_options() == values
    finally:
        dialog.close(); dialog.deleteLater(); _APP.processEvents()


def test_reference_paths_do_not_affect_export_signature_and_clear_persists(tmp_path):
    path = make_image(tmp_path/'参考.png')
    job = VideoFrameJob(Path('bird.jpg'), {'platform_safe_area': 'xiaohongshu'}, {}, {})
    signature = source_frame_signature_for_job(job)
    values = options.default_options()
    values['reference_images'] = {'xiaohongshu': {'portrait': path}}
    options.save_options(values)
    assert source_frame_signature_for_job(job) == signature
    dialog = UserOptionsDialog()
    try:
        dialog._set_reference('portrait', '')
        dialog.accept()
        assert 'reference_images' not in options.reload_options()
        assert source_frame_signature_for_job(job) == signature
    finally:
        dialog.close(); dialog.deleteLater(); _APP.processEvents()


def test_decode_is_bounded_and_runs_off_gui_thread(tmp_path, monkeypatch):
    from birdstamp.decoders import image_decoder
    actual = image_decoder.decode_image_for_preview
    calls = []
    def decode(*args, **kwargs):
        calls.append((QThread.currentThread() == _APP.thread(), kwargs['max_long_edge']))
        return actual(*args, **kwargs)
    monkeypatch.setattr(image_decoder, 'decode_image_for_preview', decode)
    path = make_image(tmp_path/'large.jpg', (2200, 3300))
    dialog = UserOptionsDialog()
    try:
        dialog._set_reference('portrait', path)
        wait_loaded(dialog.reference_loader)
        image = dialog.preview.images['portrait']
        assert max(image.width(), image.height()) <= 1400
        assert calls == [(False, 1400)]
        dialog.preview.grab(); dialog.preview.grab()
        assert len(calls) == 1
    finally:
        dialog.close(); dialog.deleteLater(); _APP.processEvents()


def test_latest_request_wins_and_pending_requests_coalesce(monkeypatch):
    from birdstamp.decoders import image_decoder
    started, release = threading.Event(), threading.Event()
    calls, delivered = [], []
    def decode(path, **kwargs):
        calls.append(str(path))
        if str(path) == 'old':
            started.set()
            assert release.wait(5)
        return Image.new('RGB', (40, 80))
    monkeypatch.setattr(image_decoder, 'decode_image_for_preview', decode)
    loader = ReferenceImageLoader()
    loader.ready.connect(lambda orientation, path, image, error: delivered.append(path) if not image.isNull() else None)
    try:
        loader.set_paths({'portrait': 'old'})
        assert started.wait(2)
        loader.set_paths({'portrait': 'skipped'})
        loader.set_paths({'portrait': 'latest'})
        release.set()
        wait_loaded(loader)
        assert calls == ['old', 'latest']
        assert delivered == ['latest']
    finally:
        release.set(); loader.close(); loader.deleteLater(); _APP.processEvents()


def test_close_waits_for_owned_worker_and_discards_pending(monkeypatch):
    from birdstamp.decoders import image_decoder
    started = threading.Event()
    calls = []
    def decode(path, **kwargs):
        calls.append(str(path))
        started.set()
        while not QThread.currentThread().isInterruptionRequested():
            time.sleep(.005)
        return Image.new('RGB', (40, 80))
    monkeypatch.setattr(image_decoder, 'decode_image_for_preview', decode)
    dialog = UserOptionsDialog()
    dialog._set_reference('portrait', 'old')
    assert started.wait(2)
    worker = dialog.reference_loader.worker
    dialog._set_reference('portrait', 'pending')
    dialog.reject()
    assert not worker.isRunning()
    assert dialog.reference_loader.worker is None and not dialog.reference_loader.pending
    assert calls == ['old']
    dialog.deleteLater(); _APP.processEvents()
