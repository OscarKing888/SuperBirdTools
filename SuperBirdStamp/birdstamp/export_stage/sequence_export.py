from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .png_export_stage import PngExportStage
from .sequence_preview import render_sequence_preview_frame
from .video_export_cancelled_error import VideoExportCancelledError


def export_aligned_sequence(sequence, destination, *, output_format='png', cancel_event,
                            progress=lambda message: None):
    """独立整组图片导出；只写新建子目录，取消/失败时撤销本次输出。"""
    if output_format not in {'png', 'jpg'}:
        raise ValueError('去抖动输出仅支持 PNG / JPG。')
    if not sequence.files_current():
        raise ValueError('照片已变化，请重新分析。')
    folder = Path(tempfile.mkdtemp(prefix='去抖动_', dir=destination))
    try:
        terminal = PngExportStage()
        total = len(sequence.jobs)
        for index, job in enumerate(sequence.jobs.values(), 1):
            if cancel_event.is_set():
                raise VideoExportCancelledError('已取消整组导出，本次输出已撤销。')
            context = terminal.process(render_sequence_preview_frame(sequence, job.path))
            target = folder / f'{index:04d}_{job.path.stem}.{output_format}'
            with context.image as image:
                image.save(target, format='PNG' if output_format == 'png' else 'JPEG',
                           **({'quality': 95, 'subsampling': 0} if output_format == 'jpg' else {}))
            progress(f'去抖动导出 {index}/{total}')
        if cancel_event.is_set():
            raise VideoExportCancelledError('已取消整组导出，本次输出已撤销。')
        if not sequence.files_current():
            raise ValueError('导出期间原图已变化，本次输出已撤销，请重新分析。')
        return folder
    except BaseException:
        shutil.rmtree(folder)
        raise
