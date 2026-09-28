"""A/B 原图编辑绑定各自路径与源图坐标；非参考图的修改仅修正该图匹配。"""
import json

import pytest
from PyQt6.QtCore import QEvent, Qt

from test_editor_dejitter import window, _APP
from test_reference_tracking import REGIONS, wait_until
from test_dejitter_tab import setup_tab, analyze
from test_editor_ab_preview import finish
from test_reference_region_handles import send
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_dejitter import _BirdStampDejitterMixin
from birdstamp.gui.edit_modes import EDIT_MODE_REFERENCE_REGION, EDIT_MODE_NONE
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter.manual_region_matches import MANUAL_MATCHES_KEY


def setup_ab(window, monkeypatch):
    paths, target, _ = setup_tab(window, monkeypatch)
    # 使用真实逐图设置入口，确保手动修正进入 worker action 与输入签名。
    monkeypatch.setattr(window, '_build_dejitter_seeds', _BirdStampDejitterMixin._build_dejitter_seeds.__get__(window))
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    window.resize(1400, 900)
    window.show()
    finish(ab)
    window._set_dejitter_view('edit')
    _APP.processEvents()
    return paths, target, ab


def move(canvas, start=(.25,.35), end=(.275,.33125)):
    send(canvas,QEvent.Type.MouseButtonPress,start)
    send(canvas,QEvent.Type.MouseMove,end)
    send(canvas,QEvent.Type.MouseButtonRelease,end)


def test_a_reference_edits_source_coordinates_while_b_shows_other_original(window,monkeypatch):
    paths,target,ab = setup_ab(window,monkeypatch)
    window.current_path,window.current_source_image = paths[1],target
    window._preview_outer_pad = (70,50,30,10)  # B 的模板遗留补边不能参与 A 坐标换算。
    window._refresh_preview_label()
    assert ab.preview.canvas.edit_mode() == EDIT_MODE_REFERENCE_REGION
    move(ab.preview.canvas)
    assert window.current_path == paths[1] and ab.path == paths[0]
    assert window._dejitter_reference_source == str(paths[0])
    assert window._dejitter_reference_regions[0] == pytest.approx((.125,.13125,.425,.53125))
    assert window._dejitter_reference_regions[1] == REGIONS[1]
    assert not window._dejitter_manual_matches
    # 两侧都选参考图时，右侧修改同步左侧，且不走模板补边坐标。
    window.current_path = paths[0]
    window._refresh_preview_label()
    move(window.preview_label.canvas, (.275,.33125), (.25,.35))
    assert window._dejitter_reference_regions[0] == pytest.approx(REGIONS[0])
    assert ab.preview.canvas.reference_regions() == window._dejitter_reference_regions


@pytest.mark.parametrize('side', ['a','b'])
def test_target_manual_match_preserves_reference_and_enters_analysis(window,monkeypatch,side):
    paths,target,ab = setup_ab(window,monkeypatch)
    if side == 'a':
        ab.select_a(paths[1])
        finish(ab)
        canvas = ab.preview.canvas
    else:
        window.current_path,window.current_source_image = paths[1],target
        window._refresh_preview_label()
        canvas = window.preview_label.canvas
    assert canvas.edit_mode() == EDIT_MODE_REFERENCE_REGION
    assert not canvas.reference_region_creation_enabled
    assert len(canvas._reference_diagnostics) == 2 and not any(row[2] for row in canvas._reference_diagnostics)
    move(canvas)
    assert window._dejitter_reference_source == str(paths[0])
    assert window._dejitter_reference_regions == REGIONS
    boxes = window._manual_boxes_for_path(paths[1])
    assert boxes[0] == pytest.approx((.125,.13125,.425,.53125)) and boxes[1] is None
    assert window._build_dejitter_seeds(paths)[0].settings[MANUAL_MATCHES_KEY] is None
    assert window._build_dejitter_seeds(paths)[1].settings[MANUAL_MATCHES_KEY]
    assert '手动' in canvas._reference_diagnostics[0][1]
    analyze(window)
    assert window._sequence_preview.output_size == (195,157)
    assert window._sequence_preview.tracking[path_key(paths[1])].manual_indices == (0,)
    # 分析结果缓存不能使撤销后的手动位置复活。
    window._set_dejitter_view('edit')
    window._commit_manual_region_match(paths[1],0,None)
    assert not window._manual_record_for_path(paths[1])
    assert window._tracking_result_for_path(paths[1]).boxes[0] is None
    analyze(window)
    assert not window._sequence_preview.tracking[path_key(paths[1])].manual_indices
    assert window._sequence_preview.output_size == (195,157)


def test_a_original_remains_editable_when_b_is_result_and_a_result_cannot_edit(window,monkeypatch):
    paths,_,ab = setup_ab(window,monkeypatch)
    analyze(window)
    assert window.preview_label.canvas.edit_mode() == EDIT_MODE_NONE
    assert ab.preview.canvas.edit_mode() == EDIT_MODE_REFERENCE_REGION
    move(ab.preview.canvas)
    assert window._sequence_preview is None
    assert window._dejitter_reference_regions[0] == pytest.approx((.125,.13125,.425,.53125))
    analyze(window)
    ab.mode.setCurrentIndex(1)
    finish(ab)
    assert ab.preview.canvas.edit_mode() == EDIT_MODE_NONE
    before = window._dejitter_reference_regions
    move(ab.preview.canvas)
    assert window._dejitter_reference_regions == before


@pytest.mark.parametrize('change', ['path','mode','page','disabled'])
def test_a_transition_cancels_drag_without_late_commit(window,monkeypatch,change):
    paths,_,ab = setup_ab(window,monkeypatch)
    canvas = ab.preview.canvas
    send(canvas,QEvent.Type.MouseButtonPress,(.25,.35))
    send(canvas,QEvent.Type.MouseMove,(.3,.4))
    if change == 'path':
        ab.select_a(paths[1])
        finish(ab)
    elif change == 'mode':
        ab.mode.setCurrentIndex(1)
    elif change == 'page':
        window.export_tabs.setCurrentIndex(0)
    else:
        ab.enabled.setChecked(False)
    send(canvas,QEvent.Type.MouseButtonRelease,(.3,.4))
    assert window._dejitter_reference_regions == REGIONS
    assert not window._dejitter_manual_matches


def test_workspace_restores_manual_input_and_cached_analysis(window,monkeypatch,tmp_path):
    paths,target,ab = setup_ab(window,monkeypatch)
    existing = set()
    for index,path in enumerate(paths):
        window._append_photo_path_to_list(path,existing_keys=existing,
            default_settings=window._build_current_render_settings(),sequence_value=index)
    window.current_path,window.current_source_image = paths[1],target
    window._refresh_preview_label()
    move(window.preview_label.canvas)
    analyze(window)
    workspace = tmp_path / '手动修正.birdstamp-workspace.json'
    payload = json.loads(json.dumps(window._collect_workspace_payload(workspace)))
    monkeypatch.setattr(BirdStampEditorWindow,'_schedule_async_bird_detect',lambda *a,**k:None)
    from birdstamp.gui import editor_sequence_preview_worker as workers
    def fail_recompute(*a,**k):
        raise AssertionError('工作区应恢复手动修正后的缓存')
    monkeypatch.setattr(workers,'prepare_sequence_preview',fail_recompute)
    fresh = BirdStampEditorWindow()
    try:
        fresh._restore_workspace_payload(payload,workspace)
        wait_until(lambda:not fresh._workspace_restore_in_progress() and fresh._sequence_preview is not None
                   and fresh._sequence_worker is None)
        assert fresh._manual_boxes_for_path(paths[1]) == window._manual_boxes_for_path(paths[1])
        assert fresh._sequence_preview.input_key == window._sequence_preview.input_key
        assert fresh._dejitter_reference_regions == REGIONS
        fresh._commit_source_reference_regions(paths[0],(REGIONS[0],))
        assert not fresh._dejitter_manual_matches
    finally:
        fresh.close()
        wait_until(lambda:fresh._sequence_worker is None)
        fresh.deleteLater()
        _APP.processEvents()


def test_b_switch_with_same_region_geometry_cancels_previous_photo_gesture(window,monkeypatch):
    paths,target,ab = setup_ab(window,monkeypatch)
    canvas = window.preview_label.canvas
    send(canvas,QEvent.Type.MouseButtonPress,(.25,.35))
    window.current_path,window.current_source_image = paths[1],target
    window._refresh_preview_label(preserve_view=True)
    assert canvas.reference_regions() == REGIONS
    send(canvas,QEvent.Type.MouseButtonRelease,(.3,.4))
    assert window._dejitter_reference_regions == REGIONS
    assert not window._dejitter_manual_matches


def test_failure_ab_manual_repair_reanalyzes_whole_group(window,monkeypatch):
    from PIL import Image
    from test_sequence_transport import populate
    paths,_,ab = setup_ab(window,monkeypatch)
    populate(window,paths)
    with Image.new('RGB',(200,160),'black') as image:
        image.save(paths[1])
    analyze(window)
    assert window._sequence_preview.partial and len(window._sequence_preview.jobs) == 1
    assert window.current_path == paths[1] and ab.path == paths[0]
    wait_until(lambda: window._preview_decode_worker is None)
    canvas = window.preview_label.canvas
    assert canvas.edit_mode() == EDIT_MODE_REFERENCE_REGION
    assert not any(row[2] for row in canvas._reference_diagnostics)
    move(canvas)
    analyze(window)
    assert not window._sequence_preview.partial
    assert len(window._sequence_preview.jobs) == 2
    assert window._valid_sequence_for_export() is window._sequence_preview


def test_target_blank_draw_is_disabled_and_right_click_resets_only_its_match(window,monkeypatch):
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QMouseEvent
    paths,target,ab = setup_ab(window,monkeypatch)
    window.current_path,window.current_source_image = paths[1],target
    window._refresh_preview_label()
    canvas = window.preview_label.canvas
    move(canvas,(.02,.02),(.08,.08))
    assert not window._dejitter_manual_matches
    move(canvas)
    rect = canvas.display_rect()
    pos = QPointF(rect.left()+rect.width()*.275,rect.top()+rect.height()*.33125)
    event = QMouseEvent(QEvent.Type.MouseButtonPress,pos,canvas.mapToGlobal(pos),
                        Qt.MouseButton.RightButton,Qt.MouseButton.RightButton,Qt.KeyboardModifier.NoModifier)
    _APP.sendEvent(canvas,event)
    assert not window._dejitter_manual_matches
    assert window._dejitter_reference_regions == REGIONS
    assert canvas.reference_regions() == REGIONS


def test_conflicting_manual_regions_remain_failed_after_analysis(window,monkeypatch):
    paths,target,ab = setup_ab(window,monkeypatch)
    window.current_path,window.current_source_image = paths[1],target
    window._refresh_preview_label()
    window._commit_manual_region_match(paths[1],0,(.125,.13125,.425,.53125))
    window._commit_manual_region_match(paths[1],1,(.5,.3,.85,.75))
    analyze(window)
    assert window._sequence_preview.partial
    window._set_dejitter_view('edit')
    result = window._tracking_result_for_path(paths[1])
    assert result.matched_count == 0 and '冲突' in result.error
    canvas = window.preview_label.canvas
    assert not any(row[2] for row in canvas._reference_diagnostics)
    assert canvas.reference_regions()[0] == pytest.approx((.125,.13125,.425,.53125))
