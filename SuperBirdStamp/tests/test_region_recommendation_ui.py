import threading
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from test_editor_dejitter import window, _APP, _finish_recommendation
from birdstamp.image_dejitter.region_recommendation import Recommendation
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
    assert window.dejitter_analysis_group.isAncestorOf(window.dejitter_subject_controls)
    assert window.dejitter_export_group.isAncestorOf(window.dejitter_export_btn)


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
