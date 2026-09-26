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
