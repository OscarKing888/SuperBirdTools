from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, wait
import shutil
import tempfile
import threading
from time import monotonic
from pathlib import Path

from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._work_pool import BrowserWorkPool
from app_common.file_browser._work_policy import WorkKind
from app_common.log import get_logger
from birdstamp.export_metadata import save_export_image, copy_export_sidecar

from .sequence_export_workers import resolve_sequence_export_workers
from .png_export_stage import PngExportStage
from .sequence_preview import render_sequence_preview_frame
from .sequence_intersection import intersection_export_sequence
from .video_export_cancelled_error import VideoExportCancelledError

_LOG = get_logger('sequence_export')


def sequence_export_targets(source_paths, folder, output_format):
    """按原列表编号生成输出清单，导出与后续工作区导入共用同一份命名规则。"""
    return tuple(Path(folder) / f'{index:04d}_{path.stem}.{output_format}'
                 for index, path in enumerate(source_paths, 1))


class SequenceExportAction(WorkerAction):
    """使用已分析的像素框完成一张原分辨率导出，不重复跟踪或读取模板。"""

    def __init__(self, sequence, path, target, output_format, source_paths, *, cancelled):
        super().__init__(cancelled=cancelled)
        self.sequence, self.path, self.target = sequence, path, target
        self.output_format, self.source_paths = output_format, source_paths

    def _check_cancelled(self):
        if self.is_cancelled():
            raise VideoExportCancelledError('已取消整组导出，本次输出已撤销。')

    def execute(self):
        self._check_cancelled()
        started = monotonic()
        context = PngExportStage().process(render_sequence_preview_frame(
            self.sequence, self.path, validate_files=False, source_paths=self.source_paths))
        rendered = monotonic()
        with context.image as image:
            self._check_cancelled()
            save_export_image(
                image, self.target, source_path=self.path,
                format='PNG' if self.output_format == 'png' else 'JPEG',
                **({'quality': 95, 'subsampling': 0} if self.output_format == 'jpg'
                   else {'compress_level': 1}),
            )
        self._check_cancelled()
        copy_export_sidecar(self.path, self.target)
        self._check_cancelled()
        return rendered - started, monotonic() - rendered


def export_aligned_sequence(sequence, destination, *, output_format='png', cancel_event,
                            progress=lambda message: None, render_workers=0, intersection_only=False,
                            progress_counts=lambda current, total, stage: None):
    """有界并行导出；线程池退出后才回滚，避免迟到写入重新创建输出目录。"""
    if output_format not in {'png', 'jpg'}:
        raise ValueError('去抖动输出仅支持 PNG / JPG。')
    if cancel_event.is_set():
        raise VideoExportCancelledError('已取消整组导出。')
    if sequence.partial:
        raise ValueError('当前仅有失败前的部分成片预览，请完成整组分析后再导出全部。')
    if not sequence.files_current():
        raise ValueError('照片已变化，请重新分析。')
    if intersection_only:
        sequence = intersection_export_sequence(sequence)
    source_paths = tuple(job.path for job in sequence.jobs.values())
    total = len(source_paths)
    if not total:
        raise ValueError('请先导入照片并分析。')
    # 补边输出可能大于任何原图，内存预算必须同时考虑输出画幅。
    max_pixels = max(width * height for width, height in
                     (*sequence.source_sizes.values(), sequence.output_size))
    workers = resolve_sequence_export_workers(render_workers, total, max_frame_pixels=max_pixels)
    stopped = threading.Event()
    cancelled = lambda: stopped.is_set() or cancel_event.is_set()
    pool = None
    started = monotonic()
    folder = Path(tempfile.mkdtemp(prefix='去抖动_', dir=destination))
    try:
        # 沿用共享 WorkerAction 线程及线程独有 ExifTool 会话；关闭浏览器缩略图
        # 额度预留，全部 action 使用同一队列。实际在途数仍受 workers 限制。
        pool = BrowserWorkPool(workers)
        pool.set_thumbnail_mode(False)
        # 编号先于提交分配，与完成顺序无关，同 stem/大小写/Unicode 也不会互相覆盖。
        actions = iter(tuple(SequenceExportAction(
            sequence, path, target,
            output_format, source_paths, cancelled=cancelled,
        ) for path, target in zip(source_paths, sequence_export_targets(source_paths, folder, output_format))))
        pending = set()
        completed = 0
        render_seconds = write_seconds = 0.0

        def refill():
            while len(pending) < workers and not cancelled():
                action = next(actions, None)
                if action is None:
                    break
                pending.add(pool.submit_action(action, kind=WorkKind.METADATA))

        _LOG.info('sequence export start photos=%s workers=%s format=%s reused_plans=%s',
                  total, workers, output_format, total)
        progress(f'去抖动导出 0/{total} · 最多 {workers} 张并行')
        progress_counts(0, total, '导出图片')
        refill()
        while pending:
            if cancel_event.is_set():
                raise VideoExportCancelledError('已取消整组导出，本次输出已撤销。')
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                render_time, write_time = future.result()
                render_seconds += render_time
                write_seconds += write_time
                completed += 1
                progress(f'去抖动导出 {completed}/{total} · 最多 {workers} 张并行')
                progress_counts(completed, total, '导出图片')
            refill()
        progress_counts(0, 0, '校验导出结果')
        pool.shutdown()
        pool = None
        if cancel_event.is_set():
            raise VideoExportCancelledError('已取消整组导出，本次输出已撤销。')
        if not sequence.files_current():
            raise ValueError('导出期间原图已变化，本次输出已撤销，请重新分析。')
        _LOG.info('sequence export complete photos=%s workers=%s elapsed_s=%.3f '
                  'render_sum_s=%.3f write_sum_s=%.3f',
                  total, workers, monotonic() - started, render_seconds, write_seconds)
        return folder
    except BaseException as exc:
        stopped.set()
        if pool is not None:
            pool.shutdown()
        shutil.rmtree(folder)
        _LOG.warning('sequence export rolled back photos=%s workers=%s elapsed_s=%.3f error=%s',
                     total, workers, monotonic() - started, exc)
        if cancel_event.is_set() and isinstance(exc, Exception):
            raise VideoExportCancelledError('已取消整组导出，本次输出已撤销。') from exc
        raise
