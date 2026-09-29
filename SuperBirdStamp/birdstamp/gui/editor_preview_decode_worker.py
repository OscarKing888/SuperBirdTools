from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal
from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._work_policy import WorkKind

from birdstamp.decoders.image_decoder import decode_image_for_preview, read_decoded_image_size
from birdstamp import perf


def cached_preview_image(path: Path, max_long_edge: int) -> Image.Image | None:
    """复用 Viewer 的逐文件缓存作用域；缓存未命中时不解码原图或生成缩略图。"""
    from app_common.file_browser._browser_core import (
        _existing_persistent_thumb_cache_path_for_file,
        _thumb_disk_cache_path,
        _thumb_source_stamp,
    )

    source = str(path)
    directory = str(path.parent)
    sizes = tuple(size for size in (2048, 1024, 512, 256, 128) if size <= max_long_edge)
    stamp = _thumb_source_stamp(source)
    cache_path = _existing_persistent_thumb_cache_path_for_file(
        source, directory, requested_size=max_long_edge, source_stamp=stamp,
        candidate_sizes=sizes, selected_dir=directory,
    )
    candidates = [cache_path] if cache_path else []
    candidates.extend(_thumb_disk_cache_path(source, stamp, size, directory) for size in sizes)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            with Image.open(candidate) as cached:
                if max(cached.size) > max_long_edge:
                    continue
                return cached.convert("RGB")
        except (OSError, ValueError):
            continue
    return None


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
            if not self.show_raw:
                with perf.span("preview.cached_thumbnail", path=str(self.path)):
                    try:
                        image = cached_preview_image(
                            self.path, min(512, self.max_long_edge) if self.quick_only else self.max_long_edge,
                        )
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
            edge = min(512, self.max_long_edge) if self.quick_only else self.max_long_edge
            with perf.span("preview.quick_decode" if self.quick_only else "preview.decode", path=str(self.path)):
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
        self._max_long_edge = max(1, int(max_long_edge))
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
