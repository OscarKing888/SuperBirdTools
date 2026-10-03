# -*- coding: utf-8 -*-
"""SuperViewer integration of bird sharpness detection.

Directory-tree and file-list context menus start a background job that runs
``bird_sharpness`` on each photo (one ``BirdSharpnessAction`` per photo on the
browser's shared worker pool, several photos in parallel), writes the result to
the same-stem XMP sidecar and refreshes the affected list/thumbnail rows. Only one
job runs at a time; its coordinator is owned until its real ``QThread.finished``
and shutdown is latched.
"""
from __future__ import annotations

import os
import traceback
import threading
from collections import Counter
from dataclasses import dataclass, field

from app_common.bird_sharpness_fields import VERDICT_ERROR, VERDICT_STYLES
from app_common.log import get_logger

from .qt_compat import QDialog, QHBoxLayout, QLabel, QPushButton, QThread, QVBoxLayout, pyqtSignal

try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import QProgressBar
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QObject
    from PyQt5.QtWidgets import QProgressBar

_log = get_logger("bird_sharpness.viewer")


@dataclass
class BirdSharpnessJob:
    """Either explicit ``items`` [(display_path, source_path)] or a ``directory`` scan."""

    title: str
    items: list[tuple[str, str]] = field(default_factory=list)
    directory: str = ""
    recursive: bool = False
    skip_existing: bool = False


class BirdSharpnessWorker(QThread):
    """Coordinates one job; never touches widgets.

    Loads the models once, enumerates the photos and feeds ``BirdSharpnessAction``
    units into the browser's shared ``BrowserWorkPool`` as ``WorkKind.ANALYSIS``
    (bounded in-flight window, demand lease while producing). Without a pool the
    same actions run sequentially on this thread. ``run()`` returns only after
    every submitted action is finished or cancelled, so ``QThread.finished`` means
    no analysis of this job is still running.
    """

    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int, str)
    result_ready = pyqtSignal(object, object, bool)  # display path, BirdSharpnessResult, written
    item_skipped = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, job: BirdSharpnessJob, analyzer_holder: "BirdSharpnessController", pool=None) -> None:
        super().__init__()
        self.job = job
        self._holder = analyzer_holder
        self._pool = pool
        self._cancel = threading.Event()
        self.max_parallel = 0

    def stop(self) -> None:
        self._cancel.set()
        self.requestInterruption()

    def _cancelled(self) -> bool:
        return self._cancel.is_set() or self.isInterruptionRequested()

    def _job_items(self) -> list[tuple[str, str]]:
        if self.job.items:
            return list(self.job.items)
        from bird_sharpness.__main__ import collect_image_paths

        self.status_changed.emit("正在扫描目录…")
        paths = collect_image_paths([self.job.directory], recursive=self.job.recursive)
        return [(os.path.normpath(p), os.path.normpath(p)) for p in paths]

    def _emit_outcome(self, outcome) -> None:
        if outcome.skipped:
            self.item_skipped.emit(outcome.display_path)
        elif outcome.result is not None:
            if outcome.result.verdict != VERDICT_ERROR and not outcome.written:
                _log.warning("[BirdSharpness] XMP write failed path=%r", outcome.source_path)
            self.result_ready.emit(outcome.display_path, outcome.result, outcome.written)

    def run(self) -> None:
        try:
            try:
                from bird_sharpness.models import check_runtime
            except ImportError as exc:
                self.failed.emit(f"当前版本未包含鸟清晰度检测组件（bird_sharpness）：{exc}")
                return

            reason = check_runtime()
            if reason:
                self.failed.emit(reason)
                return
            from bird_sharpness.actions import BirdSharpnessAction

            items = self._job_items()
            total = len(items)
            if not total:
                self.failed.emit("没有找到可检测的图片。")
                return
            self.status_changed.emit("正在加载检测模型…")
            analyzer = self._holder.analyzer()
            analyzer.load()

            def make(item):
                display_path, source_path = item
                return BirdSharpnessAction(
                    analyzer, display_path, source_path,
                    skip_existing=self.job.skip_existing, cancelled=self._cancelled,
                )

            if self._pool is None:
                self._run_sequential(items, make)
            else:
                self._run_on_pool(items, make)
        except Exception as exc:
            _log.error("[BirdSharpness] job failed: %s", traceback.format_exc())
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    def _run_sequential(self, items, make) -> None:
        total = len(items)
        self.max_parallel = 1
        self.progress_changed.emit(0, total, "")
        for done, item in enumerate(items, start=1):
            if self._cancelled():
                break
            outcome = make(item).execute()
            if outcome.cancelled:
                break
            self._emit_outcome(outcome)
            self.progress_changed.emit(done, total, os.path.basename(item[0]))

    def _run_on_pool(self, items, make) -> None:
        from concurrent.futures import FIRST_COMPLETED, wait

        from app_common.file_browser._work_pool import BrowserPoolClosed
        from app_common.file_browser._work_policy import WorkKind
        from bird_sharpness.analyzer import BirdSharpnessResult

        pool = self._pool
        total = len(items)
        workers = max(1, int(getattr(pool, "analysis_workers", 1)))
        self.max_parallel = workers
        window = workers * 2
        self.status_changed.emit(f"正在检测（{workers} 线程并行）…")
        self.progress_changed.emit(0, total, "")
        try:
            token = pool.begin_producer(WorkKind.ANALYSIS)
        except BrowserPoolClosed:
            self._cancel.set()
            return
        pending: dict = {}
        next_index = 0
        done = 0
        cancel_sent = False
        try:
            while pending or (next_index < total and not self._cancelled()):
                while next_index < total and len(pending) < window and not self._cancelled():
                    try:
                        future = pool.submit_action(make(items[next_index]), kind=WorkKind.ANALYSIS)
                    except BrowserPoolClosed:
                        self._cancel.set()
                        break
                    pending[future] = items[next_index]
                    next_index += 1
                if not pending:
                    break
                finished, _ = wait(list(pending), timeout=0.2, return_when=FIRST_COMPLETED)
                if self._cancelled() and not cancel_sent:
                    # Queued actions are withdrawn; running ones finish their current photo.
                    cancel_sent = True
                    for future in list(pending):
                        pool.cancel(future)
                for future in finished:
                    display_path, source_path = pending.pop(future)
                    if future.cancelled():
                        continue
                    try:
                        outcome = future.result()
                    except Exception as exc:
                        _log.error("[BirdSharpness] action failed path=%r: %r", source_path, exc)
                        from bird_sharpness.actions import BirdSharpnessOutcome

                        outcome = BirdSharpnessOutcome(
                            display_path, source_path,
                            result=BirdSharpnessResult(path=source_path, verdict=VERDICT_ERROR,
                                                       error=f"{type(exc).__name__}: {exc}"),
                        )
                    if outcome.cancelled:
                        continue
                    done += 1
                    self._emit_outcome(outcome)
                    self.progress_changed.emit(done, total, os.path.basename(display_path))
        finally:
            pool.end_producer(WorkKind.ANALYSIS, token)


class BirdSharpnessProgressDialog(QDialog):
    """Non-modal progress window; closing it only requests cancellation."""

    cancel_requested = pyqtSignal()

    def __init__(self, parent, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumWidth(460)
        self._running = True
        self.label = QLabel("正在准备…", self)
        self.label.setWordWrap(True)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)
        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        self.button = QPushButton("停止", self)
        self.button.clicked.connect(self._on_button)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addWidget(self.summary)
        layout.addLayout(row)

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
        super().closeEvent(event)

    def set_status(self, text: str) -> None:
        self.label.setText(text)

    def set_progress(self, done: int, total: int, name: str) -> None:
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self.label.setText(f"已完成 {done}/{total}：{name}" if name else f"已完成 {done}/{total}")

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)

    def mark_finished(self, text: str) -> None:
        self._running = False
        self.label.setText(text)
        self.button.setEnabled(True)
        self.button.setText("关闭")


def _summary_text(counts: Counter, skipped: int, write_failures: int) -> str:
    parts = []
    for verdict, style in VERDICT_STYLES.items():
        if counts.get(verdict):
            parts.append(f"{style.label} {counts[verdict]}")
    if skipped:
        parts.append(f"已跳过 {skipped}")
    if write_failures:
        parts.append(f"XMP 写入失败 {write_failures}")
    return "，".join(parts)


class BirdSharpnessController(QObject):
    """Owns the analyzer (models load once per session) and the single running job."""

    def __init__(self, main_window, file_list, dir_browser=None) -> None:
        super().__init__(main_window)
        self._main = main_window
        self._file_list = file_list
        self._worker: BirdSharpnessWorker | None = None
        self._dialog: BirdSharpnessProgressDialog | None = None
        self._analyzer = None
        self._analyzer_lock = threading.Lock()
        self._shutdown_requested = False
        self._counts: Counter = Counter()
        self._skipped = 0
        self._write_failures = 0
        self._failure_message = ""
        if dir_browser is not None:
            dir_browser.add_context_menu_extender(self.extend_directory_menu)
        add_extender = getattr(file_list, "add_file_context_menu_extender", None)
        if callable(add_extender):
            add_extender(self.extend_file_menu)

    # ── analyzer ownership ────────────────────────────────────────────────
    def analyzer(self):
        with self._analyzer_lock:
            if self._analyzer is None:
                from bird_sharpness.analyzer import BirdSharpnessAnalyzer

                self._analyzer = BirdSharpnessAnalyzer()
            return self._analyzer

    @property
    def busy(self) -> bool:
        return self._worker is not None

    # ── menus ─────────────────────────────────────────────────────────────
    def _add_stop_action(self, menu) -> None:
        act = menu.addAction("停止鸟清晰度检测")
        act.triggered.connect(lambda checked=False: self.stop())

    def extend_directory_menu(self, menu, directory: str) -> None:
        sub = menu.addMenu("鸟清晰度检测")
        if self.busy:
            self._add_stop_action(sub)
            return
        name = os.path.basename(directory.rstrip("\\/")) or directory
        for text, recursive, skip in (
            ("检测本目录（跳过已检测）", False, True),
            ("检测本目录及子目录（跳过已检测）", True, True),
            ("重新检测本目录全部照片", False, False),
        ):
            act = sub.addAction(text)
            act.triggered.connect(
                lambda checked=False, r=recursive, s=skip: self.start(
                    BirdSharpnessJob(
                        title=f"鸟清晰度检测 - {name}", directory=directory, recursive=r, skip_existing=s,
                    )
                )
            )

    def extend_file_menu(self, menu, paths: list[str]) -> None:
        if self.busy:
            self._add_stop_action(menu)
            return
        count = len(paths)
        if not count:
            return
        act = menu.addAction(f"检测鸟清晰度（{count} 张）")
        act.triggered.connect(lambda checked=False, p=list(paths): self.start_for_paths(p))

    def start_for_paths(self, paths: list[str]) -> None:
        resolve = getattr(self._file_list, "_resolve_source_path_for_action", None)
        items = []
        for path in paths:
            source = path
            if callable(resolve):
                try:
                    source = resolve(path) or path
                except Exception:
                    source = path
            if os.path.isfile(source):
                items.append((os.path.normpath(path), os.path.normpath(source)))
        if not items:
            self._show_message("没有可检测的图片文件。")
            return
        self.start(BirdSharpnessJob(title=f"鸟清晰度检测 - {len(items)} 张", items=items))

    # ── job lifecycle ─────────────────────────────────────────────────────
    def start(self, job: BirdSharpnessJob) -> bool:
        if self._shutdown_requested:
            return False
        if self.busy:
            self._show_message("已有鸟清晰度检测任务在运行。")
            return False
        self._counts = Counter()
        self._skipped = 0
        self._write_failures = 0
        self._failure_message = ""
        pool_getter = getattr(self._file_list, "background_work_pool", None)
        pool = pool_getter() if callable(pool_getter) else None
        worker = BirdSharpnessWorker(job, self, pool)
        self._worker = worker
        dialog = BirdSharpnessProgressDialog(self._main, job.title)
        self._dialog = dialog
        dialog.cancel_requested.connect(lambda w=worker: self._request_stop(w))
        worker.status_changed.connect(lambda text, w=worker: self._on_status(w, text))
        worker.progress_changed.connect(lambda d, t, n, w=worker: self._on_progress(w, d, t, n))
        worker.result_ready.connect(lambda p, r, ok, w=worker: self._on_result(w, p, r, ok))
        worker.item_skipped.connect(lambda p, w=worker: self._on_skipped(w, p))
        worker.failed.connect(lambda msg, w=worker: self._on_failed(w, msg))
        worker.finished.connect(lambda w=worker: self._on_thread_finished(w))
        _log.info("[BirdSharpness] start job title=%r dir=%r recursive=%s items=%s skip_existing=%s",
                  job.title, job.directory, job.recursive, len(job.items), job.skip_existing)
        dialog.show()
        worker.start()
        return True

    def stop(self) -> None:
        if self._worker is not None:
            self._request_stop(self._worker)

    def _request_stop(self, worker: BirdSharpnessWorker) -> None:
        if worker is self._worker:
            worker.stop()

    def _on_status(self, worker, text: str) -> None:
        if worker is self._worker and self._dialog is not None:
            self._dialog.set_status(text)

    def _on_progress(self, worker, done: int, total: int, name: str) -> None:
        if worker is self._worker and self._dialog is not None:
            self._dialog.set_progress(done, total, name)

    def _on_skipped(self, worker, path: str) -> None:
        if worker is not self._worker:
            return
        self._skipped += 1
        self._refresh_summary()

    def _on_result(self, worker, display_path: str, result, written: bool) -> None:
        if worker is not self._worker or self._shutdown_requested:
            return
        self._counts[result.verdict] += 1
        if result.verdict != VERDICT_ERROR and not written:
            self._write_failures += 1
        if written and self._is_listed(display_path):
            from bird_sharpness.xmp_store import browser_meta_updates

            try:
                self._file_list.sync_metadata_edit_for_path(display_path, meta_updates=browser_meta_updates(result))
            except Exception:
                _log.error("[BirdSharpness] list refresh failed path=%r: %s", display_path, traceback.format_exc())
        self._refresh_summary()

    def _is_listed(self, path: str) -> bool:
        """Only rows of the directory being shown need a refresh; others reread XMP when opened."""
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

    def _refresh_summary(self) -> None:
        if self._dialog is not None:
            self._dialog.set_summary(_summary_text(self._counts, self._skipped, self._write_failures))

    def _on_thread_finished(self, worker) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        cancelled = worker._cancelled()
        dialog = self._dialog
        summary = _summary_text(self._counts, self._skipped, self._write_failures)
        _log.info("[BirdSharpness] job finished cancelled=%s summary=%s failure=%r", cancelled, summary, self._failure_message)
        try:
            worker.deleteLater()
        except Exception:
            pass
        if self._shutdown_requested:
            if dialog is not None:
                dialog._running = False
                dialog.close()
            self._dialog = None
            self._release_models()
            return
        if dialog is not None:
            if self._failure_message:
                dialog.mark_finished(f"检测未完成：{self._failure_message}")
            elif cancelled:
                dialog.mark_finished("已停止。")
            else:
                dialog.mark_finished("检测完成。")
            dialog.set_summary(summary)

    # ── shutdown ──────────────────────────────────────────────────────────
    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        if self._worker is not None:
            self._worker.stop()
        else:
            self._release_models()

    def is_shutdown_done(self) -> bool:
        return self._worker is None

    def _release_models(self) -> None:
        with self._analyzer_lock:
            analyzer, self._analyzer = self._analyzer, None
        if analyzer is not None:
            try:
                analyzer.release()
            except Exception:
                pass

    def _show_message(self, text: str) -> None:
        from .qt_compat import QMessageBox

        QMessageBox.information(self._main, "鸟清晰度检测", text)
