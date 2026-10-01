"""BirdStamp adapter for SuperViewer's exact 256-pixel thumbnail cache."""
from __future__ import annotations

from pathlib import Path
import hashlib
import logging
import os

from PIL import Image
from PyQt6.QtGui import QImage

from app_common.file_browser._browser_core import (
    _existing_persistent_thumb_cache_path_for_exact_size,
    _find_cache_superpicky_dir_for_file,
    _persistent_thumb_cache_path_for_file,
    _thumb_disk_cache_path,
    _thumb_image_matches_size_for_source,
    _thumb_source_stamp,
    _write_persistent_thumb_cache_image,
)
from birdstamp import config


_log = logging.getLogger(__name__)
THUMB_EDGE = 256


def _cache_paths(path: Path) -> tuple[str, str, float]:
    source = str(path)
    directory = str(path.parent)
    stamp = _thumb_source_stamp(source)
    shared = _persistent_thumb_cache_path_for_file(
        source, directory, THUMB_EDGE, selected_dir=directory,
    )
    key = hashlib.sha256(f"256:{os.path.normcase(os.path.abspath(source))}:{stamp}".encode("utf-8")).hexdigest()
    local = str(config.get_user_data_dir() / "config" / "cache" / "source_preview" / "256" / f"{key}.jpg")
    return shared, local, stamp


def local_thumbnail_path(path: Path) -> Path:
    return Path(_cache_paths(path)[1])


def read_thumbnail(path: Path) -> Image.Image | None:
    """Read only a valid exact-tier image; never decode the original here."""
    source = str(path)
    directory = str(path.parent)
    shared, local, stamp = _cache_paths(path)
    valid_shared = _existing_persistent_thumb_cache_path_for_exact_size(
        source, directory, THUMB_EDGE, stamp, selected_dir=directory,
    )
    viewer_local = _thumb_disk_cache_path(source, stamp, THUMB_EDGE, directory)
    for candidate in (valid_shared, viewer_local, local):
        if not candidate:
            continue
        try:
            if candidate != valid_shared and (not Path(candidate).is_file() or Path(candidate).stat().st_mtime + 0.5 < stamp):
                continue
            with Image.open(candidate) as image:
                if not _thumb_image_matches_size_for_source(source, *image.size, THUMB_EDGE):
                    continue
                return image.convert("RGB")
        except (OSError, ValueError):
            continue
    return None


def write_thumbnail(path: Path, image: Image.Image) -> bool:
    """Use Viewer's atomic writer and per-file scope; local cache is the fallback."""
    shared, local, stamp = _cache_paths(path)
    if shared and _existing_persistent_thumb_cache_path_for_exact_size(
        str(path), str(path.parent), THUMB_EDGE, stamp, selected_dir=str(path.parent),
    ):
        return True
    with image.convert("RGB") as rgb:
        if max(rgb.size) > THUMB_EDGE:
            rgb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
        data = rgb.tobytes()
        qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
    for target in (shared, local):
        if not target:
            continue
        try:
            if _write_persistent_thumb_cache_image(target, qimage, stamp):
                return True
        except OSError as exc:
            _log.warning("缩略图缓存写入失败 path=%s: %s", target, exc)
            continue
        _log.warning("缩略图缓存写入失败 path=%s", target)
    return False


class SharedThumbnailScope:
    """自动创建共享缓存目录，并记住本窗口会话内创建失败的目录。"""

    def __init__(self) -> None:
        self.failed_dirs: set[Path] = set()

    def ensure(self, path: Path, parent=None) -> bool:
        # 与缓存读取保持相同的路径语义，不解析符号链接到另一棵目录树。
        directory = Path(os.path.abspath(path.parent))
        if _find_cache_superpicky_dir_for_file(str(path), str(directory)):
            return True
        if directory in self.failed_dirs:
            return False
        target = directory / ".superpicky"
        try:
            target.mkdir(exist_ok=True)
            return True
        except OSError as exc:
            self.failed_dirs.add(directory)
            _log.warning("无法创建缩略图缓存目录，回退本地缓存 path=%s: %s", target, exc)
            return False
