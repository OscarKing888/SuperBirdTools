"""Background bird-box detection for preview overlay refresh."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from PIL import Image

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
        if self.isInterruptionRequested():
            return
        bird_box = detect_primary_bird_box(self._source_image)
        if self.isInterruptionRequested():
            return
        self.result_ready.emit(self._signature, bird_box)
