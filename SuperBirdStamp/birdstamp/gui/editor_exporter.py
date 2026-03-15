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

from birdstamp.gui import editor_options
from birdstamp.video_export import VideoFrameJob, render_video_frame, resolve_video_render_workers

OUTPUT_FORMAT_OPTIONS = editor_options.OUTPUT_FORMAT_OPTIONS
_IMAGE_EXPORT_PROGRESS_HIDE_DELAY_MS = 600


@dataclass(slots=True)
class _ImageExportTask:
    job: VideoFrameJob
    target_path: Path


class _BirdStampExporterMixin:
    """Mixin: export_current, export_all, _save_image."""

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
        for attr_name in ("export_current_btn", "export_batch_btn"):
            widget = getattr(self, attr_name, None)
            if widget is None:
                continue
            try:
                widget.setEnabled(not busy)
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
        elapsed = time.perf_counter() - started_at
        timing_text = self._format_image_export_timing_summary(len(targets), elapsed)
        self._set_status(f"批量导出完成: 成功 {len(ok_paths)}，失败 {len(failed)} | {timing_text}")

    def _normalized_image_export_target(self, target: Path, *, default_suffix: str) -> Path:
        if target.suffix.lower() in {".png", ".jpg", ".jpeg"}:
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
