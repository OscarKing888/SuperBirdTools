"""editor_exporter.py – _BirdStampExporterMixin

图片导出统一走一条渲染链：
- 主界面当前图导出
- 批量图片导出

两者仅在目标路径生成方式上不同，渲染任务均复用 video_export 中的
`render_video_frame()` 与相同的自动线程数策略。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import threading
import time

from PIL import Image
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from birdstamp.export_frame_cache import (
    SOURCE_FRAME_BUCKET_KIND,
    build_source_frame_bucket_key,
    build_source_frame_signature,
    create_frame_cache_plan,
    frame_output_path as _cache_frame_output_path,
    global_export_settings_from_settings,
    load_frame_manifest,
    path_signature,
    reusable_frame_path,
    update_frame_manifest_record,
    write_frame_manifest,
)
from birdstamp.gui import editor_options
from birdstamp.gif_export import (
    DEFAULT_GIF_BACKGROUND_COLOR,
    GifExportOptions,
    build_gif_variant_output_paths,
    export_gif,
)
from birdstamp.video_export import VideoFrameJob, render_video_frame, resolve_video_render_workers

OUTPUT_FORMAT_OPTIONS = editor_options.OUTPUT_FORMAT_OPTIONS
_IMAGE_EXPORT_PROGRESS_HIDE_DELAY_MS = 600


@dataclass(slots=True)
class _ImageExportTask:
    job: VideoFrameJob
    target_path: Path


class _BirdStampExporterMixin:
    """Mixin: export_current, export_all, _save_image."""

    def _is_gif_output_selected(self) -> bool:
        return self._selected_output_suffix() == "gif"

    def _format_export_elapsed_time(self, elapsed_seconds: float) -> str:
        seconds = max(0.0, float(elapsed_seconds or 0.0))
        if seconds < 60.0:
            return f"{seconds:.1f}秒"

        total_minutes = int(seconds // 60.0)
        second_value = seconds - total_minutes * 60.0
        if total_minutes < 60:
            return f"{total_minutes}分{second_value:04.1f}秒"

        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours}小时{minutes:02d}分{second_value:04.1f}秒"

    def _format_image_export_timing_summary(self, total_images: int, elapsed_seconds: float) -> str:
        total = max(1, int(total_images or 0))
        elapsed = max(0.0, float(elapsed_seconds or 0.0))
        average = elapsed / float(total)
        return (
            f"总时长 {self._format_export_elapsed_time(elapsed)}，"
            f"平均每张 {self._format_export_elapsed_time(average)}"
        )

    def _format_image_export_progress_text(
        self,
        current: int,
        total: int,
        *,
        label: str,
        phase_text: str | None = None,
        worker_count: int | None = None,
    ) -> str:
        total_value = max(0, int(total))
        current_value = max(0, min(int(current), max(1, total_value)))
        label_text = str(label or "图片导出").strip() or "图片导出"
        phase = str(phase_text or "").strip()
        prefix = f"{label_text}{phase}" if phase else label_text
        text = f"{prefix} {current_value}/{total_value}"
        workers = max(0, int(worker_count or 0))
        if workers > 0:
            text += f" | {workers}线程"
        return text

    def _process_image_export_ui_events(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        except Exception:
            app.processEvents()

    def _set_image_export_busy(self, busy: bool) -> None:
        self._image_export_is_busy = bool(busy)
        for attr_name in ("output_format_combo", "max_edge_combo", "gif_export_panel"):
            widget = getattr(self, attr_name, None)
            if widget is None:
                continue
            try:
                widget.setEnabled(not busy)
            except Exception:
                pass
        self._refresh_image_export_action_states()

    def _refresh_image_export_action_states(self) -> None:
        busy = bool(getattr(self, "_image_export_is_busy", False))
        is_gif = self._is_gif_output_selected()

        export_current_btn = getattr(self, "export_current_btn", None)
        if export_current_btn is not None:
            try:
                export_current_btn.setEnabled((not busy) and (not is_gif))
                export_current_btn.setToolTip("GIF 仅支持按当前照片列表导出。" if is_gif else "")
            except Exception:
                pass

        export_batch_btn = getattr(self, "export_batch_btn", None)
        if export_batch_btn is not None:
            try:
                export_batch_btn.setEnabled(not busy)
                export_batch_btn.setText("导出 GIF" if is_gif else "批量导出")
            except Exception:
                pass

        gif_export_panel = getattr(self, "gif_export_panel", None)
        if gif_export_panel is not None:
            try:
                gif_export_panel.setVisible(is_gif)
            except Exception:
                pass

    def _set_image_export_progress(
        self,
        current: int,
        total: int,
        *,
        label: str,
        phase_text: str | None = None,
        worker_count: int | None = None,
    ) -> None:
        progress = getattr(self, "image_export_progress", None)
        total_value = max(0, int(total))
        current_value = max(0, min(int(current), max(1, total_value)))
        active_worker_count = (
            max(0, int(worker_count))
            if worker_count is not None
            else max(0, int(getattr(self, "_image_export_active_worker_count", 0) or 0))
        )
        self._image_export_active_worker_count = active_worker_count
        if progress is not None:
            try:
                progress.setMaximum(max(1, total_value))
                progress.setValue(current_value)
                progress.setFormat(
                    self._format_image_export_progress_text(
                        current_value,
                        total_value,
                        label=label,
                        phase_text=phase_text,
                        worker_count=active_worker_count,
                    )
                )
                if total_value > 0:
                    progress.show()
            except Exception:
                pass
        self._set_image_export_busy(True)
        self._process_image_export_ui_events()

    def _reset_image_export_progress(self, *, expected_token: int | None = None) -> None:
        current_token = int(getattr(self, "_image_export_progress_token", 0) or 0)
        if expected_token is not None and int(expected_token) != current_token:
            return
        progress = getattr(self, "image_export_progress", None)
        if progress is not None:
            try:
                progress.setMaximum(1)
                progress.setValue(0)
                progress.setFormat("图片导出 0/0")
                progress.hide()
            except Exception:
                pass
        self._image_export_active_worker_count = 0
        self._set_image_export_busy(False)
        self._process_image_export_ui_events()

    def _begin_image_export_progress(
        self,
        *,
        total: int,
        label: str,
        phase_text: str | None = None,
        worker_count: int | None = None,
    ) -> int:
        token = int(getattr(self, "_image_export_progress_token", 0) or 0) + 1
        self._image_export_progress_token = token
        self._set_image_export_progress(0, total, label=label, phase_text=phase_text, worker_count=worker_count)
        return token

    def _finish_image_export_progress(
        self,
        *,
        current: int,
        total: int,
        label: str,
        token: int,
    ) -> None:
        self._set_image_export_progress(current, total, label=label)
        self._set_image_export_busy(False)
        QTimer.singleShot(
            _IMAGE_EXPORT_PROGRESS_HIDE_DELAY_MS,
            lambda expected_token=token: self._reset_image_export_progress(expected_token=expected_token),
        )
        self._process_image_export_ui_events()

    def export_current(self) -> None:
        if not self.current_path or self._is_placeholder_active():
            self._set_status("没有可导出的照片。")
            return
        if self._is_gif_output_selected():
            self._set_status("GIF 仅支持按当前照片列表导出。")
            return

        suffix = self._selected_output_suffix()
        default_name = f"{self.current_path.stem}__birdstamp.{suffix}"
        remembered_dir = getattr(self, "_image_export_last_output_dir", None)
        fallback_dir = remembered_dir if isinstance(remembered_dir, Path) and remembered_dir.is_dir() else self.current_path.parent
        default_path = fallback_dir / default_name
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出当前照片",
            str(default_path),
            "PNG (*.png);;JPG (*.jpg);;All Files (*.*)",
        )
        if not file_path:
            return

        target = self._normalized_image_export_target(Path(file_path), default_suffix=suffix)
        started_at = time.perf_counter()
        jobs = self._build_export_render_jobs([self.current_path], prefer_current_ui_for_current_path=True)
        try:
            self._export_render_jobs_to_images(jobs, [target], label="导出当前")
        except Exception as exc:
            self._show_error("导出失败", str(exc))
            return
        self._clear_photo_export_dirty([self.current_path])

        remembered_target_dir = target.parent.resolve(strict=False)
        self._image_export_last_output_dir = remembered_target_dir
        self._save_image_export_last_output_dir(remembered_target_dir)
        elapsed = time.perf_counter() - started_at
        timing_text = self._format_image_export_timing_summary(1, elapsed)
        self._set_status(f"导出完成: {target} | {timing_text}")

    def export_all(self) -> None:
        paths = self._list_photo_paths()
        if not paths:
            self._set_status("照片列表为空。")
            return

        if self._is_gif_output_selected():
            try:
                self._export_all_as_gif(paths)
            except Exception as exc:
                self._show_error("GIF 导出失败", str(exc))
            return

        remembered_dir = getattr(self, "_batch_export_last_output_dir", None)
        if not isinstance(remembered_dir, Path) or not remembered_dir.is_dir():
            remembered_dir = getattr(self, "_image_export_last_output_dir", None)
        if not isinstance(remembered_dir, Path) or not remembered_dir.is_dir():
            remembered_dir = paths[0].parent if paths else None
        output_dir = QFileDialog.getExistingDirectory(
            self,
            "选择批量导出目录",
            str(remembered_dir) if isinstance(remembered_dir, Path) else "",
        )
        if not output_dir:
            return

        suffix = self._selected_output_suffix()
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        remembered_output_dir = out_dir.resolve(strict=False)
        self._batch_export_last_output_dir = remembered_output_dir
        self._save_batch_export_last_output_dir(remembered_output_dir)

        targets = self._build_batch_image_targets(paths, out_dir=out_dir, suffix=suffix)
        started_at = time.perf_counter()
        prepare_token = self._begin_image_export_progress(total=len(paths), label="批量导出", phase_text="准备中", worker_count=0)
        self._set_status(f"批量导出准备中: 0/{len(paths)}")

        def _on_prepare_progress(current: int, total: int) -> None:
            self._set_image_export_progress(current, total, label="批量导出", phase_text="准备中", worker_count=0)
            self._set_status(f"批量导出准备中: {current}/{total}")

        try:
            jobs = self._build_export_render_jobs(
                paths,
                prefer_current_ui_for_current_path=True,
                progress_callback=_on_prepare_progress,
            )
            ok_paths, failed = self._export_render_jobs_to_images(jobs, targets, label="批量导出")
        except Exception:
            self._reset_image_export_progress(expected_token=prepare_token)
            raise

        if failed:
            preview = "\n".join(failed[:8])
            if len(failed) > 8:
                preview += f"\n... 另有 {len(failed) - 8} 项失败"
            QMessageBox.warning(self, "批量导出", f"成功 {len(ok_paths)}，失败 {len(failed)}\n\n{preview}")
        else:
            self._clear_photo_export_dirty(paths)
        elapsed = time.perf_counter() - started_at
        timing_text = self._format_image_export_timing_summary(len(targets), elapsed)
        self._set_status(f"批量导出完成: 成功 {len(ok_paths)}，失败 {len(failed)} | {timing_text}")

    def _default_gif_output_path(self, paths: list[Path], base_dir: Path | None) -> Path:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        if len(paths) == 1:
            stem = f"{paths[0].stem}__birdstamp"
        else:
            stem = f"birdstamp_animation_{timestamp}"
        output_dir = base_dir if isinstance(base_dir, Path) else (paths[0].parent if paths else Path.cwd())
        return output_dir / f"{stem}.gif"

    def _gif_frame_output_directory(self, output_path: Path) -> Path:
        bucket_key = build_source_frame_bucket_key(
            global_export_settings=global_export_settings_from_settings(self._build_current_render_settings())
        )
        plan = create_frame_cache_plan(
            output_path,
            bucket_kind=SOURCE_FRAME_BUCKET_KIND,
            bucket_key=bucket_key,
            persistent=True,
        )
        return plan.frames_dir

    def _gif_background_color_for_export(self) -> str:
        getter = getattr(self, "_get_crop_padding_state", None)
        if callable(getter):
            try:
                state = getter()
            except Exception:
                state = {}
        else:
            state = {}
        if isinstance(state, dict):
            color_text = str(state.get("fill") or "").strip()
            if color_text:
                return color_text
        return DEFAULT_GIF_BACKGROUND_COLOR

    def _on_gif_export_progress(self, progress, total_outputs: int) -> None:
        phase = str(getattr(progress, "phase", "") or "").strip().lower()
        message = str(getattr(progress, "message", "") or "").strip()
        current = max(0, int(getattr(progress, "current", 0) or 0))
        total = max(0, int(getattr(progress, "total", 0) or 0))
        if phase == "scan":
            self._set_image_export_progress(current, total, label="GIF 合成", phase_text="检查帧尺寸")
        else:
            self._set_image_export_progress(current, max(1, total_outputs), label="GIF 合成", phase_text="编码中")
        if message:
            self._set_status(message)

    def _ensure_gif_frame_cache(
        self,
        jobs: list[VideoFrameJob],
        *,
        output_path: Path,
    ) -> tuple[list[Path], Path]:
        total = len(jobs)
        bucket_key = build_source_frame_bucket_key(
            global_export_settings=global_export_settings_from_settings(jobs[0].settings if jobs else {})
        )
        cache_plan = create_frame_cache_plan(
            output_path,
            bucket_kind=SOURCE_FRAME_BUCKET_KIND,
            bucket_key=bucket_key,
            persistent=True,
        )
        manifest = load_frame_manifest(cache_plan)
        cache_plan.frames_dir.mkdir(parents=True, exist_ok=True)

        frame_paths = [_cache_frame_output_path(cache_plan, index, suffix="png") for index in range(1, total + 1)]
        missing_jobs: list[VideoFrameJob] = []
        missing_targets: list[Path] = []
        missing_records: list[tuple[int, VideoFrameJob, str, str]] = []
        reused_count = 0

        for index, job in enumerate(jobs, start=1):
            source_signature = path_signature(job.path)
            frame_signature = build_source_frame_signature(render_settings=job.settings)
            reusable_path = reusable_frame_path(
                cache_plan,
                manifest,
                index=index,
                source_path=job.path,
                source_signature=source_signature,
                frame_signature=frame_signature,
            )
            if reusable_path is not None:
                frame_paths[index - 1] = reusable_path
                reused_count += 1
                continue
            missing_jobs.append(job)
            missing_targets.append(frame_paths[index - 1])
            missing_records.append((index, job, source_signature, frame_signature))

        if reused_count > 0:
            self._set_status(f"GIF 导出复用缓存帧 {reused_count}/{total}")

        if missing_jobs:
            ok_paths, failed = self._export_render_jobs_to_images(missing_jobs, missing_targets, label="GIF 帧导出")
            ok_path_set = {path.resolve(strict=False) for path in ok_paths}
            for index, job, source_signature, frame_signature in missing_records:
                frame_path = frame_paths[index - 1]
                if frame_path.resolve(strict=False) not in ok_path_set:
                    continue
                update_frame_manifest_record(
                    cache_plan,
                    manifest,
                    index=index,
                    source_path=job.path,
                    source_signature=source_signature,
                    frame_signature=frame_signature,
                    frame_path=frame_path,
                )
            write_frame_manifest(cache_plan, manifest, metadata={"total": total})
            if failed:
                raise RuntimeError(self._format_gif_frame_failures(failed))
        else:
            write_frame_manifest(cache_plan, manifest, metadata={"total": total})
        return (frame_paths, cache_plan.frames_dir)

    def _export_all_as_gif(self, paths: list[Path]) -> None:
        remembered_dir = getattr(self, "_batch_export_last_output_dir", None)
        if not isinstance(remembered_dir, Path) or not remembered_dir.is_dir():
            remembered_dir = getattr(self, "_image_export_last_output_dir", None)
        if not isinstance(remembered_dir, Path) or not remembered_dir.is_dir():
            remembered_dir = paths[0].parent if paths else None

        default_output_path = self._default_gif_output_path(paths, remembered_dir)
        output_path_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出 GIF",
            str(default_output_path),
            "GIF (*.gif);;All Files (*.*)",
        )
        if not output_path_text:
            return

        started_at = time.perf_counter()
        output_path = Path(output_path_text)
        gif_request = self.gif_export_panel.current_request()
        remembered_output_dir = output_path.parent.resolve(strict=False)
        self._batch_export_last_output_dir = remembered_output_dir
        self._save_batch_export_last_output_dir(remembered_output_dir)

        prepare_token = self._begin_image_export_progress(total=len(paths), label="GIF 导出", phase_text="准备中", worker_count=0)
        self._set_status(f"GIF 导出准备中: 0/{len(paths)}")

        def _on_prepare_progress(current: int, total: int) -> None:
            self._set_image_export_progress(current, total, label="GIF 导出", phase_text="准备中", worker_count=0)
            self._set_status(f"GIF 导出准备中: {current}/{total}")

        frame_paths: list[Path] = []
        frame_output_dir: Path | None = None
        try:
            jobs = self._build_export_render_jobs(
                paths,
                prefer_current_ui_for_current_path=True,
                progress_callback=_on_prepare_progress,
            )
            frame_paths, frame_output_dir = self._ensure_gif_frame_cache(jobs, output_path=output_path)
            gif_paths = self._export_gif_from_frame_paths(
                frame_paths,
                output_path,
                fps=gif_request.fps,
                loop=gif_request.loop,
                scale_factors=gif_request.scale_factors,
            )
        except Exception:
            self._reset_image_export_progress(expected_token=prepare_token)
            raise
        finally:
            if "jobs" in locals():
                self._close_render_job_sources(jobs)
        elapsed = time.perf_counter() - started_at
        timing_text = self._format_image_export_timing_summary(max(1, len(frame_paths)), elapsed)
        outputs_text = "，".join(path.name for path in gif_paths)
        self._clear_photo_export_dirty(paths)
        if gif_request.keep_frame_images and frame_output_dir is not None:
            self._set_status(f"GIF 导出完成: {outputs_text} | 帧目录 {frame_output_dir} | {timing_text}")
        else:
            self._set_status(f"GIF 导出完成: {outputs_text} | {timing_text}")

    def _format_gif_frame_failures(self, failed: list[str]) -> str:
        preview = "\n".join(failed[:8])
        if len(failed) > 8:
            preview += f"\n... 另有 {len(failed) - 8} 项失败"
        return f"单帧图片导出失败 {len(failed)} 项，已停止 GIF 合成。\n\n{preview}"

    def _export_gif_from_frame_paths(
        self,
        frame_paths: list[Path],
        output_path: Path,
        *,
        fps: float,
        loop: int,
        scale_factors: list[float],
    ) -> list[Path]:
        variant_paths = build_gif_variant_output_paths(output_path.with_suffix(".gif"), scale_factors)
        total_outputs = 1 + len(variant_paths)
        progress_token = self._begin_image_export_progress(total=total_outputs, label="GIF 合成", phase_text="编码中")
        try:
            options = GifExportOptions(
                output_path=output_path,
                fps=fps,
                loop=loop,
                scale_factors=tuple(scale_factors),
                background_color=self._gif_background_color_for_export(),
            )
            written_paths = export_gif(
                frame_paths,
                options,
                progress_callback=lambda progress: self._on_gif_export_progress(progress, total_outputs),
            )
            self._finish_image_export_progress(
                current=total_outputs,
                total=total_outputs,
                label="GIF 合成",
                token=progress_token,
            )
            return written_paths
        except Exception:
            self._reset_image_export_progress(expected_token=progress_token)
            raise

    def _normalized_image_export_target(self, target: Path, *, default_suffix: str) -> Path:
        if target.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif"}:
            return target
        return target.with_suffix(f".{default_suffix}")

    def _build_batch_image_targets(
        self,
        paths: list[Path],
        *,
        out_dir: Path,
        suffix: str,
    ) -> list[Path]:
        targets: list[Path] = []
        stem_counter: dict[str, int] = {}
        for path in paths:
            stem = f"{path.stem}__birdstamp"
            count = stem_counter.get(stem, 0)
            stem_counter[stem] = count + 1
            file_name = f"{stem}.{suffix}" if count == 0 else f"{stem}_{count + 1}.{suffix}"
            targets.append(out_dir / file_name)
        return targets

    def _render_and_save_image_task(
        self,
        task: _ImageExportTask,
        *,
        template_paths: dict[str, Path],
        bird_box_cache: dict[str, tuple[float, float, float, float] | None],
        bird_box_lock: threading.Lock,
    ) -> Path:
        rendered = render_video_frame(
            task.job,
            template_paths=template_paths,
            bird_box_cache=bird_box_cache,
            bird_box_lock=bird_box_lock,
        )
        try:
            self._save_image(rendered, task.target_path)
        finally:
            try:
                rendered.close()
            except Exception:
                pass
        return task.target_path

    def _close_render_job_sources(self, jobs: list[VideoFrameJob]) -> None:
        for job in jobs:
            source_image = getattr(job, "source_image", None)
            if source_image is None:
                continue
            try:
                source_image.close()
            except Exception:
                pass

    def _export_render_jobs_to_images(
        self,
        jobs: list[VideoFrameJob],
        targets: list[Path],
        *,
        label: str,
    ) -> tuple[list[Path], list[str]]:
        if len(jobs) != len(targets):
            raise ValueError("导出任务与目标路径数量不一致。")
        if not jobs:
            return ([], [])

        tasks = [_ImageExportTask(job=job, target_path=target) for job, target in zip(jobs, targets)]
        template_paths = dict(getattr(self, "template_paths", {}) or {})
        bird_box_cache: dict[str, tuple[float, float, float, float] | None] = {}
        bird_box_lock = threading.Lock()
        total = len(tasks)
        worker_count = resolve_video_render_workers(0, total)
        ok_paths: list[Path] = []
        failed: list[str] = []
        progress_token = self._begin_image_export_progress(total=total, label=label, worker_count=worker_count)

        self._set_status(f"{label}开始: 0/{total}，线程数 {worker_count}")
        try:
            if total == 1:
                try:
                    ok_paths.append(
                        self._render_and_save_image_task(
                            tasks[0],
                            template_paths=template_paths,
                            bird_box_cache=bird_box_cache,
                            bird_box_lock=bird_box_lock,
                        )
                    )
                    self._set_image_export_progress(1, total, label=label, worker_count=worker_count)
                    self._set_status(f"{label}进行中: 1/{total}，线程数 {worker_count}")
                except Exception as exc:
                    failed.append(f"{tasks[0].job.path.name}: {exc}")
                    self._set_image_export_progress(1, total, label=label, worker_count=worker_count)
                    self._set_status(f"{label}进行中: 1/{total}，线程数 {worker_count}")
            else:
                with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="birdstamp-image-export") as executor:
                    futures = {
                        executor.submit(
                            self._render_and_save_image_task,
                            task,
                            template_paths=template_paths,
                            bird_box_cache=bird_box_cache,
                            bird_box_lock=bird_box_lock,
                        ): task
                        for task in tasks
                    }
                    completed = 0
                    for future in as_completed(futures):
                        task = futures[future]
                        try:
                            ok_paths.append(future.result())
                        except Exception as exc:
                            failed.append(f"{task.job.path.name}: {exc}")
                        completed += 1
                        self._set_image_export_progress(completed, total, label=label, worker_count=worker_count)
                        self._set_status(f"{label}进行中: {completed}/{total}，线程数 {worker_count}")
        finally:
            self._close_render_job_sources(jobs)
            self._finish_image_export_progress(
                current=len(ok_paths) + len(failed),
                total=total,
                label=label,
                token=progress_token,
            )

        if total == 1 and failed:
            raise RuntimeError(failed[0])
        return (ok_paths, failed)

    def _save_image(self, image: Image.Image, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix == ".png":
            image.save(path, format="PNG", optimize=True)
            return

        if suffix not in {".jpg", ".jpeg"}:
            path = path.with_suffix(".jpg")
        image.save(path, format="JPEG", quality=92, optimize=True, progressive=True)
