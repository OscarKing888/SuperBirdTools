"""Validated, bounded index of user-visible PNG/JPG exports.

The files belong to the user.  This index only remembers where a completed
render can be found; deleting or changing an export turns it into a cache miss.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Iterable

from PIL import Image

from birdstamp.config import get_config_path
from birdstamp.export_frame_cache import hash_payload, normalized_path_text, path_signature


INDEX_VERSION = 1
MAX_RECORDS = 50_000
MAX_PATHS_PER_RECORD = 3
_LOCK = threading.RLock()


def default_exported_image_index_path() -> Path:
    return get_config_path().parent / "cache" / "exported_images_v1.json"


def _format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "png"
    if suffix in {".jpg", ".jpeg"}:
        return "jpg"
    return ""


def _key(source: Path, source_signature: str, frame_signature: str, image_format: str) -> str:
    return hash_payload({
        "source": normalized_path_text(source),
        "source_signature": source_signature,
        "frame_signature": frame_signature,
        "format": image_format,
    })


class ExportedImageIndex:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_exported_image_index_path()

    def load(self) -> dict[str, list[dict]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, dict) or payload.get("version") != INDEX_VERSION:
            return {}
        records = payload.get("records")
        if not isinstance(records, dict):
            return {}
        return {
            key: items[:MAX_PATHS_PER_RECORD]
            for key, items in records.items()
            if isinstance(key, str) and isinstance(items, list)
        }

    def find(
        self,
        records: dict[str, list[dict]],
        *,
        source: Path,
        frame_signature: str,
    ) -> Path | None:
        source_signature = path_signature(source)
        for image_format in ("png", "jpg"):
            for item in records.get(_key(source, source_signature, frame_signature, image_format), ()):
                if not isinstance(item, dict):
                    continue
                try:
                    output = Path(item["path"])
                    if (_format(output) == image_format and output.is_file()
                            and path_signature(output) == item.get("signature")):
                        return output
                except (OSError, TypeError, ValueError, KeyError):
                    continue
        return None

    def add_many(self, completed: Iterable[tuple[Path, str, str, Path]]) -> None:
        """Record (source, source signature, render signature, output) tuples."""
        with _LOCK:
            records = self.load()
            changed = False
            now = time.time_ns()
            for source, source_signature, frame_signature, output in completed:
                image_format = _format(output)
                if (not image_format or not output.is_file()
                        or path_signature(source) != source_signature):
                    continue
                key = _key(source, source_signature, frame_signature, image_format)
                item = {
                    "path": str(output.resolve(strict=False)),
                    "signature": path_signature(output),
                    "updated_ns": now,
                }
                earlier = [old for old in records.get(key, ())
                           if isinstance(old, dict) and old.get("path") != item["path"]]
                records[key] = [item, *earlier][:MAX_PATHS_PER_RECORD]
                changed = True
            if not changed:
                return
            if len(records) > MAX_RECORDS:
                records = dict(sorted(
                    records.items(),
                    key=lambda pair: max((int(item.get("updated_ns", 0)) for item in pair[1]
                                          if isinstance(item, dict)), default=0),
                    reverse=True,
                )[:MAX_RECORDS])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=".exported-images-", suffix=".json", dir=self.path.parent,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({"version": INDEX_VERSION, "records": records},
                              stream, ensure_ascii=False, separators=(",", ":"))
                os.replace(temp_name, self.path)
            finally:
                Path(temp_name).unlink(missing_ok=True)


def materialize_exported_image(exported: Path, target: Path, *, original: Path) -> bool:
    """Make a PNG cache frame from a validated user export; false if unreadable."""
    image_format = _format(exported)
    if not image_format:
        return False
    try:
        with Image.open(exported) as image:
            expected_format = "PNG" if image_format == "png" else "JPEG"
            if image.format != expected_format:
                return False
            if image_format == "jpg":
                image.load()
                rgb = image.convert("RGB")
            else:
                image.verify()
                rgb = None
    except (OSError, ValueError, SyntaxError):
        return False
    if image_format == "jpg":
        from birdstamp.export_metadata import save_export_image

        try:
            save_export_image(rgb, target, source_path=original, format="PNG", compress_level=1)
        finally:
            rgb.close()
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".birdstamp-frame-", suffix=".png", dir=target.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(exported, temp_name)
        os.replace(temp_name, target)
    except OSError:
        # 导出图被删除、锁定（Windows）或无权限时退回原图渲染，不让整次导出失败。
        return False
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return True
