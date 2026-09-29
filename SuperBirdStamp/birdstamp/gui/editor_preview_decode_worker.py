from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal
from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._work_policy import WorkKind
from app_common.image_formats import HEIF_EXTENSIONS, RAW_EXTENSIONS

from birdstamp.decoders.image_decoder import decode_image, decode_image_for_preview, read_decoded_image_size
from birdstamp import perf
from .editor_shared_thumb_cache import THUMB_EDGE, read_thumbnail, write_thumbnail


def cached_preview_image(path: Path, max_long_edge: int) -> Image.Image | None:
    """复用 Viewer 的精确 256 档；缓存未命中时不解码原图。"""
    return read_thumbnail(path) if max_long_edge == 0 or max_long_edge >= THUMB_EDGE else None


class EditorPreviewAction(WorkerAction):
    """读取单张预览；Qt 协调线程与 A/B 视图共用同一动作。"""

    def __init__(self, path, max_long_edge, quick_only, emit_quick, emit_full, *, cancelled, show_raw=False):
        super().__init__(cancelled=cancelled)
        self.path = Path(path)
        self.max_long_edge = max_long_edge
        self.quick_only = quick_only
        self.emit_quick = emit_quick
        self.emit_full = emit_full
        self.show_raw = bool(show_raw)

    def execute(self):
        image: Image.Image | None = None
        try:
            if self.is_cancelled():
                return
            with perf.span("preview.cached_thumbnail", path=str(self.path)):
                try:
                    image = cached_preview_image(self.path, self.max_long_edge or THUMB_EDGE)
                except Exception:
                    image = None
            full_size = None
            if image is not None:
                try:
                    with perf.span("preview.source_size", path=str(self.path)):
                        full_size = read_decoded_image_size(self.path)
                except Exception:
                    pass
            if self.is_cancelled():
                return
            if image is not None:
                if full_size is not None:
                    self.emit_quick(image, full_size)
                    image = None
                else:
                    image.close()
                    image = None
                if self.quick_only and full_size is not None:
                    return
            if self.is_cancelled():
                return
            if (image is None and not self.quick_only and self.max_long_edge == 0
                    and self.path.suffix.lower() not in HEIF_EXTENSIONS | RAW_EXTENSIONS):
                quick = decode_image_for_preview(self.path, max_long_edge=THUMB_EDGE, decoder="auto")
                try:
                    properties = quick.info.get("birdstamp_source_properties") or {}
                    quick_size = properties.get("size") or read_decoded_image_size(self.path)
                    write_thumbnail(self.path, quick)
                    if not self.is_cancelled():
                        self.emit_quick(quick, quick_size)
                        quick = None
                finally:
                    if quick is not None:
                        quick.close()
            if self.is_cancelled():
                return
            edge = (min(THUMB_EDGE, self.max_long_edge) if self.max_long_edge else THUMB_EDGE)
            if not self.quick_only:
                edge = self.max_long_edge
            with perf.span("preview.quick_decode" if self.quick_only else "preview.decode", path=str(self.path)):
                if edge == 0:
                    image = (decode_image_for_preview(self.path, max_long_edge=2**31 - 1,
                                                      decoder="auto", show_raw=True)
                             if self.show_raw else decode_image(self.path, decoder="auto"))
                else:
                    kwargs = {"show_raw": True} if self.show_raw else {}
                    image = decode_image_for_preview(self.path, max_long_edge=edge, decoder="auto", **kwargs)
            if self.is_cancelled():
                return
            properties = image.info.get("birdstamp_source_properties") or {}
            full_size = properties.get("size") or full_size
            if full_size is None:
                try:
                    full_size = read_decoded_image_size(self.path)
                except Exception:
                    full_size = image.size
            if self.is_cancelled():
                return
            if self.quick_only:
                if edge >= THUMB_EDGE:
                    write_thumbnail(self.path, image)
                self.emit_quick(image, full_size)
            else:
                self.emit_full(image, full_size)
            image = None
        finally:
            if image is not None:
                image.close()


class EditorPreviewDecodeWorker(QThread):
    """Qt 信号协调器；实际解码由共享池中的 WorkerAction 执行。"""

    decoded = pyqtSignal(int, str, object, object)
    quick_decoded = pyqtSignal(int, str, object, object)
    failed = pyqtSignal(int, str, str)

    def __init__(
        self,
        token: int,
        path: Path,
        *,
        max_long_edge: int,
        quick_only: bool = False,
        show_raw: bool = False,
        pool=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._token = int(token)
        self._path = Path(path).resolve(strict=False)
        self._max_long_edge = max(0, int(max_long_edge))
        self._quick_only = bool(quick_only)
        self._show_raw = bool(show_raw)
        self._pool = pool

    def run(self) -> None:
        try:
            action = EditorPreviewAction(
                self._path, self._max_long_edge, self._quick_only,
                lambda image, size: self.quick_decoded.emit(self._token, str(self._path), image, size),
                lambda image, size: self.decoded.emit(self._token, str(self._path), image, size),
                cancelled=self.isInterruptionRequested, show_raw=self._show_raw,
            )
            if self._pool is None:
                action.execute()
            else:
                self._pool.submit_action(action, kind=WorkKind.METADATA).result()
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(self._token, str(self._path), str(exc))


__all__ = ["EditorPreviewAction", "EditorPreviewDecodeWorker"]
