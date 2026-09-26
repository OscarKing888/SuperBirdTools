"""一次选区在大位移、局部遮挡下自动定位；重复纹理不能强行匹配。"""
import threading

import numpy as np
import pytest
from PIL import Image

from birdstamp.image_dejitter.reference_region_tracker import ReferenceRegionTracker
from birdstamp.image_dejitter.region_template_search import normalized_correlation
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview


REGION = (.1, .3, .3, .6)


def source():
    values = np.random.default_rng(1702).integers(20, 160, (400, 600), dtype=np.uint8)
    return Image.fromarray(values).convert('RGB')


def shifted(image, dx, dy):
    result = Image.new('RGB', image.size, '#fafafa')
    result.paste(image, (dx, dy))
    return result


def test_large_translation_outside_original_region_is_found_without_adding_regions():
    with source() as ref, shifted(ref, 220, 90) as target:
        tracker = ReferenceRegionTracker(ref, (REGION,))
        result = tracker.track(target)
        assert result.matched_count == 1, result.error
        np.testing.assert_allclose(np.subtract(result.boxes[0], REGION), (220/600, 90/400)*2, atol=.001)
        # 切换目标顺序不会改变参考模板，更不会累计上一张的位移。
        with shifted(ref, -25, -65) as other:
            second = tracker.track(other)
            np.testing.assert_allclose(np.subtract(second.boxes[0], REGION), (-25/600, -65/400)*2, atol=.001)
        assert tracker.track(target).boxes == result.boxes


def test_repeated_candidates_and_unrelated_texture_remain_rejected():
    with source() as ref:
        tracker = ReferenceRegionTracker(ref, (REGION,))
        patch = ref.crop((60, 120, 180, 240))
        with Image.new('RGB', ref.size, 'white') as repeated:
            repeated.paste(patch, (260, 120))
            repeated.paste(patch, (440, 120))
            result = tracker.track(repeated)
            assert result.matched_count == 0
            assert '多个相似位置' in result.error
        patch.close()
        with Image.fromarray(np.random.default_rng(23).integers(0, 255, (400, 600), dtype=np.uint8)).convert('RGB') as unrelated:
            assert tracker.track(unrelated).matched_count == 0


def test_flat_reference_and_cancelled_search_are_rejected():
    with Image.new('RGB', (600, 400), 'gray') as flat:
        tracker = ReferenceRegionTracker(flat, (REGION,))
        assert tracker.track(flat).matched_count == 0
        with pytest.raises(InterruptedError):
            tracker.search.locate(flat, tracker.search.search_image(flat), 0, cancelled=lambda: True)


def test_normalized_correlation_matches_direct_formula():
    rng = np.random.default_rng(61)
    values = rng.normal(100, 20, (20, 30))
    template = values[7:13, 12:21].copy()
    scores = normalized_correlation(values, template)
    assert np.unravel_index(np.argmax(scores), scores.shape) == (7, 12)
    assert scores[7, 12] == pytest.approx(1)
    patch = values[2:8, 3:12]
    a, b = patch-patch.mean(), template-template.mean()
    assert scores[2, 3] == pytest.approx(np.sum(a*b)/np.linalg.norm(a)/np.linalg.norm(b))


def test_isolated_occlusion_recovers_from_visible_reference_content_not_interpolation(tmp_path):
    settings = {'dejitter_reference_regions': (REGION,), 'dejitter_reference_strength': 100}
    paths = [tmp_path/f'{i}.png' for i in range(3)]
    with source() as ref, shifted(ref, 150, 25) as target, shifted(ref, 210, 35) as following:
        # 有意令中间帧不在邻帧位移的简单中点；必须找到实际纹理，不能只输出平均。
        target.paste('white', (210, 145, 330, 205))
        ref.save(paths[0]); target.save(paths[1]); following.save(paths[2])
        tracker = ReferenceRegionTracker(ref, (REGION,))
        result = tracker.track(target)
        assert result.matched_count == 0
        after = tracker.track(following)
        # 预测限制会排除不合理大偏差；选用可见纹理所在的邻帧范围。
        expected_before = RegionTrackingResult((tuple(v + (100/600 if i % 2 == 0 else 15/400)
                                                       for i, v in enumerate(REGION)),))
        recovered = tracker.recover(target, result, expected_before, after)
        assert recovered.matched_count == 1
        np.testing.assert_allclose(np.subtract(recovered.boxes[0], REGION), (150/600, 25/400)*2, atol=.002)
        with Image.new('RGB', ref.size, 'white') as absent:
            assert tracker.recover(absent, result, expected_before, after).matched_count == 0
    # 整组入口的二次核验使用第一遍邻帧结果，不级联修复。
    with Image.open(paths[0]) as original, shifted(original, 100, 15) as previous:
        previous.save(paths[0])
    reference_path = tmp_path/'reference.png'
    with source() as original: original.save(reference_path)
    settings['dejitter_reference_source'] = str(reference_path)
    sequence = prepare_sequence_preview([RenderJobSeed(p, settings, {}, True) for p in paths],
                                        cancel_event=threading.Event())
    assert all(result.matched_count == 1 for result in sequence.tracking.values())
