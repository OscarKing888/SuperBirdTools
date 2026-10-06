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

from .scoring import ALGORITHM_VERSION, VERDICT_ERROR
from .timing import STAGE_CHECK, STAGE_WRITE, PhotoTiming, StageClock

STAGE_QUEUED = "queued"


@dataclass
class BirdSharpnessOutcome:
    display_path: str
    source_path: str
    result: Optional[BirdSharpnessResult] = None
    written: bool = False
    skipped: bool = False
    cancelled: bool = False
    timing: Optional[PhotoTiming] = None  # per-stage seconds of this photo (not set when cancelled before starting)


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

        clock = StageClock()
        self._set_stage(STAGE_CHECK)
        clock.enter(STAGE_CHECK)
        version = getattr(self.analyzer, "version", ALGORITHM_VERSION)
        if self.skip_existing and already_analyzed(self.source_path, version):
            outcome.skipped = True
            stages = clock.finish()
            outcome.timing = PhotoTiming(self.source_path, stages, round(sum(stages.values()), 3), skipped=True)
            return outcome
        checked = clock.finish()
        outcome.result = self.analyzer.analyze(self.source_path, on_stage=self._set_stage, cancelled=self.is_cancelled)
        # A stop request during analysis must not leave a freshly written sidecar behind.
        if self.is_cancelled():
            outcome.cancelled = True
            return outcome
        write_s = 0.0
        if self.write_xmp and outcome.result.verdict != VERDICT_ERROR:
            self._set_stage(STAGE_WRITE)
            t = time.perf_counter()
            outcome.written = write_result(self.source_path, outcome.result)
            write_s = time.perf_counter() - t
        stages = dict(checked)
        for stage, seconds in (getattr(outcome.result, "stage_s", None) or {}).items():
            stages[stage] = stages.get(stage, 0.0) + seconds
        if write_s or self.write_xmp:
            stages[STAGE_WRITE] = round(write_s, 3)
        outcome.timing = PhotoTiming(self.source_path, stages, round(sum(stages.values()), 3),
                                     failed=outcome.result.verdict == VERDICT_ERROR)
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
                 denoised_lookup: Optional[Callable[[str], object]] = None, image_cache=None,
                 given_input=None):
        """``image_source``: ``image_source.SOURCE_*``; ``denoised_lookup(path)`` finds the
        denoised rendering (``None`` when there is none) and runs here, on the worker.
        ``image_cache`` (``image_source.DecodedImageCache``, one per trace window) reuses
        the decoded image when the same window recomputes with other parameters.
        ``given_input``: ``(AnalysisImage, analyzer.GivenBirds)`` — measure these birds on this
        image (the model chain's 测清晰度) instead of decoding and detecting."""
        super().__init__(cancelled=cancelled)
        self.analyzer = analyzer
        self.source_path = os.path.normpath(source_path)
        self.image_source = image_source
        self.denoised_lookup = denoised_lookup
        self.image_cache = image_cache
        self.given_input = given_input

    def execute(self) -> BirdSharpnessTraceOutcome:
        path, source = self.source_path, self.image_source
        if self.is_cancelled():
            return BirdSharpnessTraceOutcome(path, cancelled=True, image_source=source)
        if self.given_input is not None:
            return self._execute_given()
        from .image_source import SOURCE_DENOISED, source_loader
        from .models import check_runtime
        from .trace import AnalysisTracer

        reason = check_runtime()
        if reason:
            return BirdSharpnessTraceOutcome(path, error=reason, image_source=source)
        lookup = self.denoised_lookup
        found = None
        if source == SOURCE_DENOISED:
            if lookup is None:
                return BirdSharpnessTraceOutcome(path, error="降噪功能不可用", image_source=source)
            found = lookup(path)
            if found is None:
                return BirdSharpnessTraceOutcome(path, needs_denoise=True, image_source=source)
            lookup = lambda _path, found=found: found  # noqa: E731 - resolved once on this worker
        loader = source_loader(source, denoised_lookup=lookup)

        tracer = AnalysisTracer()
        if self.image_cache is not None:
            from . import analyzer as analyzer_mod
            from .image_source import DecodedImageCache

            key = DecodedImageCache.key(path, source, getattr(found, "path", None))
            base = loader or (lambda p: analyzer_mod.load_analysis_image(p))  # RAW decode (late-bound)

            def loader(p, base=base, key=key):
                image, reused = self.image_cache.get_or_load(key, lambda: base(p))
                tracer.decode_reused = reused
                return image

        result = self.analyzer.analyze(path, tracer=tracer, cancelled=self.is_cancelled, image_loader=loader)
        if self.is_cancelled():
            return BirdSharpnessTraceOutcome(path, cancelled=True, image_source=source)
        if not result.ok:
            return BirdSharpnessTraceOutcome(path, result=result, error=result.error or "分析失败", image_source=source)
        return BirdSharpnessTraceOutcome(path, trace=tracer.trace, result=result, image_source=source)

    def _execute_given(self) -> BirdSharpnessTraceOutcome:
        from .models import check_runtime
        from .trace import AnalysisTracer

        path, source = self.source_path, self.image_source
        reason = check_runtime()
        if reason:
            return BirdSharpnessTraceOutcome(path, error=reason, image_source=source)
        image, given = self.given_input
        tracer = AnalysisTracer()
        tracer.decode_note = f"临时图：{given.label}" if given.label else "临时图"
        tracer.decode_filled = bool(getattr(given, "filled", False))
        result = self.analyzer.analyze(path, tracer=tracer, cancelled=self.is_cancelled,
                                       image_loader=lambda _p: image, given=given)
        if self.is_cancelled():
            return BirdSharpnessTraceOutcome(path, cancelled=True, image_source=source)
        if not result.ok:
            return BirdSharpnessTraceOutcome(path, result=result, error=result.error or "分析失败", image_source=source)
        return BirdSharpnessTraceOutcome(path, trace=tracer.trace, result=result, image_source=source)
