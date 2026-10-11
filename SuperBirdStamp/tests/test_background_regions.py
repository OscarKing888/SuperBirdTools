"""背景选区推荐：先找整组一致的运动层，再按分格配额挑选；覆盖范围与人工选区意图。"""
from cmath import exp
from math import radians
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from birdstamp.image_dejitter.background_regions import consistent_layer, covered_layer
from birdstamp.image_dejitter.region_recommendation import ordered_samples, recommend_regions

NO_BIRDS = lambda image, cancelled=None: ()


def _points(count, seed=1):
    rng = np.random.default_rng(seed)
    return rng.uniform(0, 1000, count) + 1j * rng.uniform(0, 700, count)


def test_majority_layer_wins_over_first_or_best_scored_minority():
    centers = _points(30)
    offsets = np.array([[complex(4 * s, 2 * s)] * 30 for s in (1, 2, 3)])
    offsets[:, :10] = [[complex(15 * s, 9 * s)] for s in (1, 2, 3)]  # 前 10 个是运动更大的近景
    mask = consistent_layer(centers, offsets, tolerance=1.5)
    assert mask[10:].all() and not mask[:10].any()


def test_rigid_layer_with_small_rotation_is_one_group():
    centers = _points(20, seed=3)
    pivot = 500 + 350j
    offsets = np.array([[(c - pivot) * exp(1j * radians(.8 * s)) + pivot - c + 3 * s for c in centers]
                        for s in (1, 2)])
    assert not consistent_layer(centers, offsets, tolerance=1.5).all()  # 只允许平移时会被拆开
    assert consistent_layer(centers, offsets, tolerance=1.5, max_degrees=2, min_span=70).all()


def test_manual_region_picks_layer_even_when_it_leaves_frame_later():
    centers = _points(30)
    offsets = np.array([[complex(4 * s, 2 * s)] * 30 for s in (1, 2, 3)])
    offsets[:, :8] = [[complex(15 * s, 9 * s)] for s in (1, 2, 3)]
    offsets[2, 0] = np.nan  # 人工选区 0 在最远的抽样出画
    mask = consistent_layer(centers, offsets, tolerance=1.5, required=(0,))
    assert mask[:8].all() and not mask[8:].any()
    # 人工选区所在层没有可选纹理时退回主要运动层。
    lonely = offsets.copy()
    lonely[:, 1:8] = [[complex(4 * s, 2 * s)] for s in (1, 2, 3)]
    count, layer = covered_layer(centers, lonely, needed=3, tolerance=1.5, required=(0,),
                                 selectable=[i != 0 for i in range(30)])
    assert count == 3 and not layer[0] and layer[1:].all()


def test_coverage_shrinks_to_longest_consistent_prefix():
    centers = _points(20)
    offsets = np.array([[complex(2 * s, s)] * 20 for s in (1, 2, 3, 4)])
    offsets[2:, :] = np.nan  # 第 3 个抽样起原纹理全部失配
    count, layer = covered_layer(centers, offsets, needed=5, tolerance=1)
    assert count == 2 and layer.all()


def test_samples_are_ordered_by_list_distance_from_reference():
    paths = [Path(f'{i}.jpg') for i in range(20)]
    samples = ordered_samples(paths, paths[10])
    distances = [abs(int(p.stem) - 10) for p in samples]
    assert samples[0] == paths[10] and distances == sorted(distances)


def _texture(seed, size):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (size[1] // 6, size[0] // 6, 3), dtype=np.uint8)
    return Image.fromarray(small).resize((size[0] + 300, size[1] + 300), Image.Resampling.BICUBIC).filter(
        ImageFilter.GaussianBlur(1.5))


def _frames(folder, *, count=6, size=(1200, 800), near=False, change_from=None):
    folder.mkdir()
    far, close = _texture(1, size), _texture(2, size)
    paths = []
    for i in range(count):
        frame = far.crop((6 * i, 4 * i, 6 * i + size[0], 4 * i + size[1]))
        if near:
            moving = close.crop((24 * i, 15 * i, 24 * i + size[0], 15 * i + size[1]))
            frame.paste(moving.crop((0, 0, size[0] * 2 // 5, size[1])), (0, 0))
        if change_from is not None and i >= change_from:
            frame = _texture(50 + i, size).crop((0, 0, *size))
        path = folder / f'帧{i}.png'
        frame.save(path)
        paths.append(path)
    far.close()
    close.close()
    return paths


def test_two_motion_layers_recommend_majority_background(tmp_path):
    paths = _frames(tmp_path / 'layers', near=True)
    result = recommend_regions(paths[0], paths, target_count=9, detector=NO_BIRDS)
    assert result.status == 'ready', result.message
    assert len(result.regions) >= 6
    assert all((box[0] + box[2]) / 2 > .4 for box in result.regions)  # 全部在占多数的远景
    assert result.metadata['auto_regions'] == list(result.regions)


def test_target_count_above_nine_is_reached_and_distributed(tmp_path):
    paths = _frames(tmp_path / 'many')
    result = recommend_regions(paths[0], paths, target_count=16, detector=NO_BIRDS)
    assert result.status == 'ready' and len(result.regions) == 16, result.message
    xs = sorted((box[0] + box[2]) / 2 for box in result.regions)
    assert xs[0] < .25 and xs[-1] > .75
    for i, a in enumerate(result.regions):
        for b in result.regions[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def test_scene_change_reports_coverage_for_relay(tmp_path):
    paths = _frames(tmp_path / 'change', count=8, change_from=5)
    result = recommend_regions(paths[0], paths, target_count=6, detector=NO_BIRDS)
    assert result.status == 'ready' and result.regions, result.message
    coverage = result.coverage
    assert coverage['consistent'] < coverage['samples']
    assert Path(coverage['last_ok']).name in {'帧3.png', '帧4.png', '帧2.png'}
    assert Path(coverage['first_bad']).name in {f'帧{i}.png' for i in range(5, 8)}
    assert '接力' in result.message


def test_manual_region_on_near_layer_keeps_users_motion(tmp_path):
    paths = _frames(tmp_path / 'manual', near=True)
    manual = ((.05, .4, .15, .5),)
    result = recommend_regions(paths[0], paths, existing=manual, target_count=4, detector=NO_BIRDS)
    assert result.status == 'ready', result.message
    assert all((box[0] + box[2]) / 2 < .4 for box in result.regions)
    assert not result.conflicts


def test_drafts_arrive_before_result_and_cancel_stops(tmp_path):
    paths = _frames(tmp_path / 'drafts', count=3)
    drafts = []
    recommend_regions(paths[0], paths, target_count=9, detector=NO_BIRDS, candidate_callback=drafts.append)
    assert drafts and all(item.status == 'prechecking' and item.drafts for item in drafts)
    assert {state for item in drafts for _, state in item.drafts} <= {'pending', 'failed'}
    seen = []  # 草稿出现后取消：不再继续抽样，也不返回结果。
    with pytest.raises(InterruptedError):
        recommend_regions(paths[0], paths, target_count=9, detector=NO_BIRDS, cancelled=lambda: bool(seen),
                          candidate_callback=seen.append)
