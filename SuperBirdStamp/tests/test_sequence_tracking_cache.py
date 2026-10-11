"""逐照片跟踪缓存：加接力或手动修正后只重算受影响的照片，结果与从头分析完全一致。"""
import os
from collections import Counter

import pytest
from PIL import Image

from test_dejitter_relay import BASE, RELAY, prepare, seeds_for, write_frames, window, _select, _analyze  # noqa: F401
from birdstamp.export_stage import sequence_analysis, sequence_relay
from birdstamp.export_stage.sequence_tracking_cache import SequenceTrackingCache, tracking_reuse_plan
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter.manual_region_matches import MANUAL_MATCHES_KEY, manual_match_record


@pytest.fixture
def decoded(monkeypatch):
    """记录逐帧跟踪与衔接匹配实际解码的照片。"""
    counts = Counter()
    for module in (sequence_analysis, sequence_relay):
        original = module.decode_image

        def counting(path, *args, _original=original, **kwargs):
            counts[path.name] += 1
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(module, 'decode_image', counting)
    return counts


def _same(a, b):
    assert a.jobs.keys() == b.jobs.keys()
    assert a.pixel_boxes == b.pixel_boxes and a.output_size == b.output_size
    assert {k: a.tracking[k].boxes for k in a.jobs} == {k: b.tracking[k].boxes for k in b.jobs}
    assert a.relay_segments == b.relay_segments


@pytest.mark.parametrize('mode', ['translation', 'rigid'])
def test_relay_resume_reuses_prefix_and_matches_full_analysis(tmp_path, decoded, mode):
    paths = write_frames(tmp_path / 'f')
    cache = SequenceTrackingCache()
    first = prepare(seeds_for(paths, paths[0], mode=mode), allow_partial=True, tracking_cache=cache)
    assert first.partial  # 第 4 张起原选区被遮挡
    decoded.clear()
    relayed = seeds_for(paths, paths[0], mode=mode, relays=[(paths[3], (RELAY,))])
    plan = tracking_reuse_plan(relayed, cache)
    assert plan == dict(total=4, reused=2, first_missing=3, relay=True)
    resumed = prepare(relayed, allow_partial=True, tracking_cache=cache)
    # 前 3 张不再解码跟踪；只读取接力参考图（第 4 张，取接力选区模板）、跟踪接力段第 5、6 张，
    # 以及衔接照片第 3 张对接力选区的匹配。
    assert set(decoded) == {'照片2.png', '照片3.png', '照片4.png', '照片5.png'}
    assert not resumed.partial
    _same(resumed, prepare(relayed, allow_partial=True))
    assert tracking_reuse_plan(relayed, cache) == dict(total=4, reused=4, first_missing=None, relay=False)


def test_manual_fix_and_file_change_recompute_only_that_photo(tmp_path, decoded):
    paths = write_frames(tmp_path / 'm', occluded=lambda i: False)
    seeds = seeds_for(paths, paths[0])
    cache = SequenceTrackingCache()
    prepare(seeds, tracking_cache=cache)
    decoded.clear()
    prepare(seeds, tracking_cache=cache)
    assert not decoded  # 只改强度等裁切设置时同样全部复用
    box = (BASE[0] + .02, BASE[1] + .01, BASE[2] + .02, BASE[3] + .01)
    seeds[4].settings[MANUAL_MATCHES_KEY] = manual_match_record(paths[4], paths[0], (BASE,), (box,))
    assert tracking_reuse_plan(seeds, cache)['first_missing'] == 4
    prepare(seeds, tracking_cache=cache)
    assert set(decoded) == {'照片4.png'}
    decoded.clear()
    with Image.open(paths[2]) as image:
        image.rotate(180).save(paths[2])
    os.utime(paths[2], ns=(1, 1))
    assert tracking_reuse_plan(seeds, cache)['first_missing'] == 2
    with pytest.raises(Exception):
        prepare(seeds, tracking_cache=cache)  # 内容已变：重新匹配并如实失败，不复用旧结果
    assert '照片2.png' in decoded


def test_cache_is_bounded_and_thumbnails_are_copies(tmp_path):
    cache = SequenceTrackingCache(max_results=2, max_thumbnail_bytes=3 * 16 * 16)
    for index in range(3):
        cache.put(('k', index), object(), (1, 1))
    assert ('k', 0) not in cache and ('k', 2) in cache
    path = tmp_path / 'a.png'
    Image.new('RGB', (16, 16)).save(path)
    with Image.new('RGB', (16, 16), 'red') as small:
        cache.put_thumbnail(path, 16, small)
    first = cache.thumbnail(path, 16)
    first.close()
    assert cache.thumbnail(path, 16).getpixel((0, 0)) == (255, 0, 0)
    assert cache.thumbnail(path, 32) is None


def test_gui_button_continues_relay_from_anchor(window, monkeypatch, tmp_path, decoded):
    paths = write_frames(tmp_path / 'gui')
    monkeypatch.setattr(window, '_list_photo_paths', lambda: list(paths))
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *a: None)
    window._preview_outer_pad = (0, 0, 0, 0)
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    _select(window, paths[0])
    window._commit_source_reference_regions(paths[0], (BASE,))
    window._update_dejitter_controls()
    assert window.dejitter_preprocess_btn.text() == '分析并预览成片'
    _analyze(window)
    assert window._sequence_preview.partial
    _select(window, paths[3])
    window._update_dejitter_controls()
    window.dejitter_relay_add_btn.click()
    window._commit_source_reference_regions(paths[3], (RELAY,))
    window._update_dejitter_controls()
    assert window.dejitter_preprocess_btn.text() == '继续接力分析 · 从第 4 张起'
    assert '复用已对齐的 2 张' in window.dejitter_preprocess_btn.toolTip()
    step, hint = window._dejitter_next_step()
    assert '继续接力分析' in hint
    decoded.clear()
    _analyze(window)
    assert not window._sequence_preview.partial and len(window._sequence_preview.jobs) == 6
    assert set(decoded) == {'照片2.png', '照片3.png', '照片4.png', '照片5.png'}
    assert set(window._sequence_quick_frames) == {path_key(p) for p in paths}  # 复用的照片也有快速预览
    assert window.dejitter_preprocess_btn.text() == '分析并预览成片'


def test_occlusion_recovery_is_reused_including_reference_neighbour(tmp_path, decoded):
    # 第 2 张（紧挨参考图）与第 4 张单独被遮挡，触发邻帧遮挡核验。
    paths = write_frames(tmp_path / 'o', occluded=lambda i: i in (1, 3))
    seeds = seeds_for(paths, paths[0])
    cache = SequenceTrackingCache()
    with pytest.raises(Exception):
        prepare(seeds, tracking_cache=cache)
    assert decoded['照片1.png'] == 2 and decoded['照片3.png'] == 2  # 首轮 + 遮挡核验
    decoded.clear()
    with pytest.raises(Exception):
        prepare(seeds, tracking_cache=cache)
    assert not decoded
