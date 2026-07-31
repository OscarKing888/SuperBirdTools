from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

from birdstamp.decoders.image_decoder import decode_image_for_preview, read_decoded_image_size


class EditorPreviewDecodeWorker(QThread):
    """Decode one editor preview without blocking the Qt GUI thread."""

    decoded = pyqtSignal(int, str, object, object)
    failed = pyqtSignal(int, str, str)

    def __init__(
        self,
        token: int,
        path: Path,
        *,
        max_long_edge: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._token = int(token)
        self._path = Path(path).resolve(strict=False)
        self._max_long_edge = max(1, int(max_long_edge))

    def run(self) -> None:
        image: Image.Image | None = None
        handed_off = False
        try:
            if self.isInterruptionRequested():
                return
            image = decode_image_for_preview(
                self._path,
                max_long_edge=self._max_long_edge,
                decoder="auto",
            )
            try:
                full_size = read_decoded_image_size(self._path)
            except Exception:
                full_size = image.size
            if self.isInterruptionRequested():
                return
            self.decoded.emit(self._token, str(self._path), image, full_size)
            handed_off = True
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(self._token, str(self._path), str(exc))
        finally:
            if image is not None and not handed_off:
                try:
                    image.close()
                except Exception:
                    pass


__all__ = ["EditorPreviewDecodeWorker"]
