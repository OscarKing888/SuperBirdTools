"""Per-stage timing of bird sharpness analyses, for single photos and parallel batches.

No Qt and no third-party imports, so the analyzer, the CLI and the SuperViewer progress
window share one definition of the stages and of how they are totalled.

A photo passes through the stages of :data:`STAGES` in order; ``recheck`` only runs when the
first pass found no bird, ``check`` / ``write`` belong to the batch action (version check,
sidecar write). :class:`StageClock` records one photo's stages; :class:`TimingStats` totals
many photos, which may have been processed concurrently: stage sums are *work* time across
all workers, so they exceed the wall clock by about the parallelism (:meth:`TimingStats.speedup`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

STAGE_CHECK = "check"
STAGE_DECODE = "decode"
STAGE_DETECT = "detect"
STAGE_RECHECK = "recheck"
STAGE_MEASURE = "measure"
STAGE_WRITE = "write"

# Order of a photo's life and the label shown for each stage.
STAGES: Tuple[str, ...] = (STAGE_CHECK, STAGE_DECODE, STAGE_DETECT, STAGE_RECHECK, STAGE_MEASURE, STAGE_WRITE)
STAGE_LABELS: Dict[str, str] = {
    STAGE_CHECK: "准备",
    STAGE_DECODE: "解码",
    STAGE_DETECT: "识别",
    STAGE_RECHECK: "复检/增强找鸟",
    STAGE_MEASURE: "测量",
    STAGE_WRITE: "写入",
}


def format_seconds(seconds: Optional[float]) -> str:
    """``0.42 s`` / ``12.3 s`` / ``1:05`` / ``1:02:03``; ``—`` when unknown."""
    if seconds is None:
        return "—"
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        return f"{seconds:.2f} s"
    if seconds < 60:
        return f"{seconds:.1f} s"
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def stage_summary(stages: Dict[str, float], total_s: Optional[float] = None, *, sep: str = " · ") -> str:
    """``解码 0.40 s · 识别 0.31 s · 测量 1.20 s · 合计 1.91 s`` for one photo (stages in pipeline order)."""
    parts = [f"{STAGE_LABELS.get(k, k)} {format_seconds(stages[k])}" for k in STAGES if k in stages]
    parts += [f"{k} {format_seconds(v)}" for k, v in stages.items() if k not in STAGES]
    if total_s is not None:
        parts.append(f"合计 {format_seconds(total_s)}")
    return sep.join(parts)


def stats_of_results(results) -> "TimingStats":
    """Totals of analyzer results (``stage_s`` / ``elapsed_s``), for callers without actions (CLI)."""
    stats = TimingStats()
    for r in results:
        stats.add(PhotoTiming(getattr(r, "path", ""), dict(getattr(r, "stage_s", None) or {}),
                              float(getattr(r, "elapsed_s", 0.0) or 0.0), failed=not getattr(r, "ok", True)))
    return stats


class StageClock:
    """Times consecutive stages of one photo; ``enter()`` closes the previous stage.

    One clock per analysis (never shared between threads). A stage entered twice adds up.
    """

    def __init__(self, now: Callable[[], float] = time.perf_counter) -> None:
        self._now = now
        self._started = now()
        self._stage: Optional[str] = None
        self._since = self._started
        self.stages: Dict[str, float] = {}

    def enter(self, stage: str) -> None:
        self._close()
        self._stage = stage

    def _close(self) -> None:
        t = self._now()
        if self._stage is not None:
            self.stages[self._stage] = self.stages.get(self._stage, 0.0) + (t - self._since)
        self._since = t

    def finish(self) -> Dict[str, float]:
        """Close the running stage and return ``{stage: seconds}`` (rounded to ms)."""
        self._close()
        self._stage = None
        return {k: round(v, 3) for k, v in self.stages.items()}

    @property
    def elapsed(self) -> float:
        return self._now() - self._started


@dataclass(frozen=True)
class PhotoTiming:
    """What one photo cost: seconds per stage and the photo's own total (its work time)."""

    path: str
    stages: Dict[str, float] = field(default_factory=dict)
    total_s: float = 0.0
    skipped: bool = False  # only the check ran: the sidecar already had this version's result
    failed: bool = False   # the analysis raised or reported an error


@dataclass
class StageTotals:
    count: int = 0
    total_s: float = 0.0
    max_s: float = 0.0

    @property
    def mean_s(self) -> Optional[float]:
        return self.total_s / self.count if self.count else None


class TimingStats:
    """Totals over the photos of one job.

    ``photos`` counts analysed photos (skipped ones only add their ``check`` time).
    Per stage: how many photos ran it, total work time, mean, longest. ``total`` is the
    per-photo total, so ``mean_photo_s`` is what one photo costs one worker and
    ``wall_per_photo`` (``wall / photos``) is what the user waits per photo in parallel.
    """

    def __init__(self) -> None:
        self.stages: Dict[str, StageTotals] = {}
        self.photo = StageTotals()
        self.skipped = 0
        self.failed = 0
        self.setup_s: Optional[float] = None  # model loading before the first photo
        self.slowest: Optional[Tuple[str, float]] = None

    def add(self, timing: PhotoTiming) -> None:
        for stage, seconds in timing.stages.items():
            s = self.stages.setdefault(stage, StageTotals())
            s.count += 1
            s.total_s += seconds
            s.max_s = max(s.max_s, seconds)
        if timing.skipped:
            self.skipped += 1
            return
        if timing.failed:
            self.failed += 1
        p = self.photo
        p.count += 1
        p.total_s += timing.total_s
        if timing.total_s >= p.max_s:
            p.max_s = timing.total_s
            self.slowest = (timing.path, timing.total_s)

    @property
    def photos(self) -> int:
        return self.photo.count

    @property
    def work_s(self) -> float:
        """Summed work time of every analysed photo (all workers together)."""
        return self.photo.total_s

    @property
    def mean_photo_s(self) -> Optional[float]:
        return self.photo.mean_s

    def wall_per_photo(self, wall_s: float) -> Optional[float]:
        return wall_s / self.photo.count if self.photo.count and wall_s > 0 else None

    def speedup(self, wall_s: float) -> Optional[float]:
        """Work time over wall time: about how many workers were really busy."""
        return self.photo.total_s / wall_s if self.photo.count and wall_s > 0 else None

    def share(self, stage: str) -> float:
        """Fraction (0..1) of all analysed photos' work time that ``stage`` took."""
        total = sum(s.total_s for s in self.stages.values())
        s = self.stages.get(stage)
        return (s.total_s / total) if s and total > 0 else 0.0

    def rows(self) -> List[Tuple[str, str, StageTotals, float]]:
        """``(key, label, totals, share)`` for the stages that ran, in pipeline order."""
        return [(k, STAGE_LABELS.get(k, k), self.stages[k], self.share(k)) for k in STAGES if k in self.stages]

    def lines(self, wall_s: Optional[float] = None) -> List[str]:
        """Plain-text summary (CLI, logs, tests, the progress window's copy-able text)."""
        out: List[str] = []
        head = [f"{self.photos} 张"]
        if self.skipped:
            head.append(f"跳过 {self.skipped} 张")
        if self.failed:
            head.append(f"失败 {self.failed} 张")
        if wall_s is not None:
            head.append(f"总用时 {format_seconds(wall_s)}")
        head.append(f"累计处理 {format_seconds(self.work_s)}")
        if self.mean_photo_s is not None:
            head.append(f"平均每张 {format_seconds(self.mean_photo_s)}")
        if wall_s is not None and self.wall_per_photo(wall_s) is not None:
            head.append(f"并行后每张 {format_seconds(self.wall_per_photo(wall_s))}（×{self.speedup(wall_s):.1f}）")
        out.append("，".join(head))
        if self.setup_s is not None:
            out.append(f"加载模型 {format_seconds(self.setup_s)}")
        for _key, label, s, share in self.rows():
            out.append(f"{label}：{s.count} 次，累计 {format_seconds(s.total_s)}，平均 {format_seconds(s.mean_s)}，"
                       f"最长 {format_seconds(s.max_s)}，占 {share * 100:.0f}%")
        return out
