"""自动选区：源坐标、语义门控、组合核验、模型安装与旧设置兼容。"""
from dataclasses import replace
from pathlib import Path
import hashlib
import threading
import numpy as np
import pytest
from PIL import Image, ImageFilter
from birdstamp.image_dejitter.region_recommendation import (sample_paths, recommend_regions,
    normalize_recommendation, background_candidates)
from birdstamp.image_dejitter.bird_candidates import BirdCandidate, associate_target
from birdstamp.image_dejitter.local_crop_tracker import LocalCropSubjectTracker
from birdstamp.image_dejitter.bird_parts.pose import decode_heatmaps, candidates_from_pose
from birdstamp.image_dejitter.recognition import RECOMMENDATION_KEY


def test_sample_order_bounded_includes_first_neighbors_and_end():
    paths=[Path(str(i)) for i in range(41)]
    assert sample_paths(paths,paths[0]) == tuple(paths[i] for i in (0,1,2,10,20,30,40))
    assert sample_paths(paths[:2],paths[0]) == tuple(paths[:2])
    assert len(sample_paths(paths,paths[20])) <= 7


def test_identity_does_not_select_largest_or_switch_on_overlap():
    reference=(.4,.3,.5,.5)
    a=BirdCandidate((.41,.31,.51,.51),.7)
    distractor=BirdCandidate((.7,.1,.99,.9),.99)
    assert associate_target(reference,(distractor,a)) == a.box
    with pytest.raises(ValueError,match='歧义'):
        associate_target(reference,(a,BirdCandidate((.39,.3,.49,.5),.8)))
    with pytest.raises(ValueError):associate_target(reference,(distractor,))


@pytest.mark.parametrize('shift',[(31,-17),(-20,27)])
def test_local_crop_origins_restore_real_source_displacements(shift):
    rng=np.random.default_rng(91)
    with Image.fromarray(rng.integers(0,255,(900,1300,3),dtype=np.uint8)).filter(ImageFilter.GaussianBlur(.7)) as source:
        moving=Image.new('RGB',source.size);moving.paste(source,shift)
        target=(.4,.3,.6,.65);regions=((.43,.36,.52,.55),)
        moved=tuple(v+(shift[0]/1300 if i%2==0 else shift[1]/900) for i,v in enumerate(target))
        tracker=LocalCropSubjectTracker(source,regions,target=target,detector=lambda *a,**k:(BirdCandidate(moved,.9),))
        result=tracker.track(moving);moving.close()
        assert result.observation.displacement == pytest.approx(shift,abs=.3),result.error
        assert result.observation.reference_origin != result.observation.moving_origin
        assert result.observation.source_size==(1300,900)
        for p in result.observation.points:
            if p[6]:assert (p[4]-p[2],p[5]-p[3]) == pytest.approx(shift,abs=.5)


def test_detector_box_jitter_cannot_become_compensation():
    from test_subject_local import images
    source,_=images()
    # 同一原图，检测框被扰动；图像匹配应得到零位移。
    tracker=LocalCropSubjectTracker(source,((.3,.3,.5,.6),),target=(.2,.2,.6,.7),
        detector=lambda *a,**k:(BirdCandidate((.22,.21,.62,.71),.9),))
    result=tracker.track(source);source.close()
    assert result.observation.displacement == pytest.approx((0,0),abs=.3),result.error


def test_unvalidated_model_is_not_offered_as_default(tmp_path):
    result=recommend_regions(tmp_path/'missing.jpg',[],method='subject_local')
    assert result.status=='unvalidated' and not result.regions


def test_failed_part_candidates_are_returned_separately_from_export_regions(tmp_path):
    image=Image.new('RGB',(400,300),(90,140,180))
    paths=[tmp_path/'one.png',tmp_path/'two.png']
    for path in paths:image.save(path)
    pose=dict(points=[[.4+(i%3)*.04,.3+(i//3)*.025] for i in range(23)],
              scores=[.9]*23,reliable=[i<7 for i in range(23)])
    stages=[]
    result=recommend_regions(paths[0],paths,method='subject_local',experimental=True,
        detector=lambda *a,**kw:(BirdCandidate((.2,.1,.8,.9),.8),),pose_predictor=lambda *a,**kw:pose,
        candidate_callback=lambda r:stages.append(('candidates',r)),progress=lambda s:stages.append(('progress',s)))
    assert result.status=='no_reliable_region' and not result.regions
    assert len(result.candidates)==1 and result.candidates[0].part=='head'
    assert result.candidates[0].status=='failed'
    assert result.candidates[0].total_frames==2
    early=next(i for i,v in enumerate(stages) if v[0]=='candidates')
    trajectory=next(i for i,v in enumerate(stages) if v[0]=='progress' and v[1].startswith('关联目标鸟'))
    assert early<trajectory
    assert not stages[early][1].regions


def test_multi_bird_returns_candidates_before_model_load(tmp_path):
    path=tmp_path/'ref.png';Image.new('RGB',(400,300)).save(path)
    birds=(BirdCandidate((.1,.1,.3,.6),.8),BirdCandidate((.6,.2,.9,.8),.7))
    result=recommend_regions(path,[path],method='subject_local',experimental=True,
        detector=lambda *a,**k:birds,pose_predictor=lambda *a,**k:pytest.fail('多鸟未确认不得运行模型'))
    assert result.status=='choose_target' and result.birds==birds


def test_invisible_parts_are_not_synthesized_from_box():
    pose=dict(points=[[.5,.5]]*23,scores=[.9]*23,reliable=[False]*23)
    assert not candidates_from_pose(pose,(.2,.2,.8,.8))


def test_heatmap_decode_preserves_xy_order():
    h=np.zeros((2,64,64));h[0,20,10]=1;h[1,30,40]=.5
    xy,scores=decode_heatmaps(h)
    np.testing.assert_allclose(xy,[(10/64,20/64),(40/64,30/64)])
    np.testing.assert_allclose(scores,[1,.5])


def test_background_rejects_parallel_wires_but_keeps_blurred_shapes():
    from PIL import ImageDraw
    image=Image.new('RGB',(900,600),(130,140,150));draw=ImageDraw.Draw(image)
    for y in (260,280,310):draw.line((0,y,900,y+40),fill=(10,10,10),width=6)
    candidates=background_candidates(image)
    assert not candidates
    draw.ellipse((50,50,170,170),fill=(20,45,10))
    with image.filter(ImageFilter.GaussianBlur(6)) as blurred:
        assert background_candidates(blurred)
    image.close()


def test_background_recommendation_runs_real_matcher_and_preserves_manual(tmp_path):
    from test_subject_local import images
    reference,moving=images(shift=(3,2))
    paths=[tmp_path/'a.png',tmp_path/'b.png'];reference.save(paths[0]);moving.save(paths[1])
    manual=((.4,.4,.6,.6),)
    result=recommend_regions(paths[0],paths,existing=manual,target_count=3,detector=lambda *a,**k:())
    assert result.status=='ready',result.message
    assert result.regions and len(result.regions)<=2
    assert all(row['passed'] for row in result.diagnostics if tuple(map(tuple,row['regions']))==result.regions[:1])
    assert result.metadata['auto_regions']==list(result.regions)
    reference.close();moving.close()


def test_normalization_and_observation_cache_signature(tmp_path):
    from test_subject_local import seeds_for
    from birdstamp.export_stage.sequence_preview import sequence_input_key
    from birdstamp.export_stage.core import _clone_render_settings
    seeds=seeds_for(tmp_path)
    meta=dict(version=1,target=(.1,.2,.8,.9),local_analysis=True,auto_regions=[(.2,.3,.4,.5)],part='head')
    changed=[replace(s,settings={**s.settings,RECOMMENDATION_KEY:meta}) for s in seeds]
    assert sequence_input_key(seeds)!=sequence_input_key(changed)
    assert normalize_recommendation(None)=={}
    assert _clone_render_settings(changed[0].settings)[RECOMMENDATION_KEY]['target']==meta['target']


def test_model_install_is_atomic_and_cancel_safe(tmp_path,monkeypatch):
    from birdstamp.image_dejitter.bird_parts import model_store as store
    content=b'official fixture';source=tmp_path/'source';source.write_bytes(content)
    monkeypatch.setattr(store,'MODEL_BYTES',len(content))
    monkeypatch.setattr(store,'MODEL_SHA256',hashlib.sha256(content).hexdigest())
    target=tmp_path/'models'/'model.pth'
    assert store.install_model(source,destination=target)==target
    assert target.read_bytes()==content
    source.write_bytes(b'not a valid model')
    with pytest.raises(ValueError):store.install_model(source,destination=target)
    assert target.read_bytes()==content
    with pytest.raises(InterruptedError):store.install_model(source,destination=target,cancelled=lambda:True)
    assert not list(target.parent.glob('*.part'))


def test_cancellation_propagates_without_recommendation(tmp_path):
    with pytest.raises(InterruptedError):
        recommend_regions(tmp_path/'missing',[],cancelled=lambda:True)


def test_local_analysis_real_export_pixels_and_cached_mapping(tmp_path,monkeypatch):
    from test_subject_local import seeds_for
    from birdstamp.image_dejitter import local_crop_tracker as module
    from birdstamp.export_stage.sequence_preview import prepare_sequence_preview,render_sequence_preview_frame
    from birdstamp.gui.editor_utils import path_key
    from birdstamp.gui.sequence_preview_cache import SequencePreviewCache
    from test_sequence_preview_cache import run_worker
    target=(.1,.1,.9,.9)
    monkeypatch.setattr(module,'detect_bird_candidates',lambda *a,**k:(BirdCandidate(target,.9),))
    from birdstamp.image_dejitter import bird_observation_cache
    monkeypatch.setattr(bird_observation_cache,'detect_cached',lambda *a,**k:(BirdCandidate(target,.9),))
    seeds=seeds_for(tmp_path,(0,8,16))
    meta=dict(version=1,target=target,local_analysis=True,part='torso',experimental=True)
    seeds=[replace(s,settings={**s.settings,RECOMMENDATION_KEY:meta}) for s in seeds]
    sequence=prepare_sequence_preview(seeds,cancel_event=threading.Event())
    frames=[]
    for s in seeds:
        context=render_sequence_preview_frame(sequence,s.path)
        frames.append(np.array(context.image));context.image.close()
    for frame in frames[1:]:np.testing.assert_array_equal(frame,frames[0])
    assert sequence.tracking[path_key(seeds[1].path)].observation.reference_origin != (0,0)
    cache=SequencePreviewCache(tmp_path/'cache')
    first,quick,errors=run_worker(seeds,cache)
    assert first and not errors
    restored,_,errors=run_worker(seeds,cache,restore_only=True)
    assert restored and not errors
    assert first[0][0].tracking==restored[0][0].tracking


def test_model_bad_hash_rejected_before_torch_load(tmp_path,monkeypatch):
    from birdstamp.image_dejitter.bird_parts import pose
    import torch
    path=tmp_path/'fake.pth';path.write_bytes(b'not weights')
    monkeypatch.setattr(torch,'load',lambda *a,**k:pytest.fail('未校验权重不能反序列化'))
    with pytest.raises(ValueError):pose._load_model(path,(11,1))


def test_cli_recommendation_report_can_feed_stabilization(tmp_path,monkeypatch):
    from birdstamp import region_recommendation_cli as cli
    from birdstamp.image_dejitter.region_recommendation import Recommendation
    from birdstamp.subject_stabilization_cli import stabilize_files
    from test_subject_local import seeds_for,REGIONS
    import json
    seeds=seeds_for(tmp_path,(0,8))
    monkeypatch.setattr(cli,'recommend_regions',lambda *a,**k:Recommendation('ready','ok',REGIONS,'background'))
    out=tmp_path/'roi.json'
    cli.recommend_files([s.path for s in seeds],seeds[0].path,out)
    assert json.loads(out.read_text())['regions']
    folder=stabilize_files([s.path for s in seeds],seeds[0].path,out,tmp_path/'export',method='subject_local')
    assert len(list(folder.glob('*.png')))==2
    with pytest.raises(FileExistsError):cli.recommend_files([s.path for s in seeds],seeds[0].path,out)


def test_prediction_cache_tracks_files_parameters_and_cancellation(tmp_path,monkeypatch):
    from birdstamp.image_dejitter import bird_observation_cache as cache
    path=tmp_path/'image';path.write_bytes(b'one')
    count=[]
    def compute():count.append(1);return {'points':[[1,2]]}
    first=cache.cached_observation(path,'test',('model1',),compute,lambda:False)
    first['points'][0][0]=99
    assert cache.cached_observation(path,'test',('model1',),compute,lambda:False)['points'][0][0]==1
    assert len(count)==1
    path.write_bytes(b'different-size')
    cache.cached_observation(path,'test',('model1',),compute,lambda:False)
    cache.cached_observation(path,'test',('model2',),compute,lambda:False)
    assert len(count)==3
    with pytest.raises(InterruptedError):cache.cached_observation(path,'test',(),compute,lambda:True)
    monkeypatch.setattr(cache,'MAX_ENTRIES',2)
    for i in range(4):cache.cached_observation(path,'test',(i,),compute,lambda:False)
    assert len(cache._cache)<=2 and sum(cache._sizes.values())<=cache.MAX_BYTES
