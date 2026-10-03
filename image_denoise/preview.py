"""降噪成片来源记录与只读查找；只能在后台调用文件系统查找。"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import threading
import unicodedata
import xml.etree.ElementTree as ET

from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from app_common.exif_io.xmp_sidecar import _photo_descriptions
from .types import DenoiseOptions, check_cancelled

NAMESPACE = "https://superbirdtools.local/xmp/superpicky/1.0/"
_RECENT_LIMIT = 2048
_recent: OrderedDict[str, str] = OrderedDict()
_recent_lock = threading.Lock()


def _key(value) -> str:
    return unicodedata.normalize("NFC", os.path.normcase(os.path.abspath(os.fspath(value))))


def _name_key(value) -> str:
    return unicodedata.normalize("NFC", str(value)).casefold()


def source_provenance(source: Path, camera_crop=None, *, source_stat=None) -> dict[str, str]:
    stat = source_stat or source.stat()
    return {
        "denoise_source_path": str(source.resolve()),
        "denoise_source_size": str(stat.st_size),
        "denoise_source_mtime_ns": str(stat.st_mtime_ns),
        "denoise_camera_crop": json.dumps(camera_crop),
    }


def register_denoised_output(source, destination) -> None:
    """记录本会话的自选输出位置，不写原图或其 XMP，不在 GUI 做磁盘检查。"""
    with _recent_lock:
        key = _key(source)
        _recent[key] = os.path.abspath(os.fspath(destination))
        _recent.move_to_end(key)
        while len(_recent) > _RECENT_LIMIT:
            _recent.popitem(last=False)


@dataclass(frozen=True)
class DenoisedPreview:
    path: str
    camera_crop: tuple | None = None
    legacy: bool = False


def _output_metadata(path: Path) -> dict[str, str]:
    sidecar = path.with_suffix(".xmp")
    if not sidecar.is_file():
        sidecar = next((p for p in path.parent.iterdir()
                        if _name_key(p.name) == _name_key(sidecar.name)), sidecar)
    # 输出侧车通常只有数 KB；损坏/异常大的 XML 不参与预览发现。
    if sidecar.stat().st_size > 4 * 1024 * 1024:
        return {}
    root = ET.parse(sidecar).getroot()
    values = {}
    for desc in _photo_descriptions(root, str(path), sidecar_path=str(sidecar)):
        for node in desc.iter():
            if node.tag.startswith("{" + NAMESPACE + "}"):
                values[node.tag.split("}", 1)[1]] = node.text or ""
            if node.tag == "{http://ns.adobe.com/xap/1.0/}CreatorTool":
                values["creator"] = node.text or ""
            for key, value in node.attrib.items():
                if key.startswith("{" + NAMESPACE + "}"):
                    values[key.split("}", 1)[1]] = value
                if key == "{http://ns.adobe.com/xap/1.0/}CreatorTool":
                    values["creator"] = value
    return values


def find_denoised_preview(source, options: DenoiseOptions | None = None, *, cancelled=None):
    """按来源校验成片；旧版无来源记录的成片仅在无同名歧义的源子目录兼容。"""
    source = Path(source)
    options = options or DenoiseOptions()
    stat = source.stat()
    with _recent_lock:
        recent = _recent.get(_key(source))
    directories = [source.parent / options.subdir, source.parent / "denoised"]
    if options.output_directory:
        directories.insert(0, Path(options.output_directory).expanduser())
    candidates = [Path(recent)] if recent else []
    visited = set()
    stem = re.compile(re.escape(_name_key(source.stem)) + r"_denoised(?:_[0-9]+)?$")
    for directory in directories:
        check_cancelled(cancelled)
        if _key(directory) in visited:
            continue
        visited.add(_key(directory))
        try:
            for item in directory.iterdir():
                check_cancelled(cancelled)
                if item.suffix.lower() in {".tif", ".tiff", ".jpg", ".jpeg"} and stem.fullmatch(_name_key(item.stem)):
                    candidates.append(item)
        except OSError:
            continue
    legacy_unambiguous = None
    matches = []
    for path in dict.fromkeys(candidates):
        check_cancelled(cancelled)
        try:
            if not path.is_file() or _key(path) == _key(source):
                continue
            values = _output_metadata(path)
            recorded = values.get("denoise_source_path")
            legacy = not recorded
            if recorded:
                if (_key(recorded) != _key(source)
                        or values.get("denoise_source_size") != str(stat.st_size)
                        or values.get("denoise_source_mtime_ns") != str(stat.st_mtime_ns)):
                    continue
            else:
                if not values.get("creator", "").startswith("SuperViewer RGB denoise"):
                    continue
                # 固定目录可能合并来自不同目录的同名照片，旧成片不能靠名称猜。
                if _key(path.parent.parent) != _key(source.parent):
                    continue
                if legacy_unambiguous is None:
                    legacy_unambiguous = sum(
                        p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                        and _name_key(p.stem) == _name_key(source.stem)
                        for p in source.parent.iterdir()) == 1
                if not legacy_unambiguous:
                    continue
            crop = json.loads(values.get("denoise_camera_crop", "null"))
            if crop is not None:
                crop = tuple(float(v) for v in crop)
                if len(crop) != 4 or not (0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1):
                    continue
            matches.append((path.stat().st_mtime_ns, str(path), crop, legacy))
        except (OSError, ET.ParseError, ValueError, TypeError):
            continue
    if not matches:
        return None
    _, path, crop, legacy = max(matches, key=lambda row: (row[0], row[1]))
    return DenoisedPreview(path, crop, legacy)
