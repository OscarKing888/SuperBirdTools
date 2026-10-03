"""Background bird-box detection for preview overlay refresh."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage
from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._work_policy import WorkKind

from PIL import Image

from app_common.log import get_logger
from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY
from birdstamp.gui.preview_source_geometry import preview_to_camera_box
from birdstamp.gui.editor_core import detect_primary_bird_box


class BirdDetectAction(WorkerAction):
    def __init__(self, image, camera_crop=None, *, cancelled):
        super().__init__(cancelled=cancelled)
        self.image, self.camera_crop = image, camera_crop

    def execute(self):
        if self.is_cancelled():
            return None
        image = self.image
        converted = isinstance(image, QImage)
        if converted:
            from PIL.ImageQt import fromqimage
            image = fromqimage(image)
        try:
            box = detect_primary_bird_box(image)
            crop = self.camera_crop or getattr(image, 'info', {}).get(RAW_FOCUS_CROP_KEY)
            return preview_to_camera_box(box, crop)
        finally:
            if converted:
                image.close()


class BirdDetectWorker(QThread):
    """Run YOLO bird detection off the UI thread; results feed preview overlay."""

    result_ready = pyqtSignal(str, object)

    def __init__(
        self,
        signature: str,
        source_image: Image.Image,
        *,
        parent=None,
        pool=None,
        camera_crop=None,
    ) -> None:
        super().__init__(parent)
        self._signature = signature
        self._source_image = source_image
        self._pool, self._camera_crop = pool, camera_crop

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            action = BirdDetectAction(self._source_image, self._camera_crop, cancelled=self.isInterruptionRequested)
            bird_box = (action.execute() if self._pool is None else
                        self._pool.submit_action(action, kind=WorkKind.ANALYSIS).result())
            if self.isInterruptionRequested():
                return
            self.result_ready.emit(self._signature, bird_box)
        except Exception as exc:
            if not self.isInterruptionRequested():
                get_logger("birdstamp.bird_overlay").warning("鸟体识别失败 signature=%s: %s", self._signature, exc)
        finally:
            try:
                self._source_image.close()
            except Exception:
                pass
