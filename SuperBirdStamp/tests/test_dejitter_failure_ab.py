"""真实分析失败切换 A/B 原图；异步旧任务、恢复与导出不抢占对照。"""
from dataclasses import replace
import threading

import pytest
from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QCloseEvent

from test_editor_dejitter import window, _APP
from test_dejitter_tab import setup_tab
from test_editor_ab_preview import finish
from test_reference_tracking import wait_until
from test_sequence_transport import populate
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui import editor_dejitter
from birdstamp.gui.editor_sequence_preview_worker import EditorSequencePreviewWorker


@pytest.mark.parametrize('already_open', [False, True])
def test_failed_analysis_compares_first_and_selects_exact_failed_original(window, monkeypatch, already_open):
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
    ab.activate('a')
    ab.mode.setCurrentIndex(1)
    ab.path = paths[1]
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    finish(ab)
    wait_until(lambda: window._preview_decode_worker is None)
    sequence = window._sequence_preview
    assert sequence.partial and tuple(sequence.jobs) == tuple(path_key(p) for p in paths[:2])
    assert len(window._sequence_quick_frames) == 2
    assert ab.enabled.isChecked() and ab.active_side == 'b'
    assert ab.path == paths[0] and ab.mode.currentIndex() == 0
    assert ab.image is not None and ab.frame is None
    assert ab.image.pixelColor(0, 0).getRgb()[:3] == target.getpixel((0, 0))
    assert window.current_path == failed
    assert window.photo_list.currentItem() is window._find_photo_item_by_path(failed)
    assert ab.b_panel.filename.toolTip() == str(failed)
    assert ab.b_mode.currentIndex() == 0 and not window._sequence_result_mode()
    assert window.current_source_image.getpixel((0, 0)) == (255, 0, 0)
    diagnostics = window.preview_label.canvas._reference_diagnostics
    assert len(diagnostics) == 2 and all(not row[2] for row in diagnostics)
    assert len(window._reference_tracking_results) == 3
    assert '失配' in window._sequence_message
    assert '分析失败' in window.dejitter_analysis_progress.format()
    assert window._sequence_pending_path is None and not window._sequence_upgrade_timer.isActive()
    assert window.dejitter_analysis_progress.value() == 2
    assert window.dejitter_analysis_progress.maximum() == 3
    assert not window.dejitter_export_btn.isEnabled()
    assert window._valid_sequence_for_export() is None
    assert window._collect_sequence_workspace_state()['input_key'] is None

    # 对照定位之后，切回成片能直接播放保留下来的前缀，清晰升级不再次跳回失败图。
    window._set_dejitter_view('result')
    assert window.current_path == paths[0]
    assert window._validate_sequence_preview()
    window.sequence_transport.step(1)
    wait_until(lambda: window._sequence_worker is None and path_key(paths[1]) in window._sequence_frames)
    assert window._sequence_result_mode() and window.current_path == paths[1]
    assert len(window.sequence_transport.paths) == 2
    assert '分析失败' in window._sequence_message
    assert '分析失败' in window.dejitter_analysis_progress.format()
    assert window._sequence_cache_key is None
    window.sequence_transport.loop.setChecked(False)
    window.sequence_transport.toggle()
    assert window.sequence_transport.active and window.current_path == paths[0]
    window.sequence_transport._tick()
    window.sequence_transport._tick()
    assert not window.sequence_transport.active and window.current_path == paths[1]
    # 未生成的尾帧可选中，但不会错用前一张成片，也不启动清晰帧任务。
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(failed))
    window._upgrade_sequence_frame()
    assert window.preview_label.canvas._source_pixmap is None and window._sequence_worker is None
    assert window._sequence_preview is sequence


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
    assert (window._sequence_preview is None) == (broken_index == 0)


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


@pytest.mark.parametrize('closing', [False, True])
def test_queued_partial_preview_and_failure_are_ignored_after_cancel_or_close(window, monkeypatch, closing):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    paths[1].write_bytes(b'broken')
    window.ab_preview.enabled.setChecked(False)
    emitted, release = threading.Event(), threading.Event()

    class DelayedWorker(EditorSequencePreviewWorker):
        def run(self):
            super().run()
            emitted.set()
            release.wait(5)

    monkeypatch.setattr(editor_dejitter, 'EditorSequencePreviewWorker', DelayedWorker)
    window.dejitter_preprocess_btn.click()
    worker = window._sequence_worker
    try:
        assert emitted.wait(3)
        if closing:
            event = QCloseEvent()
            window.closeEvent(event)
            assert not event.isAccepted()
        else:
            window.dejitter_preprocess_btn.click()
        assert window._sequence_worker is worker
        _APP.processEvents()
        assert window._sequence_preview is None and not window._sequence_quick_frames
        assert not window.ab_preview.enabled.isChecked()
    finally:
        release.set()
        wait_until(lambda: window._sequence_worker is None)


def test_repaired_photo_reanalysis_replaces_partial_preview_and_reenables_export(window, monkeypatch):
    paths, target, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    with Image.new('RGB', target.size, 'white') as image:
        image.save(paths[1])
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    finish(window.ab_preview)
    wait_until(lambda: window._preview_decode_worker is None)
    previous = window._sequence_preview
    assert previous.partial and not window.dejitter_export_btn.isEnabled()
    target.save(paths[1])
    assert not window._validate_sequence_preview()
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    assert window._sequence_preview is not previous and not window._sequence_preview.partial
    assert len(window._sequence_preview.jobs) == 2 and window.dejitter_export_btn.isEnabled()
    assert '分析完成' in window.dejitter_analysis_progress.format()
    assert window._sequence_cache_key == window._sequence_preview.input_key
