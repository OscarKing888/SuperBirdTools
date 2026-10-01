"""局部策略的 GUI、工作区、缓存和可视诊断契约。"""
from dataclasses import replace
import pytest

from test_editor_dejitter import window, _APP
from test_subject_local import seeds_for
from test_sequence_preview_cache import run_worker
from birdstamp.gui.sequence_preview_cache import SequencePreviewCache
from birdstamp.image_dejitter.recognition import METHOD_KEY, MODE_KEY


def test_method_form_persists_and_keeps_basic_rotation(window,tmp_path):
    from birdstamp.workspace import write_workspace_json,read_workspace_json
    controls=window.dejitter_subject_controls
    window.dejitter_alignment_combo.setCurrentIndex(window.dejitter_alignment_combo.findData('rigid'))
    controls.method.setCurrentIndex(controls.method.findData('subject_local'))
    controls.mode.setCurrentIndex(controls.mode.findData('follow'))
    controls.window.setValue(9)
    window._update_dejitter_controls()
    assert not window.dejitter_alignment_combo.isEnabled()
    assert window.dejitter_alignment_combo.isHidden()
    assert window.dejitter_auto_regions_btn.isEnabled()
    assert not window.dejitter_recommendation.experimental.isChecked()
    assert controls.window.isEnabled()
    settings=window._build_current_render_settings()
    assert settings[METHOD_KEY]=='subject_local' and settings[MODE_KEY]=='follow'
    assert not any(k.startswith('dejitter_') for k in window._photo_override_settings_from_snapshot(settings))
    path=tmp_path/'局部稳定.birdstamp-workspace.json'
    write_workspace_json(path,window._collect_workspace_payload(path))
    controls.method.setCurrentIndex(0)
    window._restore_workspace_payload(read_workspace_json(path),path)
    assert controls.method.currentData()=='subject_local'
    assert controls.mode.currentData()=='follow' and controls.window.value()==9
    controls.method.setCurrentIndex(0)
    assert window.dejitter_alignment_combo.isEnabled()
    assert window.dejitter_alignment_combo.currentData()=='rigid'


def test_old_workspace_defaults_and_debug_is_view_only(window):
    window._restore_dejitter_reference_from_settings({})
    assert window.dejitter_subject_controls.method.currentData()=='reference_region'
    epoch=window._sequence_epoch
    window.dejitter_debug_check.setChecked(True)
    assert window._sequence_epoch==epoch
    state=window._collect_sequence_workspace_state()
    window.dejitter_debug_check.setChecked(False)
    window._restore_sequence_workspace_state(state)
    assert window.dejitter_debug_check.isChecked()


def test_advanced_cache_restores_points_plans_and_pixels(tmp_path,monkeypatch):
    from birdstamp.gui import editor_sequence_preview_worker as workers
    seeds=seeds_for(tmp_path)
    cache=SequencePreviewCache(tmp_path/'cache')
    first,quick,errors=run_worker(seeds,cache)
    assert first and quick and not errors
    monkeypatch.setattr(workers,'prepare_sequence_preview',lambda *a,**kw:pytest.fail('不得重新跟踪'))
    second,restored,errors=run_worker(seeds,cache,restore_only=True)
    assert second and restored and not errors
    assert first[0][0].tracking==second[0][0].tracking
    assert first[0][0].pixel_boxes==second[0][0].pixel_boxes
    assert first[0][0].subject_plans==second[0][0].subject_plans
    assert first[0][1].image==second[0][1].image
    changed=[replace(s,settings={**s.settings,METHOD_KEY:'reference_region'}) for s in seeds]
    assert not cache.load(changed)


def test_canvas_debug_renders_without_export_or_grid_state_changes(window):
    from birdstamp.gui.editor_preview_canvas import EditorPreviewOverlayState
    from PyQt6.QtGui import QImage
    from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas
    from PyQt6.QtGui import QPixmap
    canvas=EditorPreviewCanvas()
    canvas.resize(500,300)
    pixmap=QPixmap(500,300)
    pixmap.fill()
    canvas.set_source_pixmap(pixmap)
    # 坐标绘制可独立于原片解码验证。
    state=EditorPreviewOverlayState(subject_points=((.2,.3,.25,.32,True),(.6,.5,.65,.52,False)))
    canvas.apply_overlay_state(state)
    image=QImage(canvas.size(),QImage.Format.Format_ARGB32)
    canvas.render(image)
    assert canvas._subject_points==state.subject_points
    canvas.apply_overlay_state(EditorPreviewOverlayState())
    assert not canvas._subject_points


def test_changing_keyframe_invalidates_downstream_debug(window,tmp_path):
    import threading
    from birdstamp.export_stage.sequence_preview import prepare_sequence_preview
    from birdstamp.image_dejitter.region_tracking_result import image_file_signature
    from birdstamp.image_dejitter.manual_region_matches import manual_match_record
    from birdstamp.gui.editor_utils import path_key
    seeds=seeds_for(tmp_path)
    sequence=prepare_sequence_preview(seeds,cancel_event=threading.Event())
    window._restore_dejitter_reference_from_settings(seeds[0].settings)
    window._reference_tracking_results=dict(sequence.tracking)
    window._reference_tracking_definition=window._reference_tracking_input()
    window._reference_tracking_signature=image_file_signature(seeds[0].path)
    assert window._tracking_result_for_path(seeds[2].path).observation is not None
    window._dejitter_manual_matches[path_key(seeds[1].path)]=manual_match_record(
        seeds[1].path,seeds[0].path,seeds[0].settings['dejitter_reference_regions'],sequence.tracking[path_key(seeds[1].path)].boxes)
    result=window._tracking_result_for_path(seeds[2].path)
    assert result.observation is None and result.matched_count==0
    assert '关键帧已变化' in result.error


def test_follow_bird_controls_persist_and_target_button_detects_only(window,tmp_path,monkeypatch):
    from birdstamp.workspace import write_workspace_json,read_workspace_json
    from birdstamp.image_dejitter.recognition import FOLLOW_KEY,FOLLOW_WINDOW_KEY
    controls=window.dejitter_subject_controls
    panel=window.dejitter_recommendation
    controls.method.setCurrentIndex(controls.method.findData('reference_region'))
    assert not controls.follow.isHidden() and not controls.follow_window.isEnabled()
    assert panel.target_button.isHidden()
    controls.follow.setChecked(True)
    controls.follow_window.setValue(11)
    assert controls.follow_window.isEnabled() and not panel.target_button.isHidden()
    assert '两段式' in controls.hint.text()
    settings=window._build_current_render_settings()
    assert settings[FOLLOW_KEY] is True and settings[FOLLOW_WINDOW_KEY]==11
    path=tmp_path/'两段式.birdstamp-workspace.json'
    write_workspace_json(path,window._collect_workspace_payload(path))
    controls.follow.setChecked(False)
    window._restore_workspace_payload(read_workspace_json(path),path)
    assert controls.follow.isChecked() and controls.follow_window.value()==11
    # 跟随模式下“选择目标鸟”只识别鸟，不运行选区推荐。
    started=[]
    monkeypatch.setattr(panel,'detect_targets',lambda:started.append('detect'))
    monkeypatch.setattr(panel,'recommend',lambda:started.append('recommend'))
    panel.choose_target()
    assert started==['detect']
    # 高级方法隐藏两段式选项，且不把它写入设置。
    controls.method.setCurrentIndex(controls.method.findData('subject_local'))
    assert controls.follow.isHidden()
    assert window._build_current_render_settings()[FOLLOW_KEY] is False
