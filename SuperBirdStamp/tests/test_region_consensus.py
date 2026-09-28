"""离群参考区不能否决少数可靠匹配，预测位置不能冒充跟踪结果。"""
import numpy as np
from PIL import Image
import pytest

from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
from birdstamp.image_dejitter.region_consensus import resolve_tracking_consensus, select_translation
from birdstamp.image_dejitter.reference_region_tracker import ReferenceRegionTracker
from birdstamp.export_stage.sequence_preview import common_alignment_crop
from birdstamp.gui.editor_tracking_overlay import tracking_overlays

REGIONS = ((.05, .1, .15, .2), (.2, .1, .3, .2), (.4, .1, .5, .2),
           (.6, .1, .7, .2), (.8, .1, .9, .2))


def shifted(box, dx, dy=0):
    return (box[0]+dx, box[1]+dy, box[2]+dx, box[3]+dy)


def test_minority_consensus_excludes_distractors_and_predicts_out_of_frame():
    offsets = (.2, .201, -.2, -.4, -.6)
    raw = RegionTrackingResult(tuple(shifted(r, d) for r, d in zip(REGIONS, offsets)),
                               scores=(.95, .8, .9, .8, .85))
    result = resolve_tracking_consensus(REGIONS, raw, (1000, 600), (1000, 600))
    assert result.matched_count == 2
    assert result.boxes[2:] == (None, None, None)
    assert '越界' in result.reasons[4]
    assert result.predicted_boxes[4][2] > 1
    assert select_translation(REGIONS, result, (1000, 600), (1000, 600))[:2] == pytest.approx((200.5, 0))


def test_single_reliable_region_is_sufficient_for_crop():
    result = RegionTrackingResult((None, shifted(REGIONS[1], .2), None, None, None), scores=(0,.95,0,0,0))
    resolved = resolve_tracking_consensus(REGIONS, result, (1000, 600), (1000, 600))
    boxes, size = common_alignment_crop(REGIONS, {'ref': RegionTrackingResult(REGIONS), 'target': resolved},
                                       {'ref': (1000,600), 'target': (1000,600)}, (1000,600))
    assert size == (800,600)
    assert boxes['target'] == (200,0,1000,600)
    assert resolved.matched_count == 1


def test_equal_conflicting_evidence_stays_unlocated():
    raw = RegionTrackingResult((shifted(REGIONS[0], .2), shifted(REGIONS[1], .2),
                                shifted(REGIONS[2], -.2), shifted(REGIONS[3], -.2), None),
                               scores=(.92,.93,.94,.92,0))
    result = resolve_tracking_consensus(REGIONS, raw, (1000,600), (1000,600))
    assert result.matched_count == 0
    assert all('未定位' in label for _, label, _ in tracking_overlays(REGIONS, result))


def rotated_matches(regions, angle, shift=(80, 40), size=(1200, 800)):
    rotation = np.exp(1j*np.deg2rad(angle))
    center = complex(size[0]/2, size[1]/2)
    boxes = []
    for region in regions:
        point = complex((region[0]+region[2])*size[0]/2, (region[1]+region[3])*size[1]/2)
        moved = center + rotation*(point-center) + complex(*shift)
        delta = moved-point
        boxes.append(shifted(region, delta.real/size[0], delta.imag/size[1]))
    return tuple(boxes)


@pytest.mark.parametrize('angle', [-1.2, 1.2])
def test_small_camera_rotation_preserves_measured_regions_and_translation_crop(angle):
    regions = ((.1,.1,.2,.2), (.7,.1,.8,.2), (.1,.7,.2,.8), (.7,.7,.8,.8))
    boxes = rotated_matches(regions, angle)
    raw = RegionTrackingResult(boxes, scores=(.97,)*4)
    result = resolve_tracking_consensus(regions, raw, (1200,800), (1200,800))
    assert result.boxes == boxes  # 保留实测位置，不把旋转预测框冒充匹配。
    dx, dy, accepted = select_translation(regions, result, (1200,800), (1200,800))
    assert accepted == (0,1,2,3)
    offsets = np.array([((b[0]-r[0])*1200, (b[1]-r[1])*800) for r,b in zip(regions,boxes)])
    np.testing.assert_allclose((dx,dy), np.median(offsets,axis=0))
    crops, size = common_alignment_crop(regions, {'ref': RegionTrackingResult(regions), 'target': result},
                                        {'ref': (1200,800), 'target': (1200,800)}, (1200,800))
    assert size == (1200-round(dx),800-round(dy))
    assert crops['target'] == (round(dx),round(dy),1200,800)


def test_rotation_requires_three_regions_and_rejects_large_angle_or_deformation():
    regions = ((.1,.1,.2,.2), (.7,.1,.8,.2), (.1,.7,.2,.8), (.7,.7,.8,.8))
    for count, angle in ((2,1.2),(4,4)):
        selected = regions[:count]
        raw = RegionTrackingResult(rotated_matches(selected,angle), scores=(.97,)*count)
        assert resolve_tracking_consensus(selected,raw,(1200,800),(1200,800)).matched_count == 0
    # 高相似度仍不能让任意四个位移成为共同运动。
    raw = RegionTrackingResult(tuple(shifted(r,dx,dy) for r,(dx,dy) in
                                    zip(regions, ((.08,.04),(.05,.06),(.07,.09),(.1,.07)))), scores=(.97,)*4)
    assert resolve_tracking_consensus(regions,raw,(1200,800),(1200,800)).matched_count == 0


def test_equal_independent_rotating_groups_remain_ambiguous():
    first = ((.1,.1,.2,.2), (.4,.1,.5,.2), (.1,.6,.2,.7))
    second = ((.5,.2,.6,.3), (.8,.2,.9,.3), (.7,.6,.8,.7))
    raw = RegionTrackingResult(rotated_matches(first,1.2,(70,30)) + rotated_matches(second,-1.2,(-70,-30)),
                               scores=(.97,)*6)
    assert resolve_tracking_consensus(first+second,raw,(1200,800),(1200,800)).matched_count == 0


def test_diagnostics_use_output_crop_coordinates_and_keep_original_ids():
    result = RegionTrackingResult((None, shifted(REGIONS[1], .2), None, None, None), scores=(0,.95,0,0,0))
    result = resolve_tracking_consensus(REGIONS, result, (1000,600), (1000,600))
    overlays = tracking_overlays(REGIONS, result, (.2,0,1,1))
    assert len(overlays) == 5
    assert overlays[1][0] == pytest.approx((.25,.1,.375,.2))
    assert overlays[1][1:] == ('2', True)
    assert '预计位置' in overlays[0][1]
    assert overlays[4][0][2] > 1


def test_guided_matching_requires_real_texture_and_keeps_outside_red():
    rng = np.random.default_rng(42)
    pixels = rng.integers(0,255,(200,300,3),dtype=np.uint8)
    regions = ((.05,.2,.25,.5),(.4,.2,.6,.5),(.82,.2,.98,.5))
    ref = Image.fromarray(pixels)
    target = Image.new('RGB',ref.size)
    target.paste(ref,(70,0))
    # 第二个选区被遮住；第三区域移出了右边界。
    target.paste((128,128,128),(190,40,251,100))
    result = ReferenceRegionTracker(ref,regions).track(target)
    assert result.boxes[0] is not None
    assert result.boxes[1:] == (None,None)
    assert '越界' in result.reasons[2]
    assert result.matched_count == 1
