"""Rigid estimation, native sampling and complete-pixel common canvas contracts."""
from dataclasses import replace
from math import cos, sin, radians
from threading import Event

import numpy as np
import pytest
from PIL import Image, ImageFilter, ImageEnhance

from birdstamp.image_dejitter.rigid_alignment import (
    IDENTITY, FrameAlignment, estimate_alignment, map_point, render_alignment, rectangle_points,
)
from birdstamp.image_dejitter.alignment_bounds import intersect_convex, largest_pixel_rectangle, outward_bounds
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
from birdstamp.image_dejitter.matching_options import MatchingOptions
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, render_sequence_preview_frame, sequence_input_key
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.sequence_export import export_aligned_sequence
from birdstamp.gui.editor_utils import path_key

SIZE = (1200, 800)
REGIONS = ((.1,.1,.18,.22), (.7,.1,.78,.22), (.1,.7,.18,.82), (.7,.7,.78,.82))


def evidence(angle=1.2, shift=(24, -15), count=4):
    c, s = cos(radians(angle)), sin(radians(angle))
    forward = (c, -s, shift[0], s, c, shift[1])
    boxes = []
    for l,t,r,b in REGIONS:
        x,y = (l+r)*SIZE[0]/2, (t+b)*SIZE[1]/2
        qx,qy = map_point(forward,(x,y))
        boxes.append((l+(qx-x)/SIZE[0],t+(qy-y)/SIZE[1],r+(qx-x)/SIZE[0],b+(qy-y)/SIZE[1]))
    return RegionTrackingResult(tuple(boxes[:count]+[None]*(4-count)),scores=(.99,)*4), forward


@pytest.mark.parametrize('angle', [-1.2, 1.2])
@pytest.mark.parametrize('strength', [0, 50, 100])
def test_rigid_strength_centroid_inverse_and_reference(angle, strength):
    result, forward = evidence(angle)
    alignment = estimate_alignment(REGIONS,result,SIZE,SIZE,mode='rigid',strength=strength)
    assert alignment.status == 'rigid'
    assert alignment.measured_degrees == pytest.approx(angle)
    assert alignment.applied_degrees == pytest.approx(-angle*strength/100)
    center = tuple(sum((r[i]+r[i+2])*SIZE[i]/2 for r in REGIONS)/4 for i in (0,1))
    mapped = map_point(forward,center)
    assert map_point(alignment.source_to_reference,mapped) == pytest.approx(
        tuple(q+(p-q)*strength/100 for p,q in zip(center,mapped)))
    p=(350,265)
    assert map_point(alignment.reference_to_source,map_point(alignment.source_to_reference,p)) == pytest.approx(p)
    if strength == 100:
        assert map_point(alignment.source_to_reference,map_point(forward,p)) == pytest.approx(p)
    if strength == 0:
        assert alignment.source_to_reference == IDENTITY
    assert estimate_alignment(REGIONS,result,SIZE,SIZE,mode='rigid',is_reference=True).source_to_reference == IDENTITY


@pytest.mark.parametrize('count', [1,2])
def test_insufficient_regions_fall_back_without_inventing_angle(count):
    result,_ = evidence(0,count=count)
    a = estimate_alignment(REGIONS,result,SIZE,SIZE,mode='rigid')
    assert a.status == 'fallback' and a.measured_degrees is None and a.applied_degrees == 0
    assert a.source_to_reference == (1,0,-24,0,1,15)
    assert '未纠正旋转' in a.description()


def test_outlier_exclusion_angle_limit_and_no_translation_failure():
    result,_ = evidence()
    result = replace(result,boxes=(*result.boxes[:3],(.3,.3,.4,.4)))
    a = estimate_alignment(REGIONS,result,SIZE,SIZE,mode='rigid')
    assert a.region_indices == (0,1,2) and a.measured_degrees == pytest.approx(1.2)
    # A permissive translation tolerance does not permit exceeding the angle cap.
    result,_ = evidence(3)
    a = estimate_alignment(REGIONS,result,SIZE,SIZE,mode='rigid',options=MatchingOptions(2,5))
    assert a.status == 'fallback' and not a.rotated
    with pytest.raises(ValueError):
        estimate_alignment(REGIONS,RegionTrackingResult((None,)*4),SIZE,SIZE,mode='rigid')


def test_clustered_evidence_does_not_estimate_rotation():
    regions=((.1,.1,.11,.11),(.12,.1,.13,.11),(.1,.12,.11,.13))
    result=RegionTrackingResult(regions,scores=(.99,)*3)
    a=estimate_alignment(regions,result,SIZE,SIZE,mode='rigid')
    assert a.status == 'fallback' and a.measured_degrees is None
    assert '集中' in a.reason


def test_common_canvas_failure_is_at_first_frame_without_complete_pixel():
    from birdstamp.export_stage.sequence_preview import prepare_rigid_geometry
    from birdstamp.export_stage.sequence_photo_error import SequencePhotoError
    region=((.1,.1,.2,.2),)
    tracking={'ref':RegionTrackingResult(region),
              'bad':RegionTrackingResult(((1.1,.1,1.2,.2),)),
              'later':RegionTrackingResult(region)}
    with pytest.raises(SequencePhotoError) as error:
        prepare_rigid_geometry(region,tracking,{k:(100,100) for k in tracking},(100,100),
                               'ref',{},cancelled=lambda:False)
    assert str(error.value.source_path) == 'bad'


def test_zero_strength_preserves_native_pixels():
    result,_=evidence()
    a=estimate_alignment(REGIONS,result,SIZE,SIZE,mode='rigid',strength=0)
    values=np.random.default_rng(4).integers(0,255,(800,1200,3),dtype=np.uint8)
    with Image.fromarray(values) as source,render_alignment(source,SIZE,a,(0,0,*SIZE)) as output:
        np.testing.assert_array_equal(values,output)


def inside(polygon, point):
    x,y = point
    return all((b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0]) >= -1e-7
               for a,b in zip(polygon,(*polygon[1:],polygon[0])))


@pytest.mark.parametrize('angle', [0, 1.5, -1.5, 24, -32])
def test_largest_rectangle_matches_exhaustive_complete_pixel_search(angle):
    c,s = cos(radians(angle)),sin(radians(angle))
    polygon = intersect_convex(rectangle_points((0,0,9,8)),
        tuple(map_point((c,-s,1,s,c,-1),p) for p in rectangle_points((0,0,9,8))))
    candidates = [(l,t,r,b) for l in range(9) for r in range(l+1,10)
                  for t in range(8) for b in range(t+1,9)
                  if all(inside(polygon,p) for p in rectangle_points((l,t,r,b)))]
    expected = min(candidates,key=lambda q:(-(q[2]-q[0])*(q[3]-q[1]),q[1],q[0],q[3],q[2]))
    assert largest_pixel_rectangle(polygon) == expected
    with pytest.raises(InterruptedError):
        largest_pixel_rectangle(polygon,cancelled=lambda:True)


def test_union_contains_source_and_intersection_has_no_sampling_black_edges():
    result,_ = evidence()
    a=estimate_alignment(REGIONS,result,SIZE,SIZE,mode='rigid')
    reference=FrameAlignment(status='reference')
    union=outward_bounds([a.footprint(SIZE),reference.footprint(SIZE)])
    assert all(union[0] <= x <= union[2] and union[1] <= y <= union[3]
               for x,y in a.footprint(SIZE)+reference.footprint(SIZE))
    intersection=intersect_convex(a.footprint(SIZE,safe=True),reference.footprint(SIZE))
    canvas=largest_pixel_rectangle(intersection)
    with Image.new('RGB',SIZE,(87,132,204)) as source, render_alignment(source,SIZE,a,canvas) as output:
        assert np.min(np.asarray(output)[:,:,0]) >= 86
        assert output.size == (canvas[2]-canvas[0],canvas[3]-canvas[1])
    with Image.fromarray(np.random.default_rng(3).integers(0,255,(80,120,3),dtype=np.uint8)) as source:
        translation=FrameAlignment((1,0,-5,0,1,3))
        with render_alignment(source,source.size,translation,(0,0,115,80)) as output, source.crop((5,-3,120,77)) as expected:
            np.testing.assert_array_equal(output,expected)


@pytest.fixture
def rotated_seeds(tmp_path):
    paths=[tmp_path/'ref.png',tmp_path/'rotated.png']
    values=np.random.default_rng(912).integers(30,220,(800,1200),dtype=np.uint8)
    with Image.fromarray(values).convert('RGB') as noise, noise.filter(ImageFilter.GaussianBlur(2)) as ref:
        with ref.rotate(1.2,resample=Image.Resampling.BICUBIC,translate=(50,30)) as rotated:
            with ImageEnhance.Brightness(rotated).enhance(.72) as target:
                ref.save(paths[0]); target.save(paths[1])
    settings=dict(dejitter_reference_regions=REGIONS,dejitter_reference_source=str(paths[0]),
                  dejitter_alignment_mode='rigid',dejitter_reference_strength=100)
    return [RenderJobSeed(p,dict(settings),{},True) for p in paths]


def test_quick_and_native_sampling_use_same_coordinates():
    result,_=evidence()
    a=estimate_alignment(REGIONS,result,SIZE,SIZE,mode='rigid')
    x,y=np.meshgrid(np.linspace(20,230,SIZE[0]),np.linspace(20,230,SIZE[1]))
    values=np.stack((x,y,(x+y)/2),axis=-1).astype(np.uint8)
    canvas=largest_pixel_rectangle(intersect_convex(a.footprint(SIZE,safe=True),FrameAlignment().footprint(SIZE)))
    with Image.fromarray(values) as source, source.resize((600,400),Image.Resampling.BILINEAR) as small:
        with render_alignment(source,SIZE,a,canvas) as native, render_alignment(small,SIZE,a,canvas,max_edge=300) as quick:
            with native.resize(quick.size,Image.Resampling.BILINEAR) as expected:
                assert np.max(abs(np.asarray(quick,dtype=int)-np.asarray(expected,dtype=int))) <= 2


def test_real_matching_brightness_rotation_preview_and_export(rotated_seeds,tmp_path):
    seq=prepare_sequence_preview(rotated_seeds,cancel_event=Event())
    target=rotated_seeds[1].path
    alignment=seq.alignments[path_key(target)]
    assert alignment.measured_degrees == pytest.approx(-1.2,abs=.06)
    assert alignment.applied_degrees == pytest.approx(1.2,abs=.06)
    assert path_key(target) not in seq.pixel_boxes
    exported=export_aligned_sequence(seq,tmp_path,cancel_event=Event())
    for seed,output in zip(rotated_seeds,sorted(exported.glob('*.png'))):
        with render_sequence_preview_frame(seq,seed.path).image as preview, Image.open(output) as actual:
            np.testing.assert_array_equal(preview,actual)
    with render_sequence_preview_frame(seq,rotated_seeds[0].path).image as ref, render_sequence_preview_frame(seq,target).image as target_image:
        # Brightness normalized image content should align despite interpolation.
        x=np.asarray(ref,dtype=float)[80:-80,80:-80]
        y=np.asarray(target_image,dtype=float)[80:-80,80:-80]/.72
        assert np.mean(abs(x-y)) < 3
    legacy=[replace(s,settings={k:v for k,v in s.settings.items() if k!='dejitter_alignment_mode'}) for s in rotated_seeds]
    assert sequence_input_key(legacy) != seq.input_key


def test_rotated_cache_roundtrip_and_corrupt_transform_rejected(rotated_seeds,tmp_path,monkeypatch):
    import json
    from test_sequence_preview_cache import run_worker,fail_recompute
    from birdstamp.gui.sequence_preview_cache import SequencePreviewCache
    from birdstamp.gui import editor_sequence_preview_worker as workers
    cache=SequencePreviewCache(tmp_path/'cache')
    first,quick,errors=run_worker(rotated_seeds,cache)
    assert first and quick and not errors
    monkeypatch.setattr(workers,'prepare_sequence_preview',fail_recompute)
    monkeypatch.setattr(workers,'render_sequence_preview_frame',fail_recompute)
    second,restored,errors=run_worker(rotated_seeds,cache,restore_only=True)
    assert second and restored and not errors
    seq=second[0][0]
    assert seq.alignments == first[0][0].alignments
    assert seq.canvas_box == first[0][0].canvas_box
    key=path_key(rotated_seeds[1].path)
    frame=restored[0][1][key]
    assert frame.alignment == seq.alignments[key] and frame.canvas_box == seq.canvas_box
    assert frame.image == quick[0][1][key].image
    manifest=cache.root/seq.input_key/'manifest.json'
    raw=json.loads(manifest.read_text(encoding='utf-8'))
    raw['frames'][1]['alignment']['source_to_reference'][0]=2
    manifest.write_text(json.dumps(raw),encoding='utf-8')
    assert cache.load(rotated_seeds) is None
