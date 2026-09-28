from __future__ import annotations

import threading
import math

from .editor_utils import path_key

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, render_sequence_preview_frame
from birdstamp.export_stage.sequence_photo_error import SequencePhotoError, sequence_photo_errors
from . import editor_options
from birdstamp.image_dejitter.sequence_geometry import aligned_crop_plan, render_aligned_thumbnail
from .sequence_preview_frame import SequencePreviewFrame


class EditorSequencePreviewWorker(QThread):
    ready = pyqtSignal(int, object, object)
    quick_ready = pyqtSignal(int, object, object)
    diagnostics = pyqtSignal(int, object)
    progress = pyqtSignal(int, str)
    progress_counts = pyqtSignal(int, int, int, str)
    failed = pyqtSignal(int, str)

    def __init__(self, *, token, path, seeds=(), template_paths=None, sequence=None, bird_boxes=None, restore_only=False, cache=None, parent=None):
        super().__init__(parent)
        self.token, self.path = token, path
        self.seeds, self.template_paths = tuple(seeds), dict(template_paths or {})
        self.sequence, self.bird_boxes = sequence, dict(bird_boxes or {})
        self.cancel_event = threading.Event()
        self.restore_only = restore_only
        self.cache = cache
        self.failure_path = None

    def cancel(self):
        self.cancel_event.set()
        self.requestInterruption()

    def run(self):
        sources = {}
        sources_lock = threading.Lock()
        # 按组大小缩小快速预览，原图缩略图 + 对齐缩略图总量有明确上限。
        edge = min(editor_options.DEJITTER_QUICK_MAX_EDGE,
                   max(1, int(math.sqrt(editor_options.DEJITTER_QUICK_CACHE_BYTES /
                                        (8 * max(1, len(self.seeds)))))))

        def capture(path, image):
            # 各 action 并行缩小独占原图；只在交接有界小图时锁住字典。
            with image.copy() as small:
                small.thumbnail((edge, edge), Image.Resampling.BILINEAR)
                preview = small.convert('RGB')
            with sources_lock:
                old = sources.get(path_key(path))
                sources[path_key(path)] = preview
                if old is not None:
                    old.close()

        def report_counts(current, total, stage):
            # 停留升级清晰帧不重置已完成的整组分析进度。
            if self.sequence is None and not self.cancel_event.is_set():
                self.progress_counts.emit(self.token, current, total, stage)

        try:
            sequence = self.sequence
            if sequence is None:
                report_counts(0, 0, '读取成片缓存')
                cached = self.cache.load(self.seeds, cancelled=self.cancel_event.is_set) if self.cache else None
                if cached is not None:
                    sequence, frames = cached
                    if not self.cancel_event.is_set():
                        self.quick_ready.emit(self.token, sequence, frames)
                elif self.restore_only:
                    raise ValueError('已有成片缓存缺失或已失效，请重新分析。')
            sequence = sequence or prepare_sequence_preview(
                self.seeds, self.template_paths, cancel_event=self.cancel_event,
                progress=lambda text: self.progress.emit(self.token, text), bird_boxes=self.bird_boxes,
                preview_source=capture, allow_partial=True,
                progress_counts=report_counts,
                tracking_ready=lambda key, tracking, signatures: self.diagnostics.emit(
                    self.token, (key, tracking, signatures)),
            )
            if self.cancel_event.is_set():
                return
            if sources:
                frames = {}
                report_counts(0, len(sequence.jobs), '生成快速预览')
                for index, (key, job) in enumerate(sequence.jobs.items(), 1):
                    if self.cancel_event.is_set():
                        return
                    with sequence_photo_errors(job.path), sources.pop(key) as small:
                        width, height = sequence.source_sizes[key]
                        box = sequence.pixel_boxes[key]
                        with render_aligned_thumbnail(small, (width,height), box, edge) as aligned:
                            frames[key] = SequencePreviewFrame(
                                job.path, pil_qimage(aligned), (width,height), sequence.output_size,
                                aligned_crop_plan((width,height), box), pil_qimage(small))
                    report_counts(index, len(sequence.jobs), '生成快速预览')
                if not self.cancel_event.is_set():
                    if self.cache and not sequence.partial:
                        self.progress.emit(self.token, '保存成片分析与快速预览缓存…')
                        report_counts(0, 0, '保存预览缓存')
                        self.cache.save(sequence, frames, cancelled=self.cancel_event.is_set)
                    if not self.cancel_event.is_set():
                        self.quick_ready.emit(self.token, sequence, frames)
                if sequence.partial:
                    if not self.cancel_event.is_set():
                        self.failure_path = sequence.failure.source_path
                        self.failed.emit(self.token, str(sequence.failure))
                    return
            if self.cancel_event.is_set():
                return
            cached_frame = self.cache.load_sharp(sequence, self.path) if self.cache else None
            if cached_frame is not None:
                if not self.cancel_event.is_set() and sequence.files_current():
                    self.ready.emit(self.token, sequence, cached_frame)
                return
            self.progress.emit(self.token, '生成当前照片成片预览…')
            report_counts(0, 0, '生成清晰预览')
            context = render_sequence_preview_frame(sequence, self.path)
            with sequence_photo_errors(self.path), context.image as image:
                output_size = image.size
                image.thumbnail((editor_options.DEJITTER_PREVIEW_MAX_EDGE,) * 2, Image.Resampling.LANCZOS)
                rgb = image.convert('RGB')
                try:
                    qimage = QImage(rgb.tobytes(), rgb.width, rgb.height, rgb.width * 3,
                                    QImage.Format.Format_RGB888).copy()
                finally:
                    if rgb is not image:
                        rgb.close()
            frame = SequencePreviewFrame(self.path, qimage, context.source_size, output_size, context.crop_plan)
            if not self.cancel_event.is_set():
                if self.cache:
                    self.cache.save_sharp(sequence, frame)
                if not self.cancel_event.is_set():
                    self.ready.emit(self.token, sequence, frame)
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.failure_path = exc.source_path if isinstance(exc, SequencePhotoError) else None
                self.failed.emit(self.token, str(exc))
        finally:
            for image in sources.values():
                image.close()


def pil_qimage(image):
    return QImage(image.tobytes(), image.width, image.height, image.width * 3,
                  QImage.Format.Format_RGB888).copy()


class EditorSequenceExportWorker(QThread):
    completed = pyqtSignal(int, str)
    progress = pyqtSignal(int, str)
    progress_counts = pyqtSignal(int, int, int, str)
    failed = pyqtSignal(int, str)

    def __init__(self, *, token, sequence, destination, output_format, parent=None):
        super().__init__(parent)
        self.token, self.sequence = token, sequence
        self.destination, self.output_format = destination, output_format
        self.cancel_event = threading.Event()
        self.exported_paths = ()

    def cancel(self):
        self.cancel_event.set()
        self.requestInterruption()

    def run(self):
        from birdstamp.export_stage.sequence_export import export_aligned_sequence, sequence_export_targets
        try:
            folder = export_aligned_sequence(
                self.sequence, self.destination, output_format=self.output_format,
                cancel_event=self.cancel_event,
                progress=lambda text: self.progress.emit(self.token, text),
                progress_counts=lambda current, total, stage: self.progress_counts.emit(
                    self.token, current, total, stage),
            )
            self.exported_paths = sequence_export_targets(
                (job.path for job in self.sequence.jobs.values()), folder, self.output_format)
            self.completed.emit(self.token, str(folder))
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.failed.emit(self.token, str(exc))
