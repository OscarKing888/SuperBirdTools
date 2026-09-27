"""分析并发不改变列表顺序、像素几何及仅依赖第一轮的遮挡核验。"""
from collections import Counter
import threading

import numpy as np
from PIL import Image
import pytest

from birdstamp.export_stage import sequence_analysis, sequence_preview
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.video_frame_job import VideoFrameJob
from birdstamp.export_stage.video_export_cancelled_error import VideoExportCancelledError
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter.reference_region_tracker import ReferenceRegionTracker
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult


def shifted_seeds(tmp_path, count=7):
    reference = tmp_path / '参考.png'
    pixels = np.random.default_rng(94).integers(0, 256, (384, 512, 3), dtype=np.uint8)
    settings = dict(dejitter_reference_source=str(reference),
                    dejitter_reference_regions=((.3, .3, .6, .6),), dejitter_reference_strength=100)
    seeds = []
    with Image.fromarray(pixels) as source:
        for i in range(count):
            path = reference if i == 0 else tmp_path / f'照片{i}.png'
            with Image.new('RGB', source.size) as shifted:
                shifted.paste(source, (5 * i, 3 * i))
                shifted.save(path)
            seeds.append(RenderJobSeed(path, dict(settings), {}, True))
    return seeds


def test_parallel_matches_serial_and_reports_counts_on_coordinator(tmp_path, monkeypatch):
    seeds = shifted_seeds(tmp_path)
    serial = sequence_preview.prepare_sequence_preview(seeds, cancel_event=threading.Event(), analysis_workers=1)
    lock = threading.Lock()
    barrier = threading.Barrier(3)
    active = peak = 0
    counts, threads, captured = Counter(), set(), set()
    original_track = ReferenceRegionTracker.track
    original_decode = sequence_analysis.decode_image

    def decode(path, **kwargs):
        with lock:
            counts[path] += 1
        return original_decode(path, **kwargs)

    def track(tracker, image, **kwargs):
        nonlocal active, peak
        with lock:
            threads.add(threading.get_ident())
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(10)
            return original_track(tracker, image, **kwargs)
        finally:
            with lock:
                active -= 1

    def capture(path, image):
        assert image.size == (512, 384)
        with lock:
            captured.add(path)

    owner = threading.get_ident()
    progress = []

    def report(current, total, stage):
        assert threading.get_ident() == owner
        progress.append((current, total, stage))

    monkeypatch.setattr(ReferenceRegionTracker, 'track', track)
    monkeypatch.setattr(sequence_analysis, 'decode_image', decode)
    parallel = sequence_preview.prepare_sequence_preview(
        seeds, cancel_event=threading.Event(), analysis_workers=3, preview_source=capture, progress_counts=report)
    assert peak == 3 and active == 0 and len(threads) == 3
    assert counts == Counter(seed.path for seed in seeds[1:])
    assert captured == {seed.path for seed in seeds}
    assert tuple(parallel.jobs) == tuple(parallel.tracking) == tuple(parallel.source_sizes) == tuple(serial.jobs)
    assert parallel.tracking == serial.tracking
    assert parallel.pixel_boxes == serial.pixel_boxes
    assert parallel.output_size == serial.output_size == (482, 366)
    assert [(current, total) for current, total, stage in progress if stage == '对齐照片'] == [
        (i, 7) for i in range(1, 8)]


def test_recovery_waits_for_first_pass_and_uses_original_neighbors(tmp_path):
    jobs = []
    for i in range(5):
        path = tmp_path / f'{i}.png'
        with Image.new('RGB', (40, 30), (i, 0, 0)) as image:
            image.save(path)
        jobs.append(VideoFrameJob(path, {}, {}, {}))
    lock = threading.Lock()
    first_done = set()
    recovery_barrier = threading.Barrier(2)
    phases = []

    class Tracker:
        regions = ((.1, .1, .5, .5),)
        reference_size = (40, 30)

        def track(self, image, *, cancelled):
            index = image.getpixel((0, 0))[0]
            with lock:
                first_done.add(index)
            return RegionTrackingResult((None,) if index in (1, 3) else self.regions, error=f'first-{index}')

        def recover(self, image, result, previous, following, *, cancelled):
            index = image.getpixel((0, 0))[0]
            assert first_done == set(range(5))
            assert result.error == f'first-{index}'
            assert previous.error == f'first-{index-1}'
            assert following.error == f'first-{index+1}'
            recovery_barrier.wait(10)
            return RegionTrackingResult(self.regions, error=f'recovered-{index}')

    results, sizes = sequence_analysis.analyze_sequence_frames(
        jobs, Tracker(), tmp_path / 'external.png', cancel_event=threading.Event(), analysis_workers=3,
        progress_counts=lambda current, total, stage: phases.append((stage, current, total)))
    assert tuple(results) == tuple(path_key(job.path) for job in jobs)
    assert results[path_key(jobs[1].path)].error == 'recovered-1'
    assert results[path_key(jobs[3].path)].error == 'recovered-3'
    assert all(size == (40, 30) for size in sizes.values())
    assert [entry for entry in phases if entry[0] == '核验遮挡'] == [
        ('核验遮挡', i, 2) for i in range(3)]


@pytest.mark.parametrize('cancel', [False, True])
def test_cancel_or_error_joins_all_actions_and_stops_refill(tmp_path, monkeypatch, cancel):
    seeds = shifted_seeds(tmp_path, count=6)
    peer_started, release_peer, shutdown, finished = (threading.Event() for _ in range(4))
    cancel_event = threading.Event()
    real_pool = sequence_analysis.BrowserWorkPool
    calls, errors, pools = [], [], []

    class Pool(real_pool):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            pools.append(self)

        def shutdown(self, *args, **kwargs):
            shutdown.set()
            return super().shutdown(*args, **kwargs)

    def execute(action):
        calls.append(action.path)
        if action.path == seeds[1].path:
            assert peer_started.wait(10)
            if cancel:
                cancel_event.set()
                raise InterruptedError('已取消')
            raise OSError('模拟跟踪图像读取失败')
        assert action.path == seeds[2].path
        peer_started.set()
        assert release_peer.wait(10)
        return path_key(action.path), RegionTrackingResult(((.3, .3, .6, .6),)), (512, 384)

    def run():
        try:
            sequence_preview.prepare_sequence_preview(seeds, cancel_event=cancel_event, analysis_workers=2)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    monkeypatch.setattr(sequence_analysis, 'BrowserWorkPool', Pool)
    monkeypatch.setattr(sequence_analysis.SequenceAnalysisAction, 'execute', execute)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert shutdown.wait(10)
        assert not finished.is_set()
    finally:
        release_peer.set()
        thread.join(10)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], VideoExportCancelledError if cancel else OSError)
    assert set(calls) == {seeds[1].path, seeds[2].path}
    assert all(pool.is_finished() for pool in pools)


def test_changed_sidecar_during_parallel_analysis_rejects_result(tmp_path):
    seeds = shifted_seeds(tmp_path, count=3)

    def progress(current, total, stage):
        if stage == '对齐照片' and current == total:
            seeds[1].path.with_suffix('.XMP').write_text('中文变更', encoding='utf-8')

    with pytest.raises(ValueError, match='分析期间发生变化'):
        sequence_preview.prepare_sequence_preview(seeds, cancel_event=threading.Event(),
                                                  progress_counts=progress, analysis_workers=2)
