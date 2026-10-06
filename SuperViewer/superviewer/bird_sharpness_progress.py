# -*- coding: utf-8 -*-
"""Progress window for bird sharpness jobs with a live worker-load view.

The job coordinator publishes :class:`WorkerLoad` snapshots (a few per second);
this module only paints them. Colours come from the widget palette at paint time,
so the window follows SuperViewer's light/dark palette without extra wiring.
"""
from __future__ import annotations

import html
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from app_common.bird_sharpness_fields import VERDICT_STYLES
from bird_sharpness.timing import TimingStats, format_seconds

from .qt_compat import QDialog, QHBoxLayout, QLabel, QPushButton, QTimer, QVBoxLayout, QWidget, pyqtSignal

try:
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QPainter, QPalette
    from PyQt6.QtWidgets import QProgressBar, QSizePolicy

    _ROLE_TEXT = QPalette.ColorRole.Text
    _ROLE_WINDOW_TEXT = QPalette.ColorRole.WindowText
    _ROLE_HIGHLIGHT = QPalette.ColorRole.Highlight
    _ROLE_MID = QPalette.ColorRole.Mid
    _ROLE_BASE = QPalette.ColorRole.Base
    _ROLE_PLACEHOLDER = QPalette.ColorRole.PlaceholderText
    _ANTIALIAS = QPainter.RenderHint.Antialiasing
    _ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    _ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    _ELIDE_MIDDLE = Qt.TextElideMode.ElideMiddle
    _NO_PEN = Qt.PenStyle.NoPen
    _RICH_TEXT = Qt.TextFormat.RichText
    _FIXED_HEIGHT = QSizePolicy.Policy.Fixed
    _EXPANDING = QSizePolicy.Policy.Expanding
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import QColor, QFont, QPainter, QPalette
    from PyQt5.QtWidgets import QProgressBar, QSizePolicy

    _ROLE_TEXT = QPalette.Text
    _ROLE_WINDOW_TEXT = QPalette.WindowText
    _ROLE_HIGHLIGHT = QPalette.Highlight
    _ROLE_MID = QPalette.Mid
    _ROLE_BASE = QPalette.Base
    _ROLE_PLACEHOLDER = QPalette.PlaceholderText
    _ANTIALIAS = QPainter.Antialiasing
    _ALIGN_LEFT = Qt.AlignLeft | Qt.AlignVCenter
    _ALIGN_RIGHT = Qt.AlignRight | Qt.AlignVCenter
    _ELIDE_MIDDLE = Qt.ElideMiddle
    _NO_PEN = Qt.NoPen
    _RICH_TEXT = Qt.RichText
    _FIXED_HEIGHT = QSizePolicy.Fixed
    _EXPANDING = QSizePolicy.Expanding


# Stage keys published by bird_sharpness actions -> (label, step index of 4).
STAGE_DISPLAY = {
    "queued": ("等待", 0),
    "check": ("准备", 0),
    "decode": ("解码", 1),
    "detect": ("识别", 2),
    "recheck": ("复检", 2),
    "measure": ("测量", 3),
    "write": ("写入", 4),
}
STAGE_STEPS = 4
# Above this many lanes only the aggregate meter is shown.
MAX_LANES = 8


@dataclass(frozen=True)
class WorkerLane:
    name: str
    stage: str
    started_at: float  # time.monotonic() when the worker picked the photo up


@dataclass(frozen=True)
class WorkerLoad:
    """One snapshot of the job's worker usage, published by the coordinator."""

    capacity: int
    lanes: tuple = ()  # len == capacity; WorkerLane or None per worker slot
    queued: int = 0
    pool_threads: int = 0
    pool_thumbnail_active: int = 0
    pool_metadata_active: int = 0
    shared_pool: bool = True

    @property
    def busy(self) -> int:
        return sum(1 for lane in self.lanes if lane is not None)


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _with_alpha(color: QColor, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


class WorkerLoadView(QWidget):
    """Capacity meter plus one lane per worker: file, stage pips and elapsed time."""

    HEADER_H = 22
    LANE_H = 26
    LANE_GAP = 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._load: Optional[WorkerLoad] = None
        self._finished = False
        self.setSizePolicy(_EXPANDING, _FIXED_HEIGHT)
        self._update_height()

    # ── state ──
    def set_load(self, load: Optional[WorkerLoad]) -> None:
        self._load = load
        self._update_height()
        self.update()

    def set_finished(self) -> None:
        self._finished = True
        if self._load is not None:
            self._load = WorkerLoad(
                capacity=self._load.capacity,
                lanes=tuple(None for _ in range(self._load.capacity)),
                pool_threads=self._load.pool_threads,
                shared_pool=self._load.shared_pool,
            )
        self._update_height()
        self.update()

    def load(self) -> Optional[WorkerLoad]:
        return self._load

    def shows_lanes(self) -> bool:
        return not self._finished and self._load is not None and 0 < self._load.capacity <= MAX_LANES

    def headline(self) -> str:
        load = self._load
        if load is None:
            return "工作线程：准备中"
        if self._finished:
            return f"工作线程：已全部空闲（共 {load.capacity} 个）"
        return f"工作线程：{load.busy} / {load.capacity} 个在工作"

    def _update_height(self) -> None:
        lanes = self._load.capacity if self.shows_lanes() else 0
        height = self.HEADER_H + (lanes * (self.LANE_H + self.LANE_GAP) + 6 if lanes else 4)
        self.setFixedHeight(height)

    # ── painting ──
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        try:
            painter.setRenderHint(_ANTIALIAS)
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        pal = self.palette()
        text = pal.color(_ROLE_WINDOW_TEXT)
        muted = pal.color(_ROLE_PLACEHOLDER)
        accent = pal.color(_ROLE_HIGHLIGHT)
        track = _with_alpha(pal.color(_ROLE_MID), 90)
        width = self.width()
        load = self._load

        # Header: "工作线程：3 / 4 个在工作" + segmented capacity meter on the right.
        header_font = QFont(self.font())
        header_font.setBold(True)
        painter.setFont(header_font)
        painter.setPen(text)
        painter.drawText(QRectF(0, 0, width * 0.55, self.HEADER_H), _ALIGN_LEFT, self.headline())
        if load is not None and load.capacity > 0:
            meter_w = min(220.0, width * 0.42)
            gap = 3.0 if load.capacity <= 16 else 1.0
            seg_w = max(2.0, (meter_w - gap * (load.capacity - 1)) / load.capacity)
            x = width - meter_w
            y = (self.HEADER_H - 10) / 2.0
            painter.setPen(_NO_PEN)
            busy = load.busy
            for i in range(load.capacity):
                painter.setBrush(accent if i < busy else track)
                painter.drawRoundedRect(QRectF(x, y, seg_w, 10), 2.5, 2.5)
                x += seg_w + gap
        if not self.shows_lanes():
            return

        now = time.monotonic()
        # Gentle "breathing" on busy dots so a stuck lane is easy to tell from a live one.
        pulse = 0.65 + 0.35 * abs(((now * 0.9) % 2.0) - 1.0)
        body_font = QFont(self.font())
        small_font = QFont(self.font())
        small_font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.0))
        y = self.HEADER_H + 6
        for index, lane in enumerate(load.lanes):
            rect = QRectF(0, y, width, self.LANE_H)
            if lane is not None:
                painter.setPen(_NO_PEN)
                painter.setBrush(_with_alpha(accent, 26))
            else:
                painter.setPen(_with_alpha(pal.color(_ROLE_MID), 70))
                painter.setBrush(_with_alpha(pal.color(_ROLE_BASE), 120))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            painter.setPen(_NO_PEN)
            cy = y + self.LANE_H / 2.0
            # status dot
            if lane is not None:
                painter.setBrush(_with_alpha(accent, int(255 * pulse)))
            else:
                painter.setBrush(track)
            painter.drawEllipse(QRectF(10, cy - 4, 8, 8))
            painter.setFont(small_font)
            painter.setPen(muted)
            painter.drawText(QRectF(24, y, 48, self.LANE_H), _ALIGN_LEFT, f"线程 {index + 1}")
            if lane is None:
                painter.drawText(QRectF(76, y, width - 86, self.LANE_H), _ALIGN_LEFT, "空闲")
                y += self.LANE_H + self.LANE_GAP
                continue
            stage_label, step = STAGE_DISPLAY.get(lane.stage, (lane.stage, 0))
            elapsed = max(0.0, now - lane.started_at)
            # right block: stage pips + stage label + elapsed
            elapsed_w, label_w = 44.0, 34.0
            pip_w, pip_gap = 14.0, 3.0
            pips_w = STAGE_STEPS * pip_w + (STAGE_STEPS - 1) * pip_gap
            right = width - 10
            painter.drawText(QRectF(right - elapsed_w, y, elapsed_w, self.LANE_H), _ALIGN_RIGHT, f"{elapsed:4.1f}s")
            right -= elapsed_w + 8
            painter.setPen(text)
            painter.drawText(QRectF(right - label_w, y, label_w, self.LANE_H), _ALIGN_LEFT, stage_label)
            right -= label_w + 6
            px = right - pips_w
            painter.setPen(_NO_PEN)
            for s in range(STAGE_STEPS):
                if s < step - 1:
                    painter.setBrush(accent)
                elif s == step - 1:
                    painter.setBrush(_with_alpha(accent, int(255 * pulse)))
                else:
                    painter.setBrush(track)
                painter.drawRoundedRect(QRectF(px, cy - 3, pip_w, 6), 3, 3)
                px += pip_w + pip_gap
            # file name in the remaining middle space
            painter.setFont(body_font)
            painter.setPen(text)
            name_left = 76.0
            name_w = max(10.0, right - pips_w - 12 - name_left)
            shown = painter.fontMetrics().elidedText(lane.name, _ELIDE_MIDDLE, int(name_w))
            painter.drawText(QRectF(name_left, y, name_w, self.LANE_H), _ALIGN_LEFT, shown)
            y += self.LANE_H + self.LANE_GAP


class BirdSharpnessProgressDialog(QDialog):
    """Non-modal progress window; closing it only requests cancellation.

    Also reused by other SuperViewer batch jobs (e.g. burst info): the worker
    panel stays hidden until a job publishes :class:`WorkerLoad`, and
    ``set_summary()`` accepts a plain-text summary instead of verdict counts.
    """

    cancel_requested = pyqtSignal()

    def __init__(self, parent, title: str, *, running_text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumWidth(560)
        self._running = True
        self._started_at: Optional[float] = None
        self._finished_at: Optional[float] = None
        self._done = 0
        self._total = 0
        self._summary_plain = ""
        self._running_text = running_text

        self.label = QLabel("正在准备…", self)
        headline = QFont(self.label.font())
        headline.setPointSizeF(headline.pointSizeF() + 1.5)
        headline.setBold(True)
        self.label.setFont(headline)
        self.label.setWordWrap(True)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%v / %m（%p%）")

        self.stats = QLabel("", self)
        self._last_name = ""
        self.warning = QLabel("", self)
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #c99a06;")
        self.warning.setVisible(False)

        self.load_view = WorkerLoadView(self)
        self.load_view.setVisible(False)
        self.pool_note = QLabel("", self)
        self.pool_note.setWordWrap(True)
        self.pool_note.setVisible(False)

        self.timing = QLabel("", self)  # per-stage timing, see set_timing()
        self.timing.setTextFormat(_RICH_TEXT)
        self.timing.setWordWrap(True)
        self.timing.setVisible(False)
        self._timing_stats: Optional[TimingStats] = None
        self._timing_plain = ""

        self.summary = QLabel("", self)
        self.summary.setTextFormat(_RICH_TEXT)
        self.summary.setWordWrap(True)

        self.button = QPushButton("停止", self)
        self.button.clicked.connect(self._on_button)
        for muted in (self.stats, self.pool_note):
            muted.setForegroundRole(_ROLE_PLACEHOLDER)

        row = QHBoxLayout()
        row.addWidget(self.summary, 1)
        row.addWidget(self.button)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addWidget(self.stats)
        layout.addWidget(self.warning)
        layout.addSpacing(4)
        layout.addWidget(self.load_view)
        layout.addWidget(self.pool_note)
        layout.addSpacing(4)
        layout.addWidget(self.timing)
        layout.addSpacing(4)
        layout.addLayout(row)

        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # ── user actions ──
    def _on_button(self) -> None:
        if self._running:
            self.button.setEnabled(False)
            self.button.setText("正在停止…")
            self.cancel_requested.emit()
        else:
            self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._running:
            self.cancel_requested.emit()
        self._tick.stop()
        super().closeEvent(event)

    # ── updates from the controller ──
    def set_status(self, text: str) -> None:
        self.label.setText(text)

    def set_progress(self, done: int, total: int, name: str) -> None:
        if self._started_at is None:
            self._started_at = time.monotonic()
            if self._running_text:
                self.label.setText(self._running_text)
        self._done, self._total = done, total
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        if name:
            self._last_name = name
        self._refresh_stats()

    def set_load(self, load: WorkerLoad) -> None:
        self.load_view.setVisible(True)
        self.load_view.set_load(load)
        note = self._pool_note(load)
        self.pool_note.setText(note)
        self.pool_note.setVisible(bool(note))

    def set_timing(self, stats: TimingStats) -> None:
        """Show the job's per-stage timing (work time summed over all workers, mean, longest)."""
        self._timing_stats = stats
        self._refresh_timing()

    def elapsed_seconds(self) -> Optional[float]:
        """Wall-clock seconds since the first photo started (until the job finished)."""
        if self._started_at is None:
            return None
        return max(0.0, (self._finished_at or time.monotonic()) - self._started_at)

    def timing_text(self) -> str:
        """The timing block as plain text (what the label shows)."""
        return self._timing_plain

    def _refresh_timing(self) -> None:
        stats = self._timing_stats
        if stats is None or not (stats.photos or stats.skipped):
            self.timing.setVisible(False)
            self._timing_plain = ""
            return
        wall = self.elapsed_seconds()
        self._timing_plain = "\n".join(stats.lines(wall))
        self.timing.setText(self._timing_html(stats, wall))
        self.timing.setVisible(True)

    @staticmethod
    def _timing_html(stats: TimingStats, wall: Optional[float]) -> str:
        head = [f"总用时 <b>{format_seconds(wall)}</b>" if wall is not None else "",
                f"累计处理 <b>{format_seconds(stats.work_s)}</b>（各线程相加）",
                f"平均每张 <b>{format_seconds(stats.mean_photo_s)}</b>" if stats.photos else ""]
        per_photo = stats.wall_per_photo(wall) if wall is not None else None
        if per_photo is not None:
            head.append(f"并行后每张 <b>{format_seconds(per_photo)}</b>（约 ×{stats.speedup(wall):.1f} 并行）")
        if stats.setup_s is not None:
            head.append(f"加载模型 {format_seconds(stats.setup_s)}")
        rows = ['<tr style="color:gray"><td>阶段</td><td align="right">次数</td><td align="right">累计</td>'
                '<td align="right">平均</td><td align="right">最长</td><td align="right">占比</td><td></td></tr>']
        for _key, label, s, share in stats.rows():
            bar = "▇" * max(1, int(round(share * 14))) if share > 0 else ""
            rows.append(
                f"<tr><td>{html.escape(label)}</td><td align='right'>{s.count}</td>"
                f"<td align='right'>{format_seconds(s.total_s)}</td><td align='right'>{format_seconds(s.mean_s)}</td>"
                f"<td align='right'>{format_seconds(s.max_s)}</td><td align='right'>{share * 100:.0f}%</td>"
                f"<td>&nbsp;<span style='color:#4a90d9'>{bar}</span></td></tr>")
        slowest = ""
        if stats.slowest is not None and stats.photos > 1:
            import os

            slowest = f"<br>最慢 {html.escape(os.path.basename(stats.slowest[0]))}  {format_seconds(stats.slowest[1])}"
        return ("<b>计时</b>　" + "　·　".join(h for h in head if h) + "<table cellspacing='4'>" + "".join(rows)
                + "</table>" + slowest)

    def set_counts(self, counts: Counter, skipped: int, write_failures: int) -> None:
        chips, plain = [], []
        for verdict, style in VERDICT_STYLES.items():
            n = counts.get(verdict, 0)
            if n:
                chips.append(f'<span style="color:{style.color}">●</span> {html.escape(style.label)} <b>{n}</b>')
                plain.append(f"{style.label} {n}")
        if skipped:
            chips.append(f"已跳过 <b>{skipped}</b>")
            plain.append(f"已跳过 {skipped}")
        if write_failures:
            chips.append(f'<span style="color:#d64545">XMP 写入失败 <b>{write_failures}</b></span>')
            plain.append(f"XMP 写入失败 {write_failures}")
        self._summary_plain = "，".join(plain)
        self.summary.setText("&nbsp;&nbsp;&nbsp;".join(chips))

    def set_warning(self, text: str) -> None:
        self.warning.setText(f"⚠ {text}" if text else "")
        self.warning.setVisible(bool(text))

    def set_summary(self, text: str) -> None:
        """Plain-text summary for jobs without verdict counts."""
        self._summary_plain = text
        self.summary.setText(html.escape(text))

    def summary_text(self) -> str:
        return self._summary_plain

    def mark_finished(self, text: str) -> None:
        self._running = False
        self._finished_at = time.monotonic()
        self.label.setText(text)
        self.button.setEnabled(True)
        self.button.setText("关闭")
        self.load_view.set_finished()
        self.pool_note.setText("")
        self.pool_note.setVisible(False)
        self._refresh_stats()
        self._refresh_timing()
        self._tick.stop()
        self.adjustSize()

    # ── derived text ──
    @staticmethod
    def _pool_note(load: WorkerLoad) -> str:
        if not load.shared_pool:
            return "顺序检测（未使用共享线程池）"
        parts = []
        if load.queued:
            parts.append(f"排队 {load.queued} 张")
        others = load.pool_thumbnail_active + load.pool_metadata_active
        parts.append(
            f"共享线程池 {load.pool_threads} 线程：缩略图 {load.pool_thumbnail_active} · 元数据 {load.pool_metadata_active}"
        )
        if load.queued and load.busy < load.capacity and others:
            parts.append("浏览优先，空闲后自动补满")
        return "　·　".join(parts)

    def _refresh_stats(self) -> None:
        if self._started_at is None:
            self.stats.setText("")
            return
        end = self._finished_at or time.monotonic()
        elapsed = max(0.001, end - self._started_at)
        parts = [f"已用 {_format_duration(elapsed)}"]
        if self._done:
            rate = self._done / elapsed
            parts.append(f"速度 {rate * 60:.0f} 张/分")
            if self._running and self._total > self._done:
                parts.append(f"剩余约 {_format_duration((self._total - self._done) / rate)}")
        text = "　·　".join(parts)
        if self._last_name and self._running:
            text = f"{text}　·　最近完成 {self._last_name}"
        self.stats.setText(text)

    def _on_tick(self) -> None:
        if not self._running:
            return
        self._refresh_stats()
        self._refresh_timing()
        self.load_view.update()
