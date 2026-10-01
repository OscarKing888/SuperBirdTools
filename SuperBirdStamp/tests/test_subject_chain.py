"""链式关键帧：渐变姿态可跟随，双向一致才融合，单侧受误差上限约束。"""
from types import SimpleNamespace
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
from birdstamp.image_dejitter.subject_local_tracker import SubjectLocalTracker, LocalObservation
from birdstamp.image_dejitter.subject_keyframes import chain_keyframe_segments

SIZE = (480, 360)
BOX = (180, 120, 280, 220)             # 主体（鸟）像素框
REGION = (BOX[0]/SIZE[0], BOX[1]/SIZE[1], BOX[2]/SIZE[0], BOX[3]/SIZE[1])
WIRE = (.02, .62, .3, .8)


def texture(seed):
    rng = np.random.default_rng(seed)
    return np.asarray(Image.fromarray(rng.integers(0,255,(BOX[3]-BOX[1],BOX[2]-BOX[0]),dtype=np.uint8))
                      .filter(ImageFilter.GaussianBlur(1.)), dtype=float)


def frame(alpha, shift, *, wire=False):
    """alpha=1 为参考姿态，0 为完全不同的姿态；shift 为相机平移。"""
    background = Image.new('L', SIZE, 160)
    if wire:
        ImageDraw.Draw(background).line([(-50,250),(SIZE[0]+50,252)], fill=30, width=3)
    subject = alpha*texture(1)+(1-alpha)*texture(2)
    background.paste(Image.fromarray(subject.clip(0,255).astype(np.uint8)), BOX[:2])
    image = background.filter(ImageFilter.GaussianBlur(.5))
    moved = Image.new('L', SIZE, 160)
    moved.paste(image, (int(shift[0]), int(shift[1])))
    return moved.convert('RGB')


def sequence(tmp_path, alphas, shifts, *, wire=False):
    paths = []
    for i, (a, s) in enumerate(zip(alphas, shifts)):
        path = tmp_path/f'{i:02d}.png'
        frame(a, s, wire=wire).save(path)
        paths.append(path)
    regions = (REGION, WIRE) if wire else (REGION,)
    with Image.open(paths[0]) as reference:
        tracker = SubjectLocalTracker(reference.convert('RGB'), regions)
    results = {path_key(paths[0]): RegionTrackingResult(regions, observation=LocalObservation('reference', displacement=(0.,0.)))}
    for path in paths[1:]:
        with Image.open(path) as image:
            results[path_key(path)] = tracker.track(image.convert('RGB'))
    return [SimpleNamespace(path=p) for p in paths], tracker, results


def test_gradual_pose_change_is_bridged_from_both_anchors(tmp_path):
    alphas = (1, .75, .5, .25, 0, .25, .5, .75, 1)
    shifts = [(i, -i) for i in range(len(alphas))]
    jobs, tracker, results = sequence(tmp_path, alphas, shifts)
    failed = [i for i, j in enumerate(jobs) if results[path_key(j.path)].matched_count == 0]
    assert failed, '固定参考应在姿态变化较大时失配'
    output = chain_keyframe_segments(jobs, tracker, results, cancelled=lambda: False)
    for i in failed:
        obs = output[path_key(jobs[i].path)].observation
        assert obs.status == 'keyframe_bridge', output[path_key(jobs[i].path)].error
        assert obs.displacement == pytest.approx(shifts[i], abs=.5)
        assert len(obs.keyframe_paths) == 2
    assert all(results[path_key(jobs[i].path)].matched_count == 0 for i in failed)  # 输入只读


def test_one_sided_tail_chain_reports_links_and_drift(tmp_path):
    alphas = (1, .75, .5, .25, 0, 0)
    shifts = [(i, 2*i) for i in range(len(alphas))]
    jobs, tracker, results = sequence(tmp_path, alphas, shifts)
    assert any(results[path_key(j.path)].matched_count == 0 for j in jobs)
    output = chain_keyframe_segments(jobs, tracker, results, cancelled=lambda: False)
    for i, j in enumerate(jobs[1:], 1):
        result = output[path_key(j.path)]
        if results[path_key(j.path)].matched_count:
            continue
        obs = result.observation
        if result.matched_count:
            assert obs.status == 'keyframe_chain' and obs.chain[2] == 'forward'
            assert obs.chain[1] <= tracker.geometry.scales[0]+1e-9
            assert obs.displacement == pytest.approx(shifts[i], abs=.6)
        else:
            assert '链式' in result.error and '人工关键帧' in result.error


def test_wire_keeps_vertical_anchored_while_bird_is_chained(tmp_path):
    alphas = (1, .75, .5, .25, 0, 0)
    shifts = [(i, 2*i) for i in range(len(alphas))]
    jobs, tracker, results = sequence(tmp_path, alphas, shifts, wire=True)
    assert tracker.geometry.kinds == ('2d','1d')
    output = chain_keyframe_segments(jobs, tracker, results, cancelled=lambda: False)
    chained = [output[path_key(j.path)].observation for j in jobs[1:]
               if output[path_key(j.path)].observation.status in ('keyframe_chain','keyframe_bridge')]
    assert chained
    for obs in chained:
        cov = np.asarray(obs.covariance).reshape(2,2)
        # 竖直由电线每帧直接锚定原参考，不随链增长。
        assert cov[1,1] < cov[0,0]


def test_chain_honors_cancellation_and_skips_missing_frames(tmp_path):
    jobs, tracker, results = sequence(tmp_path, (1, .5, 0, .5, 1), [(0,0)]*5)
    with pytest.raises(InterruptedError):
        chain_keyframe_segments(jobs, tracker, results, cancelled=lambda: True)
    partial = dict(results)
    partial.pop(path_key(jobs[2].path))
    assert chain_keyframe_segments(jobs, tracker, partial, cancelled=lambda: False).keys() == partial.keys()
