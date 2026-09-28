"""手动匹配为明确输入：编号、失效、平移共识及缓存/导出一致性。"""
from dataclasses import replace
import json
import threading

import numpy as np
import pytest
from PIL import Image

from test_dejitter_tab import sequence
from test_sequence_preview_cache import run_worker
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, sequence_input_key, render_sequence_preview_frame
from birdstamp.export_stage.sequence_export import export_aligned_sequence
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui.sequence_preview_cache import SequencePreviewCache
from birdstamp.image_dejitter.manual_region_matches import (
    MANUAL_MATCHES_KEY, manual_match_record, valid_manual_boxes, apply_manual_boxes, without_manual_boxes,
)
from birdstamp.image_dejitter.region_consensus import select_translation
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
from birdstamp.image_dejitter.reference_region_tracker import ReferenceRegionTracker


def corrected_seeds(seeds, boxes):
    target = seeds[1]
    record = manual_match_record(target.path, seeds[0].path, target.settings['dejitter_reference_regions'], boxes)
    return [seeds[0], replace(target, settings={**target.settings, MANUAL_MATCHES_KEY: record})]


def test_manual_position_cannot_be_outvoted_by_automatic_matches():
    regions = ((.1,.1,.2,.2), (.3,.3,.4,.4), (.6,.6,.7,.7))
    auto = RegionTrackingResult(tuple(tuple(v+.05 for v in box) for box in regions), scores=(1,1,1))
    manual = (.2,.2,.3,.3)
    result = apply_manual_boxes(auto, (manual,None,None))
    dx,dy,indices = select_translation(regions,result,(100,100),(100,100))
    assert (dx,dy) == pytest.approx((10,10)) and indices == (0,)
    cleared = without_manual_boxes(result)
    assert cleared.boxes[0] is None and cleared.boxes[1:] == auto.boxes[1:]
    assert not cleared.manual_indices
    conflicting = apply_manual_boxes(auto, (manual,regions[1],None))
    assert select_translation(regions,conflicting,(100,100),(100,100)) is None


def test_manual_anchor_keeps_rotated_automatic_evidence():
    from test_region_consensus import rotated_matches

    regions = ((.1,.1,.2,.2), (.7,.1,.8,.2), (.1,.7,.2,.8), (.7,.7,.8,.8))
    boxes = rotated_matches(regions, 1.2)
    tracked = RegionTrackingResult(boxes, scores=(.97,) * len(boxes))
    corrected = apply_manual_boxes(tracked, (boxes[0], None, None, None))
    translation = select_translation(regions, corrected, (1200,800), (1200,800))
    assert translation is not None and translation[2] == (0,1,2,3)


@pytest.mark.parametrize('change', ['source', 'reference', 'regions', 'bad_box'])
def test_serialized_records_validate_files_and_reference_definition(sequence, change):
    seeds, result = sequence
    regions = seeds[0].settings['dejitter_reference_regions']
    record = manual_match_record(seeds[1].path, seeds[0].path, regions, result.tracking[path_key(seeds[1].path)].boxes)
    record = json.loads(json.dumps(record))
    assert len(valid_manual_boxes(record, seeds[1].path, seeds[0].path, regions)) == 2
    if change in ('source', 'reference'):
        path = seeds[1 if change == 'source' else 0].path
        path.write_bytes(path.read_bytes()+b'changed')
    elif change == 'regions':
        regions = (regions[1], regions[0])
    else:
        record['boxes'] = [[0, 0, float('nan'), 1], [0,0,1,2]]
    assert not any(valid_manual_boxes(record, seeds[1].path, seeds[0].path, regions))


def test_all_manual_worker_bypasses_search_reuses_geometry_and_exports_same_pixels(sequence, tmp_path, monkeypatch):
    seeds, expected = sequence
    boxes = expected.tracking[path_key(seeds[1].path)].boxes
    seeds = corrected_seeds(seeds, boxes)
    def fail_search(*args, **kwargs):
        raise AssertionError('所有选区已手动指定，不应重新搜索')
    monkeypatch.setattr(ReferenceRegionTracker, 'track', fail_search)
    result = prepare_sequence_preview(seeds, cancel_event=threading.Event())
    assert result.tracking[path_key(seeds[1].path)].manual_indices == (0,1)
    assert result.pixel_boxes == expected.pixel_boxes
    assert result.input_key != expected.input_key
    folder = export_aligned_sequence(result, tmp_path, cancel_event=threading.Event())
    for seed, path in zip(seeds, sorted(folder.glob('*.png'))):
        with render_sequence_preview_frame(result, seed.path).image as preview, Image.open(path) as actual:
            np.testing.assert_array_equal(np.asarray(preview), np.asarray(actual))


def test_one_manual_region_repairs_unmatched_image_and_cache_roundtrips(sequence, tmp_path):
    seeds, _ = sequence
    with Image.new('RGB',(200,160),'black') as image:
        image.save(seeds[1].path)
    seeds = corrected_seeds(seeds, ((.125,.13125,.425,.53125), None))
    cache = SequencePreviewCache(tmp_path / 'manual-cache')
    results, _, errors = run_worker(seeds, cache)
    assert results and not errors
    result = results[0][0]
    assert result.tracking[path_key(seeds[1].path)].manual_indices == (0,)
    assert result.output_size == (195,157)
    restored, _, errors = run_worker(seeds, cache, restore_only=True)
    assert not errors and restored[0][0].tracking == result.tracking
    assert sequence_input_key(seeds) == result.input_key


def test_neighbor_recovery_keeps_manual_region(tmp_path):
    from birdstamp.export_stage.sequence_analysis import analyze_sequence_frames
    from birdstamp.export_stage.video_frame_job import VideoFrameJob
    regions = ((.1,.1,.3,.3),(.5,.5,.7,.7))
    shifted = tuple(tuple(v+.025 for v in box) for box in regions)
    jobs = []
    for i in range(3):
        path = tmp_path / f'{i}.png'
        with Image.new('RGB',(100,100),(i,0,0)) as image:
            image.save(path)
        jobs.append(VideoFrameJob(path,{}, {},{}))
    jobs[1].settings[MANUAL_MATCHES_KEY] = manual_match_record(jobs[1].path,jobs[0].path,regions,(shifted[0],None))
    recovered = []
    class Tracker:
        reference_size = (100,100)
        def track(self,image,**kwargs):
            return RegionTrackingResult((None,None) if image.getpixel((0,0))[0] == 1 else shifted)
        def recover(self,image,result,previous,following,**kwargs):
            recovered.append(result)
            return RegionTrackingResult((regions[0],shifted[1]))
    tracker = Tracker()
    tracker.regions = regions
    tracking,_ = analyze_sequence_frames(jobs,tracker,jobs[0].path,cancel_event=threading.Event())
    result = tracking[path_key(jobs[1].path)]
    assert recovered and recovered[0].manual_indices == (0,)
    assert result.boxes == shifted and result.manual_indices == (0,)
