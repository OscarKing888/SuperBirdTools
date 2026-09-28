"""Load bounded original-photo frames for the editor's sequence transport."""
from collections import deque
import logging
from pathlib import Path
from threading import Condition

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

from birdstamp.decoders.image_decoder import decode_image_for_preview, read_decoded_image_size
from .editor_preview_decode_worker import cached_preview_image


_log = logging.getLogger(__name__)


class SourceQuickLoader(QThread):
    ready = pyqtSignal(str, str, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._condition = Condition()
        self._pending = deque()
        self._queued = set()
        self._inflight = set()
        self._failed = set()

    def enqueue(self, entries):
        """Put the current frame and its neighbors ahead of older prefetch work."""
        with self._condition:
            for signature, path in reversed(tuple(entries)):
                if signature in self._queued or signature in self._inflight or signature in self._failed:
                    continue
                self._queued.add(signature)
                self._pending.appendleft((signature, Path(path)))
            self._condition.notify_all()

    def stop(self):
        self.requestInterruption()
        with self._condition:
            self._condition.notify_all()

    def run(self):
        while not self.isInterruptionRequested():
            with self._condition:
                while not self._pending and not self.isInterruptionRequested():
                    self._condition.wait()
                if self.isInterruptionRequested():
                    return
                signature, path = self._pending.popleft()
                self._queued.discard(signature)
                self._inflight.add(signature)
            image = None
            try:
                try:
                    image = cached_preview_image(path, 512)
                except Exception:
                    image = None
                full_size = None
                if image is not None:
                    try:
                        full_size = read_decoded_image_size(path)
                    except Exception:
                        image.close()
                        image = None
                if image is None:
                    image = decode_image_for_preview(path, max_long_edge=512, decoder="auto")
                    properties = image.info.get("birdstamp_source_properties") or {}
                    full_size = properties.get("size") or image.size
                if self.isInterruptionRequested():
                    return
                if image.mode != "RGB":
                    converted = image.convert("RGB")
                    image.close()
                    image = converted
                self.ready.emit(signature, str(path), image, tuple(full_size or image.size))
                image = None
            except Exception as exc:
                _log.warning("[SourceQuickLoader] failed path=%s: %s", path, exc)
                with self._condition:
                    self._failed.add(signature)
            finally:
                if image is not None:
                    image.close()
                with self._condition:
                    self._inflight.discard(signature)
