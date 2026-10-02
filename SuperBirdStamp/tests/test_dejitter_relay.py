"""接力追踪：失败照片上新增选区，经相邻已对齐照片接回原参考图坐标并继续整组分析。"""
from math import radians
import threading

import numpy as np
import pytest
from PIL import Image

from test_editor_dejitter import window, _APP
from test_reference_tracking import wait_until
from birdstamp.export_stage import sequence_preview
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.sequence_photo_error import SequencePhotoError
from birdstamp.gui.edit_modes import EDIT_MODE_REFERENCE_REGION
from birdstamp.gui.editor_tracking_overlay import tracking_overlays
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui.sequence_preview_cache import SequencePreviewCache
from birdstamp.image_dejitter.manual_region_matches import MANUAL_MATCHES_KEY
from birdstamp.image_dejitter.relay_anchors import (
    RELAY_ANCHORS_KEY, blended_alignment, compose, relay_record, relay_segments, relay_settings_value,
    resolve_relay_anchors,
)
from birdstamp.image_dejitter.rigid_alignment import inverse_matrix

BASE = (.05, .3, .3, .6)    # 位于之后被遮挡的左侧
RELAY = (.6, .3, .9, .7)    # 右侧在整组都可见
SIZE = (512, 384)


def write_frames(folder, *, count=6, occluded=lambda i: i >= 3, seed=7):
    """第 i 张整体平移 (5i, 3i)；被遮挡帧的左侧换成每张不同的噪声，原选区无法匹配。"""
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, (SIZE[1], SIZE[0], 3), dtype=np.uint8)
    paths = []
    for i in range(count):
        with Image.new('RGB', SIZE) as frame:
            frame.paste(Image.fromarray(pixels), (5 * i, 3 * i))
            data = np.array(frame)
        if occluded(i):
            data[:, :200] = rng.integers(0, 256, (SIZE[1], 200, 3), dtype=np.uint8)
        path = folder / f'照片{i}.png'
        Image.fromarray(data).save(path)
        paths.append(path)
    return paths


def seeds_for(paths, reference, *, mode='translation', relays=(), strength=100):
    settings = dict(dejitter_reference_source=str(reference), dejitter_reference_regions=(BASE,),
                    dejitter_reference_strength=strength, dejitter_alignment_mode=mode)
    if relays:
        settings[RELAY_ANCHORS_KEY] = relay_settings_value([relay_record(path, regions) for path, regions in relays])
    return [RenderJobSeed(path, dict(settings), {}, True) for path in paths]


def prepare(seeds, **kwargs):
    return sequence_preview.prepare_sequence_preview(seeds, cancel_event=threading.Event(), **kwargs)


@pytest.mark.parametrize('mode', ['translation', 'rigid'])
@pytest.mark.parametrize('strength', [100, 50])
def test_relay_continues_after_failed_photo_with_unoccluded_geometry(tmp_path, mode, strength):
    clean = write_frames(tmp_path / 'clean', occluded=lambda i: False)
    expected = prepare(seeds_for(clean, clean[0], mode=mode, strength=strength))
    paths = write_frames(tmp_path / 'occluded')
    partial = prepare(seeds_for(paths, paths[0], mode=mode, strength=strength), allow_partial=True)
    assert partial.partial and path_key(partial.failure.source_path) == path_key(paths[3])
    assert '接力追踪' in partial.failure.message

    result = prepare(seeds_for(paths, paths[0], mode=mode, strength=strength, relays=[(paths[3], (RELAY,))]))
    assert not result.partial and len(result.jobs) == 6
    assert result.output_size == expected.output_size
    assert list(result.pixel_boxes.values()) == list(expected.pixel_boxes.values())
    assert set(result.relay_segments) == {path_key(p) for p in paths[3:]}
    assert all(segment == (str(paths[3]), (RELAY,)) for segment in result.relay_segments.values())
    assert result.relay_bridges[path_key(paths[3])].matched_count == 1
    # 接力段的诊断编号属于接力选区；原参考选区之前的照片保持原编号。
    assert result.regions_for(path_key(paths[4]), (BASE,)) == (RELAY,)
    assert result.regions_for(path_key(paths[1]), (BASE,)) == (BASE,)
    for path, wanted in zip(paths[3:], clean[3:]):
        with sequence_preview.render_sequence_preview_frame(result, path).image as actual, \
                sequence_preview.render_sequence_preview_frame(expected, wanted).image as reference:
            assert actual.size == reference.size


def test_relay_before_middle_reference_bridges_towards_reference(tmp_path):
    clean = write_frames(tmp_path / 'clean', occluded=lambda i: False)
    expected = prepare(seeds_for(clean, clean[3]))
    paths = write_frames(tmp_path / 'occluded', occluded=lambda i: i <= 1)
    with pytest.raises(SequencePhotoError) as caught:
        prepare(seeds_for(paths, paths[3]))
    assert path_key(caught.value.source_path) == path_key(paths[0])
    result = prepare(seeds_for(paths, paths[3], relays=[(paths[1], (RELAY,))]))
    assert list(result.pixel_boxes.values()) == list(expected.pixel_boxes.values())
    assert set(result.relay_segments) == {path_key(paths[0]), path_key(paths[1])}


def test_chained_relays_compose_through_previous_relay(tmp_path):
    clean = write_frames(tmp_path / 'clean', occluded=lambda i: False)
    expected = prepare(seeds_for(clean, clean[0]))
    paths = write_frames(tmp_path / 'occluded')
    result = prepare(seeds_for(paths, paths[0], relays=[(paths[3], (RELAY,)), (paths[5], (RELAY,))]))
    assert list(result.pixel_boxes.values()) == list(expected.pixel_boxes.values())
    assert result.relay_segments[path_key(paths[4])][0] == str(paths[3])
    assert result.relay_segments[path_key(paths[5])][0] == str(paths[5])


def test_relay_regions_missing_in_bridge_fail_at_anchor_and_keep_prefix(tmp_path):
    paths = write_frames(tmp_path / 'occluded')
    # 接力选区落在接力图新出现的噪声上，衔接照片中没有对应纹理，不能用预测位置接上。
    result = prepare(seeds_for(paths, paths[0], relays=[(paths[3], (BASE,))]), allow_partial=True)
    assert result.partial and path_key(result.failure.source_path) == path_key(paths[3])
    assert '衔接照片' in result.failure.message and paths[2].name in result.failure.message
    assert tuple(result.jobs) == tuple(path_key(p) for p in paths[:3])


def test_relay_input_key_and_resolution_rules(tmp_path):
    paths = write_frames(tmp_path / 'frames', count=4, occluded=lambda i: False)
    plain = sequence_preview.sequence_input_key(seeds_for(paths, paths[0]))
    # 空接力不改变签名，已有缓存保持有效。
    assert sequence_preview.sequence_input_key(seeds_for(paths, paths[0], relays=[(paths[2], ())])) == plain
    relayed = sequence_preview.sequence_input_key(seeds_for(paths, paths[0], relays=[(paths[2], (RELAY,))]))
    assert relayed != plain
    records = [relay_record(paths[0], (RELAY,)), relay_record(paths[2], (RELAY,)),
               relay_record(tmp_path / '不在列表.png', (RELAY,))]
    anchors = resolve_relay_anchors(records, paths, paths[0])
    assert [(a.index, a.bridge) for a in anchors] == [(2, 1)]  # 参考图本身和列表外照片被忽略
    assert not resolve_relay_anchors([relay_record(paths[0], (RELAY,))], paths, tmp_path / '列表外参考.png')
    paths[2].write_bytes(paths[2].read_bytes() + b'changed')
    assert not resolve_relay_anchors(records, paths, paths[0])  # 原图变化后接力选区不再生效
    backward, forward = resolve_relay_anchors([relay_record(paths[3], (RELAY,)), relay_record(paths[0], (RELAY,))],
                                              paths, paths[1])
    assert (backward.index, backward.bridge, forward.index, forward.bridge) == (0, 1, 3, 2)
    owners = relay_segments(6, 2, (type(forward)(4, 3, paths[0], (RELAY,)), type(forward)(0, 1, paths[0], (RELAY,))))
    assert [o.index if o else None for o in owners] == [0, None, None, None, 4, 4]


def test_blended_relay_alignment_matches_strength_and_rotation_rules():
    angle = radians(1.5)
    rotation = (np.cos(angle), -np.sin(angle), 12.3, np.sin(angle), np.cos(angle), -4.2)
    full = blended_alignment(rotation, (400, 300), 100, rigid=True, status='rigid')
    assert full.applied_degrees == pytest.approx(1.5) and full.measured_degrees == pytest.approx(-1.5)
    assert full.source_to_reference == pytest.approx(rotation)
    half = blended_alignment(rotation, (400, 300), 50, rigid=True, status='rigid')
    assert half.applied_degrees == pytest.approx(.75)
    centre = np.array(half.source_to_reference).reshape(2, 3) @ (200, 150, 1)
    target = np.array(rotation).reshape(2, 3) @ (200, 150, 1)
    np.testing.assert_allclose(centre, (np.array((200, 150)) + target) / 2, atol=1e-6)
    still = blended_alignment(rotation, (400, 300), 0, rigid=True, status='rigid')
    assert still.source_to_reference == (1., 0., 0., 0., 1., 0.)
    fallback = blended_alignment((1., 0., -7., 0., 1., 3.), (400, 300), 100, rigid=True, status='fallback', reason='r')
    assert fallback.status == 'fallback' and fallback.source_to_reference == (1., 0., -7., 0., 1., 3.)
    back = compose(rotation, inverse_matrix(rotation))
    assert back == pytest.approx((1, 0, 0, 0, 1, 0))


def test_cache_restores_relay_segments(tmp_path):
    from test_sequence_preview_cache import run_worker
    paths = write_frames(tmp_path / 'occluded')
    seeds = seeds_for(paths, paths[0], relays=[(paths[3], (RELAY,))])
    root = tmp_path / 'cache'
    first, _, errors = run_worker(seeds, SequencePreviewCache(root))
    assert not errors and first
    second, _, errors = run_worker(seeds, SequencePreviewCache(root), restore_only=True)
    assert not errors and second
    assert second[0][0].relay_segments == first[0][0].relay_segments
    assert second[0][0].pixel_boxes == first[0][0].pixel_boxes


def _select(window, path):
    window.current_path = path
    if window.current_source_image is not None:
        window.current_source_image.close()
    with Image.open(path) as image:
        window.current_source_image = image.convert('RGB')
    window.current_source_full_size = window.current_source_image.size


def _analyze(window):
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    assert window._sequence_preview is not None, window._sequence_message


def test_gui_relay_from_failed_photo_reanalyzes_whole_group(window, monkeypatch, tmp_path):
    paths = write_frames(tmp_path / 'gui')
    monkeypatch.setattr(window, '_list_photo_paths', lambda: list(paths))
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *a: None)
    window._preview_outer_pad = (0, 0, 0, 0)
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    _select(window, paths[0])
    window._commit_source_reference_regions(paths[0], (BASE,))
    assert window._relay_add_block_reason(paths[0])  # 参考图本身不能接力
    _analyze(window)
    assert window._sequence_preview.partial
    assert window._sequence_message.count('接力追踪') == 1

    _select(window, paths[3])
    window._refresh_preview_label()
    window._update_dejitter_controls()
    assert window._relay_add_block_reason(paths[3]) == ''
    assert window.dejitter_relay_add_btn.isEnabled()
    window.dejitter_relay_add_btn.click()
    assert window._relay_anchor_for_path(paths[3]) is not None
    assert window._reference_regions_editable() and window._current_edit_mode_id() == EDIT_MODE_REFERENCE_REGION
    assert window._sequence_preview is None or window._sequence_preview.partial
    # 未框选的接力不进入分析设置，也不改变其后照片的定义；框选后只写接力选区，不改原参考区。
    assert RELAY_ANCHORS_KEY not in window._build_dejitter_seeds(paths)[0].settings
    assert window._definition_for_path(paths[3]) == (str(paths[3]), ())
    assert window._definition_for_path(paths[3], effective=True) == (str(paths[0]), (BASE,))
    assert window._definition_for_path(paths[5]) == (str(paths[0]), (BASE,))
    assert '尚未框选' in window.dejitter_relay_status.text()
    window._commit_source_reference_regions(paths[3], (RELAY,))
    assert window._dejitter_reference_regions == (BASE,) and window._dejitter_reference_source == str(paths[0])
    assert window._definition_for_path(paths[5]) == (str(paths[3]), (RELAY,))
    assert window._definition_for_path(paths[2]) == (str(paths[0]), (BASE,))
    assert window._build_dejitter_seeds(paths)[0].settings[RELAY_ANCHORS_KEY]
    assert '接力' in window.dejitter_reference_status.text()
    assert window.dejitter_region_list.item(0).text().startswith('接力选区 1')

    _analyze(window)
    sequence = window._sequence_preview
    assert not sequence.partial and len(sequence.jobs) == 6
    assert '接力参考图' in window._sequence_message
    labels = [window.sequence_transport.strip.item(i).text() for i in range(window.sequence_transport.strip.count())]
    if labels:
        assert '接力' in labels[3]
    overlays = tracking_overlays(sequence.regions_for(path_key(paths[4])), sequence.tracking[path_key(paths[4])],
                                 prefix='接力')
    assert overlays[0][1] == '接力1' and overlays[0][2]

    # 接力段内修正的是接力选区编号，记录以接力参考图为定义。
    window._set_dejitter_view('edit')
    window._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
    _select(window, paths[4])
    assert window._editable_regions_for_path(paths[4])[0] != RELAY
    window._commit_manual_region_match(paths[4], 0, (.62, .32, .92, .72))
    record = window._manual_record_for_path(paths[4])
    assert record and record['reference'] == str(paths[3])
    assert window._build_dejitter_seeds(paths)[4].settings[MANUAL_MATCHES_KEY] == record

    # 工作区往返：接力与其手动修正一起恢复。
    saved = window._relay_settings_value()
    manual = list(window._dejitter_manual_matches.values())
    window._clear_relay_anchors()
    window._dejitter_manual_matches.clear()
    window._restore_relay_anchors(saved)
    window._restore_manual_region_matches(manual)
    assert window._definition_for_path(paths[5]) == (str(paths[3]), (RELAY,))
    assert window._manual_record_for_path(paths[4])

    _select(window, paths[3])
    window._update_dejitter_controls()
    window.dejitter_relay_remove_btn.click()
    assert window._relay_anchor_for_path(paths[3]) is None
    assert not window._manual_record_for_path(paths[4])
    assert RELAY_ANCHORS_KEY not in window._build_dejitter_seeds(paths)[0].settings
