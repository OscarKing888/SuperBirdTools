"""部分分析只保留列表中失败前的连续成功帧，复用坐标并保留完整输入校验。"""
from collections import Counter
import threading

import numpy as np
from PIL import Image
import pytest

from test_sequence_analysis import shifted_seeds
from birdstamp.export_stage import sequence_analysis, sequence_preview
from birdstamp.export_stage.sequence_export import export_aligned_sequence
from birdstamp.export_stage.sequence_photo_error import SequencePhotoError
from birdstamp.export_stage.video_export_cancelled_error import VideoExportCancelledError
from birdstamp.gui.editor_sequence_preview_worker import EditorSequencePreviewWorker
from birdstamp.gui.sequence_preview_cache import SequencePreviewCache
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult


@pytest.mark.parametrize('kind', ['unmatched', 'decode'])
@pytest.mark.parametrize('workers', [1, 3])
@pytest.mark.parametrize('pad', [False, True])
def test_success_prefix_matches_standalone_geometry_without_reanalysis(tmp_path, monkeypatch, kind, workers, pad):
    seeds = shifted_seeds(tmp_path, count=6)
    for seed in seeds:
        seed.settings['dejitter_pad_to_union'] = pad
    expected = sequence_preview.prepare_sequence_preview(seeds[:3], cancel_event=threading.Event())
    failed = seeds[3].path
    if kind == 'decode':
        failed.write_bytes(b'broken image')
    else:
        with Image.new('RGB', (512, 384), 'white') as image:
            image.save(failed)
    decoded = Counter()
    lock = threading.Lock()
    real_decode = sequence_analysis.decode_image

    def decode(path, **kwargs):
        with lock:
            decoded[path] += 1
        return real_decode(path, **kwargs)

    monkeypatch.setattr(sequence_analysis, 'decode_image', decode)
    result = sequence_preview.prepare_sequence_preview(seeds, cancel_event=threading.Event(),
                                                       allow_partial=True, analysis_workers=workers)
    assert result.partial and path_key(result.failure.source_path) == path_key(failed)
    assert result.failure.__traceback__ is None and result.failure.__cause__ is None
    assert result.failure.__context__ is None
    assert tuple(result.jobs) == tuple(expected.jobs)
    assert result.pixel_boxes == expected.pixel_boxes and result.output_size == expected.output_size
    assert result.files_current() and tuple(result.all_jobs) == tuple(path_key(s.path) for s in seeds)
    assert all(decoded[s.path] == 1 for s in seeds[1:3])
    for seed in seeds[:3]:
        with sequence_preview.render_sequence_preview_frame(result, seed.path).image as actual, \
                sequence_preview.render_sequence_preview_frame(expected, seed.path).image as wanted:
            np.testing.assert_array_equal(actual, wanted)
    with pytest.raises(ValueError, match='部分成片预览'):
        export_aligned_sequence(result, tmp_path, cancel_event=threading.Event())
    assert not list(tmp_path.glob('去抖动_*'))
    # 包括失败后未参与裁切的输入及新出现的大写 XMP，仍须使旧结果失效。
    seeds[-1].path.with_suffix('.XMP').write_text('中文变更', encoding='utf-8')
    assert not result.files_current()


def test_parallel_error_waits_for_earlier_frames_and_uses_earliest_list_failure(tmp_path, monkeypatch):
    seeds = shifted_seeds(tmp_path, count=6)
    later_failed = threading.Event()
    real_execute = sequence_analysis.SequenceAnalysisAction.execute
    calls = []

    def execute(action):
        calls.append(action.path)
        if action.path == seeds[3].path:
            later_failed.set()
            raise OSError('后序先报错')
        if action.path in (seeds[1].path, seeds[2].path):
            assert later_failed.wait(5)
        if action.path == seeds[2].path:
            raise OSError('前序后报错')
        return real_execute(action)

    monkeypatch.setattr(sequence_analysis.SequenceAnalysisAction, 'execute', execute)
    result = sequence_preview.prepare_sequence_preview(seeds, cancel_event=threading.Event(),
                                                       allow_partial=True, analysis_workers=3)
    assert path_key(result.failure.source_path) == path_key(seeds[2].path)
    assert tuple(result.jobs) == tuple(path_key(s.path) for s in seeds[:2])
    assert set(calls) == {s.path for s in seeds[1:4]}


def test_first_geometry_failure_wins_over_later_match_failure(tmp_path, monkeypatch):
    seeds = shifted_seeds(tmp_path, count=5)
    region = tuple(seeds[0].settings['dejitter_reference_regions'])
    tracking = {path_key(s.path): RegionTrackingResult(region) for s in seeds}
    tracking[path_key(seeds[2].path)] = RegionTrackingResult(((1.4, .3, 1.7, .6),))
    tracking[path_key(seeds[4].path)] = RegionTrackingResult((None,))
    sizes = {k: (512, 384) for k in tracking}
    monkeypatch.setattr(sequence_preview, 'analyze_sequence_frames', lambda *args, **kwargs: (tracking, sizes))
    result = sequence_preview.prepare_sequence_preview(seeds, cancel_event=threading.Event(), allow_partial=True)
    assert path_key(result.failure.source_path) == path_key(seeds[2].path)
    assert '共同覆盖' in str(result.failure)
    assert tuple(result.jobs) == tuple(path_key(s.path) for s in seeds[:2])
    assert result.output_size == (512, 384)


def test_recovery_error_can_shorten_prefix_before_decode_failure(tmp_path, monkeypatch):
    seeds = shifted_seeds(tmp_path, count=4)
    real_execute = sequence_analysis.SequenceAnalysisAction.execute

    def execute(action):
        if action.path == seeds[3].path:
            raise OSError('后帧读取失败')
        if action.path == seeds[1].path:
            if action.recovery is not None:
                raise OSError('前帧核验失败')
            return path_key(action.path), RegionTrackingResult((None,)), (512, 384)
        return real_execute(action)

    monkeypatch.setattr(sequence_analysis.SequenceAnalysisAction, 'execute', execute)
    result = sequence_preview.prepare_sequence_preview(seeds, cancel_event=threading.Event(),
                                                       allow_partial=True, analysis_workers=3)
    assert path_key(result.failure.source_path) == path_key(seeds[1].path)
    assert tuple(result.jobs) == (path_key(seeds[0].path),)
    assert '前帧核验失败' in str(result.failure)


@pytest.mark.parametrize('kind', ['unmatched', 'decode'])
def test_first_list_photo_failure_cannot_return_later_successes(tmp_path, kind):
    seeds = shifted_seeds(tmp_path, count=3)
    # 参考图在列表第二张；不能拿已成功的参考图冒充第一张以前的结果。
    ordered = [seeds[1], seeds[0], seeds[2]]
    if kind == 'decode':
        ordered[0].path.write_bytes(b'broken')
    else:
        with Image.new('RGB', (512, 384), 'white') as image:
            image.save(ordered[0].path)
    with pytest.raises(SequencePhotoError) as caught:
        sequence_preview.prepare_sequence_preview(ordered, cancel_event=threading.Event(), allow_partial=True)
    assert path_key(caught.value.source_path) == path_key(ordered[0].path)


def test_partial_worker_publishes_preview_then_failure_without_complete_disk_cache(tmp_path):
    seeds = shifted_seeds(tmp_path, count=4)
    seeds[2].path.write_bytes(b'broken')
    cache = SequencePreviewCache(tmp_path / 'cache')
    worker = EditorSequencePreviewWorker(token=1, path=seeds[3].path, seeds=seeds, cache=cache)
    events = []
    worker.quick_ready.connect(lambda _, sequence, frames: events.append(('quick', sequence, frames)))
    worker.ready.connect(lambda *args: events.append(('full',)))
    worker.failed.connect(lambda _, message: events.append(('failed', message)))
    worker.run()
    assert [e[0] for e in events] == ['quick', 'failed']
    sequence, frames = events[0][1:]
    assert len(frames) == 2 and tuple(frames) == tuple(sequence.jobs)
    assert worker.failure_path == seeds[2].path
    assert cache.load(seeds) is None
    cache.save(sequence, frames)
    cache.save_sharp(sequence, next(iter(frames.values())))
    assert not list(cache.root.glob('*/manifest.json'))
    assert not list(cache.root.glob('*/sharp-*.png'))
    # 部分结果仍能按需渲染清晰帧，不能重复发出最初的失败事件。
    upgrade = EditorSequencePreviewWorker(token=2, path=seeds[1].path, sequence=sequence, cache=cache)
    ready, failed = [], []
    upgrade.ready.connect(lambda *args: ready.append(args))
    upgrade.failed.connect(lambda *args: failed.append(args))
    upgrade.run()
    assert len(ready) == 1 and not failed


def test_cancellation_does_not_publish_partial_result(tmp_path):
    seeds = shifted_seeds(tmp_path, count=4)
    seeds[2].path.write_bytes(b'broken')
    cancel = threading.Event()

    def progress(current, total, stage):
        if stage == '计算共同画幅':
            cancel.set()

    with pytest.raises(VideoExportCancelledError):
        sequence_preview.prepare_sequence_preview(seeds, cancel_event=cancel, allow_partial=True,
                                                  progress_counts=progress)
