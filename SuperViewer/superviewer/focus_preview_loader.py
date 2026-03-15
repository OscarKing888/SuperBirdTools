# -*- coding: utf-8 -*-
"""
焦点框提取与预览图加载：扩展名常量、预览 QPixmap、焦点元数据与 report.db 保底。
可依赖 paths_settings、exif_helpers、app_common、qt_compat，不依赖 SuperViewer 类模块。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import piexif
from PIL import Image, ImageOps

from app_common.exif_io import extract_metadata_with_xmp_priority, get_exiftool_executable_path
from app_common.focus_calc import (
    extract_focus_box,
    resolve_focus_camera_type_from_metadata,
    resolve_focus_display_orientation,
)
from app_common.log import get_logger
from app_common.report_db import find_report_root, ReportDB

from .exif_helpers import (
    HEIF_EXTENSIONS,
    ORIENTATION_TAG,
    RAW_EXTENSIONS,
    load_exif_heic,
)
from .qt_compat import QImage, QPixmap, QTransform, _SmoothTransformation

try:
    import exifread
except ImportError:
    exifread = None

try:
    import rawpy
except ImportError:
    rawpy = None

_log = get_logger("focus_preview_loader")

# Re-export for scripts / main re-exports
IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif",
    ".heic", ".heif", ".hif",
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".rw2", ".raw", ".orf", ".ori", ".raf", ".dng", ".pef", ".ptx",
    ".x3f", ".rwl", ".3fr", ".dcr", ".kdc", ".mef", ".mrw", ".rwz",
)
IMAGE_EXTENSIONS = tuple(dict.fromkeys(e.lower() for e in IMAGE_EXTENSIONS))


def _get_orientation_from_file(path: str) -> int:
    """从文件中读取 EXIF Orientation 值 (1–8)。先 piexif 再 exifread，返回 1 表示正常。"""
    try:
        data = piexif.load(path)
        for ifd in ("0th", "Exif"):
            if data.get(ifd) and ORIENTATION_TAG in data[ifd]:
                v = data[ifd][ORIENTATION_TAG]
                if isinstance(v, int) and 1 <= v <= 8:
                    return v
    except Exception:
        pass
    if exifread:
        try:
            with open(path, "rb") as f:
                tags = exifread.process_file(f, details=False)
            for key in ("Image Orientation", "EXIF Orientation"):
                if key in tags:
                    try:
                        v = int(tags[key].values[0])
                        if 1 <= v <= 8:
                            return v
                    except (IndexError, ValueError, TypeError):
                        pass
        except Exception:
            pass
    return 1


def _apply_orientation_to_pixmap(pix: QPixmap, orientation: int) -> QPixmap:
    """根据 EXIF Orientation (1–8) 对 QPixmap 做旋转/翻转。"""
    if orientation == 1 or pix.isNull():
        return pix
    tr = QTransform()
    if orientation == 2:
        tr.scale(-1, 1)
    elif orientation == 3:
        tr.rotate(180)
    elif orientation == 4:
        tr.scale(1, -1)
    elif orientation == 5:
        tr.rotate(-90)
        tr.scale(-1, 1)
    elif orientation == 6:
        tr.rotate(90)
    elif orientation == 7:
        tr.rotate(90)
        tr.scale(-1, 1)
    elif orientation == 8:
        tr.rotate(-90)
    else:
        return pix
    return pix.transformed(tr, _SmoothTransformation)


def _load_preview_pixmap_with_orientation(path: str) -> QPixmap | None:
    """加载图片并应用 EXIF 方向。仅对 PIL 可解码格式；失败或 RAW 时返回 None。"""
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            w, h = img.size
            data = img.tobytes()
        bpl = w * 3
        fmt = QImage.Format.Format_RGB888 if hasattr(QImage.Format, "Format_RGB888") else QImage.Format.RGB888
        qimg = QImage(data, w, h, bpl, fmt).copy()
        if qimg.isNull():
            return None
        return QPixmap.fromImage(qimg)
    except Exception:
        return None


def _load_raw_full_as_pixmap(path: str) -> QPixmap | None:
    """使用 rawpy 解码 RAW 为完整原图并转为 QPixmap（应用 EXIF 方向）。"""
    if rawpy is None or Path(path).suffix.lower() not in RAW_EXTENSIONS:
        return None
    try:
        import numpy as np
        with rawpy.imread(path) as rp:
            rgb = rp.postprocess()
        if rgb is None or rgb.size == 0:
            return None
        h, w = rgb.shape[0], rgb.shape[1]
        if rgb.dtype != np.uint8:
            rgb = (rgb.astype(np.float32) * (255.0 / rgb.max())).astype(np.uint8)
        data = rgb.copy().tobytes()
        bpl = w * 3
        fmt = QImage.Format.Format_RGB888 if hasattr(QImage.Format, "Format_RGB888") else getattr(QImage.Format, "RGB888", 4)
        qimg = QImage(data, w, h, bpl, fmt).copy()
        if qimg.isNull():
            return None
        pix = QPixmap.fromImage(qimg)
        return _apply_orientation_to_pixmap(pix, _get_orientation_from_file(path))
    except Exception:
        return None


def get_raw_thumbnail(path: str) -> bytes | None:
    """从 RAW 文件中获取嵌入的 JPEG 缩略图字节。"""
    if Path(path).suffix.lower() not in RAW_EXTENSIONS:
        return None
    try:
        data = piexif.load(path)
        thumb = data.get("thumbnail")
        if isinstance(thumb, bytes) and len(thumb) > 100:
            return thumb
    except Exception:
        pass
    if rawpy is None:
        return None
    try:
        with rawpy.imread(path) as rp:
            thumb = rp.extract_thumb()
        if thumb is None:
            return None
        if hasattr(rawpy, "ThumbFormat") and thumb.format == rawpy.ThumbFormat.JPEG:
            if isinstance(thumb.data, bytes):
                return thumb.data
    except Exception:
        pass
    return None


def _load_preview_pixmap_for_canvas(path: str) -> QPixmap | None:
    """加载预览用 QPixmap（原图，含方向修正），供 PreviewPanel 使用。"""
    pix = _load_preview_pixmap_with_orientation(path)
    is_raw = Path(path).suffix.lower() in RAW_EXTENSIONS
    if (pix is None or pix.isNull()) and is_raw:
        pix = _load_raw_full_as_pixmap(path)
    if (pix is None or pix.isNull()) and is_raw:
        thumb_data = get_raw_thumbnail(path)
        if thumb_data:
            pix = QPixmap()
            if pix.loadFromData(thumb_data):
                pix = _apply_orientation_to_pixmap(pix, _get_orientation_from_file(path))
                return pix
    if pix is None or pix.isNull():
        pix = QPixmap(path)
    if pix is not None and not pix.isNull() and is_raw:
        pix = _apply_orientation_to_pixmap(pix, _get_orientation_from_file(path))
    return pix


def _run_exiftool_json_for_focus(path: str) -> dict | None:
    """为焦点提取执行 exiftool 读取（-j -G1 -n -a -u）。"""
    exiftool_path = get_exiftool_executable_path()
    if not exiftool_path:
        return None
    path_norm = os.path.normpath(path)
    use_argfile = sys.platform.startswith("win") and any(ord(c) > 127 for c in path_norm)
    cmd_common = [
        exiftool_path,
        "-j", "-G1", "-n", "-a", "-u",
        "-charset", "filename=UTF8",
        "-api", "largefilesupport=1",
    ]
    try:
        if use_argfile:
            fd, argfile_path = tempfile.mkstemp(suffix=".args", prefix="exiftool_focus_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(path_norm + "\n")
                cp = subprocess.run(
                    [*cmd_common, "-@", argfile_path],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            finally:
                try:
                    os.unlink(argfile_path)
                except OSError:
                    pass
        else:
            cp = subprocess.run(
                [*cmd_common, path_norm],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        if cp.returncode != 0 or not (cp.stdout or "").strip():
            return None
        payload = json.loads(cp.stdout)
    except Exception:
        return None
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
        return None
    return payload if isinstance(payload, dict) else None


def _load_exifread_metadata_for_focus(path: str) -> dict[str, object]:
    """针对 RAW 焦点提取的 exifread 补充读取。"""
    if exifread is None:
        return {}
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=True, extract_thumbnail=False)
    except Exception:
        return {}
    if not isinstance(tags, dict) or not tags:
        return {}

    out: dict[str, object] = {}

    def _tag_value(tag_obj):
        values = getattr(tag_obj, "values", None)
        if values not in (None, []):
            return values
        printable = getattr(tag_obj, "printable", None)
        if printable not in (None, ""):
            return printable
        return str(tag_obj)

    for key, tag in tags.items():
        lk = str(key).strip().lower()
        if not lk:
            continue
        keep = (
            lk in {"image make", "image model", "image orientation", "exif exifimagewidth", "exif exifimagelength"}
            or lk.startswith("makernote tag 0x2027")
            or lk.startswith("makernote tag 0x204a")
            or ("focus" in lk)
            or ("subject" in lk)
            or ("region" in lk)
        )
        if not keep:
            continue
        out[str(key)] = _tag_value(tag)

    if "Image Make" in out:
        out.setdefault("Make", out["Image Make"])
    if "Image Model" in out:
        out.setdefault("Model", out["Image Model"])
    if "EXIF ExifImageWidth" in out:
        out.setdefault("ExifImageWidth", out["EXIF ExifImageWidth"])
    if "EXIF ExifImageLength" in out:
        out.setdefault("ExifImageHeight", out["EXIF ExifImageLength"])

    return out


def _focus_metadata_value_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _merge_focus_metadata_parts(parts: list[tuple[str, dict | None]]) -> tuple[dict | None, list[str]]:
    merged: dict[str, object] = {}
    used_providers: list[str] = []
    for label, part in parts:
        if not isinstance(part, dict) or not part:
            continue
        used_providers.append(f"{label}:{len(part)}")
        for key, value in part.items():
            key_text = str(key).strip()
            if not key_text or not _focus_metadata_value_present(value):
                continue
            if key_text not in merged or not _focus_metadata_value_present(merged.get(key_text)):
                merged[key_text] = value
    return (merged or None), used_providers


def _load_heif_piexif_metadata_for_focus(path: str) -> dict[str, object]:
    data = load_exif_heic(path)
    if not isinstance(data, dict) or not data:
        return {}

    out: dict[str, object] = {"SourceFile": path}
    for ifd_name, ifd_data in data.items():
        if not isinstance(ifd_data, dict):
            continue
        tag_defs = piexif.TAGS.get(ifd_name, {})
        for tag_id, raw_value in ifd_data.items():
            info = tag_defs.get(tag_id)
            if not isinstance(info, dict):
                continue
            tag_name = str(info.get("name") or "").strip()
            if not tag_name:
                continue
            out[f"{ifd_name}:{tag_name}"] = raw_value
            if tag_name == "Make":
                out.setdefault("Make", raw_value)
            elif tag_name == "Model":
                out.setdefault("Model", raw_value)
            elif tag_name == "Orientation":
                out.setdefault("Orientation", raw_value)
            elif tag_name == "ExifImageWidth":
                out.setdefault("ExifImageWidth", raw_value)
            elif tag_name == "ExifImageLength":
                out.setdefault("ExifImageHeight", raw_value)
            elif tag_name == "ImageWidth":
                out.setdefault("ImageWidth", raw_value)
            elif tag_name == "ImageLength":
                out.setdefault("ImageHeight", raw_value)

    return out


def _load_focus_metadata_for_path(path: str) -> dict | None:
    path_text = str(path or "").strip()
    if not path_text:
        return None

    ext = Path(path_text).suffix.lower()
    primary = None
    try:
        primary = extract_metadata_with_xmp_priority(Path(path_text), mode="auto")
    except Exception:
        primary = None

    if ext in RAW_EXTENSIONS:
        parts = [
            ("exiftool", _run_exiftool_json_for_focus(path_text)),
            ("primary", primary if isinstance(primary, dict) else None),
            ("exifread", _load_exifread_metadata_for_focus(path_text)),
        ]
    elif ext in HEIF_EXTENSIONS:
        parts = [
            ("exiftool", _run_exiftool_json_for_focus(path_text)),
            ("heif_piexif", _load_heif_piexif_metadata_for_focus(path_text)),
            ("primary", primary if isinstance(primary, dict) else None),
            ("exifread", _load_exifread_metadata_for_focus(path_text)),
        ]
    else:
        parts = [
            ("exiftool", _run_exiftool_json_for_focus(path_text)),
            ("primary", primary if isinstance(primary, dict) else None),
        ]

    merged, providers = _merge_focus_metadata_parts(parts)
    _log.info(
        "[_load_focus_metadata_for_path] path=%r ext=%r providers=%s merged_keys=%s",
        path_text, ext, providers or ["none"], len(merged or {}),
    )
    return merged


def _focus_box_from_center_and_span(
    center_x: float, center_y: float, span_x: float, span_y: float
) -> tuple[float, float, float, float]:
    """由归一化中心与宽高比得到 (l,t,r,b)，并 clamp 到 [0,1]。"""
    cx = max(0.0, min(1.0, center_x))
    cy = max(0.0, min(1.0, center_y))
    sx = max(0.01, min(1.0, span_x))
    sy = max(0.01, min(1.0, span_y))
    half_x = sx * 0.5
    half_y = sy * 0.5
    left = cx - half_x
    right = cx + half_x
    top = cy - half_y
    bottom = cy + half_y
    if left < 0.0:
        right = min(1.0, right - left)
        left = 0.0
    if right > 1.0:
        left = max(0.0, left - (right - 1.0))
        right = 1.0
    if top < 0.0:
        bottom = min(1.0, bottom - top)
        top = 0.0
    if bottom > 1.0:
        top = max(0.0, top - (bottom - 1.0))
        bottom = 1.0
    return (left, top, right, bottom)


def _resolve_focus_display_orientation_for_path(
    path: str,
    raw_metadata: dict | None = None,
    *,
    camera_type=None,
) -> int:
    """为“显示对焦点”解析预览显示方向。"""
    metadata = raw_metadata if isinstance(raw_metadata, dict) and raw_metadata else _load_focus_metadata_for_path(path)
    if isinstance(metadata, dict) and metadata:
        return resolve_focus_display_orientation(metadata, camera_type=camera_type)
    return _get_orientation_from_file(path)


def _resolve_focus_calc_image_size(raw_metadata: dict, fallback: tuple[int, int]) -> tuple[int, int]:
    """为 focus_calc 解析尽量接近元数据坐标系的原图尺寸。"""

    def _parse_int(value) -> int | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            iv = int(value)
            return iv if iv > 0 else None
        m = re.search(r"(\d+)", str(value))
        if not m:
            return None
        try:
            iv = int(m.group(1))
            return iv if iv > 0 else None
        except Exception:
            return None

    def _parse_pair(value) -> tuple[int, int] | None:
        if value is None:
            return None
        nums = re.findall(r"\d+", str(value))
        if len(nums) < 2:
            return None
        try:
            w, h = int(nums[0]), int(nums[1])
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        return (w, h)

    lookup: dict[str, object] = {}
    for k, v in (raw_metadata or {}).items():
        key = str(k).strip().lower()
        if not key:
            continue
        lookup.setdefault(key, v)
        if ":" in key:
            lookup.setdefault(key.split(":")[-1], v)

    key_pairs = [
        ("exif:exifimagewidth", "exif:exifimageheight"),
        ("exifimagewidth", "exifimageheight"),
        ("exif:imagewidth", "exif:imageheight"),
        ("rawimagewidth", "rawimageheight"),
        ("imagewidth", "imageheight"),
        ("file:imagewidth", "file:imageheight"),
    ]
    for w_key, h_key in key_pairs:
        w = _parse_int(lookup.get(w_key))
        h = _parse_int(lookup.get(h_key))
        if w and h:
            return (w, h)

    for pair_key in ("composite:imagesize", "imagesize", "exif:image size"):
        parsed = _parse_pair(lookup.get(pair_key))
        if parsed:
            return parsed

    fw = int(fallback[0]) if fallback and len(fallback) > 0 else 0
    fh = int(fallback[1]) if fallback and len(fallback) > 1 else 0
    if fw > 0 and fh > 0:
        return (fw, fh)
    return (1, 1)


def _transform_focus_box_by_orientation(focus_box, orientation: int):
    """将原图坐标系中的归一化焦点框按 EXIF Orientation 映射到预览坐标系。"""
    if not focus_box:
        return None
    try:
        left, top, right, bottom = [float(v) for v in focus_box]
    except Exception:
        return None
    left = max(0.0, min(1.0, left))
    top = max(0.0, min(1.0, top))
    right = max(0.0, min(1.0, right))
    bottom = max(0.0, min(1.0, bottom))
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top

    o = int(orientation or 1)
    if o == 1:
        return (left, top, right, bottom)

    def _map_point(x: float, y: float) -> tuple[float, float]:
        if o == 2:
            return (1.0 - x, y)
        if o == 3:
            return (1.0 - x, 1.0 - y)
        if o == 4:
            return (x, 1.0 - y)
        if o == 5:
            return (y, x)
        if o == 6:
            return (1.0 - y, x)
        if o == 7:
            return (1.0 - y, 1.0 - x)
        if o == 8:
            return (y, 1.0 - x)
        return (x, y)

    pts = [
        _map_point(left, top),
        _map_point(right, top),
        _map_point(left, bottom),
        _map_point(right, bottom),
    ]
    xs = [max(0.0, min(1.0, p[0])) for p in pts]
    ys = [max(0.0, min(1.0, p[1])) for p in pts]
    nl, nr = min(xs), max(xs)
    nt, nb = min(ys), max(ys)
    if nr - nl < 1e-6 or nb - nt < 1e-6:
        return None
    return (nl, nt, nr, nb)


def _load_focus_box_from_report_db(
    path: str,
    width: int,
    height: int,
    ref_size: tuple[int, int] | None = None,
    raw_metadata: dict | None = None,
    camera_type=None,
) -> tuple[float, float, float, float] | None:
    """从 report.db 的 focus_x、focus_y 构造焦点框（保底）。"""
    if width <= 0 or height <= 0:
        return None
    try:
        directory = str(Path(path).parent)
        stem = Path(path).stem
        if not stem:
            return None
        report_root = find_report_root(directory)
        if not report_root:
            return None
        db = ReportDB.open_if_exists(report_root)
        if not db:
            return None
        try:
            row = db.get_photo(stem)
        finally:
            db.close()
        if not row:
            return None
        fx, fy = row.get("focus_x"), row.get("focus_y")
        if fx is None or fy is None:
            return None
        fx, fy = float(fx), float(fy)
        ref_w = float(ref_size[0]) if ref_size and len(ref_size) > 0 and int(ref_size[0]) > 0 else float(width)
        ref_h = float(ref_size[1]) if ref_size and len(ref_size) > 1 and int(ref_size[1]) > 0 else float(height)
        if ref_w <= 0 or ref_h <= 0:
            return None
        if fx <= 1.0 and fy <= 1.0:
            cx, cy = fx, fy
        else:
            cx = max(0.0, min(1.0, fx / ref_w))
            cy = max(0.0, min(1.0, fy / ref_h))
        span_x = 128.0 / ref_w
        span_y = 128.0 / ref_h
        box = _focus_box_from_center_and_span(cx, cy, span_x, span_y)
        orientation = _resolve_focus_display_orientation_for_path(
            path, raw_metadata, camera_type=camera_type,
        )
        _log.info(
            "[_load_focus_box_from_report_db] path=%r focus=(%s,%s) ref_size=%sx%s orientation=%s box=%r",
            path, fx, fy, int(ref_w), int(ref_h), orientation, box,
        )
        return _transform_focus_box_by_orientation(box, orientation)
    except Exception:
        _log.exception("[_load_focus_box_from_report_db] path=%r", path)
        return None


def _load_focus_box_for_preview(path: str, width: int, height: int, *, allow_report_db_fallback: bool = True):
    """用 focus_calc + exiftool 元数据提取焦点框，返回归一化坐标 (l,t,r,b)。"""
    if width <= 0 or height <= 0:
        return None

    raw_metadata = _load_focus_metadata_for_path(path)
    if raw_metadata is None:
        _log.info("[_load_focus_box_for_preview] no metadata path=%r", path)
        if allow_report_db_fallback:
            focus_box = _load_focus_box_from_report_db(path, width, height)
            if focus_box is not None:
                _log.info("[_load_focus_box_for_preview] fallback report_db path=%r focus_box=%r", path, focus_box)
            return focus_box
        return None

    focus_width, focus_height = _resolve_focus_calc_image_size(raw_metadata, fallback=(width, height))
    camera_type = resolve_focus_camera_type_from_metadata(raw_metadata)
    try:
        focus_box = extract_focus_box(
            raw_metadata,
            focus_width,
            focus_height,
            camera_type=camera_type,
        )
        if focus_box is None:
            if allow_report_db_fallback:
                focus_box = _load_focus_box_from_report_db(
                    path, width, height,
                    ref_size=(focus_width, focus_height),
                    raw_metadata=raw_metadata,
                    camera_type=camera_type,
                )
                if focus_box is not None:
                    _log.info("[_load_focus_box_for_preview] fallback report_db path=%r focus_box=%r", path, focus_box)
                    return focus_box
            _log.info(
                "[_load_focus_box_for_preview] focus_calc none path=%r camera_type=%s calc_size=%sx%s",
                path, str(getattr(camera_type, "value", camera_type)), focus_width, focus_height,
            )
            return None
        orientation = _resolve_focus_display_orientation_for_path(
            path, raw_metadata, camera_type=camera_type,
        )
        mapped_box = _transform_focus_box_by_orientation(focus_box, orientation)
        _log.info(
            "[_load_focus_box_for_preview] path=%r camera_type=%s calc_size=%sx%s orientation=%s focus_box=%r",
            path, str(getattr(camera_type, "value", camera_type)), focus_width, focus_height, orientation, mapped_box,
        )
        return mapped_box
    except Exception:
        _log.exception("[_load_focus_box_for_preview] failed path=%r", path)
        if allow_report_db_fallback:
            focus_box = _load_focus_box_from_report_db(
                path, width, height,
                ref_size=(focus_width, focus_height),
                raw_metadata=raw_metadata,
                camera_type=camera_type,
            )
            if focus_box is not None:
                _log.info("[_load_focus_box_for_preview] fallback report_db after exception path=%r", path)
            return focus_box
        return None


def _resolve_focus_report_fallback_ref_size(path: str, fallback: tuple[int, int]) -> tuple[tuple[int, int], bool]:
    raw_metadata = _load_focus_metadata_for_path(path)
    if isinstance(raw_metadata, dict) and raw_metadata:
        return _resolve_focus_calc_image_size(raw_metadata, fallback=fallback), True
    fw = int(fallback[0]) if fallback and len(fallback) > 0 else 0
    fh = int(fallback[1]) if fallback and len(fallback) > 1 else 0
    if fw > 0 and fh > 0:
        return (fw, fh), False
    return (1, 1), False
