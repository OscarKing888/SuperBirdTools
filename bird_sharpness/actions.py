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
        outcome.result = self.analyzer.analyze(self.source_path, on_stage=self._set_stage)
        # A stop request during analysis must not leave a freshly written sidecar behind.
        if self.is_cancelled():
            outcome.cancelled = True
            return outcome
        if self.write_xmp and outcome.result.verdict != VERDICT_ERROR:
            self._set_stage(STAGE_WRITE)
            outcome.written = write_result(self.source_path, outcome.result)
        return outcome
