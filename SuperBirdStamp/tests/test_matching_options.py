"""Matching parameters affect evidence checks, geometry, persistence and invalidation."""
from dataclasses import replace
import threading

import numpy as np
import pytest
from PIL import Image, ImageFilter

from birdstamp.image_dejitter.matching_options import MATCHING_KEYS, MatchingOptions, normalize_matching_settings
from birdstamp.image_dejitter.region_consensus import resolve_tracking_consensus
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, common_alignment_crop, sequence_input_key
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from test_region_consensus import rotated_matches, shifted
from test_editor_dejitter import window, _APP
from test_dejitter_tab import setup_tab, analyze
from test_reference_tracking import install_sequence, wait_until


def custom(rotation=2, tolerance=.3):
    return dict(zip(MATCHING_KEYS, ('custom',rotation,tolerance)))


def test_normalization_rejects_nonfinite_and_bounds_custom_values():
    assert MatchingOptions.from_settings(custom(float('nan'),float('inf'))) == MatchingOptions()
    assert MatchingOptions.from_settings(custom(99,-5)) == MatchingOptions(5,.05)
    assert MatchingOptions.from_settings(custom('bad',None)) == MatchingOptions()
    assert MatchingOptions.from_settings({**custom(5,1),MATCHING_KEYS[0]:'auto'}) == MatchingOptions()
    assert MatchingOptions.from_settings({}) == MatchingOptions()


def test_rotation_setting_changes_consensus_and_diagnostic_without_loosening_texture():
    regions = ((.1,.1,.2,.2),(.7,.1,.8,.2),(.1,.7,.2,.8),(.7,.7,.8,.8))
    raw = RegionTrackingResult(rotated_matches(regions,3),scores=(.97,)*4)
    rejected = resolve_tracking_consensus(regions,raw,(1200,800),(1200,800))
    assert rejected.matched_count == 0
    assert '纹理匹配通过' in rejected.error and '高级自定义' in rejected.error
    assert '2°' in rejected.error and '0.3%' in rejected.error
    options = MatchingOptions.from_settings(custom(4))
    result = resolve_tracking_consensus(regions,raw,(1200,800),(1200,800),options=options)
    assert result.boxes == raw.boxes
    crops, size = common_alignment_crop(regions,{'ref':RegionTrackingResult(regions),'target':result},
                                       {'ref':(1200,800),'target':(1200,800)},(1200,800),options=options)
    assert size[0] > 1000 and size[1] > 700
    assert crops['target'][2:] == (1200,800)
    missing = RegionTrackingResult((None,)*4,reasons=('存在多个相似位置，无法唯一定位',)*4)
    assert resolve_tracking_consensus(regions,missing,(1200,800),(1200,800),options=options).matched_count == 0


def test_position_tolerance_changes_consensus_in_native_pixels():
    regions = ((.1,.2,.2,.3),(.4,.2,.5,.3),(.7,.2,.8,.3))
    raw = RegionTrackingResult(tuple(shifted(r,dx/1200) for r,dx in zip(regions,(10,14,18))),scores=(.98,)*3)
    strict = MatchingOptions.from_settings(custom(0,.05))
    loose = MatchingOptions.from_settings(custom(0,.8))
    assert resolve_tracking_consensus(regions,raw,(1200,800),(1200,800),options=strict).matched_count == 0
    assert resolve_tracking_consensus(regions,raw,(1200,800),(1200,800),options=loose).matched_count == 3
    assert loose.pixel_tolerance((1200,800)) == pytest.approx(6.4)


def test_sequence_analysis_uses_custom_options_for_tracking_and_crop(tmp_path):
    regions = ((.1,.1,.18,.22),(.7,.1,.78,.22),(.1,.7,.18,.82),(.7,.7,.78,.82))
    values = np.random.default_rng(912).integers(20,220,(800,1200),dtype=np.uint8)
    ref_path, target_path = tmp_path/'ref.png', tmp_path/'target.png'
    with Image.fromarray(values) as noise, noise.filter(ImageFilter.GaussianBlur(2)) as ref:
        with ref.rotate(1.2,resample=Image.Resampling.BICUBIC,translate=(50,30)) as target:
            ref.save(ref_path)
            target.save(target_path)
    settings = dict(dejitter_reference_regions=regions,dejitter_reference_source=str(ref_path),**custom(0))
    seeds = [RenderJobSeed(p,settings,{},True) for p in (ref_path,target_path)]
    with pytest.raises(ValueError,match='几何偏差'):
        prepare_sequence_preview(seeds,cancel_event=threading.Event())
    enabled = [replace(s,settings={**settings,**custom(2)}) for s in seeds]
    result = prepare_sequence_preview(enabled,cancel_event=threading.Event())
    assert len(result.jobs) == 2 and not result.partial
    assert all(r.matched_count == 4 for r in result.tracking.values())
    assert sequence_input_key(enabled) != sequence_input_key(seeds)


def test_controls_default_custom_reset_and_global_snapshot(window,monkeypatch,tmp_path):
    controls = window.dejitter_matching_controls
    assert controls.settings() == normalize_matching_settings()
    assert controls.advanced.isHidden()
    controls.mode.setCurrentIndex(1)
    controls.rotation.setValue(3.5)
    controls.tolerance.setValue(.55)
    assert not controls.advanced.isHidden()
    saved = window._clone_render_settings(window._build_current_render_settings())
    assert all(saved[key] == value for key,value in custom(3.5,.55).items())
    assert not any(key in window._photo_override_settings_from_snapshot(saved) for key in MATCHING_KEYS)
    # 切图应用逐图设置不能覆盖整组匹配参数。
    window._apply_render_settings_to_ui({**saved,**custom(.1,.1)})
    assert controls.settings() == custom(3.5,.55)
    controls.reset.click()
    assert controls.settings() == normalize_matching_settings()
    window._restore_dejitter_reference_from_settings(saved)
    assert controls.settings() == custom(3.5,.55)
    window._restore_dejitter_reference_from_settings({})
    assert controls.settings() == normalize_matching_settings()


def test_changing_matching_options_discards_previews_and_tracking(window,monkeypatch):
    setup_tab(window,monkeypatch)
    analyze(window)
    assert window._reference_tracking_results and window._sequence_preview is not None
    epoch = window._sequence_epoch
    window.dejitter_matching_controls.mode.setCurrentIndex(1)
    assert window._sequence_epoch > epoch
    assert window._sequence_preview is None and not window._sequence_quick_frames
    assert not window._reference_tracking_results
    assert not window.dejitter_export_btn.isEnabled()
    assert '重新分析' in window._sequence_message


def test_parameter_change_cancels_owned_worker_and_rejects_late_diagnostics(window,monkeypatch):
    from birdstamp.gui import editor_sequence_preview_worker as workers
    setup_tab(window,monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = workers.prepare_sequence_preview

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(workers,'prepare_sequence_preview',delayed)
    window.dejitter_preprocess_btn.click()
    worker = window._sequence_worker
    try:
        wait_until(entered.is_set)
        old_token = worker.token
        window.dejitter_matching_controls.mode.setCurrentIndex(1)
        assert window._sequence_worker is worker  # 真实 finished 前保留线程所有权。
        assert worker.cancel_event.is_set() and worker.isInterruptionRequested()
        worker.diagnostics.emit(old_token, {'input_key':'stale','tracking':{},'signatures':()})
        _APP.processEvents()
        assert not window._reference_tracking_results and window._sequence_preview is None
    finally:
        release.set()
        wait_until(lambda:window._sequence_worker is None)
    assert window._sequence_preview is None and not window.dejitter_export_btn.isEnabled()


def test_matching_settings_survive_workspace_roundtrip(window,monkeypatch,tmp_path):
    paths,target = install_sequence(window,monkeypatch)
    target.close()
    existing = set()
    for i,path in enumerate(paths):
        window._append_photo_path_to_list(path,existing_keys=existing,
            default_settings=window._build_current_render_settings(),sequence_value=i)
    monkeypatch.setattr(window,'_schedule_async_bird_detect',lambda *a,**k:None)
    controls = window.dejitter_matching_controls
    controls.mode.setCurrentIndex(1)
    controls.rotation.setValue(3.4)
    controls.tolerance.setValue(.6)
    from birdstamp.workspace import write_workspace_json, read_workspace_json
    path = tmp_path/'matching.birdstamp-workspace.json'
    payload = window._collect_workspace_payload(path)
    write_workspace_json(path,payload)
    controls.reset.click()
    window._restore_workspace_payload(read_workspace_json(path),path)
    wait_until(lambda:not window._workspace_restore_in_progress())
    assert controls.settings() == custom(3.4,.6)
    assert all(window._build_dejitter_seeds(paths)[0].settings[k] == v for k,v in custom(3.4,.6).items())


def test_bundled_defaults_supply_custom_controls(window,monkeypatch):
    from birdstamp.gui import editor_options
    from birdstamp.gui.editor_matching_controls import DejitterMatchingControls
    defaults = normalize_matching_settings({**custom(1.5,.25),MATCHING_KEYS[0]:'auto'})
    monkeypatch.setattr(editor_options,'DEJITTER_MATCHING_DEFAULTS',defaults)
    controls = DejitterMatchingControls(editor_options.DEJITTER_MATCHING_DEFAULTS,window)
    assert controls.settings() == defaults
    controls.mode.setCurrentIndex(1)
    assert controls.rotation.value() == 1.5 and controls.tolerance.value() == .25
    controls.reset.click()
    assert controls.settings() == defaults
