"""固定参考模板的有界并行跟踪；第二轮只读取第一轮的邻帧证据。"""
from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import replace
import threading
from time import monotonic

from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._work_pool import BrowserWorkPool
from app_common.file_browser._work_policy import WorkKind
from app_common.log import get_logger
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult, image_file_signature
from .core import estimate_video_job_max_pixels, resolve_video_render_workers
from .video_export_cancelled_error import VideoExportCancelledError

_LOG = get_logger('sequence_analysis')


class SequenceAnalysisAction(WorkerAction):
    """一张照片的跟踪或遮挡核验；共享参考模板只读，原图由 action 独占。"""

    def __init__(self, path, tracker, *, cancelled, preview_source=None, recovery=None):
        super().__init__(cancelled=cancelled)
        self.path, self.tracker = path, tracker
        self.preview_source, self.recovery = preview_source, recovery

    def execute(self):
        if self.is_cancelled():
            raise VideoExportCancelledError('已取消去抖动分析。')
        with decode_image(self.path, decoder='auto') as image:
            if self.recovery is None:
                result = self.tracker.track(image, cancelled=self.is_cancelled)
                if self.preview_source is not None and not self.is_cancelled():
                    self.preview_source(self.path, image)
            else:
                result = self.tracker.recover(image, *self.recovery, cancelled=self.is_cancelled)
            if self.is_cancelled():
                raise VideoExportCancelledError('已取消去抖动分析。')
            return (path_key(self.path), replace(result, signature=image_file_signature(self.path)), image.size)


def analyze_sequence_frames(jobs, tracker, reference, *, cancel_event, preview_source=None,
                            progress=lambda message: None,
                            progress_counts=lambda current, total, stage: None, analysis_workers=0):
    """preview_source 在池线程调用；进度及结果汇总只由调用线程执行。"""
    keys = tuple(path_key(job.path) for job in jobs)
    tracking, sizes = {}, {}
    reference_key = path_key(reference)
    if reference_key in keys:
        tracking[reference_key] = RegionTrackingResult(tracker.regions, signature=image_file_signature(reference))
        sizes[reference_key] = tracker.reference_size
    # 分析另有 FFT 临时数组；尺寸缺失/小图也至少按 2400 万像素估算预算。
    max_pixels = max(24_000_000, estimate_video_job_max_pixels(jobs),
                     tracker.reference_size[0] * tracker.reference_size[1])
    workers = resolve_video_render_workers(analysis_workers, len(jobs), max_frame_pixels=max_pixels)
    stopped = threading.Event()

    def cancelled():
        return stopped.is_set() or cancel_event.is_set()

    def check():
        if cancelled():
            raise VideoExportCancelledError('已取消去抖动分析。')

    check()
    started = monotonic()
    pool = BrowserWorkPool(workers)
    pool.set_thumbnail_mode(False)

    def run_phase(actions, stage, total, completed=0):
        actions = iter(actions)
        pending = set()

        def report():
            progress_counts(completed, total, stage)
            progress(f'{stage} {completed}/{total} · 最多 {workers} 张并行')

        def refill():
            check()
            while len(pending) < workers:
                action = next(actions, None)
                if action is None:
                    break
                pending.add(pool.submit_action(action, kind=WorkKind.METADATA))

        report()
        refill()
        while pending:
            check()
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                key, result, size = future.result()
                tracking[key], sizes[key] = result, size
                completed += 1
                report()
            refill()

    try:
        _LOG.info('sequence analysis start photos=%s workers=%s', len(jobs), workers)
        actions = (SequenceAnalysisAction(job.path, tracker, cancelled=cancelled, preview_source=preview_source)
                   for job in jobs if path_key(job.path) != reference_key)
        run_phase(actions, '对齐照片', len(jobs), len(tracking))
        # 用列表顺序恢复字典；完成顺序不能改变邻帧或最终输出的语义。
        tracking = {key: tracking[key] for key in keys}
        sizes = {key: sizes[key] for key in keys}
        first_pass = dict(tracking)
        recovery_actions = []
        for index in range(1, len(jobs) - 1):
            check()
            result = first_pass[keys[index]]
            if result.matched_count == len(tracker.regions):
                continue
            previous, following = first_pass[keys[index - 1]], first_pass[keys[index + 1]]
            if any(box is None and previous.boxes[i] is not None and following.boxes[i] is not None
                   for i, box in enumerate(result.boxes)):
                recovery_actions.append(SequenceAnalysisAction(
                    jobs[index].path, tracker, cancelled=cancelled, recovery=(result, previous, following)))
        if recovery_actions:
            run_phase(recovery_actions, '核验遮挡', len(recovery_actions))
        check()
        _LOG.info('sequence analysis complete photos=%s recovery=%s workers=%s elapsed_s=%.3f',
                  len(jobs), len(recovery_actions), workers, monotonic() - started)
        return tracking, sizes
    except BaseException as exc:
        stopped.set()
        _LOG.warning('sequence analysis stopped photos=%s workers=%s elapsed_s=%.3f error=%s',
                     len(jobs), workers, monotonic() - started, exc)
        if cancel_event.is_set() and isinstance(exc, Exception):
            raise VideoExportCancelledError('已取消去抖动分析。') from exc
        raise
    finally:
        # 调用线程拥有线程池；等待 action 完成后，GUI worker 才能释放小图缓存。
        pool.shutdown()
