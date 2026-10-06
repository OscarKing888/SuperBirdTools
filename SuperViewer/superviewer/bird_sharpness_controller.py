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
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field

from app_common.bird_sharpness_fields import VERDICT_ERROR
from app_common.log import get_logger

from bird_sharpness.timing import TimingStats

from .bird_sharpness_progress import BirdSharpnessProgressDialog, WorkerLane, WorkerLoad
from .qt_compat import QThread, pyqtSignal

try:
    from PyQt6.QtCore import QObject
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QObject

_log = get_logger("bird_sharpness.viewer")


def _viewer_focus_box(path: str, width: int, height: int):
    """Worker-thread focus lookup shared with the preview overlay (metadata, then report.db)."""
    from .focus_preview_loader import _load_focus_box_for_preview

    return _load_focus_box_for_preview(path, width, height, allow_report_db_fallback=True)


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
    load_changed = pyqtSignal(object)  # WorkerLoad snapshot for the progress window
    warning_changed = pyqtSignal(str)
    timing_ready = pyqtSignal(object)  # PhotoTiming of every finished (or skipped) photo
    setup_timed = pyqtSignal(float)    # seconds spent loading the models before the first photo

    def __init__(self, job: BirdSharpnessJob, analyzer_holder: "BirdSharpnessController", pool=None) -> None:
        super().__init__()
        self.job = job
        self._holder = analyzer_holder
        self._pool = pool
        self._cancel = threading.Event()
        self.max_parallel = 0
        self._source_label = ""  # image source of this job, shown in the status line

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
        timing = getattr(outcome, "timing", None)
        if timing is not None:
            self.timing_ready.emit(timing)
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
            t_setup = time.perf_counter()
            analyzer = self._holder.analyzer()
            from bird_sharpness.image_source import SOURCE_DENOISED, SOURCE_LABELS

            source = getattr(getattr(analyzer, "params", None), "image_source", "")
            if source == SOURCE_DENOISED and getattr(analyzer, "denoised_lookup", None) is None:
                self.failed.emit("图像来源为「降噪成片」，但降噪功能不可用；请在 设置 → 鸟清晰度 改选其他图像来源。")
                return
            self._source_label = SOURCE_LABELS.get(source, "")
            analyzer.load()
            sam_model = getattr(getattr(analyzer, "params", None), "sam_model", "")
            if sam_model:  # fail the job once, not every photo, when the SAM model is missing
                analyzer.refiner_provider(sam_model).load()
            self.setup_timed.emit(time.perf_counter() - t_setup)
            models = getattr(analyzer, "models", None)
            if models is not None and getattr(models, "has_keypoints", True) is False:
                self.warning_changed.emit("未找到鸟眼关键点模型：鸟体按整只鸟计算，翅膀/尾羽和遮挡树叶会干扰结果，准确度明显降低。")

            def make(item, on_stage=None):
                display_path, source_path = item
                return BirdSharpnessAction(
                    analyzer, display_path, source_path,
                    skip_existing=self.job.skip_existing, cancelled=self._cancelled, on_stage=on_stage,
                )

            if self._pool is None:
                self._run_sequential(items, make)
            else:
                self._run_on_pool(items, make)
        except Exception as exc:
            _log.error("[BirdSharpness] job failed: %s", traceback.format_exc())
            self.failed.emit(f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _lane(action) -> WorkerLane:
        return WorkerLane(os.path.basename(action.display_path), action.stage, action.started_at or time.monotonic())

    def _run_sequential(self, items, make) -> None:
        total = len(items)
        self.max_parallel = 1

        def publish(action) -> None:
            self.load_changed.emit(WorkerLoad(capacity=1, lanes=(self._lane(action),), shared_pool=False))

        self.progress_changed.emit(0, total, "")
        for done, item in enumerate(items, start=1):
            if self._cancelled():
                break
            outcome = make(item, on_stage=publish).execute()
            if outcome.cancelled:
                break
            self._emit_outcome(outcome)
            self.progress_changed.emit(done, total, os.path.basename(item[0]))
        self.load_changed.emit(WorkerLoad(capacity=1, lanes=(None,), shared_pool=False))

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
        source = f"，图像：{self._source_label}" if self._source_label else ""
        self.status_changed.emit(f"正在检测（{workers} 线程并行{source}）…")
        self.progress_changed.emit(0, total, "")
        try:
            token = pool.begin_producer(WorkKind.ANALYSIS)
        except BrowserPoolClosed:
            self._cancel.set()
            return
        pending: dict = {}  # future -> (item, action)
        lane_of: dict = {}  # running future -> stable worker slot index
        next_index = 0
        done = 0
        cancel_sent = False

        def publish() -> None:
            free = [i for i in range(workers) if i not in lane_of.values()]
            for fut, (_item, _action) in pending.items():
                if fut not in lane_of and fut.running() and free:
                    lane_of[fut] = free.pop(0)
            lanes = [None] * workers
            for fut, slot in lane_of.items():
                lanes[slot] = self._lane(pending[fut][1])
            try:
                snap = pool.snapshot()
            except Exception:
                snap = {}
            self.load_changed.emit(WorkerLoad(
                capacity=workers,
                lanes=tuple(lanes),
                queued=sum(1 for fut in pending if not fut.running() and not fut.done()),
                pool_threads=int(snap.get("total", 0)),
                pool_thumbnail_active=int(snap.get("thumbnail_active", 0)),
                pool_metadata_active=int(snap.get("metadata_active", 0)),
            ))

        try:
            while pending or (next_index < total and not self._cancelled()):
                while next_index < total and len(pending) < window and not self._cancelled():
                    action = make(items[next_index])
                    try:
                        future = pool.submit_action(action, kind=WorkKind.ANALYSIS)
                    except BrowserPoolClosed:
                        self._cancel.set()
                        break
                    pending[future] = (items[next_index], action)
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
                    (display_path, source_path), _action = pending.pop(future)
                    lane_of.pop(future, None)
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
                publish()
        finally:
            pool.end_producer(WorkKind.ANALYSIS, token)
            pending.clear()
            lane_of.clear()
            publish()


class _TraceBridge(QObject):
    """Delivers trace outcomes from pool threads to the GUI thread (queued signal)."""

    done = pyqtSignal(object, object)  # request, BirdSharpnessTraceOutcome or Exception


def _analysis_options() -> dict:
    """Every bird sharpness option (``AnalysisParams.as_params`` names) from the SuperViewer
    user options (设置 → 鸟清晰度), defaults when unavailable. Batch detection runs on these;
    trace windows start from them and may change them for one window."""
    from bird_sharpness.image_source import SOURCE_JPEG
    from bird_sharpness.params import AnalysisParams

    try:
        from app_common.superviewer_user_options import get_bird_sharpness_params

        return AnalysisParams.from_params(get_bird_sharpness_params()).as_params()
    except Exception:  # SuperViewer measures the embedded JPEG by default (the user option's default)
        return AnalysisParams(image_source=SOURCE_JPEG).as_params()


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
        self._timing = TimingStats()
        self._trace_requests: list = []  # [dialog, future, cancel_event]
        self._denoise = None  # DenoiseController, for "降噪成片" traces
        self._denoise_waits: dict = {}  # normcase(source) -> dialogs waiting for its denoised image
        self._trace_executor = None  # fallback when the browser pool is unavailable
        self._trace_bridge = _TraceBridge(self)
        self._trace_bridge.done.connect(self._on_trace_done)
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

                # Same focus-box loader as the preview overlay, so the measured window is what users see.
                self._analyzer = BirdSharpnessAnalyzer(focus_provider=_viewer_focus_box)
            # Read at every job start: a changed user option applies to the next detection or trace.
            from bird_sharpness.params import AnalysisParams

            self._analyzer.params = AnalysisParams.from_params(_analysis_options())
            # Batch detection with image source 降噪成片 finds the renderings with the current denoise settings.
            self._analyzer.denoised_lookup = self._denoised_lookup if self._denoise is not None else None
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
        if paths:
            trace_act = menu.addAction("查看清晰度计算过程…")
            trace_act.setToolTip("逐步显示这张照片的清晰度是如何算出来的（只读，不写入）；按设置里的图像来源开始，"
                                 "窗口里可切换 相机 JPEG / RAW 解码 / 降噪成片")
            trace_act.triggered.connect(lambda checked=False, p=paths[0]: self.show_trace(p))
        if self.busy:
            self._add_stop_action(menu)
            return
        count = len(paths)
        if not count:
            return
        act = menu.addAction(f"检测鸟清晰度（{count} 张）")
        act.triggered.connect(lambda checked=False, p=list(paths): self.start_for_paths(p))

    # ── per-photo computation trace ───────────────────────────────────────
    def set_denoise_controller(self, controller) -> None:
        """Lets the trace viewer measure denoised images, denoising first when there is none."""
        self._denoise = controller
        controller.output_ready.connect(self._on_denoised_output)
        controller.batch_finished.connect(self._on_denoise_finished)

    def _denoised_lookup(self, path: str):
        """Worker-thread lookup with the user's current denoise output settings."""
        from image_denoise.preview import find_denoised_preview

        from .denoise_controller import current_denoise_options

        return find_denoised_preview(path, current_denoise_options())

    def show_trace(self, path: str, image_source: str | None = None):
        """Open a step viewer for one photo; the trace runs as a pool ANALYSIS action.
        ``image_source`` defaults to the user option (设置 → 鸟清晰度 → 图像来源)."""
        if self._shutdown_requested:
            return None
        from .bird_sharpness_trace_view import BirdSharpnessTraceDialog

        resolve = getattr(self._file_list, "_resolve_source_path_for_action", None)
        source = path
        if callable(resolve):
            try:
                source = resolve(path) or path
            except Exception:
                source = path
        params = _analysis_options()
        image_source = image_source or params["image_source"]
        dialog = BirdSharpnessTraceDialog(self._main, source, image_source, params=params)
        from bird_sharpness.image_source import DecodedImageCache

        # 「按此参数重新计算」and source switches reuse this window's decode instead of decoding again.
        dialog.image_cache = DecodedImageCache()
        from .model_chain_state import ModelChainStore

        # The model chain on the right is saved on every change and rebuilt in the next window.
        dialog.chain_store = ModelChainStore()
        self._connect_trace_dialog(dialog)
        self._run_trace(dialog, image_source)
        dialog.show()
        return dialog

    def _connect_trace_dialog(self, dialog) -> None:
        dialog.closed.connect(self._on_trace_dialog_closed)
        dialog.source_changed.connect(lambda d, s: self._run_trace(d, s))
        dialog.params_changed.connect(lambda d: self._run_trace(d, d.image_source))
        dialog.save_defaults_requested.connect(self._save_trace_params)
        dialog.analyze_pixels_requested.connect(self.show_trace_for_given)

    def show_trace_for_given(self, source_dialog, image, given, title: str):
        """A model chain window's 「测清晰度」: a new trace window measuring its results as birds on
        a temporary image (the photo's pixels around them), with the source window's parameters."""
        if self._shutdown_requested:
            return None
        from .bird_sharpness_trace_view import BirdSharpnessTraceDialog

        dialog = BirdSharpnessTraceDialog(self._main, source_dialog.path, source_dialog.image_source,
                                          params=dict(source_dialog.params))
        dialog.set_given_input(image, given, title)
        self._connect_trace_dialog(dialog)
        self._run_trace(dialog, source_dialog.image_source)
        dialog.show()
        return dialog

    def _run_trace(self, dialog, image_source: str) -> None:
        from bird_sharpness.actions import BirdSharpnessTraceAction

        for request in [r for r in self._trace_requests if r[0] is dialog]:
            self._cancel_trace(request)  # a newer source replaces the running one
        self._forget_denoise_wait(dialog)
        dialog.set_loading()
        cancel = threading.Event()
        request = [dialog, None, cancel]
        params = getattr(dialog, "params", None) or {}
        from bird_sharpness.params import AnalysisParams

        # This window's parameters on top of the user options; the shared analyzer stays untouched.
        base = self.analyzer()
        analyzer = base.with_options(params=AnalysisParams.from_params(
            {**base.params.as_params(), **params, "image_source": image_source}))
        action = BirdSharpnessTraceAction(analyzer, dialog.path, cancelled=cancel.is_set,
                                          image_source=image_source,
                                          image_cache=getattr(dialog, "image_cache", None),
                                          denoised_lookup=self._denoised_lookup if self._denoise is not None else None,
                                          given_input=getattr(dialog, "given_input", None))
        pool_getter = getattr(self._file_list, "background_work_pool", None)
        pool = pool_getter() if callable(pool_getter) else None
        future = None
        if pool is not None:
            from app_common.file_browser._work_pool import BrowserPoolClosed
            from app_common.file_browser._work_policy import WorkKind

            try:
                future = pool.submit_action(action, kind=WorkKind.ANALYSIS)
            except BrowserPoolClosed:
                future = None
        if future is None:
            if self._trace_executor is None:
                from concurrent.futures import ThreadPoolExecutor

                self._trace_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bird-sharpness-trace")
            future = self._trace_executor.submit(action.execute)
        request[1] = future
        self._trace_requests.append(request)
        bridge = self._trace_bridge
        future.add_done_callback(lambda f, r=request: bridge.done.emit(r, f))

    def _save_trace_params(self, dialog) -> None:
        """「保存为默认设置」in a trace window: store its parameters as user options."""
        from app_common.superviewer_user_options import (apply_runtime_user_options,
                                                         bird_sharpness_params_to_options,
                                                         get_runtime_user_options, save_user_options)

        params = dialog.selected_params()
        options = get_runtime_user_options()
        options.update(bird_sharpness_params_to_options(params))
        try:
            normalized = save_user_options(options)
        except Exception as exc:
            _log.error("[BirdSharpness] saving trace parameters failed: %r", exc)
            self._show_message(f"无法保存鸟清晰度参数：\n{exc}")
            return
        apply_runtime_user_options(normalized)
        self._show_message("已保存为默认设置，之后的鸟清晰度检测将使用这些参数。")

    def _on_trace_dialog_closed(self, dialog) -> None:
        for request in [r for r in self._trace_requests if r[0] is dialog]:
            self._cancel_trace(request)
        self._forget_denoise_wait(dialog)
        cache = getattr(dialog, "image_cache", None)
        if cache is not None:
            cache.clear()  # free the decoded image(s) with the window

    def _cancel_trace(self, request) -> None:
        request[2].set()
        future = request[1]
        if future is not None and not future.done():
            future.cancel()

    def _on_trace_done(self, request, future) -> None:
        if request in self._trace_requests:
            self._trace_requests.remove(request)
        dialog = request[0]
        if self._shutdown_requested or request[2].is_set() or future.cancelled():
            return
        try:
            outcome = future.result()
        except Exception as exc:
            _log.error("[BirdSharpness] trace failed path=%r: %r", dialog.path, exc)
            dialog.set_error(f"{type(exc).__name__}: {exc}")
            return
        if outcome.needs_denoise:
            self._denoise_then_trace(dialog)
        elif outcome.trace is not None:
            dialog.set_trace(outcome.trace)
        elif not outcome.cancelled:
            dialog.set_error(outcome.error or "分析失败")

    # ── denoise on demand ──
    def _denoise_then_trace(self, dialog) -> None:
        denoise = self._denoise
        if denoise is None:
            dialog.set_error("降噪功能不可用。")
            return
        key = os.path.normcase(os.path.normpath(dialog.path))
        if key not in self._denoise_waits:
            if denoise.busy:
                dialog.set_error("降噪正在处理其它照片，完成后再选「降噪成片」；也可在降噪进度窗口里停止它。")
                return
            if not denoise.start_for_paths([dialog.path]):
                dialog.set_error("降噪没有开始（已取消选择输出目录，或降噪设置无效）。")
                return
        self._denoise_waits.setdefault(key, []).append(dialog)
        dialog.set_loading(f"{os.path.basename(dialog.path)} 还没有降噪成片，正在降噪…\n"
                           "（进度见降噪窗口，约 1–2 分钟；完成后自动按降噪成片计算）")

    def _forget_denoise_wait(self, dialog) -> None:
        for key, dialogs in list(self._denoise_waits.items()):
            if dialog in dialogs:
                dialogs.remove(dialog)
            if not dialogs:
                del self._denoise_waits[key]

    def _on_denoised_output(self, source: str, _destination: str) -> None:
        if self._shutdown_requested:
            return
        from bird_sharpness.image_source import SOURCE_DENOISED

        for dialog in self._denoise_waits.pop(os.path.normcase(os.path.normpath(source)), []):
            if dialog.image_source == SOURCE_DENOISED:
                self._run_trace(dialog, SOURCE_DENOISED)

    def _on_denoise_finished(self) -> None:
        waits, self._denoise_waits = self._denoise_waits, {}
        for dialogs in waits.values():
            for dialog in dialogs:
                dialog.set_error("降噪没有生成成片（已停止或失败，详见降噪窗口）。")

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
        self._timing = TimingStats()
        pool_getter = getattr(self._file_list, "background_work_pool", None)
        pool = pool_getter() if callable(pool_getter) else None
        worker = BirdSharpnessWorker(job, self, pool)
        self._worker = worker
        dialog = BirdSharpnessProgressDialog(self._main, job.title, running_text="正在检测鸟清晰度…")
        self._dialog = dialog
        dialog.cancel_requested.connect(lambda w=worker: self._request_stop(w))
        worker.status_changed.connect(lambda text, w=worker: self._on_status(w, text))
        worker.progress_changed.connect(lambda d, t, n, w=worker: self._on_progress(w, d, t, n))
        worker.result_ready.connect(lambda p, r, ok, w=worker: self._on_result(w, p, r, ok))
        worker.item_skipped.connect(lambda p, w=worker: self._on_skipped(w, p))
        worker.failed.connect(lambda msg, w=worker: self._on_failed(w, msg))
        worker.load_changed.connect(lambda load, w=worker: self._on_load(w, load))
        worker.warning_changed.connect(lambda text, w=worker: self._on_warning(w, text))
        worker.timing_ready.connect(lambda t, w=worker: self._on_timing(w, t))
        worker.setup_timed.connect(lambda s, w=worker: self._on_setup_timed(w, s))
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

    def _on_timing(self, worker, timing) -> None:
        if worker is self._worker and self._dialog is not None:
            self._timing.add(timing)
            self._dialog.set_timing(self._timing)

    def _on_setup_timed(self, worker, seconds: float) -> None:
        if worker is self._worker:
            self._timing.setup_s = seconds
            if self._dialog is not None:
                self._dialog.set_timing(self._timing)

    def _on_warning(self, worker, text: str) -> None:
        if worker is self._worker and self._dialog is not None:
            self._dialog.set_warning(text)

    def _on_load(self, worker, load) -> None:
        if worker is self._worker and self._dialog is not None:
            self._dialog.set_load(load)

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
            self._dialog.set_counts(self._counts, self._skipped, self._write_failures)

    def _on_thread_finished(self, worker) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        cancelled = worker._cancelled()
        dialog = self._dialog
        _log.info("[BirdSharpness] job finished cancelled=%s counts=%s skipped=%s write_failures=%s failure=%r",
                  cancelled, dict(self._counts), self._skipped, self._write_failures, self._failure_message)
        if self._timing.photos or self._timing.skipped:
            wall = dialog.elapsed_seconds() if dialog is not None else None
            _log.info("[BirdSharpness] timing %s", " | ".join(self._timing.lines(wall)))
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
            dialog.set_counts(self._counts, self._skipped, self._write_failures)

    # ── shutdown ──────────────────────────────────────────────────────────
    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        for request in list(self._trace_requests):
            self._cancel_trace(request)
            try:
                request[0].close()
            except Exception:
                pass
        if self._trace_executor is not None:
            self._trace_executor.shutdown(wait=False, cancel_futures=True)
        if self._worker is not None:
            self._worker.stop()
        elif not self._trace_requests:
            self._release_models()

    def is_shutdown_done(self) -> bool:
        traces_done = all(r[1] is None or r[1].done() for r in self._trace_requests)
        if self._shutdown_requested and self._worker is None and traces_done and self._analyzer is not None:
            self._release_models()
        return self._worker is None and traces_done

    def _release_models(self) -> None:
        with self._analyzer_lock:
            analyzer, self._analyzer = self._analyzer, None
        if analyzer is not None:
            try:
                analyzer.release()
            except Exception:
                pass
        # Trace windows may have loaded other detectors / SAM models: free those too.
        try:
            from bird_sharpness.models import release_shared_models
            from bird_sharpness.refine import release_shared_refiners

            release_shared_models()
            release_shared_refiners()
        except Exception:
            pass

    def _show_message(self, text: str) -> None:
        from .qt_compat import QMessageBox

        QMessageBox.information(self._main, "鸟清晰度检测", text)
