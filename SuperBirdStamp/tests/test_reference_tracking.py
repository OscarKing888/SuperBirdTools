"""多区域真实平移、异步跟踪与 Qt 所有权回归。"""
import threading
import time

import numpy as np
import pytest
from PIL import Image
from PyQt6.QtGui import QColor, QCloseEvent, QPixmap
from PyQt6.QtTest import QTest

from test_editor_dejitter import window, _APP
from birdstamp.gui import editor_reference_tracking as tracking_ui
from birdstamp.gui.editor_reference_tracking_worker import EditorReferenceTrackingWorker
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui.edit_modes import EDIT_MODE_REFERENCE_REGION, EDIT_MODE_NONE
from birdstamp.image_dejitter.reference_region_tracker import ReferenceRegionTracker
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult, image_file_signature

REGIONS = ((.1, .15, .4, .55), (.55, .4, .9, .85))


def images():
    rng = np.random.default_rng(2026)
    pixels = (rng.random((160, 200)) * 255).astype('uint8')
    shifted = np.roll(np.roll(pixels, 5, axis=1), -3, axis=0)
    return Image.fromarray(pixels).convert('RGB'), Image.fromarray(shifted).convert('RGB')


def wait_until(predicate):
    deadline = time.monotonic() + 6
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(1)
    assert predicate()


def install_sequence(window, monkeypatch):
    ref, target = images()
    source = window.current_path
    target_path = source.with_name('另一张.png')
    ref.save(source)
    target.save(target_path)
    window.current_source_image.close()
    window.current_source_image = ref
    window.current_source_full_size = ref.size
    window._preview_outer_pad = (0, 0, 0, 0)
    window.preview_pixmap = QPixmap(200, 160)
    window.preview_pixmap.fill(QColor('black'))
    paths = [source, target_path]
    monkeypatch.setattr(window, '_list_photo_paths', lambda: paths)
    window._on_canvas_reference_region_changed(REGIONS)
    return paths, target


def test_multiple_regions_follow_known_source_translation():
    ref, target = images()
    result = ReferenceRegionTracker(ref, REGIONS).track(target)
    assert result.matched_count == 2
    for original, tracked in zip(REGIONS, result.boxes):
        np.testing.assert_allclose(np.subtract(tracked, original), (5/200, -3/160, 5/200, -3/160), atol=.003)


def test_failed_region_keeps_original_index():
    ref, target = images()
    target.paste((0, 0, 0), (0, 0, 100, 96))
    result = ReferenceRegionTracker(ref, REGIONS).track(target)
    assert result.boxes[0] is None
    assert result.boxes[1] is not None
    assert result.matched_count == 1


def test_core_tracking_honors_cancellation():
    ref, target = images()
    with pytest.raises(InterruptedError):
        ReferenceRegionTracker(ref, REGIONS).track(target, cancelled=lambda: True)


def test_preprocess_switch_preview_and_reference_edit_protection(window, monkeypatch):
    paths, target = install_sequence(window, monkeypatch)
    window._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._reference_tracking_worker is None)
    assert len(window._reference_tracking_results) == 2
    window.current_path = paths[1]
    window.current_source_image = target
    window._refresh_preview_label(preserve_view=True)
    canvas = window.preview_label.canvas
    assert canvas.edit_mode() == EDIT_MODE_NONE
    assert canvas._reference_region_labels == ('1', '2')
    for original, shown in zip(REGIONS, canvas.reference_regions()):
        np.testing.assert_allclose(np.subtract(shown, original), (5/200, -3/160, 5/200, -3/160), atol=.003)
    # 跟踪框不可回写为原参考区，参考图的八手柄模式返回后恢复。
    window._on_canvas_reference_region_changed(((.2, .2, .8, .8),))
    assert window._dejitter_reference_regions == REGIONS
    assert window._dejitter_reference_source == str(paths[0])
    window.current_path = paths[0]
    window._refresh_preview_label(preserve_view=True)
    assert canvas.edit_mode() == EDIT_MODE_REFERENCE_REGION
    assert canvas.reference_regions() == REGIONS
    # 调整参考区使整批结果失效。
    window._on_canvas_reference_region_changed(((.2, .2, .5, .5), REGIONS[1]))
    assert not window._reference_tracking_results


def test_stale_file_signature_hides_old_tracking(window, monkeypatch):
    paths, target = install_sequence(window, monkeypatch)
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._reference_tracking_worker is None)
    window.current_path = paths[1]
    assert window._tracking_result_for_current() is not None
    Image.new('RGB', target.size, 'white').save(paths[1])
    assert window._tracking_result_for_current() is None
    window._refresh_preview_label(preserve_view=True)
    assert not window.preview_label.canvas.reference_regions()


def test_partial_failures_display_only_matching_ids(window, monkeypatch):
    paths, target = install_sequence(window, monkeypatch)
    target.paste((0, 0, 0), (0, 0, 100, 96))
    target.save(paths[1])
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._reference_tracking_worker is None)
    window.current_path = paths[1]
    window._refresh_preview_label(preserve_view=True)
    assert window.preview_label.canvas._reference_region_labels == ('2',)
    assert len(window.preview_label.canvas.reference_regions()) == 1
    assert '1/2' in window.dejitter_tracking_status.text()


def controlled_worker(monkeypatch):
    emitted = threading.Event()
    release = threading.Event()
    class ControlledWorker(EditorReferenceTrackingWorker):
        def run(self):
            result = RegionTrackingResult(self.regions, image_file_signature(self.paths[0]))
            self.resultsReady.emit(self.token, {path_key(self.paths[0]): result})
            emitted.set()
            release.wait(6)  # 业务结果已到达，但线程仍存活的窗口期。
    monkeypatch.setattr(tracking_ui, 'EditorReferenceTrackingWorker', ControlledWorker)
    return emitted, release


def test_result_signal_does_not_release_worker_and_cancel_drops_late_result(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    emitted, release = controlled_worker(monkeypatch)
    window.dejitter_preprocess_btn.click()
    worker = window._reference_tracking_worker
    try:
        assert emitted.wait(2)
        # 结果尚未分发前取消；旧 token 的结果不能重新填回缓存。
        window._invalidate_reference_tracking()
        _APP.processEvents()
        assert window._reference_tracking_worker is worker
        assert worker.isRunning()
        assert not window._reference_tracking_results
        assert not window.dejitter_preprocess_btn.isEnabled()
    finally:
        release.set()
        wait_until(lambda: window._reference_tracking_worker is None)


def test_close_waits_for_actual_thread_finish_and_ignores_result(window, monkeypatch):
    install_sequence(window, monkeypatch)
    emitted, release = controlled_worker(monkeypatch)
    window.dejitter_preprocess_btn.click()
    worker = window._reference_tracking_worker
    try:
        assert emitted.wait(2)
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert window._reference_tracking_worker is worker
        assert window._reference_tracking_shutdown
        _APP.processEvents()
        assert not window._reference_tracking_results
    finally:
        release.set()
        wait_until(lambda: window._reference_tracking_worker is None)


def test_missing_reference_is_reported_and_worker_finishes(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    paths[0].unlink()
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._reference_tracking_worker is None)
    assert '预处理失败' in window.dejitter_tracking_status.text()
    assert not window._reference_tracking_results


def test_list_change_clears_visible_results_and_allows_reprocessing(window, monkeypatch):
    paths, target = install_sequence(window, monkeypatch)
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._reference_tracking_worker is None)
    window.current_path = paths[1]
    window._refresh_preview_label(preserve_view=True)
    assert len(window.preview_label.canvas.reference_regions()) == 2
    window._invalidate_reference_tracking()
    assert not window.preview_label.canvas.reference_regions()
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._reference_tracking_worker is None)
    assert len(window.preview_label.canvas.reference_regions()) == 2


def test_unreadable_target_isolated_from_successful_frames(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    broken = paths[0].with_name('broken.png')
    broken.write_bytes(b'not an image')
    paths.append(broken)
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._reference_tracking_worker is None)
    results = window._reference_tracking_results
    assert results[path_key(paths[1])].matched_count == 2
    assert results[path_key(broken)].error
    assert results[path_key(broken)].matched_count == 0


def test_tracking_and_export_use_same_displacement():
    from birdstamp.export_stage import VideoFrameJob, prepare_uniform_auto_crop_plans
    from birdstamp.export_stage import core
    from pathlib import Path
    ref, target = images()
    settings = dict(ratio=1, center_mode='custom', crop_box=(.1, .1, .9, .9),
                    dejitter_strategy='reference_region', dejitter_reference_enabled=True,
                    dejitter_reference_regions=REGIONS, dejitter_reference_source='ref.png')
    jobs = [VideoFrameJob(Path(name), settings, {}, {}, source_image=image)
            for name, image in [('ref.png', ref), ('target.png', target)]]
    prepare_uniform_auto_crop_plans(jobs)
    centers = [core._crop_plan_center_in_source_pixels(source_width=200, source_height=160, crop_plan=job.crop_plan)
               for job in jobs]
    tracked = ReferenceRegionTracker(ref, REGIONS).track(target).boxes[0]
    preview_delta = ((tracked[0] - REGIONS[0][0]) * 200, (tracked[1] - REGIONS[0][1]) * 160)
    np.testing.assert_allclose(np.subtract(centers[1], centers[0]), preview_delta, atol=1)
