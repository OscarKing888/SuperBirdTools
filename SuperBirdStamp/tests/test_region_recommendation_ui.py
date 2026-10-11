import threading
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from test_editor_dejitter import window, _APP, _finish_recommendation
from birdstamp.image_dejitter.region_recommendation import Recommendation,CandidateProposal
from birdstamp.image_dejitter.recognition import RECOMMENDATION_KEY


def test_recommendation_metadata_roundtrip_and_manual_edits(window):
    p=window.dejitter_recommendation
    a,b=(.2,.3,.4,.5),(.6,.3,.8,.5)
    window._commit_source_reference_regions(window.current_path,(a,b))
    p.set_metadata(dict(version=1,target=(.1,.2,.9,.8),auto_regions=[a],part='head',resolved_part='head',
                        local_analysis=True,experimental=True))
    settings=window._dejitter_reference_settings()
    assert settings[RECOMMENDATION_KEY]['auto_regions']==[a]
    window._restore_dejitter_reference_from_settings(settings)
    assert p.metadata['target']==(.1,.2,.9,.8)
    assert p.experimental.isChecked()
    window._commit_source_reference_regions(window.current_path,((.21,.3,.41,.5),b))
    assert not p.metadata['auto_regions']
    assert not any(k==RECOMMENDATION_KEY for k in window._photo_override_settings_from_snapshot(settings))


def test_late_recommendation_cannot_overwrite_changed_source(window,monkeypatch):
    from birdstamp.gui import region_recommendation_panel as module
    started=threading.Event();release=threading.Event()
    original=((.1,.1,.2,.2),)
    window._commit_source_reference_regions(window.current_path,original)
    def recommend(*args,**kwargs):
        started.set();release.wait(5)
        return Recommendation('ready','旧结果',((.5,.5,.7,.7),),'background')
    monkeypatch.setattr(module,'recommend_regions',recommend)
    window.dejitter_recommendation.recommend()
    assert started.wait(2)
    window.current_path=window.current_path.with_name('other.png')
    release.set();_finish_recommendation(window)
    assert window._dejitter_reference_regions==original
    assert '丢弃' in window.dejitter_recommendation.status.text()


def test_cancel_keeps_worker_owned_until_real_finish(window,monkeypatch):
    from birdstamp.gui import region_recommendation_panel as module
    started=threading.Event();release=threading.Event()
    def recommend(*args,**kwargs):
        started.set();release.wait(5);return Recommendation('ready','迟到',((.1,.1,.2,.2),))
    monkeypatch.setattr(module,'recommend_regions',recommend)
    panel=window.dejitter_recommendation
    panel.recommend();assert started.wait(2)
    worker=panel.worker;panel.cancel()
    assert panel.worker is worker and worker.isRunning()
    assert not panel.shutdown()
    release.set();_finish_recommendation(window)
    assert not window._dejitter_reference_regions


def test_group_ownership_and_default_experimental_gate(window):
    panel=window.dejitter_recommendation
    assert window.dejitter_selection_group.isAncestorOf(panel)
    assert not panel.experimental.isChecked()
    assert window.dejitter_method_group.isAncestorOf(window.dejitter_subject_controls)
    assert window.dejitter_method_group.isAncestorOf(panel.target_button)
    assert window.dejitter_analysis_group.isAncestorOf(window.dejitter_preprocess_btn)
    assert window.export_action_bar.isAncestorOf(window.dejitter_export_btn)


def test_failed_preflight_shows_frame_and_reason_in_analysis(window,monkeypatch):
    from birdstamp.gui import region_recommendation_panel as module
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    monkeypatch.setattr(module,'recommend_regions',lambda *a,**k:Recommendation(
        'no_reliable_region','没有可靠选区',diagnostics=[dict(
            file='frame2.JPG',part='head',passed=False,reason='部位不可见')]))
    window.dejitter_recommendation.recommend()
    _finish_recommendation(window)
    assert 'frame2.JPG 头部：部位不可见' in window.dejitter_tracking_status.text()
    assert not window._dejitter_reference_regions


def draft_result():
    candidate=CandidateProposal('head',((.2,.2,.4,.5),),.8,'failed',1,2,
        (dict(file='second.JPG',passed=False,reason='纹理对应不足'),))
    return Recommendation('no_reliable_region','已找到部位，预检未通过',target=(.1,.1,.6,.8),
        metadata=dict(version=1,target=(.1,.1,.6,.8),part='auto',experimental=True,local_analysis=True),
        candidates=(candidate,))


def test_failed_draft_visible_but_only_explicit_adoption_creates_manual_regions(window,monkeypatch):
    from birdstamp.gui import region_recommendation_panel as module
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    monkeypatch.setattr(module,'recommend_regions',lambda *a,**kw:draft_result())
    panel=window.dejitter_recommendation
    panel.recommend();_finish_recommendation(window)
    assert panel.candidates.choice.count()==1
    assert '1/2' in panel.candidates.choice.currentText()
    assert 'second.JPG' in panel.candidates.detail.text()
    assert not window._dejitter_reference_regions
    assert not window.dejitter_export_btn.isEnabled()
    panel.candidates.apply.click()
    assert window._dejitter_reference_regions==draft_result().candidates[0].regions
    assert not panel.metadata['auto_regions']
    assert panel.metadata['target']==draft_result().target
    assert not window.dejitter_export_btn.isEnabled()


def test_draft_does_not_replace_manual_region_and_disappears_after_source_change(window,monkeypatch):
    from birdstamp.gui import region_recommendation_panel as module
    original=((.6,.2,.8,.5),)
    window._commit_source_reference_regions(window.current_path,original)
    monkeypatch.setattr(module,'recommend_regions',lambda *a,**kw:draft_result())
    panel=window.dejitter_recommendation
    panel.recommend();_finish_recommendation(window)
    panel.candidates.apply.click()
    assert window._dejitter_reference_regions==original
    assert '人工区冲突' in panel.status.text()
    # The panel status label is hidden; the refusal must reach the preview HUD message.
    assert '人工区冲突' in window._sequence_message
    window.current_path=window.current_path.with_name('other.png')
    panel.sync(True)
    assert panel.candidates.result is None
    assert not panel.candidates.apply.isEnabled()


def test_candidates_arrive_before_preflight_finishes_and_cancel_discards_them(window,monkeypatch):
    from birdstamp.gui import region_recommendation_panel as module
    import time
    release=threading.Event()
    def recommend(*a,**kw):
        kw['candidate_callback'](draft_result())
        release.wait(5)
        return draft_result()
    monkeypatch.setattr(module,'recommend_regions',recommend)
    panel=window.dejitter_recommendation;panel.recommend()
    deadline=time.monotonic()+2
    while panel.candidates.result is None and time.monotonic()<deadline:
        _APP.processEvents();time.sleep(.005)
    assert panel.candidates.result is not None
    assert panel.worker is not None
    assert not panel.candidates.apply.isEnabled()
    panel.cancel();release.set();_finish_recommendation(window)
    assert panel.candidates.result is None
    assert not window._dejitter_reference_regions


def test_switching_candidate_does_not_paint_into_base_image(window):
    from PIL import Image
    preview=window.dejitter_recommendation.candidates
    a=CandidateProposal('head',((.2,.2,.3,.3),),.9)
    b=CandidateProposal('torso',((.5,.5,.6,.6),),.9)
    preview.set_result(Recommendation('failed','',target=(.1,.1,.8,.8),candidates=(a,b)),
                       Image.new('RGB',(300,300),'blue'))
    preview.choice.setCurrentIndex(1)
    assert all(preview._base.pixelColor(x,y).red()==0
               for x in range(preview._base.width()) for y in range(preview._base.height()))
    assert preview.minimumHeight()>=preview.layout().sizeHint().height()


def test_background_drafts_paint_during_recommendation_then_clear_and_hint_list_positions(window,monkeypatch):
    from birdstamp.gui import region_recommendation_panel as module
    import time
    current=Path(window.current_path)
    paths=[current.with_name(f'前{i}.jpg') for i in range(2)]+[current]+[current.with_name(f'后{i}.jpg') for i in range(3)]
    monkeypatch.setattr(window,'_list_photo_paths',lambda:list(paths))
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    window._set_dejitter_view('edit')
    release=threading.Event();draft=((.1,.1,.2,.2),'pending'),((.6,.6,.7,.7),'failed')
    def recommend(*a,**kw):
        kw['candidate_callback'](Recommendation('prechecking','预检中',drafts=draft))
        release.wait(5)
        # 核心按文件名给出覆盖范围；面板换算为照片列表位置。
        return Recommendation('ready','推荐 1 个',((.3,.3,.4,.4),),'background',
                              coverage=dict(samples=3,consistent=1,last_ok=str(paths[3]),first_bad=str(paths[5])))
    monkeypatch.setattr(module,'recommend_regions',recommend)
    panel=window.dejitter_recommendation;panel.recommend()
    deadline=time.monotonic()+2
    while not panel.draft_overlays(current) and time.monotonic()<deadline:
        _APP.processEvents();time.sleep(.005)
    overlays=panel.draft_overlays(current)
    assert [o[1] for o in overlays]==['候选 1','候选 2 · 失配'] and [o[2] for o in overlays]==[False,True]
    assert not panel.draft_overlays(paths[0])  # 只画在推荐所用的参考图上
    assert window.preview_label.canvas._region_drafts
    assert not window._dejitter_reference_regions  # 草稿不进入选区
    release.set();_finish_recommendation(window)
    assert not panel.draft_overlays(current) and not window.preview_label.canvas._region_drafts
    assert window._dejitter_reference_regions==((.3,.3,.4,.4),)
    assert '第 4–6 张' in panel.status.text() and '接力追踪' in panel.status.text()
