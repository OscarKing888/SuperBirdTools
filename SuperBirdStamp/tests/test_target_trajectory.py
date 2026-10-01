"""身份轨迹与真实像素位移独立；跨帧累计移动、竞争、缓存及分析接线。"""
from pathlib import Path
import threading

import numpy as np
import pytest
from PIL import Image, ImageDraw

from birdstamp.image_dejitter.bird_candidates import BirdCandidate
from birdstamp.image_dejitter.target_trajectory import (associate_temporal,build_target_trajectory,
    refinement_allowed,TargetFrame,TargetTrajectory)
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from birdstamp.gui.editor_utils import path_key


def sequence(tmp_path, count=11):
    paths,boxes=[],[]
    for i in range(count):
        box=(.1+i*.03,.3,.16+i*.03,.4)
        image=Image.new('RGB',(600,400),(180,190,200))
        ImageDraw.Draw(image).rectangle(tuple(v*s for v,s in zip(box,(600,400,600,400))),fill=(20,60,130))
        image.putpixel((0,0),(i,0,0))
        path=tmp_path/f'{i}.png';image.save(path);image.close()
        paths.append(path);boxes.append(box)
    def detector(image,**kwargs):
        return (BirdCandidate(boxes[image.getpixel((0,0))[0]],.9),)
    return paths,boxes,detector


def test_tracks_beyond_initial_radius_in_both_directions_and_invalidates(tmp_path):
    paths,boxes,detector=sequence(tmp_path)
    result=build_target_trajectory(paths[5],paths,boxes[5],detector=detector)
    assert [result.frame(p).box for p in paths]==boxes
    with pytest.raises(TypeError):result.frames['new']=None
    with paths[-1].open('ab') as f:f.write(b'changed')
    assert result.frame(paths[-1]).box is None
    assert '失效' in result.frame(paths[-1]).error


def test_appearance_and_competing_birds_are_rejected_without_largest_selection():
    box=(.4,.3,.5,.5)
    descriptor=(1.,0.)
    candidates=(BirdCandidate((.405,.3,.505,.5),.7),BirdCandidate((.415,.3,.515,.5),.99))
    with pytest.raises(ValueError,match='歧义'):
        associate_temporal(box,np.zeros(2),candidates,(descriptor,descriptor),descriptor,descriptor)
    with pytest.raises(ValueError,match='外观'):
        associate_temporal(box,np.zeros(2),candidates[:1],((0.,1.),),descriptor,descriptor)
    assert not refinement_allowed(box,candidates)
    nested=(BirdCandidate(box,.7),BirdCandidate((.41,.32,.49,.48),.6))
    assert refinement_allowed(box,nested)


def test_failure_does_not_jump_over_occlusion_and_cancel_is_honored(tmp_path):
    paths,boxes,detect=sequence(tmp_path,5)
    def detector(image,**kw):
        return () if image.getpixel((0,0))[0]==2 else detect(image,**kw)
    result=build_target_trajectory(paths[0],paths,boxes[0],detector=detector)
    assert result.frame(paths[1]).box==boxes[1]
    assert all(result.frame(p).box is None for p in paths[2:])
    assert '中断于 2.png' in result.frame(paths[-1]).error
    with pytest.raises(InterruptedError):
        build_target_trajectory(paths[0],paths,boxes[0],detector=detect,cancelled=lambda:True)


def test_target_box_movement_does_not_drive_crop_compensation(tmp_path):
    from test_subject_local import images
    from birdstamp.image_dejitter.local_crop_tracker import LocalCropSubjectTracker
    image,_=images()
    path=tmp_path/'same.png';image.save(path)
    tracker=LocalCropSubjectTracker(image,((.3,.3,.5,.6),),target=(.2,.2,.6,.7))
    frame=TargetFrame((.23,.22,.63,.72),image_file_signature(path))
    tracker.target_trajectory=TargetTrajectory({path_key(path):frame},'test')
    result=tracker.track(image,source_path=path)
    assert result.observation.displacement==pytest.approx((0.,0.),abs=.3)
    tracker.target_trajectory=TargetTrajectory({path_key(path):TargetFrame(None,frame.signature,'身份歧义')},'failed')
    assert tracker.track_cached(path,image,cancelled=lambda:False).matched_count==0
    image.close()


def test_full_analysis_prepares_readonly_targets_before_parallel_matching(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from birdstamp.export_stage.subject_sequence import analyze_subject_sequence
    from birdstamp.export_stage import sequence_analysis
    from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
    paths,boxes,detector=sequence(tmp_path,3)
    calls=[]
    class Tracker:
        regions=((.1,.3,.15,.4),)
        reference_size=(600,400)
        def prepare_targets(self,reference,paths,**kw):
            calls.append('prepare')
            self.target_trajectory=build_target_trajectory(reference,paths,boxes[0],detector=detector,**kw)
    tracker=Tracker()
    def analyze(jobs,tracker,reference,**kw):
        assert calls==['prepare']
        assert tracker.target_trajectory.frame(paths[-1]).box==boxes[-1]
        return {path_key(p):RegionTrackingResult(tracker.regions) for p in paths},{path_key(p):(600,400) for p in paths}
    monkeypatch.setattr(sequence_analysis,'analyze_sequence_frames',analyze)
    analyze_subject_sequence([SimpleNamespace(path=p,settings={}) for p in paths],tracker,paths[0],cancel_event=threading.Event())
