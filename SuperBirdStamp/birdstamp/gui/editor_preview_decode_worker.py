from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

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


class EditorPreviewDecodeWorker(QThread):
    """Decode one editor preview without blocking the Qt GUI thread."""

    decoded = pyqtSignal(int, str, object, object)
    quick_decoded = pyqtSignal(int, str, object, object)
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
            with perf.span("preview.cached_thumbnail", path=str(self._path)):
                try:
                    image = cached_preview_image(self._path, self._max_long_edge)
                except Exception:
                    image = None  # 缓存损坏或不可读仍继续解码源图。
            full_size = None
            if image is not None:
                try:
                    with perf.span("preview.source_size", path=str(self._path)):
                        full_size = read_decoded_image_size(self._path)
                except Exception:
                    pass
            if self.isInterruptionRequested():
                return
            # 只有原尺寸已知才显示可编辑预览，避免把缩略图像素当作裁切留边的单位。
            if image is not None:
                if full_size is not None:
                    self.quick_decoded.emit(self._token, str(self._path), image, full_size)
                else:
                    image.close()
                image = None
            if self.isInterruptionRequested():
                return
            with perf.span("preview.decode", path=str(self._path)):
                image = decode_image_for_preview(
                    self._path, max_long_edge=self._max_long_edge, decoder="auto",
                )
            if self.isInterruptionRequested():
                return
            properties = image.info.get("birdstamp_source_properties") or {}
            full_size = properties.get("size") or full_size
            if full_size is None:
                try:
                    full_size = read_decoded_image_size(self._path)
                except Exception:
                    full_size = image.size
            if self.isInterruptionRequested():
                return
            self.decoded.emit(self._token, str(self._path), image, full_size or image.size)
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
