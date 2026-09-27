"""真实分析失败切换 A/B 原图；异步旧任务、恢复与导出不抢占对照。"""
from dataclasses import replace

import pytest
from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal

from test_editor_dejitter import window, _APP
from test_dejitter_tab import setup_tab
from test_editor_ab_preview import finish
from test_reference_tracking import wait_until
from test_sequence_transport import populate


@pytest.mark.parametrize('already_open', [False, True])
def test_failed_analysis_pins_first_and_selects_exact_failed_original(window, monkeypatch, already_open):
    paths, target, seeds = setup_tab(window, monkeypatch)
    failed = paths[0].parent / '子目录' / paths[1].name
    failed.parent.mkdir()
    with Image.new('RGB', (200, 160), 'red') as image:
        image.save(failed)
    # 第一张并非参考图，且失败图与第一张重名。
    paths[:] = [paths[1], paths[0], failed]
    seeds[:] = [seeds[1], seeds[0], replace(seeds[0], path=failed)]
    populate(window, paths)
    ab = window.ab_preview
    ab.enabled.setChecked(already_open)
    ab.pin.setChecked(False)
    ab.mode.setCurrentIndex(1)
    ab.path = paths[1]
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    finish(ab)
    wait_until(lambda: window._preview_decode_worker is None)
    assert window._sequence_preview is None
    assert ab.enabled.isChecked() and ab.pin.isChecked()
    assert ab.path == paths[0] and ab.mode.currentIndex() == 0
    assert ab.image is not None and ab.frame is None
    assert ab.image.pixelColor(0, 0).getRgb()[:3] == target.getpixel((0, 0))
    assert window.current_path == failed
    assert window.photo_list.currentItem() is window._find_photo_item_by_path(failed)
    assert ab.b_photos.currentData() == str(failed)
    assert ab.b_mode.currentIndex() == 1 and not window._sequence_result_mode()
    assert window.current_source_image.getpixel((0, 0)) == (255, 0, 0)
    diagnostics = window.preview_label.canvas._reference_diagnostics
    assert len(diagnostics) == 2 and all(not row[2] for row in diagnostics)
    assert len(window._reference_tracking_results) == 3
    assert '失配' in window._sequence_message
    assert '分析失败' in window.dejitter_analysis_progress.format()
    assert window._sequence_pending_path is None and not window._sequence_upgrade_timer.isActive()


def test_initial_sharp_preview_failure_switches_after_quick_frames_arrive(window, monkeypatch):
    from birdstamp.export_stage import sequence_preview
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(paths[1]))
    wait_until(lambda: window._preview_decode_worker is None)

    def fail(*args, **kwargs):
        raise OSError('清晰帧生成失败')

    monkeypatch.setattr(sequence_preview.ImageProcPipeline, 'process', fail)
    window.ab_preview.enabled.setChecked(False)
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    finish(window.ab_preview)
    wait_until(lambda: window._preview_decode_worker is None)
    assert window._sequence_preview is not None and window._sequence_quick_frames
    assert window.ab_preview.enabled.isChecked()
    assert window.ab_preview.path == paths[0]
    assert window.current_path == paths[1] and window.current_source_image is not None
    assert not window._sequence_result_mode()
    assert '清晰帧生成失败' in window._sequence_message
    assert '分析失败' in window.dejitter_analysis_progress.format()


@pytest.mark.parametrize('broken_index', [0, 1])
def test_broken_photo_failure_selects_it_without_showing_previous_pixels(window, monkeypatch, broken_index):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    paths[broken_index].write_bytes(b'broken image')
    errors = []
    monkeypatch.setattr(window, '_show_error', lambda *args: errors.append(args))
    window.ab_preview.enabled.setChecked(False)
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    finish(window.ab_preview)
    wait_until(lambda: window._preview_decode_worker is None)
    assert window.ab_preview.enabled.isChecked() and window.ab_preview.path == paths[0]
    assert window.current_path == paths[broken_index] and window.current_source_image is None
    assert window.preview_label.canvas._source_pixmap is None
    assert errors and errors[0][0] == '读取失败'


@pytest.mark.parametrize('route', ['export', 'restore', 'upgrade', 'stale', 'cancel', 'shutdown', 'unknown', 'removed'])
def test_other_failure_routes_do_not_change_ab_or_selection(window, monkeypatch, route):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    ab = window.ab_preview
    ab.enabled.setChecked(False)

    class Failure(QObject):
        failed = pyqtSignal(int, str)
        failure_path = paths[1] if route != 'unknown' else None
        restore_only = route == 'restore'

        def isInterruptionRequested(self):
            return route == 'cancel'

    worker = Failure(window)
    worker.failed.connect(window._on_sequence_failed)
    window._sequence_worker = worker
    window._sequence_progress_kind = 'export' if route == 'export' else None if route == 'upgrade' else 'analysis'
    window._sequence_exporting = route == 'export'
    window._sequence_shutdown = route == 'shutdown'
    if route == 'removed':
        paths.pop()
    try:
        worker.failed.emit(window._sequence_epoch - (route == 'stale'), '模拟失败')
        assert not ab.enabled.isChecked()
        assert window.current_path == paths[0]
    finally:
        window._sequence_worker = None
        window._sequence_shutdown = False
        window._sequence_exporting = False
