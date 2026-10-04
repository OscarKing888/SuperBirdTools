"""Per-photo bird sharpness unit of work for ``app_common`` worker pools.

``BirdSharpnessAction`` analyses one file and writes its sidecar; the browser's
``BrowserWorkPool`` runs many of them concurrently as ``WorkKind.ANALYSIS``.
Model inference is serialised inside :class:`~bird_sharpness.models.BirdSharpnessModels`
while RAW decoding, preprocessing and blur measurement run in parallel.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app_common.file_browser._work_action import WorkerAction

from .analyzer import BirdSharpnessAnalyzer, BirdSharpnessResult

STAGE_QUEUED = "queued"
STAGE_CHECK = "check"
STAGE_WRITE = "write"
from .scoring import ALGORITHM_VERSION, VERDICT_ERROR


@dataclass
class BirdSharpnessOutcome:
    display_path: str
    source_path: str
    result: Optional[BirdSharpnessResult] = None
    written: bool = False
    skipped: bool = False
    cancelled: bool = False


class BirdSharpnessAction(WorkerAction):
    def __init__(
        self,
        analyzer: BirdSharpnessAnalyzer,
        display_path: str,
        source_path: str,
        *,
        write_xmp: bool = True,
        skip_existing: bool = False,
        cancelled: Callable[[], bool] = lambda: False,
        on_stage: Optional[Callable[["BirdSharpnessAction"], None]] = None,
    ):
        super().__init__(cancelled=cancelled)
        self.analyzer = analyzer
        self.display_path = os.path.normpath(display_path)
        self.source_path = os.path.normpath(source_path)
        self.write_xmp = write_xmp
        self.skip_existing = skip_existing
        # Read by the job coordinator to show live per-worker load; plain attribute
        # writes are atomic, so no lock is needed for this best-effort display.
        self.stage = STAGE_QUEUED
        self.started_at: Optional[float] = None
        self._on_stage = on_stage

    def _set_stage(self, stage: str) -> None:
        self.stage = stage
        if self._on_stage is not None:
            self._on_stage(self)

    def execute(self) -> BirdSharpnessOutcome:
        self.started_at = time.monotonic()
        outcome = BirdSharpnessOutcome(self.display_path, self.source_path)
        if self.is_cancelled():
            outcome.cancelled = True
            return outcome
        from .xmp_store import already_analyzed, write_result

        self._set_stage(STAGE_CHECK)
        if self.skip_existing and already_analyzed(self.source_path, ALGORITHM_VERSION):
            outcome.skipped = True
            return outcome
        outcome.result = self.analyzer.analyze(self.source_path, on_stage=self._set_stage, cancelled=self.is_cancelled)
        # A stop request during analysis must not leave a freshly written sidecar behind.
        if self.is_cancelled():
            outcome.cancelled = True
            return outcome
        if self.write_xmp and outcome.result.verdict != VERDICT_ERROR:
            self._set_stage(STAGE_WRITE)
            outcome.written = write_result(self.source_path, outcome.result)
        return outcome


@dataclass
class BirdSharpnessTraceOutcome:
    source_path: str
    trace: Optional[object] = None   # bird_sharpness.trace.AnalysisTrace
    result: Optional[BirdSharpnessResult] = None
    cancelled: bool = False
    error: str = ""
    image_source: str = "raw"
    needs_denoise: bool = False  # SOURCE_DENOISED requested but no denoised image exists yet


class BirdSharpnessTraceAction(WorkerAction):
    """Analyse one photo with a tracer for the step viewer. Read-only: never writes XMP."""

    def __init__(self, analyzer: BirdSharpnessAnalyzer, source_path: str, *,
                 cancelled: Callable[[], bool] = lambda: False, image_source: str = "raw",
                 denoised_lookup: Optional[Callable[[str], object]] = None):
        """``image_source``: ``image_source.SOURCE_*``; ``denoised_lookup(path)`` finds the
        denoised rendering (``None`` when there is none) and runs here, on the worker."""
        super().__init__(cancelled=cancelled)
        self.analyzer = analyzer
        self.source_path = os.path.normpath(source_path)
        self.image_source = image_source
        self.denoised_lookup = denoised_lookup

    def execute(self) -> BirdSharpnessTraceOutcome:
        path, source = self.source_path, self.image_source
        if self.is_cancelled():
            return BirdSharpnessTraceOutcome(path, cancelled=True, image_source=source)
        from .image_source import SOURCE_DENOISED, source_loader
        from .models import check_runtime
        from .trace import AnalysisTracer

        reason = check_runtime()
        if reason:
            return BirdSharpnessTraceOutcome(path, error=reason, image_source=source)
        lookup = self.denoised_lookup
        if source == SOURCE_DENOISED:
            if lookup is None:
                return BirdSharpnessTraceOutcome(path, error="降噪功能不可用", image_source=source)
            found = lookup(path)
            if found is None:
                return BirdSharpnessTraceOutcome(path, needs_denoise=True, image_source=source)
            lookup = lambda _path, found=found: found  # noqa: E731 - resolved once on this worker
        loader = source_loader(source, denoised_lookup=lookup)

        tracer = AnalysisTracer()
        result = self.analyzer.analyze(path, tracer=tracer, cancelled=self.is_cancelled, image_loader=loader)
        if self.is_cancelled():
            return BirdSharpnessTraceOutcome(path, cancelled=True, image_source=source)
        if not result.ok:
            return BirdSharpnessTraceOutcome(path, result=result, error=result.error or "分析失败", image_source=source)
        return BirdSharpnessTraceOutcome(path, trace=tracer.trace, result=result, image_source=source)
