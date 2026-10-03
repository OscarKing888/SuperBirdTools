"""Background bird-box detection for preview overlay refresh."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from PIL import Image

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY
from birdstamp.gui.preview_source_geometry import preview_to_camera_box
from birdstamp.gui.editor_core import detect_primary_bird_box


class BirdDetectWorker(QThread):
    """Run YOLO bird detection off the UI thread; results feed preview overlay."""

    result_ready = pyqtSignal(str, object)

    def __init__(
        self,
        signature: str,
        source_image: Image.Image,
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._signature = signature
        self._source_image = source_image

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            bird_box = preview_to_camera_box(detect_primary_bird_box(self._source_image),
                                            getattr(self._source_image, 'info', {}).get(RAW_FOCUS_CROP_KEY))
            if self.isInterruptionRequested():
                return
            self.result_ready.emit(self._signature, bird_box)
        finally:
            try:
                self._source_image.close()
            except Exception:
                pass
