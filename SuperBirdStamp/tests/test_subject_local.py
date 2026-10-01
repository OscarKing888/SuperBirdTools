"""主体局部识别、源坐标、失败门控、关键帧与真实导出回归。"""
from dataclasses import replace
import threading
import numpy as np
import pytest
from PIL import Image, ImageFilter

from birdstamp.image_dejitter.subject_local_tracker import SubjectLocalTracker
from birdstamp.image_dejitter.recognition import SubjectSettings, METHOD_KEY, MODE_KEY
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, render_sequence_preview_frame, sequence_input_key
from birdstamp.export_stage.sequence_photo_error import SequencePhotoError
from birdstamp.gui.editor_utils import path_key

REGIONS = ((.15,.2,.38,.65),(.58,.25,.8,.7))


def images(size=(512,384), shift=(8,-5)):
    rng = np.random.default_rng(343)
    image = Image.fromarray(rng.integers(0,256,(*size[::-1],3),dtype=np.uint8)).filter(ImageFilter.GaussianBlur(.8))
    moved = Image.new('RGB',size)
    moved.paste(image,shift)
    return image,moved


def seeds_for(tmp_path, shifts=(0,8,16), *, mode='lock'):
    source,_ = images()
    reference = tmp_path/'参考.png'
    settings = {METHOD_KEY:'subject_local',MODE_KEY:mode,'dejitter_reference_source':str(reference),
                'dejitter_reference_regions':REGIONS,'dejitter_reference_strength':100,'dejitter_alignment_mode':'rigid'}
    seeds=[]
    for i,dx in enumerate(shifts):
        path = reference if i == 0 else tmp_path/f'帧{i}.png'
        image=Image.new('RGB',source.size)
        image.paste(source,(dx,0)); image.save(path); image.close()
        seeds.append(RenderJobSeed(path,dict(settings),{},True))
    source.close()
    return seeds


@pytest.mark.parametrize('shift',[(8,-5),(-11,7)])
def test_translation_source_coordinates_and_no_rotation(shift):
    source,moved=images(shift=shift)
    tracker=SubjectLocalTracker(source,REGIONS)
    result=tracker.track(moved)
    assert result.observation.status == 'tracked', result.error
    assert result.observation.displacement == pytest.approx(shift,abs=.25)
    assert result.matched_count == 2
    assert len(result.observation.points) <= 280
    for original,box in zip(REGIONS,result.boxes):
        assert (box[2]-box[0],box[3]-box[1]) == pytest.approx((original[2]-original[0],original[3]-original[1]))
    source.close(); moved.close()


def test_low_texture_and_resolution_refuse_false_success():
    # 平坦选区在创建时即明确拒绝，不进入逐帧“纹理不足”的笼统失败。
    with Image.new('RGB',(512,384),'white') as source:
        with pytest.raises(ValueError,match='纹理不足'):
            SubjectLocalTracker(source,REGIONS)
    source,moved=images()
    tracker=SubjectLocalTracker(source,REGIONS)
    with Image.new('RGB',(512,384),'white') as blank:
        result=tracker.track(blank)
        assert result.observation.status == 'needs_keyframe'
        assert result.observation.displacement is None and result.matched_count == 0
    with Image.new('RGB',(384,512)) as different:
        assert '尺寸' in tracker.track(different).error
    source.close(); moved.close()


def test_outside_motion_does_not_change_local_anchor():
    source,_=images()
    moving=Image.new('RGB',source.size)
    moving.paste(source,(-30,17))
    # 局部邻域真实移动 +9,+4，外部背景强烈反向运动。
    for l,t,r,b in REGIONS:
        box=(int(l*512)-35,int(t*384)-35,int(r*512)+35,int(b*384)+35)
        with source.crop(box) as patch:
            moving.paste(patch,(box[0]+9,box[1]+4))
    result=SubjectLocalTracker(source,REGIONS).track(moving)
    assert result.observation.displacement == pytest.approx((9,4),abs=.3),result.error
    source.close(); moving.close()


def test_conflicting_regions_refused_even_if_one_has_more_points():
    source,_=images()
    moving=source.copy()
    for region,dx in zip(REGIONS,(8,-9)):
        l,t,r,b=region
        box=(int(l*512)-25,int(t*384)-25,int(r*512)+25,int(b*384)+25)
        with source.crop(box) as patch:
            moving.paste(patch,(box[0]+dx,box[1]))
    result=SubjectLocalTracker(source,REGIONS).track(moving)
    assert result.matched_count == 0 and '冲突' in result.error
    source.close(); moving.close()


def test_overlap_deduplicates_persistent_point_ids():
    source,moving=images()
    tracker=SubjectLocalTracker(source,((.1,.1,.65,.8),(.4,.2,.9,.9)))
    result=tracker.track(moving)
    points=result.observation.points
    assert len({p[0] for p in points}) == len(points)
    assert len({(p[2],p[3]) for p in points}) == len(points)
    source.close(); moving.close()


def test_large_source_mapping_uses_actual_analysis_scale():
    source,small=images(shift=(7,3))
    with source.resize((2560,1920)) as reference, small.resize((2560,1920)) as moving:
        result=SubjectLocalTracker(reference,REGIONS).track(moving)
    # 每区按自身尺寸取规范比例（几何平均边长≈128 分析像素），并恢复到源像素。
    from birdstamp.image_dejitter.analysis_window import region_scale
    assert result.observation.region_scales == pytest.approx(tuple(region_scale((2560,1920),r) for r in REGIONS))
    assert min(result.observation.region_scales) > 2
    assert result.observation.displacement == pytest.approx((35,15),abs=1)
    source.close(); small.close()


def test_cancel_is_not_cached_or_reported_as_success():
    source,moving=images()
    with pytest.raises(InterruptedError):
        SubjectLocalTracker(source,REGIONS).track(moving,cancelled=lambda:True)
    source.close(); moving.close()


def test_sequence_crop_sign_and_real_output(tmp_path):
    seeds=seeds_for(tmp_path)
    sequence=prepare_sequence_preview(seeds,cancel_event=threading.Event())
    assert not sequence.alignments  # subject 模式忽略旧工作区的 rigid 设置。
    assert sequence.output_size == (496,384)
    frames=[]
    for i,seed in enumerate(seeds):
        box=sequence.pixel_boxes[path_key(seed.path)]
        assert box[0] == 8*i
        context=render_sequence_preview_frame(sequence,seed.path)
        frames.append(np.array(context.image)); context.image.close()
    for frame in frames[1:]:
        np.testing.assert_array_equal(frame,frames[0])


def test_strength_reuses_observations_and_changes_only_plan(tmp_path,monkeypatch):
    seeds=seeds_for(tmp_path)
    first=prepare_sequence_preview(seeds,cancel_event=threading.Event())
    monkeypatch.setattr(SubjectLocalTracker,'track',lambda *a,**kw:pytest.fail('重新运行跟踪'))
    revised=[replace(s,settings={**s.settings,'dejitter_reference_strength':50}) for s in seeds]
    second=prepare_sequence_preview(revised,cancel_event=threading.Event())
    assert first.input_key != second.input_key
    assert second.output_size == (504,384)
    assert first.tracking == second.tracking


def test_failure_keeps_prefix_and_diagnostics(tmp_path):
    seeds=seeds_for(tmp_path)
    with Image.new('RGB',(512,384),'white') as blank:
        blank.save(seeds[1].path)
    partial=prepare_sequence_preview(seeds,cancel_event=threading.Event(),allow_partial=True)
    assert partial.partial and len(partial.jobs)==1
    assert partial.tracking[path_key(seeds[1].path)].observation.status == 'needs_keyframe'
    with pytest.raises(SequencePhotoError):
        prepare_sequence_preview(seeds,cancel_event=threading.Event())


def test_full_manual_correction_becomes_new_keyframe(tmp_path):
    from birdstamp.image_dejitter.manual_region_matches import manual_match_record, MANUAL_MATCHES_KEY
    seeds=seeds_for(tmp_path,(0,8,125,133))
    boxes=tuple((l+125/512,t,r+125/512,b) for l,t,r,b in ((.15,.2,.38,.65),(.45,.25,.65,.7)))
    # 保持选区在画面内，并确保最后一帧超出旧参考的搜索范围。
    regions=((.15,.2,.38,.65),(.45,.25,.65,.7))
    seeds=[replace(s,settings={**s.settings,'dejitter_reference_regions':regions}) for s in seeds]
    record=manual_match_record(seeds[2].path,seeds[0].path,regions,boxes)
    seeds[2]=replace(seeds[2],settings={**seeds[2].settings,MANUAL_MATCHES_KEY:record})
    sequence=prepare_sequence_preview(seeds,cancel_event=threading.Event())
    assert sequence.output_size == (379,384)
    assert sequence.tracking[path_key(seeds[2].path)].observation.status == 'user_override'
    assert sequence.tracking[path_key(seeds[3].path)].observation.displacement == pytest.approx((133,0),abs=.3)
    observation=sequence.tracking[path_key(seeds[3].path)].observation
    offsets=[(p[4]-p[2],p[5]-p[3]) for p in observation.points if p[6]]
    assert np.median(offsets,axis=0)==pytest.approx((133,0),abs=.3)


def test_follow_preserves_constant_speed_and_two_frames_lock(tmp_path):
    seeds=seeds_for(tmp_path,(0,4,8,12,16),mode='follow')
    seq=prepare_sequence_preview(seeds,cancel_event=threading.Event())
    assert seq.output_size == (512,384)
    two=prepare_sequence_preview(seeds[:2],cancel_event=threading.Event())
    assert two.output_size == (508,384)


def test_settings_and_signatures_old_unknown_and_encoding(tmp_path):
    assert SubjectSettings.from_settings({}).method=='reference_region'
    assert SubjectSettings.from_settings({'dejitter_subject_version':100,METHOD_KEY:'subject_local'}).method=='reference_region'
    seeds=seeds_for(tmp_path)
    a=sequence_input_key(seeds)
    assert a != sequence_input_key([replace(s,settings={**s.settings,METHOD_KEY:'reference_region'}) for s in seeds])
    assert a == sequence_input_key([replace(s,settings={**s.settings,'quality':12}) for s in seeds])


def test_smoothing_reduces_alternating_jitter_preserves_trend():
    from birdstamp.export_stage.subject_sequence import smooth_path
    times=np.arange(15,dtype=float)
    path=np.column_stack((3*times+np.array([0,2,-2]*5),times))
    target=smooth_path(path,times,7)
    assert np.std(target[:,0]-3*times) < np.std(path[:,0]-3*times)/2
    np.testing.assert_allclose(target[:,1],times,atol=1e-9)


def test_capture_gap_refuses_unrelated_shoots(tmp_path):
    seeds=seeds_for(tmp_path,(0,4))
    seeds=[replace(s,raw_metadata={'EXIF:DateTimeOriginal':f'2026:09:30 12:{minute}:00'})
           for s,minute in zip(seeds,('00','05'))]
    with pytest.raises(SequencePhotoError,match='间隔'):
        prepare_sequence_preview(seeds,cancel_event=threading.Event())


def test_manual_conflict_cannot_reuse_old_successful_observation():
    source,moving=images()
    tracker=SubjectLocalTracker(source,REGIONS)
    result=tracker.track(moving)
    corrected=tracker.resolve_manual(result,(REGIONS[0],None),source.size)
    assert corrected.matched_count == 0
    assert corrected.observation is None or corrected.observation.displacement is None
    source.close(); moving.close()


def test_cli_actual_png_report_npz_and_inputs_unchanged(tmp_path):
    import json
    from birdstamp.subject_stabilization_cli import stabilize_files
    seeds=seeds_for(tmp_path,(0,8))
    regions=tmp_path/'选区.json'
    regions.write_text(json.dumps({'regions':REGIONS}),encoding='utf-8')
    original=[s.path.read_bytes() for s in seeds]
    folder=stabilize_files([s.path for s in seeds],seeds[0].path,regions,tmp_path/'out',debug=True)
    report=(folder/'report.json').read_text(encoding='utf-8')
    assert str(tmp_path) not in report
    report=json.loads(report)
    assert report['status']=='complete'
    assert report['frames'][1]['plan']['applied']==[8,0]
    assert (folder/'tracks.npz').is_file()
    with Image.open(folder/'0001_参考.png') as first,Image.open(folder/'0002_帧1.png') as second:
        np.testing.assert_array_equal(np.array(first),np.array(second))
    assert original==[s.path.read_bytes() for s in seeds]


def test_debug_coordinates_account_for_crop_and_failure():
    from birdstamp.gui.editor_tracking_overlay import subject_debug_points
    source,moving=images()
    result=SubjectLocalTracker(source,REGIONS).track(moving)
    p=result.observation.points[0]
    mapped=subject_debug_points(result,(.1,.2,.9,.8))[0]
    assert mapped[:4]==pytest.approx(((p[2]/512-.1)/.8,(p[3]/384-.2)/.6,
                                       (p[4]/512-.1)/.8,(p[5]/384-.2)/.6))
    refused=replace(result,observation=replace(result.observation,status='needs_keyframe'))
    assert not any(p[-1] for p in subject_debug_points(refused))
    source.close(); moving.close()
