"""去抖动图片导出的独立并发预算，不继承视频渲染的固定线程/内存上限。"""
from __future__ import annotations

import os

from app_common.log import get_logger

_LOG = get_logger('sequence_export')


def _available_memory_bytes() -> int | None:
    try:
        import psutil

        return max(0, int(psutil.virtual_memory().available))
    except (ImportError, OSError, AttributeError, ValueError, TypeError):
        return None


def resolve_sequence_export_workers(render_workers: int, pending_jobs: int, *, max_frame_pixels: int = 0) -> int:
    """自动尽量用满可用逻辑核心；显式 render_workers 保留原有覆盖语义。"""
    if pending_jobs <= 0:
        return 1
    # Python 3.13+ 优先尊重进程的 CPU 配额；旧 Python / Windows 同样可用。
    cpu_count = max(1, getattr(os, 'process_cpu_count', os.cpu_count)() or os.cpu_count() or 1)
    available = _available_memory_bytes()
    # 原图解码、裁切、转色和编码仍占内存。使用当前可用内存的一半，不再封顶 4 GiB。
    # 探测不可用时仅退回固定内存预算，不以固定的 4 个线程限制所有尺寸的照片。
    budget = available // 2 if available is not None else 2 * 1024**3
    pixels = max(0, int(max_frame_pixels)) or 24_000_000
    memory_workers = max(1, budget // (pixels * 24))
    recommended = max(1, min(cpu_count, memory_workers, pending_jobs))
    requested = max(0, int(render_workers))
    workers = min(requested, pending_jobs) if requested else recommended
    _LOG.info('sequence export worker budget workers=%s cpu=%s memory_workers=%s '
              'memory_budget_bytes=%s memory_detected=%s max_frame_pixels=%s requested=%s',
              workers, cpu_count, memory_workers, budget, available is not None, pixels, requested)
    return workers
