"""第二配准后端、局部映射、候选边界及双向分段导出回归。"""
from dataclasses import replace
from types import SimpleNamespace
import threading
import numpy as np
import pytest
from PIL import Image, ImageFilter

from birdstamp.image_dejitter.local_registration import register_region, LocalRegistrationTracker
from birdstamp.image_dejitter.local_crop_tracker import LocalCropSubjectTracker
from birdstamp.image_dejitter.subject_local_tracker import SubjectLocalTracker, LocalObservation
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult, image_file_signature
from birdstamp.image_dejitter.target_trajectory import TargetFrame, TargetTrajectory
from birdstamp.image_dejitter.subject_keyframes import recover_keyframe_segments
from birdstamp.gui.editor_utils import path_key
from test_subject_local import images


@pytest.mark.parametrize('shift',[(11,-7),(-8,5)])
def test_independent_registration_subpixel_translation(shift):
    source,moving=images(shift=shift)
    a,b=np.array(source.convert('L')),np.array(moving.convert('L'))
    delta,quality,points=register_region(a,b,(140,110,240,220))
    assert delta==pytest.approx(shift,abs=.2)
    assert quality>.95 and len(points)==4
    source.close();moving.close()


def test_fallback_is_independent_of_lk_thresholds(monkeypatch):
    from birdstamp.image_dejitter import region_measurement
    source,moving=images()
    tracker=LocalRegistrationTracker(source,((.25,.25,.5,.6),))
    # LK 完全失败时只对该区调用独立 NCC/ECC 配准，方法与证据如实记录。
    monkeypatch.setattr(region_measurement,'_lk',lambda template,moving,init,levels:(
        template.corners.copy(),np.zeros(len(template.corners),bool),np.full(len(template.corners),np.inf)))
    result=tracker.track(moving)
    assert result.observation.method=='ncc_ecc'
    assert result.observation.displacement==pytest.approx((8,-5),abs=.2)
    assert '子块' in result.observation.summary()
    with pytest.raises(InterruptedError):tracker.track(moving,cancelled=lambda:True)
    source.close();moving.close()


def test_wire_repetition_occlusion_and_split_motion_are_rejected():
    x,y=np.meshgrid(np.arange(300),np.arange(300))
    wire=(100+70*np.sin(y/4)).astype(np.uint8)
    with pytest.raises(ValueError,match='单向'):register_region(wire,wire,(100,100,180,180))
    repeated=(100+35*np.sin(y/4)+35*np.cos(x/4)).astype(np.uint8)
    with pytest.raises(ValueError,match='重复'):register_region(repeated,repeated,(100,100,180,180))
    source,_=images();a=np.array(source.convert('L'))
    occluded=a.copy();occluded[100:220,100:220]=127
    with pytest.raises(ValueError):register_region(a,occluded,(100,100,220,220))
    # 分块产生相反运动，不得以大面积相似度当成刚性平移。
    moving=a.copy();moving[100:160,104:224]=a[100:160,100:220]
    moving[160:220,96:216]=a[160:220,100:220]
    with pytest.raises(ValueError):register_region(a,moving,(100,100,220,220))
    source.close()


def test_stale_bird_target_and_background_report_region_numbers():
    with Image.new('RGB',(11232,7488)) as source:
        with pytest.raises(ValueError,match='选区 2、3 远离目标鸟'):
            LocalCropSubjectTracker(source,((.466,.427,.471,.444),(0,.456,.165,.486),(.92,.537,1,.572)),
                target=(.466,.426,.490,.471))


def test_manual_region_near_target_fits_crop_and_maps_origin():
    source,moving=images(shift=(-11,7))
    tracker=LocalCropSubjectTracker(source,((.265,.275,.395,.355),),target=(.3,.3,.38,.38))
    result=tracker.track(moving,target_box=(.28,.32,.36,.4))
    assert result.observation.displacement==pytest.approx((-11,7),abs=.4),result.error
    with pytest.raises(ValueError,match='原图内'):
        LocalCropSubjectTracker(source,((-.01,.2,.3,.4),),target=(.1,.1,.3,.3))
    source.close();moving.close()


def test_variants_do_not_escape_anatomical_support():
    from birdstamp.image_dejitter.bird_parts.pose import PartCandidate
    from birdstamp.image_dejitter.part_region_variants import part_region_variants
    source,_=images()
    candidate=PartCandidate('torso',((.2,.2,.5,.7),),.8)
    variants=part_region_variants(source,(candidate,))
    assert len(variants)>1 and len({v.variant for v in variants})==len(variants)
    for c in variants:
        assert c.part=='torso'
        for l,t,r,b in c.regions:assert .2<=l<r<=.5 and .2<=t<b<=.7
    source.close()


def bridge_case(tmp_path,count=5):
    source,_=images()
    regions=((.3,.3,.45,.55),);target=(.25,.2,.55,.65)
    tracker=LocalCropSubjectTracker(source,regions,target=target)
    jobs,frames,results={}, {}, {}
    for i in range(count):
        path=tmp_path/f'{i}.png';image=Image.new('RGB',source.size);image.paste(source,(i*4,i*2));image.save(path)
        box=tuple(v+(i*4/512 if j%2==0 else i*2/384) for j,v in enumerate(target))
        result=tracker.track(image,target_box=box)
        assert result.matched_count==1,result.error
        image.close();key=path_key(path)
        jobs[i]=SimpleNamespace(path=path,settings={});results[key]=result
        frames[key]=TargetFrame(box,image_file_signature(path))
    tracker.target_trajectory=TargetTrajectory(frames,'synthetic')
    source.close()
    return list(jobs.values()),tracker,results


def test_bidirectional_bridge_source_coordinates_and_identity_gate(tmp_path):
    jobs,tracker,results=bridge_case(tmp_path)
    key=path_key(jobs[2].path)
    results[key]=tracker._failed('固定参考失配')
    output=recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:False)
    obs=output[key].observation
    assert obs.status=='keyframe_bridge'
    assert obs.displacement==pytest.approx((8,4),abs=.3)
    assert obs.keyframe_paths==(str(jobs[1].path),str(jobs[3].path))
    assert results[key].matched_count==0  # 输入只读。
    frames=dict(tracker.target_trajectory.frames);frames[key]=TargetFrame(None,frames[key].signature,'遮挡')
    tracker.target_trajectory=TargetTrajectory(frames,'occlusion')
    assert recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:False)[key].matched_count==0
    with pytest.raises(InterruptedError):recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:True)


def test_tail_chain_is_bounded_and_two_sided_chains_must_agree(tmp_path):
    jobs,tracker,results=bridge_case(tmp_path)
    key=path_key(jobs[-1].path);results[key]=tracker._failed('末尾无锚点')
    # 末尾只有单侧锚点：在误差与链数上限内以链式关键帧发布，并标明方向。
    tail=recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:False)[key]
    assert tail.observation.status=='keyframe_chain' and tail.observation.chain[2]=='forward'
    assert tail.observation.displacement==pytest.approx((16,8),abs=.4)
    key=path_key(jobs[2].path);results[key]=tracker._failed('固定参考失配')
    right=path_key(jobs[3].path);r=results[right]
    # 伪造右锚点移到完全不同纹理，两条链不能悄悄平均。
    boxes=tuple((l+.08,t,r+.08,b) for l,t,r,b in r.boxes)
    results[right]=replace(r,boxes=boxes,observation=replace(r.observation,displacement=(53.,6.)))
    output=recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:False)[key]
    assert output.matched_count==0 and '前向与反向' in output.error


def test_chain_drift_and_link_limits_are_explicit(tmp_path,monkeypatch):
    from birdstamp.image_dejitter import subject_keyframes
    jobs,tracker,results=bridge_case(tmp_path,6)
    for j in jobs[2:]:results[path_key(j.path)]=tracker._failed('姿态变化')
    monkeypatch.setattr(subject_keyframes,'CHAIN_STD',.01)
    output=recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:False)
    assert all(output[path_key(j.path)].matched_count==0 for j in jobs[2:])
    assert '链式累计误差超限' in output[path_key(jobs[2].path)].error
    monkeypatch.setattr(subject_keyframes,'CHAIN_STD',1.)
    monkeypatch.setattr(subject_keyframes,'REKEY_LK',2.)   # 每帧都换关键帧
    monkeypatch.setattr(subject_keyframes,'MAX_CHAIN_LINKS',2)
    output=recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:False)
    links=[output[path_key(j.path)].observation.chain for j in jobs[2:4]]
    assert [c[0] for c in links]==[1,2]
    assert output[path_key(jobs[4].path)].matched_count==0
    assert '链式累计误差超限' in output[path_key(jobs[4].path)].error and '3 段' in output[path_key(jobs[4].path)].error


def test_bridge_is_used_by_actual_export_geometry(tmp_path,monkeypatch):
    from birdstamp.export_stage.render_job_seed import RenderJobSeed
    from birdstamp.export_stage.sequence_preview import prepare_sequence_preview,render_sequence_preview_frame
    jobs,tracker,results=bridge_case(tmp_path)
    reference=jobs[0].path
    settings={'dejitter_recognition_method':'subject_local','dejitter_reference_source':str(reference),
        'dejitter_reference_regions':tracker.regions,'dejitter_region_recommendation':{
            'version':1,'local_analysis':True,'target':tracker.target}}
    seeds=[RenderJobSeed(j.path,dict(settings),{},True) for j in jobs]
    monkeypatch.setattr(LocalCropSubjectTracker,'prepare_targets',lambda self,*a,**kw:setattr(self,'target_trajectory',tracker.target_trajectory))
    original=LocalCropSubjectTracker.track_cached
    def track(self,path,image,**kw):
        return self._failed('固定参考失配') if path==jobs[2].path else original(self,path,image,**kw)
    monkeypatch.setattr(LocalCropSubjectTracker,'track_cached',track)
    sequence=prepare_sequence_preview(seeds,cancel_event=threading.Event())
    assert sequence.tracking[path_key(jobs[2].path)].observation.status=='keyframe_bridge'
    assert sequence.pixel_boxes[path_key(jobs[2].path)][:2]==(8,4)
    rendered=[]
    for j in jobs:
        context=render_sequence_preview_frame(sequence,j.path)
        rendered.append(np.array(context.image));context.image.close()
    for array in rendered[1:]:np.testing.assert_array_equal(array,rendered[0])


def test_leg_identity_is_retained_in_independent_variants():
    from birdstamp.image_dejitter.bird_parts.pose import candidates_from_pose
    from birdstamp.image_dejitter.part_region_variants import part_region_variants
    pose=dict(points=[[.4,.4] for _ in range(23)],scores=[.9]*23,reliable=[False]*23)
    for i,xy in ((16,[.3,.55]),(18,[.3,.8]),(17,[.6,.55]),(19,[.6,.8])):
        pose['reliable'][i]=True;pose['points'][i]=xy
    source,_=images()
    candidates=candidates_from_pose(pose,(.2,.2,.8,.9))
    variants=part_region_variants(source,candidates)
    assert {c.support_ids for c in variants}=={(0,1),(0,),(1,)}
    pose['reliable'][16]=False
    assert candidates_from_pose(pose,(.2,.2,.8,.9))[0].support_ids==(1,)
    source.close()


def test_subpixel_refinement_and_fixed_keyframe_scale():
    import cv2
    source,_=images()
    a=np.array(source.convert('L'))
    b=cv2.warpAffine(a,np.array([[1,0,5.3],[0,1,-3.6]],np.float32),(512,384))
    delta,_,_=register_region(a,b,(140,110,240,220))
    assert delta==pytest.approx((5.3,-3.6),abs=.25)
    tracker=LocalCropSubjectTracker(source,((.3,.3,.45,.55),),target=(.25,.2,.55,.65))
    next_tracker=tracker.at_keyframe(source,tracker.regions,(.245,.19,.555,.66))
    # 关键帧沿用固定分析比例与纹理角色，阈值尺度不随目标框变化。
    assert next_tracker.geometry==tracker.geometry and next_tracker.analysis_size==tracker.analysis_size
    source.close()


def test_long_gap_is_chained_from_both_anchors(tmp_path):
    jobs,tracker,results=bridge_case(tmp_path,7)
    for j in jobs[1:-1]:results[path_key(j.path)]=tracker._failed('长失配段')
    recovered=recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:False)
    for i,j in enumerate(jobs[1:-1],1):
        obs=recovered[path_key(j.path)].observation
        assert obs.status=='keyframe_bridge' and obs.displacement==pytest.approx((i*4,i*2),abs=.4)


def test_cancellation_during_bridge_is_not_swallowed_as_io_error(tmp_path,monkeypatch):
    jobs,tracker,results=bridge_case(tmp_path)
    results[path_key(jobs[2].path)]=tracker._failed('固定参考失配')
    def interrupted(*args,**kwargs):raise InterruptedError('核验中取消')
    monkeypatch.setattr(LocalCropSubjectTracker,'template_at',interrupted)
    with pytest.raises(InterruptedError,match='核验中取消'):
        recover_keyframe_segments(jobs,tracker,results,cancelled=lambda:False)
