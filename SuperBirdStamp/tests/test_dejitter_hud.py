"""去抖动面板顺序、预览区 HUD 与就地取消按钮的交互契约。"""
import threading

from test_editor_dejitter import window, _APP, _finish_recommendation
from birdstamp.image_dejitter.region_recommendation import Recommendation


def test_hud_floats_on_canvas_and_follows_dejitter_tab(window):
    hud=window.dejitter_hud
    assert hud.parentWidget() is window.preview_label.canvas
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    assert not hud.isHidden()
    assert hud.pos().x()==8 and hud.pos().y()==8
    assert hud.width()<=window.preview_label.canvas.width() or window.preview_label.canvas.width()<176
    window.export_tabs.setCurrentIndex(0)
    assert hud.isHidden()


def test_hud_next_step_follows_workflow(window):
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    hud=window.dejitter_hud
    window._commit_source_reference_regions(window.current_path,())
    window._update_dejitter_controls()
    assert hud._step==1 and '框选参考区' in hud.hint.text()
    window._commit_source_reference_regions(window.current_path,((.1,.1,.3,.3),))
    window._update_dejitter_controls()
    assert hud._step==2 and '分析并预览成片' in hud.hint.text()


def test_hud_collapse_and_matching_section_persist(window):
    hud=window.dejitter_hud
    hud.toggle.click()
    window.dejitter_matching_section.set_expanded(True)
    state=window._collect_sequence_workspace_state()
    assert state['hud_collapsed'] is True and state['matching_expanded'] is True
    hud.set_collapsed(False);window.dejitter_matching_section.set_expanded(False)
    window._restore_sequence_workspace_state(state)
    assert hud.is_collapsed() and window.dejitter_matching_section.is_expanded()
    # 旧工作区没有这两个键：HUD 展开、匹配参数折叠。
    window._restore_sequence_workspace_state({})
    assert not hud.is_collapsed() and not window.dejitter_matching_section.is_expanded()


def test_matching_summary_reflects_current_values(window):
    window.dejitter_reference_strength_slider.setValue(94)
    window.dejitter_alignment_combo.setCurrentIndex(window.dejitter_alignment_combo.findData('rigid'))
    text=window.dejitter_matching_section.header_button.text()
    assert '平移＋旋转' in text and '自动' in text and '94%' in text


def test_recommend_button_becomes_cancel_while_running(window,monkeypatch):
    from birdstamp.gui import region_recommendation_panel as module
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    started=threading.Event();release=threading.Event()
    def recommend(*a,**kw):
        started.set();release.wait(5);return Recommendation('ready','迟到',((.1,.1,.2,.2),))
    monkeypatch.setattr(module,'recommend_regions',recommend)
    button=window.dejitter_auto_regions_btn
    assert not hasattr(window.dejitter_recommendation,'cancel_button')
    button.click();assert started.wait(2)
    window._update_dejitter_controls()
    assert button.text()=='取消推荐' and button.isEnabled()
    button.click()
    assert window.dejitter_recommendation.worker.isInterruptionRequested()
    assert button.text()=='正在停止…' and not button.isEnabled()
    release.set();_finish_recommendation(window)
    assert button.text()=='一键推荐选区'
    assert not window._dejitter_reference_regions


def test_reference_toggle_starts_selection_without_existing_reference(window):
    from birdstamp.gui.edit_modes import EDIT_MODE_REFERENCE_REGION
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    window._dejitter_reference_source=None
    window._update_dejitter_controls()
    toggle=window.dejitter_edit_reference_btn
    assert toggle.isEnabled() and not hasattr(window,'dejitter_draw_btn')
    mode=window._edit_mode_buttons[EDIT_MODE_REFERENCE_REGION]
    if toggle.isChecked():
        toggle.click()
    assert not toggle.isChecked() and not mode.isChecked()
    # 没有参考图时同一按钮直接在当前照片开始框选，不再需要单独的“框选 / 追加选区”。
    toggle.click()
    assert toggle.isChecked() and mode.isChecked()


def test_basic_method_hides_unrelated_rows_and_orders_target_after_follow(window):
    controls=window.dejitter_subject_controls
    panel=window.dejitter_recommendation
    controls.method.setCurrentIndex(controls.method.findData('reference_region'))
    window._update_dejitter_controls()
    assert controls.mode.isHidden() and controls.window.isHidden()
    assert panel.part.isHidden() and panel.model_row.isHidden()
    row=lambda w:controls.form.getWidgetPosition(w)[0]
    assert row(controls.method)<row(controls.follow)<row(panel.target_button)<row(controls.follow_window)
    controls.method.setCurrentIndex(controls.method.findData('subject_local'))
    window._update_dejitter_controls()
    assert not controls.mode.isHidden() and not panel.part.isHidden() and not panel.target_button.isHidden()
    assert row(panel.part)<row(panel.target_button)<row(controls.mode)
