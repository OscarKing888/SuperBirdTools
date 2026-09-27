"""独立去抖动批量导出的并发、预计算复用与写入生命周期。"""
from collections import Counter
import threading

from PIL import Image
import pytest

from app_common.exif_io import exiftool_runner
from birdstamp.export_stage import sequence_export, sequence_preview
from birdstamp.export_stage.sequence_preview import SequencePreview, file_signatures, sequence_files
from birdstamp.export_stage.video_frame_job import VideoFrameJob
from birdstamp.export_stage.video_export_cancelled_error import VideoExportCancelledError
from birdstamp.gui.editor_utils import path_key


def prepared_sequence(tmp_path, count=6):
    jobs, boxes, sizes = {}, {}, {}
    for index in range(count):
        # 不同目录中的同名照片仍须按列表顺序获得独立输出名。
        path = tmp_path / str(index) / '同名照片.jpg'
        path.parent.mkdir()
        exif = Image.Exif()
        exif[271] = f'Camera {index}'
        with Image.new('RGB', (96, 64), (index * 30, 50, 80)) as image:
            image.save(path, exif=exif)
        path.with_suffix('.XMP').write_text(f'中文元数据 {index}', encoding='utf-8')
        key = path_key(path)
        jobs[key] = VideoFrameJob(path, {}, {}, {})
        boxes[key], sizes[key] = (4, 2, 92, 62), (96, 64)
    return SequencePreview('prepared', jobs, file_signatures(sequence_files(jobs.values())),
                           pixel_boxes=boxes, source_sizes=sizes, output_size=(88, 60))


@pytest.mark.parametrize('output_format', ['png', 'jpg'])
def test_parallel_export_reuses_plans_and_worker_sessions(tmp_path, monkeypatch, output_format):
    sequence = prepared_sequence(tmp_path)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    decoded, thread_ids, sessions = Counter(), set(), set()
    checks = []
    active = peak = 0
    original_execute = sequence_export.SequenceExportAction.execute
    original_render = sequence_export.render_sequence_preview_frame
    original_save = sequence_export.save_export_image
    original_check = SequencePreview.files_current
    before_sessions = set(exiftool_runner._read_sessions)

    def execute(action):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        try:
            return original_execute(action)
        finally:
            with lock:
                active -= 1

    def checked(seq):
        checks.append(threading.get_ident())
        return original_check(seq)

    def render(seq, path, **kwargs):
        with lock:
            decoded[path] += 1
            thread_ids.add(threading.get_ident())
            number = sum(decoded.values())
        if number <= 2:
            barrier.wait(timeout=10)
        return original_render(seq, path, **kwargs)

    def save(*args, **kwargs):
        original_save(*args, **kwargs)
        with lock:
            # 元数据写入使用池线程自己的会话，不串行争用 GUI 全局会话。
            sessions.update(exiftool_runner._worker_local.session.values())

    def no_analysis(*args, **kwargs):
        pytest.fail('导出不能重新进行参考区跟踪或准备元数据')

    monkeypatch.setattr(SequencePreview, 'files_current', checked)
    monkeypatch.setattr(sequence_export.SequenceExportAction, 'execute', execute)
    monkeypatch.setattr(sequence_export, 'render_sequence_preview_frame', render)
    monkeypatch.setattr(sequence_export, 'save_export_image', save)
    monkeypatch.setattr(sequence_preview, 'ReferenceRegionTracker', no_analysis)
    monkeypatch.setattr(sequence_preview, 'prepare_render_jobs', no_analysis)
    messages = []
    owner = threading.get_ident()

    def progress(message):
        assert threading.get_ident() == owner
        messages.append(message)

    folder = sequence_export.export_aligned_sequence(
        sequence, tmp_path, output_format=output_format, cancel_event=threading.Event(),
        render_workers=2, progress=progress)
    assert peak == 2 and active == 0
    # 共享池保留最少三个线程；并发额度不绑定固定线程身份。
    assert 2 <= len(thread_ids) <= 3
    assert len(sessions) == len(thread_ids)
    assert all(session._closed for session in sessions)
    assert exiftool_runner._read_sessions == before_sessions
    assert checks == [owner, owner]  # 完整签名只在整批前后检查，避免逐张扫描整组。
    assert decoded == Counter(job.path for job in sequence.jobs.values())
    assert [message.split(' · ')[0] for message in messages] == [
        f'去抖动导出 {i}/6' for i in range(7)]
    assert len(list(folder.iterdir())) == 12
    for index, job in enumerate(sequence.jobs.values(), 1):
        path = folder / f'{index:04d}_{job.path.stem}.{output_format}'
        assert path.with_suffix('.xmp').read_bytes() == job.path.with_suffix('.XMP').read_bytes()
        with Image.open(path) as exported, Image.open(job.path) as source:
            assert exported.size == (88, 60)
            assert exported.getexif()[271] == f'Camera {index - 1}'
            if output_format == 'png':
                with source.crop((4, 2, 92, 62)) as expected:
                    assert exported.tobytes() == expected.tobytes()


@pytest.mark.parametrize('cancel', [False, True])
def test_failure_or_cancel_joins_inflight_writes_before_rollback(tmp_path, monkeypatch, cancel):
    sequence = prepared_sequence(tmp_path)
    first, second = [job.path for job in sequence.jobs.values()][:2]
    peer_started, release_peer, shutdown_started, finished = (threading.Event() for _ in range(4))
    cancel_event = threading.Event()
    real_pool = sequence_export.BrowserWorkPool
    saved, errors, pools = [], [], []
    before = set(tmp_path.iterdir())
    before_sessions = set(exiftool_runner._read_sessions)

    class ObservedPool(real_pool):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            pools.append(self)

        def shutdown(self, *args, **kwargs):
            shutdown_started.set()
            return super().shutdown(*args, **kwargs)

    def save(image, target, *, source_path, **kwargs):
        saved.append(source_path)
        if source_path == first:
            assert peer_started.wait(10)
            if cancel:
                cancel_event.set()
                return
            raise OSError('模拟磁盘写入失败')
        assert source_path == second  # 有界提交，不应在取消/失败后继续提交剩余照片。
        peer_started.set()
        assert release_peer.wait(10)
        # 模拟取消不敏感的图片编码/文件系统写入；结束前必须保留目录所有权。
        image.save(target, format='PNG')

    def run():
        try:
            sequence_export.export_aligned_sequence(
                sequence, tmp_path, cancel_event=cancel_event, render_workers=2)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    monkeypatch.setattr(sequence_export, 'BrowserWorkPool', ObservedPool)
    monkeypatch.setattr(sequence_export, 'save_export_image', save)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert shutdown_started.wait(10)
        assert not finished.is_set()
        assert len(list(tmp_path.glob('去抖动_*'))) == 1
    finally:
        release_peer.set()
        thread.join(10)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], VideoExportCancelledError if cancel else OSError)
    assert set(saved) == {first, second}
    assert set(tmp_path.iterdir()) == before
    assert all(pool.is_finished() for pool in pools)
    assert exiftool_runner._read_sessions == before_sessions


@pytest.mark.parametrize('change', ['source', 'sidecar', 'external_reference'])
def test_changes_during_export_invalidate_the_entire_output(tmp_path, change):
    sequence = prepared_sequence(tmp_path)
    source = next(iter(sequence.jobs.values())).path
    if change == 'external_reference':
        changed = tmp_path / '外部参考.jpg'
        with Image.new('RGB', (96, 64)) as image:
            image.save(changed)
        for job in sequence.jobs.values():
            job.settings['dejitter_reference_source'] = str(changed)
        sequence.signatures = file_signatures(sequence_files(sequence.jobs.values()))
    else:
        changed = source if change == 'source' else source.with_suffix('.XMP')
    before = set(tmp_path.iterdir())

    def progress(message):
        if message.startswith('去抖动导出 1/'):
            with changed.open('ab') as stream:
                stream.write(b'changed')

    with pytest.raises(ValueError, match='重新分析'):
        sequence_export.export_aligned_sequence(sequence, tmp_path, cancel_event=threading.Event(),
                                                render_workers=2, progress=progress)
    assert set(tmp_path.iterdir()) == before


def test_pre_cancelled_export_creates_no_pool_or_output(tmp_path, monkeypatch):
    sequence = prepared_sequence(tmp_path)
    before = set(tmp_path.iterdir())
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(sequence_export, 'BrowserWorkPool', lambda *a: pytest.fail('取消后不能创建线程池'))
    with pytest.raises(VideoExportCancelledError):
        sequence_export.export_aligned_sequence(sequence, tmp_path, cancel_event=cancel)
    assert set(tmp_path.iterdir()) == before
