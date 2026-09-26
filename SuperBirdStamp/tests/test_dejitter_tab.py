"""独立页、同一画布辅助层、真实成片与整组导出计划复用。"""
from dataclasses import replace
import threading

import numpy as np
import pytest
from PIL import Image
from PyQt6.QtGui import QCloseEvent

from test_editor_dejitter import window, _APP
from test_reference_tracking import images, REGIONS, wait_until, install_sequence
from birdstamp.export_stage.sequence_export import export_aligned_sequence
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.sequence_preview import (
    prepare_sequence_preview, render_sequence_preview_frame, sequence_input_key, common_alignment_crop,
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


def test_actual_preview_equals_independent_export_with_maximum_common_area(sequence, tmp_path):
    seeds, result = sequence
    assert result.output_size == (195, 157)
    folder = export_aligned_sequence(result, tmp_path, cancel_event=threading.Event())
    exported = sorted(folder.glob('*.png'))
    assert len(exported) == 2
    with Image.open(exported[0]) as first, Image.open(exported[1]) as second:
        np.testing.assert_array_equal(np.asarray(first), np.asarray(second))
    for seed, path in zip(seeds, exported):
        with render_sequence_preview_frame(result, seed.path).image as preview, Image.open(path) as output:
            assert output.size == result.output_size
            np.testing.assert_array_equal(np.asarray(output), np.asarray(preview))
    assert result.tracking[path_key(seeds[1].path)].matched_count == 2


def test_input_signature_ignores_template_crop_but_detects_reference_xmp_and_order(sequence):
    seeds, result = sequence
    assert sequence_input_key(seeds, {}) == result.input_key
    assert sequence_input_key(list(reversed(seeds)), {}) != result.input_key
    unrelated = [replace(seed, settings={**seed.settings, 'ratio': 'no_crop', 'draw_text': True, 'max_long_edge': 10}) for seed in seeds]
    assert sequence_input_key(unrelated, {'default': '/missing/template.json'}) == result.input_key
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
    monkeypatch.setattr(window, '_build_dejitter_seeds', lambda paths: seeds)
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
    window.dejitter_view_buttons['edit'].click()
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
    window.dejitter_view_buttons['edit'].click()
    assert len(window.preview_label.canvas.reference_regions()) == 2
    assert window.preview_label.canvas.edit_mode() == EDIT_MODE_NONE


def test_no_crop_allows_analysis_without_erasing_selection(window, monkeypatch):
    setup_tab(window, monkeypatch)
    regions = window._dejitter_reference_regions
    window.ratio_combo.setCurrentIndex(window.ratio_combo.findData('no_crop'))
    window._update_dejitter_controls()
    assert window.dejitter_preprocess_btn.isEnabled()
    assert window.dejitter_reference_strength_slider.isEnabled()
    analyze(window)
    assert window._sequence_preview.output_size == (195, 157)
    assert window._dejitter_reference_regions == regions


def test_stale_input_clears_result_and_disables_independent_export(window, monkeypatch):
    paths, _, seeds = setup_tab(window, monkeypatch)
    analyze(window)
    assert window.dejitter_export_btn.isEnabled()
    # 清除参数和文件变化均不能显示或用于导出旧计划。
    paths[1].with_suffix('.xmp').write_text('changed', encoding='utf-8')
    assert window._valid_sequence_for_export() is None
    window._refresh_preview_label()
    assert not window._sequence_frames
    assert not window.dejitter_export_btn.isEnabled()
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


def test_real_gui_snapshots_are_independent_from_normal_export_and_keep_valid_after_switching(window, monkeypatch):
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
    assert not seeds[0].crop_plan_prepared
    assert not seeds[0].settings['dejitter_reference_enabled']
    assert seeds[0].settings['dejitter_reference_regions'] == []


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


def test_common_rectangle_handles_different_sizes_and_rejects_conflict():
    region = ((.2, .2, .4, .4),)
    tracked = {'a': RegionTrackingResult(region),
               'b': RegionTrackingResult(((.25, .2, .5, .4),))}
    boxes, size = common_alignment_crop(region, tracked, {'a': (200, 100), 'b': (160, 100)}, (200, 100))
    assert size == (160, 100)
    assert boxes['a'] == boxes['b'] == (0, 0, 160, 100)
    two = ((.1, .1, .3, .3), (.6, .6, .8, .8))
    with pytest.raises(ValueError, match='运动不一致'):
        common_alignment_crop(two, {'a': RegionTrackingResult((two[0], (.7, .6, .9, .8)))},
                              {'a': (200, 100)}, (200, 100))
    with pytest.raises(ValueError, match='失配'):
        common_alignment_crop(region, {'a': RegionTrackingResult((None,))}, {'a': (200, 100)}, (200, 100))


def test_cancelled_export_removes_only_own_output_and_preserves_existing_files(sequence, tmp_path):
    from birdstamp.export_stage.video_export_cancelled_error import VideoExportCancelledError
    seeds, result = sequence
    existing = tmp_path / '已有照片.png'
    existing.write_bytes(b'keep')
    before = set(tmp_path.iterdir())
    cancel = threading.Event()
    with pytest.raises(VideoExportCancelledError):
        export_aligned_sequence(result, tmp_path, cancel_event=cancel,
                                progress=lambda message: cancel.set())
    assert set(tmp_path.iterdir()) == before
    assert existing.read_bytes() == b'keep'


def test_export_error_rolls_back_partial_folder(sequence, tmp_path, monkeypatch):
    from birdstamp.export_stage import sequence_export
    seeds, result = sequence
    original = sequence_export.render_sequence_preview_frame
    def fail_on_second(seq, path):
        if path == seeds[1].path:
            raise OSError('模拟磁盘/解码故障')
        return original(seq, path)
    monkeypatch.setattr(sequence_export, 'render_sequence_preview_frame', fail_on_second)
    before = set(tmp_path.iterdir())
    with pytest.raises(OSError):
        export_aligned_sequence(result, tmp_path, cancel_event=threading.Event())
    assert set(tmp_path.iterdir()) == before


def test_list_deletes_selected_region_then_last_region_and_invalidates_result(window, monkeypatch):
    setup_tab(window, monkeypatch)
    analyze(window)
    window.dejitter_region_list.item(0).setSelected(True)
    window.dejitter_delete_region_btn.click()
    assert window._dejitter_reference_regions == (REGIONS[1],)
    assert window._sequence_preview is None
    assert not window.dejitter_export_btn.isEnabled()
    window.dejitter_view_buttons['edit'].click()
    assert window.preview_label.canvas.reference_regions() == (REGIONS[1],)
    window.dejitter_region_list.item(0).setSelected(True)
    window.dejitter_delete_region_btn.click()
    assert not window._dejitter_reference_regions
    assert window._dejitter_reference_source is None
    assert not window.dejitter_preprocess_btn.isEnabled()
    assert not window.preview_label.canvas.reference_regions()


def test_edit_view_uses_full_source_and_preserves_source_coordinates_despite_template_padding(window, monkeypatch):
    setup_tab(window, monkeypatch)
    window._preview_outer_pad = (100, 200, 300, 400)
    window._refresh_preview_label()
    assert window.preview_label.canvas._source_pixmap.size().width() == 200
    assert window.preview_label.canvas._source_pixmap.size().height() == 160
    assert window._reference_region_preview_to_source(REGIONS[0]) == REGIONS[0]
    assert window._reference_regions_source_to_preview(REGIONS) == REGIONS
    assert not window._edit_mode_buttons[EDIT_MODE_CROP_ADJUST].isEnabled()


def test_gui_export_all_writes_independent_files_and_does_not_invalidate_on_template_changes(window, monkeypatch, tmp_path):
    from birdstamp.gui.editor import BirdStampEditorWindow
    paths, _, _ = setup_tab(window, monkeypatch)
    analyze(window)
    result = window._sequence_preview
    # 使用真实 handler，普通模板参数变化不能清除独立流程结果。
    window._on_output_settings_changed = BirdStampEditorWindow._on_output_settings_changed.__get__(window)
    window.draw_text_check.setChecked(True)
    window.ratio_combo.setCurrentIndex(window.ratio_combo.findData('no_crop'))
    window._preview_debounce_timer.stop()
    assert window._sequence_preview is result
    monkeypatch.setattr(editor_dejitter.QFileDialog, 'getExistingDirectory', lambda *args: str(tmp_path))
    window.dejitter_export_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    files = sorted(tmp_path.glob('去抖动_*/*.png'))
    assert len(files) == len(paths)
    with Image.open(files[0]) as first, Image.open(files[1]) as second:
        assert first.size == (195, 157)
        np.testing.assert_array_equal(np.asarray(first), np.asarray(second))
    assert '导出完成' in window._sequence_message


def test_jpg_export_uses_same_full_resolution_crop(sequence, tmp_path):
    _, result = sequence
    folder = export_aligned_sequence(result, tmp_path, output_format='jpg', cancel_event=threading.Event())
    paths = list(folder.glob('*.jpg'))
    assert len(paths) == 2
    for path in paths:
        with Image.open(path) as image:
            assert image.size == result.output_size
            assert image.format == 'JPEG'


def test_export_worker_close_holds_ownership_and_rejects_late_completion(window, monkeypatch, tmp_path):
    from birdstamp.gui.editor_sequence_preview_worker import EditorSequenceExportWorker
    setup_tab(window, monkeypatch)
    analyze(window)
    release, emitted = threading.Event(), threading.Event()

    class DelayedExport(EditorSequenceExportWorker):
        def run(self):
            self.completed.emit(self.token, str(tmp_path / 'late'))
            emitted.set()
            release.wait(5)

    monkeypatch.setattr(editor_dejitter, 'EditorSequenceExportWorker', DelayedExport)
    monkeypatch.setattr(editor_dejitter.QFileDialog, 'getExistingDirectory', lambda *a: str(tmp_path))
    window.dejitter_export_btn.click()
    worker = window._sequence_worker
    assert emitted.wait(2)
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        _APP.processEvents()
        assert window._sequence_worker is worker
        assert '导出完成' not in window._sequence_message
    finally:
        release.set()
        wait_until(lambda: window._sequence_worker is None)
