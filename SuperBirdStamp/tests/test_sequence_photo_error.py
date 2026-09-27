"""分析与预览失败携带完整源路径，不以文件名或当前选图猜测失败帧。"""
import pytest

from test_sequence_analysis import shifted_seeds
from birdstamp.export_stage import sequence_preview
from birdstamp.export_stage.sequence_photo_error import SequencePhotoError
from birdstamp.export_stage.sequence_preview import common_alignment_crop
from birdstamp.gui import editor_sequence_preview_worker as worker_module
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult


@pytest.mark.parametrize('reason', ['missing_match', 'conflict', 'empty_intersection'])
def test_geometry_error_identifies_exact_failed_photo(tmp_path, reason):
    regions = ((.1, .1, .2, .2), (.4, .4, .5, .5))
    paths = [tmp_path / folder / '同名.png' for folder in ('a', 'b', 'c')]
    reference = RegionTrackingResult(regions)
    if reason == 'missing_match':
        failed = RegionTrackingResult((None, None))
    elif reason == 'conflict':
        failed = RegionTrackingResult((regions[0], (.7, .6, .8, .7)))
    else:
        failed = RegionTrackingResult(tuple((x1 + 1.1, y1, x2 + 1.1, y2) for x1, y1, x2, y2 in regions))
    tracking = {str(paths[0]): reference, str(paths[1]): failed, str(paths[2]): reference}
    sizes = {key: (100, 100) for key in tracking}
    with pytest.raises(SequencePhotoError) as caught:
        common_alignment_crop(regions, tracking, sizes, (100, 100))
    assert caught.value.source_path == paths[1]


@pytest.mark.parametrize('broken_index', [0, 2])
def test_worker_identifies_reference_or_parallel_decode_error(tmp_path, broken_index):
    seeds = shifted_seeds(tmp_path, count=3)
    failed = seeds[broken_index].path
    failed.write_bytes(b'broken image')
    worker = worker_module.EditorSequencePreviewWorker(token=7, path=seeds[1].path, seeds=seeds)
    errors = []
    worker.failed.connect(lambda token, message: errors.append((token, message)))
    worker.run()
    assert len(errors) == 1 and errors[0][0] == 7
    assert worker.failure_path == failed
    assert failed.name in errors[0][1]


@pytest.mark.parametrize('stage', ['quick', 'sharp'])
def test_worker_identifies_preview_render_error(tmp_path, monkeypatch, stage):
    seeds = shifted_seeds(tmp_path, count=3)
    worker = worker_module.EditorSequencePreviewWorker(token=8, path=seeds[2].path, seeds=seeds)
    errors = []
    worker.failed.connect(lambda token, message: errors.append(message))

    def fail(*args, **kwargs):
        raise OSError('测试预览失败')

    if stage == 'quick':
        monkeypatch.setattr(worker_module, 'render_aligned_thumbnail', fail)
    else:
        monkeypatch.setattr(sequence_preview.ImageProcPipeline, 'process', fail)
    worker.run()
    assert len(errors) == 1 and '测试预览失败' in errors[0]
    assert worker.failure_path == seeds[0 if stage == 'quick' else 2].path


def test_group_signature_error_does_not_blame_current_photo(tmp_path):
    import threading
    seeds = shifted_seeds(tmp_path, count=3)
    sequence = sequence_preview.prepare_sequence_preview(seeds, cancel_event=threading.Event())
    seeds[1].path.with_suffix('.xmp').write_text('changed', encoding='utf-8')
    worker = worker_module.EditorSequencePreviewWorker(token=9, path=seeds[0].path, sequence=sequence)
    errors = []
    worker.failed.connect(lambda token, message: errors.append(message))
    worker.run()
    assert len(errors) == 1 and '已变化' in errors[0]
    assert worker.failure_path is None
