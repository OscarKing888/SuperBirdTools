"""BirdStamp adapter for SuperViewer's exact 256-pixel thumbnail cache."""
from __future__ import annotations

from pathlib import Path
import hashlib
import os

from PIL import Image
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

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
    target = shared or local
    if not target:
        return False
    with image.convert("RGB") as rgb:
        if max(rgb.size) > THUMB_EDGE:
            rgb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
        data = rgb.tobytes()
        qimage = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
    return _write_persistent_thumb_cache_image(target, qimage, stamp)


class SharedThumbnailScope:
    """Window-session permission for new .superpicky directories."""

    def __init__(self) -> None:
        self.declined: set[Path] = set()

    @staticmethod
    def _ask(target: Path, parent) -> bool:
        application = QApplication.instance()
        if application is None or application.platformName() == "offscreen":
            return False
        try:
            answer = QMessageBox.question(
                parent, "创建缩略图缓存目录",
                f"是否在当前目录创建：\n{target}\n\n只会创建目录用于预览缓存，不会创建 report.db。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
        except Exception:
            return False
        return answer == QMessageBox.StandardButton.Yes

    def ensure(self, path: Path, parent=None) -> bool:
        directory = path.parent.resolve(strict=False)
        if _find_cache_superpicky_dir_for_file(str(path), str(directory)):
            return True
        if directory in self.declined:
            return False
        target = directory / ".superpicky"
        if not self._ask(target, parent):
            self.declined.add(directory)
            return False
        try:
            target.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            self.declined.add(directory)
            application = QApplication.instance()
            if application is not None and application.platformName() != "offscreen":
                QMessageBox.warning(parent, "无法创建缩略图缓存目录", f"无法创建：\n{target}\n\n{exc}")
            return False
