# -*- coding: utf-8 -*-
"""SuperViewer 目录右键「计算连拍信息」。

按拍摄时间计算连拍分组（:mod:`app_common.burst_info`，与 SuperPicky 规则一致），
写入同 stem XMP sidecar 的 ``XMP-superpicky:burst_id`` / ``burst_position``，
随后批量刷新文件列表的「连拍」列、连拍分组底框和缩略图。只读 EXIF 时间与 sidecar，
不解码图像。同一时间只运行一个任务；关闭窗口时请求停止并等待线程真正结束。
"""
from __future__ import annotations

import os
import threading
import traceback
from dataclasses import dataclass

from app_common import burst_info
from app_common.log import get_logger

from .bird_sharpness_controller import BirdSharpnessProgressDialog
from .qt_compat import QDialog, QDialogButtonBox, QLabel, QSpinBox, QThread, QVBoxLayout, pyqtSignal

try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import QFormLayout
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QObject
    from PyQt5.QtWidgets import QFormLayout

_log = get_logger("burst_info.viewer")

_READ_CHUNK = 200


@dataclass
class BurstInfoJob:
    title: str
    directory: str
    recursive: bool = False
    max_gap_ms: int = burst_info.DEFAULT_MAX_GAP_MS
    min_count: int = burst_info.DEFAULT_MIN_COUNT


class BurstInfoWorker(QThread):
    """扫描目录 → 批量读拍摄时间 → 分组 → 写 sidecar；不触碰任何控件。"""

    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int, str)
    shots_written = pyqtSignal(object)  # {path: meta_updates}
    planned = pyqtSignal(object)  # BurstPlanStats
    failed = pyqtSignal(str)

    def __init__(self, job: BurstInfoJob) -> None:
        super().__init__()
        self.job = job
        self._cancel = threading.Event()
        self.written = 0
        self.write_failures = 0

    def stop(self) -> None:
        self._cancel.set()
        self.requestInterruption()

    def _cancelled(self) -> bool:
        return self._cancel.is_set() or self.isInterruptionRequested()

    def _scan(self) -> list[str]:
        self.status_changed.emit("正在扫描目录…")
        return burst_info.collect_directory_images(self.job.directory, recursive=self.job.recursive)

    def _read_capture_times(self, paths: list[str]) -> dict:
        times: dict = {}
        total = len(paths)
        self.status_changed.emit("正在读取拍摄时间…")
        for start in range(0, total, _READ_CHUNK):
            if self._cancelled():
                break
            chunk = paths[start:start + _READ_CHUNK]
            try:
                times.update(burst_info.read_capture_times(chunk, cancel_event=self._cancel))
            except Exception:
                if self._cancelled():
                    break
                _log.error("[BurstInfo] capture time read failed: %s", traceback.format_exc())
            done = min(total, start + len(chunk))
            self.progress_changed.emit(done, total, f"读取拍摄时间 {os.path.basename(chunk[-1])}")
        return times

    def run(self) -> None:
        try:
            paths = self._scan()
            if not paths:
                self.failed.emit("没有找到图片。")
                return
            times = self._read_capture_times(paths)
            if self._cancelled():
                return
            plan, stats = burst_info.plan_bursts(
                paths, times, max_gap_ms=self.job.max_gap_ms, min_count=self.job.min_count,
            )
            self.planned.emit(stats)
            if not stats.timed_shots:
                self.failed.emit("照片缺少拍摄时间（DateTimeOriginal），无法计算连拍。")
                return
            self.status_changed.emit("正在写入 XMP…")
            pending: dict = {}

            def flush() -> None:
                if pending:
                    self.shots_written.emit(dict(pending))
                    pending.clear()

            def on_outcome(outcome, done: int, total: int) -> None:
                if outcome.fields:
                    if outcome.written:
                        self.written += 1
                        updates = burst_info.browser_meta_updates(outcome.fields)
                        for path in outcome.paths:
                            pending[path] = updates
                    else:
                        self.write_failures += 1
                        _log.warning("[BurstInfo] XMP write failed path=%r", outcome.paths[0])
                if len(pending) >= _READ_CHUNK or done == total:
                    flush()
                if done == total or done % 20 == 0:
                    self.progress_changed.emit(done, total, f"写入 {os.path.basename(outcome.paths[0])}")

            burst_info.write_burst_plan(plan, cancelled=self._cancelled, on_outcome=on_outcome)
            flush()
        except Exception as exc:
            _log.error("[BurstInfo] job failed: %s", traceback.format_exc())
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class BurstInfoOptionsDialog(QDialog):
    def __init__(self, parent, directory: str, recursive: bool, max_gap_ms: int, min_count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("计算连拍信息")
        self.setMinimumWidth(420)
        scope = "本目录及子目录（每个目录单独编号）" if recursive else "本目录"
        intro = QLabel(
            f"{directory}\n范围：{scope}\n\n"
            "按拍摄时间（含亚秒）排序，相邻间隔不超过阈值且张数达到下限的照片归为一组。"
            "结果写入同名 XMP sidecar（XMP-superpicky:burst_id / burst_position），不修改原图；"
            "已有的连拍信息会被重新计算覆盖。",
            self,
        )
        intro.setWordWrap(True)
        self.gap_spin = QSpinBox(self)
        self.gap_spin.setRange(10, 5000)
        self.gap_spin.setSingleStep(10)
        self.gap_spin.setSuffix(" 毫秒")
        self.gap_spin.setValue(int(max_gap_ms))
        self.gap_spin.setToolTip("连拍速度不低于 1000/阈值 张每秒。无亚秒时间的照片按 1 秒容差比较。")
        self.count_spin = QSpinBox(self)
        self.count_spin.setRange(2, 1000)
        self.count_spin.setSuffix(" 张")
        self.count_spin.setValue(int(min_count))
        form = QFormLayout()
        form.addRow("相邻最大间隔", self.gap_spin)
        form.addRow("每组最少张数", self.count_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始计算")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple[int, int]:
        return int(self.gap_spin.value()), int(self.count_spin.value())


def _summary_text(stats, written: int, write_failures: int) -> str:
    if stats is None:
        return ""
    parts = [f"连拍组 {stats.groups}", f"连拍照片 {stats.burst_shots}/{stats.shots}"]
    missing = stats.shots - stats.timed_shots
    if missing:
        parts.append(f"无拍摄时间 {missing}")
    parts.append(f"更新 XMP {written}")
    if write_failures:
        parts.append(f"XMP 写入失败 {write_failures}")
    return "，".join(parts)


class BurstInfoController(QObject):
    """目录树右键菜单入口，拥有唯一运行中的任务。"""

    def __init__(self, main_window, file_list, dir_browser=None) -> None:
        super().__init__(main_window)
        self._main = main_window
        self._file_list = file_list
        self._worker: BurstInfoWorker | None = None
        self._dialog: BirdSharpnessProgressDialog | None = None
        self._shutdown_requested = False
        self._stats = None
        self._failure_message = ""
        self.max_gap_ms = burst_info.DEFAULT_MAX_GAP_MS
        self.min_count = burst_info.DEFAULT_MIN_COUNT
        if dir_browser is not None:
            dir_browser.add_context_menu_extender(self.extend_directory_menu)

    @property
    def busy(self) -> bool:
        return self._worker is not None

    # ── menus ─────────────────────────────────────────────────────────────
    def extend_directory_menu(self, menu, directory: str) -> None:
        sub = menu.addMenu("计算连拍信息")
        if self.busy:
            act = sub.addAction("停止连拍计算")
            act.triggered.connect(lambda checked=False: self.stop())
            return
        for text, recursive in (("计算本目录…", False), ("计算本目录及子目录…", True)):
            act = sub.addAction(text)
            act.triggered.connect(lambda checked=False, r=recursive: self.prompt_and_start(directory, r))

    def prompt_and_start(self, directory: str, recursive: bool) -> bool:
        dialog = BurstInfoOptionsDialog(self._main, directory, recursive, self.max_gap_ms, self.min_count)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.max_gap_ms, self.min_count = dialog.values()
        name = os.path.basename(directory.rstrip("\\/")) or directory
        return self.start(BurstInfoJob(
            title=f"计算连拍信息 - {name}", directory=directory, recursive=recursive,
            max_gap_ms=self.max_gap_ms, min_count=self.min_count,
        ))

    # ── job lifecycle ─────────────────────────────────────────────────────
    def start(self, job: BurstInfoJob) -> bool:
        if self._shutdown_requested:
            return False
        if self.busy:
            self._show_message("已有连拍计算任务在运行。")
            return False
        self._stats = None
        self._failure_message = ""
        worker = BurstInfoWorker(job)
        self._worker = worker
        dialog = BirdSharpnessProgressDialog(self._main, job.title)
        self._dialog = dialog
        dialog.cancel_requested.connect(lambda w=worker: self._request_stop(w))
        worker.status_changed.connect(lambda text, w=worker: self._on_status(w, text))
        worker.progress_changed.connect(lambda d, t, n, w=worker: self._on_progress(w, d, t, n))
        worker.planned.connect(lambda stats, w=worker: self._on_planned(w, stats))
        worker.shots_written.connect(lambda updates, w=worker: self._on_written(w, updates))
        worker.failed.connect(lambda msg, w=worker: self._on_failed(w, msg))
        worker.finished.connect(lambda w=worker: self._on_thread_finished(w))
        _log.info("[BurstInfo] start dir=%r recursive=%s max_gap_ms=%s min_count=%s",
                  job.directory, job.recursive, job.max_gap_ms, job.min_count)
        dialog.show()
        worker.start()
        return True

    def stop(self) -> None:
        if self._worker is not None:
            self._request_stop(self._worker)

    def _request_stop(self, worker: BurstInfoWorker) -> None:
        if worker is self._worker:
            worker.stop()

    def _on_status(self, worker, text: str) -> None:
        if worker is self._worker and self._dialog is not None:
            self._dialog.set_status(text)

    def _on_progress(self, worker, done: int, total: int, name: str) -> None:
        if worker is self._worker and self._dialog is not None:
            self._dialog.set_progress(done, total, name)

    def _on_planned(self, worker, stats) -> None:
        if worker is self._worker:
            self._stats = stats
            if self._dialog is not None:
                self._dialog.set_summary(_summary_text(stats, 0, 0))

    def _on_written(self, worker, updates: dict) -> None:
        if worker is not self._worker or self._shutdown_requested:
            return
        listed = {path: meta for path, meta in (updates or {}).items() if self._is_listed(path)}
        if listed:
            try:
                batch = getattr(self._file_list, "sync_metadata_edits_for_paths", None)
                if callable(batch):
                    batch(listed)
                else:
                    for path, meta in listed.items():
                        self._file_list.sync_metadata_edit_for_path(path, meta_updates=meta)
            except Exception:
                _log.error("[BurstInfo] list refresh failed: %s", traceback.format_exc())
        if self._dialog is not None:
            self._dialog.set_summary(_summary_text(self._stats, worker.written, worker.write_failures))

    def _is_listed(self, path: str) -> bool:
        """只刷新当前显示目录中的行；其它目录打开时会重新读取 XMP。"""
        model = getattr(self._file_list, "_file_table_model", None)
        row_for_path = getattr(model, "row_for_path", None)
        if not callable(row_for_path):
            return True
        try:
            return row_for_path(path) is not None
        except Exception:
            return True

    def _on_failed(self, worker, message: str) -> None:
        if worker is self._worker:
            self._failure_message = message

    def _on_thread_finished(self, worker) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        cancelled = worker._cancelled()
        dialog = self._dialog
        summary = _summary_text(self._stats, worker.written, worker.write_failures)
        _log.info("[BurstInfo] job finished cancelled=%s summary=%s failure=%r",
                  cancelled, summary, self._failure_message)
        try:
            worker.deleteLater()
        except Exception:
            pass
        if self._shutdown_requested:
            if dialog is not None:
                dialog._running = False
                dialog.close()
            self._dialog = None
            return
        if dialog is not None:
            if self._failure_message:
                dialog.mark_finished(f"计算未完成：{self._failure_message}")
            elif cancelled:
                dialog.mark_finished("已停止。")
            else:
                dialog.mark_finished("连拍信息计算完成。")
            dialog.set_summary(summary)

    # ── shutdown ──────────────────────────────────────────────────────────
    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        if self._worker is not None:
            self._worker.stop()

    def is_shutdown_done(self) -> bool:
        return self._worker is None

    def _show_message(self, text: str) -> None:
        from .qt_compat import QMessageBox

        QMessageBox.information(self._main, "计算连拍信息", text)
