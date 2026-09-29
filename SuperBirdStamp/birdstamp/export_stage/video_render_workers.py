"""Resource budgets and aggregate diagnostics for the two video frame stages."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import os
import threading
from time import perf_counter

from PIL import Image

from app_common.log import get_logger
from .video_export_cancelled_error import VideoExportCancelledError

_LOG = get_logger("video_render")
UNKNOWN_FRAME_PIXELS = 24_000_000


def available_memory_bytes() -> int | None:
    try:
        import psutil

        return max(0, int(psutil.virtual_memory().available))
    except (ImportError, OSError, AttributeError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class VideoWorkerBudget:
    workers: int
    recommended: int
    warning: str


def resolve_video_frame_budget(requested: int, pending: int, *, max_frame_pixels: int,
                               stage: str) -> VideoWorkerBudget:
    """Keep explicit overrides; automatic work uses available CPUs and half free RAM."""
    cpu_count = max(1, getattr(os, "process_cpu_count", os.cpu_count)() or os.cpu_count() or 1)
    available = available_memory_bytes()
    budget = available // 2 if available is not None else 2 * 1024**3
    pixels = max(0, int(max_frame_pixels)) or UNKNOWN_FRAME_PIXELS
    memory_workers = max(1, budget // (pixels * 24))
    recommended = max(1, min(cpu_count, memory_workers, pending))
    requested = max(0, int(requested))
    workers = max(1, min(requested, pending)) if requested else recommended
    limits = {"cpu": cpu_count, "memory": memory_workers, "pending": max(1, pending)}
    reason = "+".join(name for name, limit in limits.items() if limit == recommended)
    warning = ""
    if requested > recommended:
        warning = f"；警告：显式线程数 {requested} 超出推荐值 {recommended}，实际使用 {workers}"
        _LOG.warning("video %s explicit workers=%s exceeds recommended=%s; honoring explicit setting",
                     stage, requested, recommended)
    _LOG.info("video worker budget stage=%s workers=%s recommended=%s cpu=%s available_bytes=%s "
              "memory_budget_bytes=%s memory_workers=%s max_frame_pixels=%s pending=%s requested=%s limit=%s",
              stage, workers, recommended, cpu_count, available, budget, memory_workers,
              pixels, pending, requested, "explicit" if requested else reason)
    return VideoWorkerBudget(workers, recommended, warning)


def estimate_normalize_pixels(paths, target_size, *, check_cancel=lambda: None) -> int:
    """Read PNG headers only; full source buffers exist even when downscaling."""
    pixels = int(target_size[0]) * int(target_size[1])
    for path in paths:
        check_cancel()
        with Image.open(path) as image:
            pixels = max(pixels, image.width * image.height)
    return pixels


class VideoStageStats:
    """Worker updates stay in memory; the coordinator writes one completion record."""
    def __init__(self, stage):
        self.stage = stage
        self.started = perf_counter()
        self.workers = 0
        self.reused = 0
        self.completed = 0
        self.seconds = {"processing": 0.0, "writing": 0.0}
        self.lock = threading.Lock()

    @contextmanager
    def measure(self, operation):
        started = perf_counter()
        try:
            yield
        finally:
            with self.lock:
                self.seconds[operation] += perf_counter() - started

    def frame_completed(self):
        with self.lock:
            self.completed += 1

    def report(self, status):
        elapsed = perf_counter() - self.started
        _LOG.info("video stage=%s status=%s workers=%s completed=%s reused=%s elapsed_s=%.3f "
                  "fps=%.3f processing_sum_s=%.3f writing_sum_s=%.3f",
                  self.stage, status, self.workers, self.completed, self.reused, elapsed,
                  self.completed / max(elapsed, 0.001), self.seconds["processing"], self.seconds["writing"])


def track_video_stage(stage):
    """Include cache-only, single-frame, failure and cancellation paths in diagnostics."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            stats = VideoStageStats(stage)
            status = "failed"
            try:
                result = function(*args, **kwargs, stats=stats)
                status = "complete"
                return result
            except VideoExportCancelledError:
                status = "cancelled"
                raise
            finally:
                stats.report(status)
        return run
    return decorate
