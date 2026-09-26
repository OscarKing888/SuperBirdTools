from __future__ import annotations

from dataclasses import dataclass
import threading

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, render_sequence_preview_frame
from . import editor_options


@dataclass(slots=True)
class SequencePreviewFrame:
    path: object
    image: QImage
    source_size: tuple
    output_size: tuple
    crop_plan: tuple


class EditorSequencePreviewWorker(QThread):
    ready = pyqtSignal(int, object, object)
    progress = pyqtSignal(int, str)
    failed = pyqtSignal(int, str)

    def __init__(self, *, token, path, seeds=(), template_paths=None, sequence=None, bird_boxes=None, parent=None):
        super().__init__(parent)
        self.token, self.path = token, path
        self.seeds, self.template_paths = tuple(seeds), dict(template_paths or {})
        self.sequence, self.bird_boxes = sequence, dict(bird_boxes or {})
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()
        self.requestInterruption()

    def run(self):
        try:
            sequence = self.sequence or prepare_sequence_preview(
                self.seeds, self.template_paths, cancel_event=self.cancel_event,
                progress=lambda text: self.progress.emit(self.token, text), bird_boxes=self.bird_boxes,
            )
            if self.cancel_event.is_set():
                return
            self.progress.emit(self.token, '生成当前照片成片预览…')
            context = render_sequence_preview_frame(sequence, self.path)
            with context.image as image:
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
                self.ready.emit(self.token, sequence, frame)
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.failed.emit(self.token, str(exc))


class EditorSequenceExportWorker(QThread):
    completed = pyqtSignal(int, str)
    progress = pyqtSignal(int, str)
    failed = pyqtSignal(int, str)

    def __init__(self, *, token, sequence, destination, output_format, parent=None):
        super().__init__(parent)
        self.token, self.sequence = token, sequence
        self.destination, self.output_format = destination, output_format
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()
        self.requestInterruption()

    def run(self):
        from birdstamp.export_stage.sequence_export import export_aligned_sequence
        try:
            folder = export_aligned_sequence(
                self.sequence, self.destination, output_format=self.output_format,
                cancel_event=self.cancel_event,
                progress=lambda text: self.progress.emit(self.token, text),
            )
            self.completed.emit(self.token, str(folder))
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.failed.emit(self.token, str(exc))
