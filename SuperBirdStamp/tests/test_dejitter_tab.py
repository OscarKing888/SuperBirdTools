"""独立页、同一画布辅助层、真实成片与整组导出计划复用。"""
from dataclasses import replace
import threading

import numpy as np
import pytest
from PIL import Image
from PyQt6.QtGui import QCloseEvent

from test_editor_dejitter import window, _APP
from test_reference_tracking import images, REGIONS, wait_until, install_sequence
from birdstamp.export_stage import core
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.sequence_preview import (
    prepare_sequence_preview, render_sequence_preview_frame, apply_sequence_plans, sequence_input_key,
)
from birdstamp.gui import editor_core, editor_dejitter, editor_options
from birdstamp.gui.edit_modes import EDIT_MODE_NONE, EDIT_MODE_REFERENCE_REGION, EDIT_MODE_CROP_ADJUST
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui.editor_sequence_preview_worker import EditorSequencePreviewWorker


@pytest.fixture
def sequence(tmp_path):
    ref, shifted = images()
    paths = [tmp_path / '参考.png', tmp_path / '第二张.png']
    for path, image in zip(paths, (ref, shifted)):
        image.save(path)
        image.close()
    settings = dict(ratio=1, center_mode='custom', crop_box=(.1, .1, .9, .9),
                    draw_text=False, draw_banner=False, draw_focus=False,
                    dejitter_strategy='reference_region', dejitter_reference_enabled=True,
                    dejitter_reference_regions=REGIONS, dejitter_reference_source=str(paths[0]),
                    dejitter_reference_strength=100)
    seeds = [RenderJobSeed(p, dict(settings), {}, True) for p in paths]
    result = prepare_sequence_preview(seeds, {}, cancel_event=threading.Event())
    return seeds, result


def test_actual_preview_pixels_equal_export_and_subset_uses_full_sequence_plan(sequence):
    seeds, result = sequence
    context = render_sequence_preview_frame(result, seeds[1].path)
    job = replace(result.jobs[path_key(seeds[1].path)], crop_plan=None)
    apply_sequence_plans(result, [job])
    plan = job.crop_plan
    assert job.crop_plan_prepared
    assert job.source_paths == tuple(seed.path for seed in seeds)
    core.prepare_uniform_auto_crop_plans([job])
    assert job.crop_plan == plan
    with core.render_video_frame(job) as exported, context.image as preview:
        assert exported.size == preview.size
        np.testing.assert_array_equal(np.asarray(exported), np.asarray(preview))
    tracked = result.tracking[path_key(seeds[1].path)]
    assert tracked.matched_count == 2


def test_input_signature_detects_crop_reference_xmp_and_order(sequence):
    seeds, result = sequence
    assert sequence_input_key(seeds, {}) == result.input_key
    assert sequence_input_key(list(reversed(seeds)), {}) != result.input_key
    changed = [replace(seed, settings={**seed.settings, 'dejitter_reference_strength': 50}) for seed in seeds]
    assert sequence_input_key(changed, {}) != result.input_key
    seeds[1].path.with_suffix('.xmp').write_text('<test/>', encoding='utf-8')
    assert not result.files_current()
    with pytest.raises(ValueError, match='重新分析'):
        render_sequence_preview_frame(result, seeds[1].path)


def setup_tab(window, monkeypatch):
    paths, target = install_sequence(window, monkeypatch)
    window.draw_text_check.setChecked(False)
    window.draw_banner_check.setChecked(False)
    window._set_center_mode_value('custom', emit_changed=False)
    window._crop_box_override = (.1, .1, .9, .9)
    settings = window._build_current_render_settings()
    settings.update(ratio=1, crop_box=(.1, .1, .9, .9), center_mode='custom')
    raw = {'Make': 'SONY', 'Model': 'ILCE-1', 'ImageWidth': 200, 'ImageHeight': 160,
           'SubjectArea': '80 80 20 16'}
    seeds = [RenderJobSeed(path, dict(settings), dict(raw), True) for path in paths]
    monkeypatch.setattr(window, '_build_video_export_job_seeds', lambda paths, **kw: seeds)
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *a: None)
    window._preview_debounce_timer.stop()
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    return paths, target, seeds


def analyze(window):
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    assert window._sequence_preview is not None, window._sequence_message


def test_tab_switch_preserves_canvas_modes_and_all_auxiliary_controls(window, monkeypatch):
    canvas = window.preview_label.canvas
    window._set_edit_mode_button_checked(EDIT_MODE_CROP_ADJUST)
    paths, target, seeds = setup_tab(window, monkeypatch)
    assert window.export_tabs.tabText(1) == '去抖动'
    assert canvas is window.preview_label.canvas
    assert canvas.edit_mode() == EDIT_MODE_REFERENCE_REGION
    window.preview_grid_combo.setCurrentIndex(window.preview_grid_combo.findData('thirds'))
    window.show_focus_box_check.setChecked(True)
    window.show_bird_box_check.setChecked(True)
    analyze(window)
    assert window._sequence_result_mode()
    assert canvas.edit_mode() == EDIT_MODE_NONE
    assert canvas._composition_grid_mode == 'thirds'
    assert canvas._crop_effect_box == (0, 0, 1, 1)
    assert canvas._focus_box is not None
    assert not canvas._reference_regions
    window.export_tabs.setCurrentIndex(0)
    assert not window._sequence_result_mode()
    assert canvas.edit_mode() == EDIT_MODE_CROP_ADJUST
    assert window.show_focus_box_check.isChecked()
    assert window.show_bird_box_check.isChecked()
    assert window.preview_grid_combo.currentData() == 'thirds'
    window.export_tabs.setCurrentIndex(1)
    assert canvas.edit_mode() == EDIT_MODE_NONE
    window.dejitter_view_combo.setCurrentIndex(0)
    assert canvas.edit_mode() == EDIT_MODE_REFERENCE_REGION
    assert len(canvas.reference_regions()) == 2


def test_result_switching_photo_maps_focus_bird_and_grid_through_final_crop(window, monkeypatch):
    paths, target, seeds = setup_tab(window, monkeypatch)
    bird = (.2, .2, .6, .7)
    window._bird_box_cache[window._source_signature(paths[1])] = bird
    analyze(window)
    window.current_path = paths[1]
    window.current_source_image = target
    window._refresh_preview_label(preserve_view=True)
    wait_until(lambda: window._sequence_worker is None)
    result = window._sequence_frames[path_key(paths[1])]
    crop, (pt, pb, pl, pr) = result.crop_plan
    expected = editor_core.transform_source_box_after_crop_padding(
        bird, crop_box=crop, source_width=200, source_height=160, pt=pt, pb=pb, pl=pl, pr=pr,
    )
    assert window.preview_label.canvas._bird_box == pytest.approx(expected)
    assert window.preview_label.canvas._focus_box is not None
    window.show_focus_box_check.setChecked(False)
    assert not window.preview_label.canvas._show_focus_box
    # 切回源图视图后显示源图跟踪坐标，不保留成片裁切坐标。
    window.dejitter_view_combo.setCurrentIndex(0)
    assert len(window.preview_label.canvas.reference_regions()) == 2
    assert window.preview_label.canvas.edit_mode() == EDIT_MODE_NONE


def test_no_crop_disables_compensation_without_erasing_selection(window, monkeypatch):
    setup_tab(window, monkeypatch)
    regions = window._dejitter_reference_regions
    window.ratio_combo.setCurrentIndex(window.ratio_combo.findData('no_crop'))
    window._update_dejitter_controls()
    assert '未生效' in window.dejitter_effective_status.text()
    assert not window.dejitter_preprocess_btn.isEnabled()
    assert not window.dejitter_reference_strength_slider.isEnabled()
    assert window._dejitter_reference_regions == regions


def test_stale_input_clears_result_and_valid_plan_reuses_export_subset(window, monkeypatch):
    paths, _, seeds = setup_tab(window, monkeypatch)
    analyze(window)
    exported = [replace(window._sequence_preview.jobs[path_key(paths[1])], crop_plan=None)]
    window._reuse_sequence_plans(exported)
    assert exported[0].crop_plan_prepared
    # 清除参数和文件变化均不能显示或用于导出旧计划。
    paths[1].with_suffix('.xmp').write_text('changed', encoding='utf-8')
    assert window._valid_sequence_for_export() is None
    window._refresh_preview_label()
    assert not window._sequence_frames
    assert window.preview_label.canvas._source_pixmap is None


def test_result_cache_is_bounded(window, monkeypatch):
    paths, target, _ = setup_tab(window, monkeypatch)
    monkeypatch.setattr(editor_options, 'DEJITTER_PREVIEW_CACHE_BYTES', 1)
    analyze(window)
    window.current_path = paths[1]
    window.current_source_image = target
    window._refresh_preview_label()
    wait_until(lambda: window._sequence_worker is None)
    assert list(window._sequence_frames) == [path_key(paths[1])]


def test_real_gui_job_snapshots_reuse_plans_after_switching_photo(window, monkeypatch):
    from birdstamp.export_stage import render_job_seed
    paths, target = install_sequence(window, monkeypatch)
    window.draw_text_check.setChecked(False)
    window.draw_banner_check.setChecked(False)
    window._set_center_mode_value('custom', emit_changed=False)
    window._crop_box_override = (.1, .1, .9, .9)
    snapshot = window._build_current_render_settings()
    for path in paths:
        window.photo_render_overrides[path_key(path)] = window._photo_override_settings_from_snapshot(snapshot)
    monkeypatch.setattr(render_job_seed, 'extract_many_with_xmp_priority',
                        lambda paths, **kw: {p.resolve(): {'SourceFile': str(p)} for p in paths})
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *a: None)
    window._preview_debounce_timer.stop()
    window.export_tabs.setCurrentIndex(1)
    analyze(window)
    key = window._sequence_preview.input_key
    window.current_path = paths[1]
    window.current_source_image = target
    window._apply_render_settings_to_ui(window._render_settings_for_path(paths[1], prefer_current_ui=False))
    assert window._valid_sequence_for_export() is not None
    assert window._sequence_preview.input_key == key
    seeds = window._build_video_export_job_seeds([paths[1]])
    assert seeds[0].crop_plan_prepared
    assert seeds[0].source_paths == tuple(paths)


def test_analysis_error_is_visible_and_releases_worker(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    paths[1].write_bytes(b'broken')
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    assert window._sequence_preview is None
    assert '失败' in window._sequence_message
    assert window.dejitter_preprocess_btn.isEnabled()


@pytest.mark.parametrize('closing', [False, True])
def test_cancel_and_close_hold_worker_until_real_finished_and_ignore_late_result(window, monkeypatch, closing):
    setup_tab(window, monkeypatch)
    release = threading.Event()
    emitted = threading.Event()

    class DelayedWorker(EditorSequencePreviewWorker):
        def run(self):
            self.ready.emit(self.token, None, None)
            emitted.set()
            release.wait(5)

    monkeypatch.setattr(editor_dejitter, 'EditorSequencePreviewWorker', DelayedWorker)
    window.dejitter_preprocess_btn.click()
    worker = window._sequence_worker
    assert emitted.wait(2)
    try:
        if closing:
            event = QCloseEvent()
            window.closeEvent(event)
            assert not event.isAccepted()
        else:
            window.dejitter_preprocess_btn.click()
        assert window._sequence_worker is worker
        _APP.processEvents()
        assert window._sequence_preview is None
    finally:
        release.set()
        wait_until(lambda: window._sequence_worker is None)
