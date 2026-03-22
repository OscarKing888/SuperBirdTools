from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, Optional

from PIL import Image

from app_common.exif_io.config import load_exif_settings
from app_common.report_db import PHOTO_COLUMNS
from birdstamp.config import resolve_bundled_path
from birdstamp.meta.normalize import format_settings_line, normalize_metadata

# 与 editor_utils 中一致：不写入 context 的路径列
_REPORT_DB_PATH_COLUMNS = frozenset({
    "original_path", "current_path", "temp_jpeg_path", "debug_crop_path", "yolo_debug_path",
})

_PHOTO_AUTHOR_KEY_CANDIDATES: tuple[str, ...] = (
    "XMP-dc:Creator",
    "XMP:Creator",
    "Creator",
    "EXIF:Artist",
    "Artist",
    "IPTC:By-line",
    "By-line",
    "Author",
)

_FROM_FILE_CONTEXT_KEY_ALIASES: dict[str, str] = {
    "capture_time": "capture_text",
    "capture_datetime": "capture_text",
    "date_time_original": "capture_text",
    "datetime_original": "capture_text",
    "date": "capture_date",
}

TEMPLATE_SOURCE_EXIF = "exif"
TEMPLATE_SOURCE_REPORT_DB = "report_db"
TEMPLATE_SOURCE_FROM_FILE = "from_file"
TEMPLATE_SOURCE_EDITOR = "editor"
TEMPLATE_SOURCE_AUTO = "auto"
TEMPLATE_SOURCE_METADATA_LEGACY = "metadata"

TemplateContext = Dict[str, str]
NormalizedCropBox = tuple[float, float, float, float]

_REPORT_DB_ROW_RESOLVER: Optional[Callable[[Path], Optional[Dict[str, Any]]]] = None
_PHOTO_INFO_CROP_BOX_UNSET = object()

_BASE_TEMPLATE_CONTEXT: TemplateContext = {
    "bird": "",
    "bird_latin": "",
    "bird_scientific": "",
    "bird_common": "",
    "bird_family": "",
    "bird_order": "",
    "bird_class": "",
    "bird_phylum": "",
    "bird_kingdom": "",
    "capture_date": "",
    "capture_text": "",
    "author": "",
    "location": "",
    "gps_text": "",
    "camera": "",
    "lens": "",
    "settings_text": "",
    "stem": "",
    "filename": "",
}


@dataclass(slots=True)
class PhotoInfo:
    """模板渲染链路使用的照片信息。

    - path: 当前图片文件路径
    - sidecar_path: 与图片配对的 XMP sidecar 路径（若存在）
    - raw_metadata: 已读取的原始 metadata；允许为空，便于渐进式接入
    """

    path: Path
    sidecar_path: Path | None = None
    raw_metadata: Dict[str, Any] | None = None

    @classmethod
    def from_path(
        cls,
        path: Path | str,
        *,
        sidecar_path: Path | str | None = None,
        raw_metadata: Dict[str, Any] | None = None,
    ) -> "PhotoInfo":
        resolved_path = Path(path).resolve(strict=False)
        resolved_sidecar = _normalize_sidecar_path(sidecar_path, source_path=resolved_path)
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        return cls(
            path=resolved_path,
            sidecar_path=resolved_sidecar,
            raw_metadata=metadata,
        )


@dataclass(slots=True)
class EditorPhotoInfo(PhotoInfo):
    """编辑器使用的照片信息，额外保存逐图裁切框。"""

    crop_box: NormalizedCropBox | None = None
    editor_row_number: int | None = None

    @classmethod
    def from_path(
        cls,
        path: Path | str,
        *,
        sidecar_path: Path | str | None = None,
        raw_metadata: Dict[str, Any] | None = None,
        crop_box: Any = None,
        editor_row_number: Any = None,
    ) -> "EditorPhotoInfo":
        resolved_path = Path(path).resolve(strict=False)
        resolved_sidecar = _normalize_sidecar_path(sidecar_path, source_path=resolved_path)
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        return cls(
            path=resolved_path,
            sidecar_path=resolved_sidecar,
            raw_metadata=metadata,
            crop_box=normalize_crop_box(crop_box),
            editor_row_number=_normalize_editor_row_number(editor_row_number),
        )


def _resolve_sidecar_path(source_path: Path) -> Path | None:
    try:
        from app_common.exif_io import find_xmp_sidecar
    except Exception:
        find_xmp_sidecar = None
    if not callable(find_xmp_sidecar):
        return None
    try:
        sidecar_text = find_xmp_sidecar(str(source_path))
    except Exception:
        return None
    if not sidecar_text:
        return None
    try:
        return Path(sidecar_text).resolve(strict=False)
    except Exception:
        return None


def _normalize_sidecar_path(
    sidecar_path: Path | str | None,
    *,
    source_path: Path,
) -> Path | None:
    if sidecar_path is None:
        return _resolve_sidecar_path(source_path)
    text = str(sidecar_path or "").strip()
    if not text:
        return None
    try:
        return Path(text).resolve(strict=False)
    except Exception:
        return None


def ensure_photo_info(
    photo: PhotoInfo | Path | str,
    *,
    raw_metadata: Dict[str, Any] | None = None,
    sidecar_path: Path | str | None = None,
) -> PhotoInfo:
    if isinstance(photo, PhotoInfo):
        if isinstance(raw_metadata, dict):
            photo.raw_metadata = dict(raw_metadata)
        elif not isinstance(photo.raw_metadata, dict):
            photo.raw_metadata = {}
        if sidecar_path is not None or photo.sidecar_path is None:
            photo.sidecar_path = _normalize_sidecar_path(sidecar_path, source_path=photo.path)
        return photo
    return PhotoInfo.from_path(
        photo,
        sidecar_path=sidecar_path,
        raw_metadata=raw_metadata,
    )


def normalize_crop_box(crop_box: Any) -> NormalizedCropBox | None:
    if crop_box is None or not isinstance(crop_box, (list, tuple)) or len(crop_box) != 4:
        return None
    try:
        return tuple(float(value) for value in crop_box)
    except (TypeError, ValueError):
        return None


def photo_crop_box(photo: PhotoInfo | None) -> NormalizedCropBox | None:
    if not isinstance(photo, PhotoInfo):
        return None
    return normalize_crop_box(getattr(photo, "crop_box", None))


def _normalize_editor_row_number(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    if normalized <= 0:
        return None
    return normalized


def ensure_editor_photo_info(
    photo: PhotoInfo | Path | str,
    *,
    raw_metadata: Dict[str, Any] | None = None,
    sidecar_path: Path | str | None = None,
    crop_box: Any = _PHOTO_INFO_CROP_BOX_UNSET,
    editor_row_number: Any = _PHOTO_INFO_CROP_BOX_UNSET,
) -> EditorPhotoInfo:
    normalized_crop_box = (
        photo_crop_box(photo) if crop_box is _PHOTO_INFO_CROP_BOX_UNSET else normalize_crop_box(crop_box)
    )
    normalized_editor_row_number = (
        getattr(photo, "editor_row_number", None)
        if editor_row_number is _PHOTO_INFO_CROP_BOX_UNSET
        else _normalize_editor_row_number(editor_row_number)
    )
    if isinstance(photo, EditorPhotoInfo):
        if isinstance(raw_metadata, dict):
            photo.raw_metadata = dict(raw_metadata)
        elif not isinstance(photo.raw_metadata, dict):
            photo.raw_metadata = {}
        if sidecar_path is not None or photo.sidecar_path is None:
            photo.sidecar_path = _normalize_sidecar_path(sidecar_path, source_path=photo.path)
        if crop_box is not _PHOTO_INFO_CROP_BOX_UNSET:
            photo.crop_box = normalized_crop_box
        if editor_row_number is not _PHOTO_INFO_CROP_BOX_UNSET:
            photo.editor_row_number = normalized_editor_row_number
        return photo

    base = ensure_photo_info(
        photo,
        raw_metadata=raw_metadata,
        sidecar_path=sidecar_path,
    )
    return EditorPhotoInfo(
        path=base.path,
        sidecar_path=base.sidecar_path,
        raw_metadata=dict(base.raw_metadata) if isinstance(base.raw_metadata, dict) else {},
        crop_box=normalized_crop_box,
        editor_row_number=normalized_editor_row_number,
    )


def _photo_raw_metadata(photo_info: PhotoInfo) -> Dict[str, Any]:
    raw = photo_info.raw_metadata
    return dict(raw) if isinstance(raw, dict) else {}


def _normalize_lookup(raw: Dict[str, Any]) -> Dict[str, Any]:
    lookup: Dict[str, Any] = {}
    for key, value in raw.items():
        key_text = str(key or "").strip().lower()
        if not key_text:
            continue
        lookup.setdefault(key_text, value)
        if ":" in key_text:
            lookup.setdefault(key_text.split(":")[-1], value)
    return lookup


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for codec in ("utf-8", "utf-16le", "latin1"):
            try:
                value = value.decode(codec, errors="ignore")
                break
            except Exception:
                continue
    if isinstance(value, (list, tuple)):
        text_items = [_clean_text(item) for item in value]
        return " ".join(item for item in text_items if item).strip()
    if isinstance(value, dict):
        text_items = [_clean_text(item) for item in value.values()]
        return " ".join(item for item in text_items if item).strip()
    text = str(value).replace("\x00", " ").strip()
    return re.sub(r"\s+", " ", text)


def _parse_datetime_value(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    normalized = text.replace("T", " ").strip()
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    for pattern in (
        "%Y:%m:%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _extract_capture_date_text(photo_info: PhotoInfo, raw_metadata: Dict[str, Any]) -> str:
    dt = _extract_capture_datetime(photo_info, raw_metadata)
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d")


def _extract_capture_datetime_from_metadata(raw_metadata: Dict[str, Any]) -> datetime | None:
    lookup = _normalize_lookup(raw_metadata)
    for key in (
        "DateTimeOriginal",
        "CreateDate",
        "DateTimeCreated",
        "DateCreated",
        "MediaCreateDate",
    ):
        value = lookup.get(key.lower())
        dt = _parse_datetime_value(value)
        if dt is not None:
            return dt
    return None


def _extract_capture_datetime(photo_info: PhotoInfo, raw_metadata: Dict[str, Any]) -> datetime | None:
    dt = _extract_capture_datetime_from_metadata(raw_metadata)
    if dt is not None:
        return dt
    try:
        return datetime.fromtimestamp(photo_info.path.stat().st_ctime)
    except Exception:
        return None


def _extract_capture_text(photo_info: PhotoInfo, raw_metadata: Dict[str, Any]) -> str:
    dt = _extract_capture_datetime(photo_info, raw_metadata)
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _extract_author_text(raw_metadata: Dict[str, Any]) -> str:
    lookup = _normalize_lookup(raw_metadata)
    for key in _PHOTO_AUTHOR_KEY_CANDIDATES:
        value = lookup.get(key.lower())
        text = _clean_text(value)
        if text:
            return text
    for key, value in raw_metadata.items():
        key_text = str(key or "").strip().lower()
        if any(token in key_text for token in ("creator", "artist", "author", "by-line")):
            text = _clean_text(value)
            if text:
                return text
    return ""


def _lookup_metadata_text(raw_metadata: Dict[str, Any], *candidate_keys: str) -> str:
    for candidate in candidate_keys:
        text = lookup_exif_text(candidate, raw_metadata, {})
        if text:
            return text
    return ""


def _try_parse_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = _clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"-?\d+\s*/\s*-?\d+", text):
        try:
            numerator_text, denominator_text = text.split("/", 1)
            denominator = float(denominator_text.strip())
            if denominator == 0:
                return None
            return float(numerator_text.strip()) / denominator
        except Exception:
            return None
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    try:
        return float(text)
    except Exception:
        return None


def _format_decimal_number(value: float) -> str:
    if abs(float(value) - round(float(value))) < 1e-9:
        return str(int(round(float(value))))
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _format_length_mm_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(?:mm|毫米)?", text, flags=re.IGNORECASE)
    if match:
        try:
            return f"{_format_decimal_number(float(match.group(1)))} 毫米"
        except Exception:
            return text
    return text


def _format_aperture_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    normalized = text.strip().lower().replace(" ", "")
    if normalized.startswith("f/"):
        return text
    if normalized.startswith("f"):
        normalized = normalized[1:]
    if re.fullmatch(r"-?\d+(?:\.\d+)?", normalized):
        try:
            return f"f/{_format_decimal_number(float(normalized))}"
        except Exception:
            return text
    return text


def _format_exposure_time_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    normalized = text.strip()
    normalized_lower = normalized.lower()
    if normalized_lower.endswith("s") and len(normalized) > 1:
        normalized = normalized[:-1].strip()
        normalized_lower = normalized.lower()
    if "/" in normalized:
        return normalized
    seconds = _try_parse_float(normalized_lower)
    if seconds is None or seconds <= 0:
        return text
    if seconds < 1:
        fraction = Fraction(seconds).limit_denominator(10000)
        if fraction.denominator > 0:
            return f"{fraction.numerator}/{fraction.denominator}"
    return _format_decimal_number(seconds)


def _format_yes_no_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    lowered = text.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on", "enabled", "enable", "supported", "support", "是", "有"}:
        return "是"
    if lowered in {"0", "false", "no", "n", "off", "disabled", "disable", "none", "否", "无"}:
        return "否"
    parsed = _try_parse_float(lowered)
    if parsed is not None:
        return "是" if parsed != 0 else "否"
    return text


def _format_datetime_text(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


def _format_iso_text(value: Any) -> str:
    parsed = _try_parse_float(value)
    if parsed is not None:
        return _format_decimal_number(parsed)
    return _clean_text(value)


def _format_white_balance_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parsed = _try_parse_float(text)
    if parsed is not None:
        if int(round(parsed)) == 0:
            return "自动"
        if int(round(parsed)) == 1:
            return "手动"
    lowered = text.lower()
    if lowered in {"auto", "automatic"}:
        return "自动"
    if lowered in {"manual"}:
        return "手动"
    return text


def _format_flash_text(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parsed = _try_parse_float(text)
    if parsed is not None:
        return "是" if (int(round(parsed)) & 0x1) else "否"
    lowered = text.lower()
    if any(token in lowered for token in ("did not fire", "not fire", "no flash", "flash off", "off")):
        return "否"
    if any(token in lowered for token in ("fired", "flash on", "on")):
        return "是"
    if lowered in {"yes", "true"}:
        return "是"
    if lowered in {"no", "false"}:
        return "否"
    return text


def _format_file_size_text(size_bytes: Any) -> str:
    parsed = _try_parse_float(size_bytes)
    if parsed is None or parsed < 0:
        return _clean_text(size_bytes)
    size = float(parsed)
    units = ("B", "KB", "MB", "GB", "TB")
    unit_index = 0
    while size >= 1000.0 and unit_index < len(units) - 1:
        size /= 1000.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(round(size))} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def _stat_timestamp_to_datetime(timestamp: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(timestamp))
    except Exception:
        return None


def _extract_file_created_datetime(photo_info: PhotoInfo) -> datetime | None:
    try:
        stat_result = photo_info.path.stat()
    except Exception:
        return None
    birth_time = getattr(stat_result, "st_birthtime", None)
    if birth_time not in (None, 0):
        dt = _stat_timestamp_to_datetime(birth_time)
        if dt is not None:
            return dt
    return _stat_timestamp_to_datetime(getattr(stat_result, "st_ctime", None))


def _extract_file_modified_datetime(photo_info: PhotoInfo) -> datetime | None:
    try:
        return _stat_timestamp_to_datetime(photo_info.path.stat().st_mtime)
    except Exception:
        return None


@lru_cache(maxsize=1024)
def _probe_image_file_properties(path_text: str) -> tuple[int | None, int | None, bool | None]:
    try:
        with Image.open(path_text) as image:
            width, height = image.size
            bands = tuple(str(band or "").upper() for band in image.getbands())
            has_alpha = "A" in bands
            if not has_alpha:
                has_alpha = "transparency" in image.info
            return int(width), int(height), bool(has_alpha)
    except Exception:
        return None, None, None


def _extract_title_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "Meta:Title",
        "XMP-dc:Title",
        "XMP:Title",
        "Title",
        "IFD0:XPTitle",
        "XPTitle",
        "IPTC:ObjectName",
        "ObjectName",
    )


def _extract_description_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "Meta:Description",
        "XMP-dc:Description",
        "XMP:Description",
        "Description",
        "EXIF:ImageDescription",
        "ExifIFD:ImageDescription",
        "IFD0:ImageDescription",
        "ImageDescription",
        "IPTC:Caption-Abstract",
        "Caption-Abstract",
    )


def _extract_device_make_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "Make",
        "IFD0:Make",
        "EXIF:Make",
        "XMP-tiff:Make",
    )


def _extract_device_model_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "Model",
        "CameraModelName",
        "IFD0:Model",
        "EXIF:Model",
        "XMP-tiff:Model",
    )


def _extract_lens_model_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "LensModel",
        "ExifIFD:LensModel",
        "EXIF:LensModel",
        "Composite:LensModel",
        "Lens",
        "LensID",
        "XMP-aux:LensModel",
        "XMP-aux:Lens",
    )


def _extract_color_space_text(raw_metadata: Dict[str, Any], *, profile_description: str = "") -> str:
    text = _lookup_metadata_text(
        raw_metadata,
        "ColorSpace",
        "EXIF:ColorSpace",
        "ExifIFD:ColorSpace",
        "ICC_Profile:ColorSpaceData",
        "ICC_Profile:ColorSpace",
    )
    if not text:
        profile_lower = profile_description.lower()
        if "rgb" in profile_lower:
            return "RGB"
        if "cmyk" in profile_lower:
            return "CMYK"
        return ""

    lowered = text.lower()
    if lowered in {"1", "srgb", "adobergb", "adobe rgb"}:
        return "RGB"
    if lowered in {"65535", "uncalibrated"}:
        profile_lower = profile_description.lower()
        if "rgb" in profile_lower:
            return "RGB"
    return text


def _extract_profile_description_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "ICC_Profile:ProfileDescription",
        "ICC_Profile:ProfileDescriptionML",
        "ProfileDescription",
        "ICC_Profile:Description",
    )


def _extract_focal_length_text(raw_metadata: Dict[str, Any]) -> str:
    return _format_length_mm_text(
        _lookup_metadata_text(
            raw_metadata,
            "FocalLength",
            "EXIF:FocalLength",
            "ExifIFD:FocalLength",
            "Composite:FocalLength",
            "XMP-exif:FocalLength",
        )
    )


def _extract_iso_text(raw_metadata: Dict[str, Any]) -> str:
    return _format_iso_text(
        _lookup_metadata_text(
            raw_metadata,
            "ISO",
            "PhotographicSensitivity",
            "ISOSpeedRatings",
            "EXIF:ISO",
            "ExifIFD:ISO",
            "XMP-exif:PhotographicSensitivity",
            "XMP-exif:ISOSpeedRatings",
        )
    )


def _extract_alpha_channel_text(photo_info: PhotoInfo, raw_metadata: Dict[str, Any]) -> str:
    text = _lookup_metadata_text(
        raw_metadata,
        "AlphaChannel",
        "Alpha",
        "HasAlpha",
        "AlphaChannels",
        "File:AlphaChannels",
    )
    normalized = _format_yes_no_text(text)
    if normalized:
        return normalized
    _width, _height, has_alpha = _probe_image_file_properties(str(photo_info.path))
    if has_alpha is None:
        return ""
    return "是" if has_alpha else "否"


def _extract_red_eye_text(raw_metadata: Dict[str, Any]) -> str:
    return _format_yes_no_text(
        _lookup_metadata_text(
            raw_metadata,
            "RedEye",
            "RedEyeMode",
            "RedEyeReduction",
            "FlashRedEyeMode",
        )
    )


def _extract_metering_mode_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "MeteringMode",
        "EXIF:MeteringMode",
        "ExifIFD:MeteringMode",
    )


def _extract_aperture_text(raw_metadata: Dict[str, Any]) -> str:
    return _format_aperture_text(
        _lookup_metadata_text(
            raw_metadata,
            "FNumber",
            "EXIF:FNumber",
            "ExifIFD:FNumber",
            "Aperture",
            "Composite:Aperture",
            "ApertureValue",
        )
    )


def _extract_exposure_program_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "ExposureProgram",
        "EXIF:ExposureProgram",
        "ExifIFD:ExposureProgram",
    )


def _extract_exposure_time_text(raw_metadata: Dict[str, Any]) -> str:
    return _format_exposure_time_text(
        _lookup_metadata_text(
            raw_metadata,
            "ExposureTime",
            "EXIF:ExposureTime",
            "ExifIFD:ExposureTime",
            "ShutterSpeed",
            "Composite:ShutterSpeed",
        )
    )


def _extract_flash_text(raw_metadata: Dict[str, Any]) -> str:
    return _format_flash_text(
        _lookup_metadata_text(
            raw_metadata,
            "Flash",
            "EXIF:Flash",
            "ExifIFD:Flash",
            "FlashMode",
            "FlashFired",
        )
    )


def _extract_white_balance_text(raw_metadata: Dict[str, Any]) -> str:
    return _format_white_balance_text(
        _lookup_metadata_text(
            raw_metadata,
            "WhiteBalance",
            "EXIF:WhiteBalance",
            "ExifIFD:WhiteBalance",
        )
    )


def _extract_dimensions_text(photo_info: PhotoInfo, raw_metadata: Dict[str, Any]) -> str:
    width = _try_parse_float(
        _lookup_metadata_text(
            raw_metadata,
            "ImageWidth",
            "File:ImageWidth",
            "ExifImageWidth",
            "EXIF:ImageWidth",
            "RawImageWidth",
        )
    )
    height = _try_parse_float(
        _lookup_metadata_text(
            raw_metadata,
            "ImageHeight",
            "File:ImageHeight",
            "ExifImageHeight",
            "EXIF:ImageHeight",
            "RawImageHeight",
        )
    )
    if width is None or height is None:
        probed_width, probed_height, _has_alpha = _probe_image_file_properties(str(photo_info.path))
        if width is None and probed_width is not None:
            width = float(probed_width)
        if height is None and probed_height is not None:
            height = float(probed_height)
    if width is None or height is None:
        return ""
    return f"{_format_decimal_number(width)}x{_format_decimal_number(height)}"


def _extract_resolution_dpi_text(raw_metadata: Dict[str, Any]) -> str:
    x_resolution = _try_parse_float(
        _lookup_metadata_text(
            raw_metadata,
            "XResolution",
            "IFD0:XResolution",
            "EXIF:XResolution",
        )
    )
    y_resolution = _try_parse_float(
        _lookup_metadata_text(
            raw_metadata,
            "YResolution",
            "IFD0:YResolution",
            "EXIF:YResolution",
        )
    )
    if x_resolution is None and y_resolution is None:
        return ""
    if x_resolution is None:
        x_resolution = y_resolution
    if y_resolution is None:
        y_resolution = x_resolution
    return f"{_format_decimal_number(float(x_resolution))}x{_format_decimal_number(float(y_resolution))}"


def _extract_creator_tool_text(raw_metadata: Dict[str, Any]) -> str:
    return _lookup_metadata_text(
        raw_metadata,
        "XMP-xmp:CreatorTool",
        "XMP:CreatorTool",
        "CreatorTool",
        "Software",
        "ProcessingSoftware",
    )


def _extract_content_created_time_text(raw_metadata: Dict[str, Any]) -> str:
    return _format_datetime_text(_extract_capture_datetime_from_metadata(raw_metadata))


def _extract_file_created_time_text(photo_info: PhotoInfo) -> str:
    return _format_datetime_text(_extract_file_created_datetime(photo_info))


def _extract_file_modified_time_text(photo_info: PhotoInfo) -> str:
    return _format_datetime_text(_extract_file_modified_datetime(photo_info))


def _extract_file_size_text(photo_info: PhotoInfo, raw_metadata: Dict[str, Any]) -> str:
    try:
        return _format_file_size_text(photo_info.path.stat().st_size)
    except Exception:
        return _format_file_size_text(
            _lookup_metadata_text(
                raw_metadata,
                "File:FileSize",
                "FileSize",
            )
        )


def _extract_normalized_file_entries(photo_info: PhotoInfo, raw_metadata: Dict[str, Any]) -> TemplateContext:
    try:
        normalized = normalize_metadata(
            photo_info.path,
            raw_metadata,
            bird_arg=None,
            bird_priority=["meta", "filename"],
            bird_regex=r"(?P<bird>[^_]+)_",
            time_format="%Y-%m-%d %H:%M",
        )
    except Exception:
        return {}

    context: TemplateContext = {}
    if normalized.location:
        context["location"] = normalized.location
    if normalized.gps_text:
        context["gps_text"] = normalized.gps_text
    if normalized.settings_text:
        context["settings_text"] = normalized.settings_text
    else:
        settings = format_settings_line(normalized, show_eq_focal=True) or ""
        if settings:
            context["settings_text"] = settings
    return context


def set_report_db_row_resolver(
    resolver: Optional[Callable[[Path], Optional[Dict[str, Any]]]]
) -> None:
    """设置全局 report.db 行解析函数（由 GUI 层注入）。

    - resolver(path) 返回与给定图片路径对应的 report 行（dict），或 None。
    - 传入 None 将禁用 report.db provider 的行解析。
    """
    global _REPORT_DB_ROW_RESOLVER
    _REPORT_DB_ROW_RESOLVER = resolver


def get_report_db_row_for_path(path: Path) -> Optional[Dict[str, Any]]:
    """根据图片路径查询 report.db 中对应的行（若配置了 resolver）。"""
    resolver = _REPORT_DB_ROW_RESOLVER
    if resolver is None:
        return None
    try:
        return resolver(path)
    except Exception:
        return None


def report_db_lookup_keys_for_value(value: Any) -> tuple[str, ...]:
    """将 report.db 中的 filename 值规范为可兼容匹配的查找 key。

    兼容以下常见情况：
    - 数据库存的是完整文件名，如 ``sample.jpg``
    - 数据库存的是 stem，如 ``sample``
    - 数据库存的是带目录的相对/绝对路径，且分隔符可能来自 Windows 或 macOS
    """
    text = str(value or "").replace("\x00", " ").strip()
    if not text:
        return ()
    basename = text.replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem = Path(basename).stem if basename else ""
    keys: list[str] = []
    for candidate in (text, basename, stem):
        if candidate and candidate not in keys:
            keys.append(candidate)
    return tuple(keys)


def report_db_lookup_keys_for_path(path: Path) -> tuple[str, ...]:
    """返回图片路径可用于匹配 report.db 行的候选 key。"""
    try:
        return report_db_lookup_keys_for_value(path.name)
    except Exception:
        return ()


def _normalize_from_file_context_key(source_key: str) -> str:
    text = str(source_key or "").strip()
    if not text:
        return ""
    match = re.fullmatch(r"\{([^{}]+)\}", text)
    if match:
        text = str(match.group(1) or "").strip()
    lowered = text.lower()
    return _FROM_FILE_CONTEXT_KEY_ALIASES.get(lowered, lowered)


def format_text_with_context(text: str, context: TemplateContext) -> str:
    if not text:
        return ""
    safe = defaultdict(str, context)
    try:
        return text.format_map(safe)
    except Exception:
        return text


def lookup_exif_text(tag: str, raw_metadata: Dict[str, Any], context: TemplateContext) -> str:
    token = (tag or "").strip()
    if not token:
        return ""
    lowered = token.lower()
    if lowered in context:
        return _clean_text(context[lowered])
    lookup = _normalize_lookup(raw_metadata)
    value = lookup.get(lowered)
    if value is None and ":" in lowered:
        value = lookup.get(lowered.split(":")[-1])
    if value is None:
        suffix = f":{lowered}"
        for key, candidate in lookup.items():
            if key.endswith(suffix):
                value = candidate
                break
    return _clean_text(value)


@dataclass(frozen=True, slots=True)
class TemplateContextField:
    """数据源字段定义。"""

    key: str
    display_label: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AutoProxyFieldRoute:
    """AutoProxy 中某个逻辑字段到子 provider 的候选字段映射。"""

    provider_id: str
    candidate_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AutoProxyCandidateResult:
    """AutoProxy 字段解析过程中的单个候选结果。"""

    provider_id: str
    provider_name: str
    source_key: str
    display_caption: str
    text_content: str


_FALLBACK_AUTO_PROXY_ROUTE_CONFIG: dict[str, list[dict[str, Any]]] = {
    "bird_species_cn": [
        {
            "provider_id": TEMPLATE_SOURCE_EXIF,
            "candidate_keys": [
                "XMP-dc:Title",
                "IFD0:XPTitle",
                "Title",
                "XPTitle",
                "EXIF:ImageDescription",
                "ImageDescription",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_REPORT_DB,
            "candidate_keys": [
                "bird_species_cn",
                "report.bird_species_cn",
            ],
        },
    ],
    "title": [
        {
            "provider_id": TEMPLATE_SOURCE_EXIF,
            "candidate_keys": [
                "XMP-dc:Title",
                "IFD0:XPTitle",
                "Title",
                "XPTitle",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_REPORT_DB,
            "candidate_keys": [
                "title",
                "report.title",
            ],
        },
    ],
    "caption": [
        {
            "provider_id": TEMPLATE_SOURCE_EXIF,
            "candidate_keys": [
                "XMP-dc:Description",
                "IPTC:Caption-Abstract",
                "Caption-Abstract",
                "EXIF:ImageDescription",
                "ImageDescription",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_REPORT_DB,
            "candidate_keys": [
                "caption",
                "report.caption",
            ],
        },
    ],
    "date_time_original": [
        {
            "provider_id": TEMPLATE_SOURCE_FROM_FILE,
            "candidate_keys": [
                "capture_text",
                "{capture_text}",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_EXIF,
            "candidate_keys": [
                "EXIF:DateTimeOriginal",
                "DateTimeOriginal",
                "EXIF:CreateDate",
                "CreateDate",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_REPORT_DB,
            "candidate_keys": [
                "date_time_original",
                "report.date_time_original",
            ],
        },
    ],
    "camera_model": [
        {
            "provider_id": TEMPLATE_SOURCE_FROM_FILE,
            "candidate_keys": [
                "camera",
                "{camera}",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_EXIF,
            "candidate_keys": [
                "EXIF:Model",
                "IFD0:Model",
                "Model",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_REPORT_DB,
            "candidate_keys": [
                "camera_model",
                "report.camera_model",
            ],
        },
    ],
    "lens_model": [
        {
            "provider_id": TEMPLATE_SOURCE_FROM_FILE,
            "candidate_keys": [
                "lens",
                "{lens}",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_EXIF,
            "candidate_keys": [
                "EXIF:LensModel",
                "ExifIFD:LensModel",
                "LensModel",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_REPORT_DB,
            "candidate_keys": [
                "lens_model",
                "report.lens_model",
            ],
        },
    ],
    "rating": [
        {
            "provider_id": TEMPLATE_SOURCE_EXIF,
            "candidate_keys": [
                "XMP-xmp:Rating",
                "Rating",
                "Sony:Rating",
            ],
        },
        {
            "provider_id": TEMPLATE_SOURCE_REPORT_DB,
            "candidate_keys": [
                "rating",
                "report.rating",
            ],
        },
    ],
}


_PROVIDER_CLASS_REGISTRY: dict[str, type["TemplateContextProvider"]] = {}


def _split_words(text: str) -> list[str]:
    normalized = re.sub(r"[_\-/]+", " ", str(text or "").strip())
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)
    normalized = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", normalized)
    return [part for part in normalized.split() if part]


def _load_exif_cfg_maps() -> tuple[dict[str, str], dict[str, str], tuple[str, ...], set[str]]:
    try:
        settings = load_exif_settings()
    except Exception:
        settings = {}
    names_raw = settings.get("exif_tag_names_zh") or {}
    tokens_raw = settings.get("exif_tag_name_token_map_zh") or {}
    priority_raw = settings.get("exif_tag_priority") or []
    hidden_raw = settings.get("exif_tag_hidden") or []

    names = {
        str(key or "").strip(): str(value or "").strip()
        for key, value in names_raw.items()
        if str(key or "").strip() and str(value or "").strip()
    }
    tokens = {
        str(key or "").strip(): str(value or "").strip()
        for key, value in tokens_raw.items()
        if str(key or "").strip() and str(value or "").strip()
    }
    priority = tuple(
        text for text in (str(item or "").strip() for item in priority_raw)
        if text
    )
    hidden = {
        str(item or "").strip().lower()
        for item in hidden_raw
        if str(item or "").strip()
    }
    return names, tokens, priority, hidden


def _lookup_exif_label_from_cfg(source_key: str, names_map: dict[str, str]) -> str:
    key = str(source_key or "").strip()
    if not key:
        return ""
    candidates = [key]
    if ":" in key:
        namespace, tag_name = key.split(":", 1)
        candidates.extend(
            candidate
            for candidate in (
                f"{namespace.title()}:{tag_name}",
                f"{namespace.upper()}:{tag_name}",
                f"{namespace.lower()}:{tag_name}",
                tag_name,
            )
            if candidate not in candidates
        )
    for candidate in candidates:
        label = str(names_map.get(candidate) or "").strip()
        if label:
            return label
    return ""


def _humanize_exif_source_key(source_key: str, token_map: dict[str, str]) -> str:
    key = str(source_key or "").strip()
    if not key:
        return ""
    namespace, _, tag_name = key.partition(":")
    tokens = _split_words(tag_name or key)
    translated: list[str] = []
    for token in tokens:
        translated.append(str(token_map.get(token) or token).strip())
    label = " ".join(part for part in translated if part).strip()
    if label:
        return label
    namespace_label = str(token_map.get(namespace) or namespace).strip()
    tag_label = str(token_map.get(tag_name) or tag_name).strip()
    if tag_label:
        return tag_label
    return namespace_label or key


@lru_cache(maxsize=1)
def _load_builtin_auto_proxy_route_config_raw() -> dict[str, Any]:
    try:
        route_file = resolve_bundled_path("config", "template_context_routes.json")
        text = route_file.read_text(encoding="utf-8")
        raw = json.loads(text)
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return dict(_FALLBACK_AUTO_PROXY_ROUTE_CONFIG)


def _normalize_auto_proxy_route_config(
    value: Any,
) -> dict[str, tuple[AutoProxyFieldRoute, ...]]:
    if not isinstance(value, dict):
        value = {}
    result: dict[str, tuple[AutoProxyFieldRoute, ...]] = {}
    valid_provider_ids = {
        TEMPLATE_SOURCE_EDITOR,
        TEMPLATE_SOURCE_EXIF,
        TEMPLATE_SOURCE_FROM_FILE,
        TEMPLATE_SOURCE_REPORT_DB,
    }
    for raw_logical_key, raw_routes in value.items():
        logical_key = _normalize_from_file_context_key(raw_logical_key).lower()
        if not logical_key or not isinstance(raw_routes, list):
            continue
        normalized_routes: list[AutoProxyFieldRoute] = []
        for raw_route in raw_routes:
            if not isinstance(raw_route, dict):
                continue
            provider_id = normalize_template_source_type(raw_route.get("provider_id"))
            if provider_id not in valid_provider_ids:
                continue
            raw_candidate_keys = raw_route.get("candidate_keys")
            if isinstance(raw_candidate_keys, str):
                raw_candidate_keys = [raw_candidate_keys]
            if not isinstance(raw_candidate_keys, list):
                continue
            candidate_keys: list[str] = []
            seen_keys: set[str] = set()
            for raw_candidate_key in raw_candidate_keys:
                candidate_key = str(raw_candidate_key or "").strip()
                if not candidate_key or candidate_key in seen_keys:
                    continue
                seen_keys.add(candidate_key)
                candidate_keys.append(candidate_key)
            if not candidate_keys:
                continue
            normalized_routes.append(
                AutoProxyFieldRoute(
                    provider_id=provider_id,
                    candidate_keys=tuple(candidate_keys),
                )
            )
        if normalized_routes:
            result[logical_key] = tuple(normalized_routes)
    if result:
        return result
    if value is _FALLBACK_AUTO_PROXY_ROUTE_CONFIG:
        return {}
    return _normalize_auto_proxy_route_config(_FALLBACK_AUTO_PROXY_ROUTE_CONFIG)


def normalize_template_source_type(value: Any) -> str:
    source_type = str(value or "").strip().lower()
    if source_type == TEMPLATE_SOURCE_METADATA_LEGACY:
        return TEMPLATE_SOURCE_AUTO
    if source_type in {
        TEMPLATE_SOURCE_AUTO,
        TEMPLATE_SOURCE_EDITOR,
        TEMPLATE_SOURCE_EXIF,
        TEMPLATE_SOURCE_REPORT_DB,
        TEMPLATE_SOURCE_FROM_FILE,
    }:
        return source_type
    return TEMPLATE_SOURCE_AUTO


def template_source_display_name(source_type: str) -> str:
    normalized = normalize_template_source_type(source_type)
    provider_cls = _PROVIDER_CLASS_REGISTRY.get(normalized)
    if provider_cls is None:
        return "文件"
    return provider_cls.display_name


class TemplateContextProvider(ABC):
    """模板文本来源抽象基类。

    面向对象职责：
    - 类级别声明本数据源有哪些字段
    - 类级别声明本数据源如何构建上下文
    - 实例级别只负责读取某一个字段的值
    """

    provider_id: str = ""
    display_name: str = ""
    _field_definitions_cache: tuple[TemplateContextField, ...] | None = None
    _field_lookup_cache: dict[str, TemplateContextField] | None = None

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        cls._field_definitions_cache = None
        cls._field_lookup_cache = None
        provider_id = str(getattr(cls, "provider_id", "") or "").strip()
        if provider_id:
            _PROVIDER_CLASS_REGISTRY[provider_id] = cls

    def __init__(self, source_key: str, *, display_label: str = "") -> None:
        self.source_key = str(source_key or "").strip()
        self.display_label = str(display_label or "").strip()

    @property
    def id(self) -> str:
        return self.provider_id

    @classmethod
    def normalize_field_key(cls, source_key: str) -> str:
        return str(source_key or "").strip()

    @classmethod
    @abstractmethod
    def _build_field_definitions(cls) -> tuple[TemplateContextField, ...]:
        """返回本数据源显式支持的字段定义。"""

    @classmethod
    @abstractmethod
    def build_context_entries(cls, photo_info: PhotoInfo) -> TemplateContext:
        """构建本数据源能提供的上下文字段。"""

    @abstractmethod
    def _read_text_value(
        self,
        photo_info: PhotoInfo,
        field: TemplateContextField | None,
    ) -> str:
        """返回当前实例所指字段的值。"""

    @classmethod
    def available_fields(cls) -> tuple[TemplateContextField, ...]:
        cached = cls._field_definitions_cache
        if cached is not None:
            return cached
        cls._field_definitions_cache = cls._build_field_definitions()
        return cls._field_definitions_cache

    @classmethod
    def _field_lookup(cls) -> dict[str, TemplateContextField]:
        cached = cls._field_lookup_cache
        if cached is not None:
            return cached
        lookup: dict[str, TemplateContextField] = {}
        for field in cls.available_fields():
            for candidate in (field.key, *field.aliases):
                normalized = cls.normalize_field_key(candidate).lower()
                if normalized and normalized not in lookup:
                    lookup[normalized] = field
        cls._field_lookup_cache = lookup
        return lookup

    @classmethod
    def resolve_field_definition(cls, source_key: str) -> TemplateContextField | None:
        normalized = cls.normalize_field_key(source_key).lower()
        if not normalized:
            return None
        return cls._field_lookup().get(normalized)

    @classmethod
    def field_options(cls) -> list[tuple[str, str, str]]:
        return [
            (cls.provider_id, field.key, field.display_label)
            for field in cls.available_fields()
        ]

    def get_text_content(self, photo_info: PhotoInfo) -> str:
        info = ensure_photo_info(photo_info)
        field = self.resolve_field_definition(self.source_key)
        return _clean_text(self._read_text_value(info, field))

    def get_display_caption(self, photo_info: PhotoInfo) -> str:  # noqa: ARG002
        field = self.resolve_field_definition(self.source_key)
        label = self.display_label or (field.display_label if field else "") or self.source_key or "未设置"
        prefix = str(self.display_name or "").strip()
        if prefix:
            return f"{prefix}:{label}"
        return f"{label}"


class EditorTemplateContextProvider(TemplateContextProvider):
    provider_id = TEMPLATE_SOURCE_EDITOR
    display_name = "编辑器"

    _FIELD_DEFINITIONS: tuple[TemplateContextField, ...] = (
        TemplateContextField(
            "row_number",
            "列表编号",
            aliases=("editor.row_number", "editor.index", "editor.sequence", "index", "sequence", "seq"),
        ),
    )

    @classmethod
    def normalize_field_key(cls, source_key: str) -> str:
        return _normalize_from_file_context_key(source_key)

    @classmethod
    def _build_field_definitions(cls) -> tuple[TemplateContextField, ...]:
        return cls._FIELD_DEFINITIONS

    @classmethod
    def build_context_entries(cls, photo_info: PhotoInfo) -> TemplateContext:
        row_number = _normalize_editor_row_number(getattr(photo_info, "editor_row_number", None))
        if row_number is None:
            return {}
        row_text = str(row_number)
        return {
            "row_number": row_text,
            "editor.row_number": row_text,
            "editor.index": row_text,
            "editor.sequence": row_text,
        }

    def _read_text_value(self, photo_info: PhotoInfo, field: TemplateContextField | None) -> str:
        context = self.build_context_entries(photo_info)
        if field is not None:
            for candidate in (field.key, *field.aliases):
                normalized = self.normalize_field_key(candidate)
                direct_value = _clean_text(context.get(normalized, ""))
                if direct_value:
                    return direct_value
                direct_value = _clean_text(context.get(candidate, ""))
                if direct_value:
                    return direct_value
        normalized_key = self.normalize_field_key(self.source_key)
        if normalized_key:
            direct_value = _clean_text(context.get(normalized_key, ""))
            if direct_value:
                return direct_value
        return _clean_text(context.get(self.source_key, ""))


class ExifTemplateContextProvider(TemplateContextProvider):
    provider_id = TEMPLATE_SOURCE_EXIF
    display_name = "EXIF"

    _EXIF_CONTEXT_FIELDS: tuple[TemplateContextField, ...] = (
        TemplateContextField("bird", "鸟种(归一化)"),
        TemplateContextField("capture_text", "时间(归一化)"),
        TemplateContextField("location", "地点(归一化)"),
        TemplateContextField("gps_text", "GPS 文本(归一化)"),
        TemplateContextField("camera", "相机(归一化)"),
        TemplateContextField("lens", "镜头(归一化)"),
        TemplateContextField("settings_text", "参数串(归一化)"),
    )
    _EXIF_COMMON_TAG_FIELDS: tuple[TemplateContextField, ...] = (
        TemplateContextField("XMP-dc:Title", "鸟名 (XMP)"),
        TemplateContextField("XMP-xmp:Rating", "星级 (XMP)"),
        TemplateContextField("EXIF:DateTimeOriginal", "拍摄时间 (EXIF)"),
        TemplateContextField("EXIF:CreateDate", "创建时间 (EXIF)"),
        TemplateContextField("EXIF:Model", "机身型号 (EXIF)"),
        TemplateContextField("EXIF:Make", "机身品牌 (EXIF)"),
        TemplateContextField("EXIF:LensModel", "镜头型号 (EXIF)"),
        TemplateContextField("EXIF:FNumber", "光圈 (EXIF)"),
        TemplateContextField("EXIF:ExposureTime", "快门 (EXIF)"),
        TemplateContextField("EXIF:ISO", "ISO (EXIF)"),
        TemplateContextField("EXIF:FocalLength", "焦距 (EXIF)"),
        TemplateContextField("Composite:GPSLatitude", "纬度 (GPS)"),
        TemplateContextField("Composite:GPSLongitude", "经度 (GPS)"),
    )

    @classmethod
    def _display_label_for_source_key(cls, source_key: str) -> str:
        names_map, token_map, _priority, _hidden = _load_exif_cfg_maps()
        label = _lookup_exif_label_from_cfg(source_key, names_map)
        if label:
            return label
        label = _humanize_exif_source_key(source_key, token_map)
        return label or str(source_key or "").strip()

    @classmethod
    def _build_field_definitions(cls) -> tuple[TemplateContextField, ...]:
        names_map, _token_map, priority, hidden = _load_exif_cfg_maps()
        fields: list[TemplateContextField] = list(cls._EXIF_CONTEXT_FIELDS) + list(cls._EXIF_COMMON_TAG_FIELDS)
        seen = {
            cls.normalize_field_key(field.key).lower()
            for field in fields
            if cls.normalize_field_key(field.key)
        }

        for source_key in priority:
            normalized = cls.normalize_field_key(source_key).lower()
            if not normalized or normalized in hidden or normalized in seen:
                continue
            fields.append(TemplateContextField(source_key, cls._display_label_for_source_key(source_key)))
            seen.add(normalized)

        extra_keys = sorted(
            (
                key for key in names_map
                if cls.normalize_field_key(key).lower() not in hidden
            ),
            key=lambda item: (cls._display_label_for_source_key(item), item.lower()),
        )
        for source_key in extra_keys:
            normalized = cls.normalize_field_key(source_key).lower()
            if not normalized or normalized in seen:
                continue
            fields.append(TemplateContextField(source_key, cls._display_label_for_source_key(source_key)))
            seen.add(normalized)
        return tuple(fields)

    @classmethod
    def build_context_entries(cls, photo_info: PhotoInfo) -> TemplateContext:
        metadata = _photo_raw_metadata(photo_info)
        context: TemplateContext = {}
        try:
            normalized = normalize_metadata(
                photo_info.path,
                metadata,
                bird_arg=None,
                bird_priority=["meta", "filename"],
                bird_regex=r"(?P<bird>[^_]+)_",
                time_format="%Y-%m-%d %H:%M",
            )
        except Exception:
            return context

        context["bird"] = normalized.bird or ""
        context["capture_text"] = normalized.capture_text or ""
        context["location"] = normalized.location or ""
        context["gps_text"] = normalized.gps_text or ""
        context["camera"] = normalized.camera or ""
        context["lens"] = normalized.lens or ""

        settings = normalized.settings_text or format_settings_line(normalized, show_eq_focal=True) or ""
        if settings:
            context["settings_text"] = settings
        return context

    def _read_text_value(self, photo_info: PhotoInfo, field: TemplateContextField | None) -> str:
        metadata = _photo_raw_metadata(photo_info)
        context = self.build_context_entries(photo_info)
        source_key = field.key if field is not None else self.source_key
        return lookup_exif_text(source_key, metadata, context)


class ReportDBTemplateContextProvider(TemplateContextProvider):
    provider_id = TEMPLATE_SOURCE_REPORT_DB
    display_name = "慧眼选鸟"

    _COLUMN_LABELS: dict[str, str] = {
        "filename": "文件名",
        "bird_species_cn": "鸟种中文名",
        "bird_species_en": "鸟种英文名",
        "birdid_confidence": "鸟种识别置信度",
        "date_time_original": "拍摄时间",
        "title": "标题",
        "caption": "说明",
        "city": "城市",
        "state_province": "省/州",
        "country": "国家",
        "exposure_status": "曝光状态",
        "iso": "ISO",
        "shutter_speed": "快门速度",
        "aperture": "光圈",
        "focal_length": "焦距",
        "focal_length_35mm": "35mm 等效焦距",
        "camera_model": "相机型号",
        "lens_model": "镜头型号",
        "gps_latitude": "GPS 纬度",
        "gps_longitude": "GPS 经度",
        "gps_altitude": "GPS 海拔",
        "has_bird": "有鸟",
        "confidence": "置信度",
        "rating": "评分",
        "focus_status": "对焦状态",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    }

    @classmethod
    def _column_display_label(cls, column_name: str) -> str:
        label = str(cls._COLUMN_LABELS.get(column_name) or "").strip()
        if label:
            return label
        return str(column_name or "").replace("_", " ").strip() or "未命名字段"

    @classmethod
    def _build_field_definitions(cls) -> tuple[TemplateContextField, ...]:
        fields: list[TemplateContextField] = []
        for col_name, _type_def, _default in PHOTO_COLUMNS:
            if col_name in _REPORT_DB_PATH_COLUMNS:
                continue
            fields.append(
                TemplateContextField(
                    col_name,
                    cls._column_display_label(col_name),
                    aliases=(f"report.{col_name}",),
                )
            )
        return tuple(fields)

    @classmethod
    def _resolve_row(cls, photo_info: PhotoInfo) -> dict[str, Any] | None:
        row = get_report_db_row_for_path(photo_info.path)
        return row if isinstance(row, dict) else None

    @classmethod
    def build_context_entries(cls, photo_info: PhotoInfo) -> TemplateContext:
        row = cls._resolve_row(photo_info)
        if row is None:
            return {}

        context: TemplateContext = {}
        species_cn = str(row.get("bird_species_cn") or "").strip()
        species_en = str(row.get("bird_species_en") or "").strip()

        if species_cn:
            context["bird"] = species_cn
            context["bird_common"] = species_cn
        if species_en:
            context["bird_latin"] = species_en
            context["bird_scientific"] = species_en

        for field in cls.available_fields():
            column_name = field.key
            value = row.get(column_name)
            context["report." + column_name] = "" if value is None else _clean_text(value)
        return context

    def _read_text_value(self, photo_info: PhotoInfo, field: TemplateContextField | None) -> str:
        source_key = str(field.key if field is not None else self.source_key or "").strip()
        context = self.build_context_entries(photo_info)
        if source_key in context:
            return _clean_text(context.get(source_key))
        if source_key and not source_key.startswith("report."):
            report_key = "report." + source_key
            if report_key in context:
                return _clean_text(context.get(report_key))
        row = self._resolve_row(photo_info)
        if row is None:
            return ""
        direct_key = source_key.removeprefix("report.")
        return _clean_text(row.get(direct_key))


class FromFileTemplateContextProvider(TemplateContextProvider):
    provider_id = TEMPLATE_SOURCE_FROM_FILE
    display_name = "文件"

    _FIELD_DEFINITIONS: tuple[TemplateContextField, ...] = (
        TemplateContextField("title", "标题"),
        TemplateContextField("file_created_time", "创建时间", aliases=("created_time",)),
        TemplateContextField("file_modified_time", "修改时间", aliases=("modified_time",)),
        TemplateContextField("content_created_time", "内容创建时间"),
        TemplateContextField("dimensions", "尺寸", aliases=("size", "image_size")),
        TemplateContextField("resolution_dpi", "分辨率", aliases=("resolution", "dpi")),
        TemplateContextField("device_make", "设备制造商", aliases=("make", "camera_make")),
        TemplateContextField("device_model", "设备型号", aliases=("camera", "camera_model", "model")),
        TemplateContextField("lens", "镜头型号", aliases=("lens_model",)),
        TemplateContextField("exposure_time", "曝光时间", aliases=("shutter_speed",)),
        TemplateContextField("focal_length", "焦距", aliases=("focal",)),
        TemplateContextField("iso", "ISO感光度"),
        TemplateContextField("flash", "闪光灯"),
        TemplateContextField("aperture", "光圈数", aliases=("f_number", "fnumber")),
        TemplateContextField("exposure_program", "曝光程序"),
        TemplateContextField("metering_mode", "测光模式"),
        TemplateContextField("white_balance", "白平衡"),
        TemplateContextField("color_space", "色彩空间"),
        TemplateContextField("profile_description", "颜色描述文件", aliases=("profile", "icc_profile")),
        TemplateContextField("creator_tool", "内容创作者", aliases=("software", "processing_software")),
        TemplateContextField("file_size", "文件大小"),
        TemplateContextField("description", "描述", aliases=("caption", "image_description")),
        TemplateContextField("alpha_channel", "Alpha通道", aliases=("alpha", "has_alpha")),
        TemplateContextField("red_eye", "红眼", aliases=("redeye",)),
        TemplateContextField("capture_date", "拍摄日期", aliases=("date",)),
        TemplateContextField(
            "capture_text",
            "拍摄日期时间",
            aliases=("capture_time", "capture_datetime", "date_time_original", "datetime_original"),
        ),
        TemplateContextField("author", "作者"),
        TemplateContextField("location", "拍摄地点"),
        TemplateContextField("gps_text", "GPS 坐标文字", aliases=("gps",)),
        TemplateContextField("settings_text", "拍摄参数", aliases=("settings",)),
        TemplateContextField("stem", "文件名（不含扩展名）"),
        TemplateContextField("filename", "完整文件名"),
    )

    @classmethod
    def normalize_field_key(cls, source_key: str) -> str:
        return _normalize_from_file_context_key(source_key)

    @classmethod
    def _build_field_definitions(cls) -> tuple[TemplateContextField, ...]:
        return cls._FIELD_DEFINITIONS

    @classmethod
    def build_context_entries(cls, photo_info: PhotoInfo) -> TemplateContext:
        metadata = _photo_raw_metadata(photo_info)
        context: TemplateContext = {
            "stem": photo_info.path.stem,
            "filename": photo_info.path.name,
        }

        title = _extract_title_text(metadata)
        if title:
            context["title"] = title

        file_created_time = _extract_file_created_time_text(photo_info)
        if file_created_time:
            context["file_created_time"] = file_created_time

        file_modified_time = _extract_file_modified_time_text(photo_info)
        if file_modified_time:
            context["file_modified_time"] = file_modified_time

        content_created_time = _extract_content_created_time_text(metadata)
        if content_created_time:
            context["content_created_time"] = content_created_time

        dimensions = _extract_dimensions_text(photo_info, metadata)
        if dimensions:
            context["dimensions"] = dimensions

        resolution_dpi = _extract_resolution_dpi_text(metadata)
        if resolution_dpi:
            context["resolution_dpi"] = resolution_dpi

        device_make = _extract_device_make_text(metadata)
        if device_make:
            context["device_make"] = device_make

        device_model = _extract_device_model_text(metadata)
        if device_model:
            context["device_model"] = device_model

        lens = _extract_lens_model_text(metadata)
        if lens:
            context["lens"] = lens

        exposure_time = _extract_exposure_time_text(metadata)
        if exposure_time:
            context["exposure_time"] = exposure_time

        focal_length = _extract_focal_length_text(metadata)
        if focal_length:
            context["focal_length"] = focal_length

        iso = _extract_iso_text(metadata)
        if iso:
            context["iso"] = iso

        flash = _extract_flash_text(metadata)
        if flash:
            context["flash"] = flash

        aperture = _extract_aperture_text(metadata)
        if aperture:
            context["aperture"] = aperture

        exposure_program = _extract_exposure_program_text(metadata)
        if exposure_program:
            context["exposure_program"] = exposure_program

        metering_mode = _extract_metering_mode_text(metadata)
        if metering_mode:
            context["metering_mode"] = metering_mode

        white_balance = _extract_white_balance_text(metadata)
        if white_balance:
            context["white_balance"] = white_balance

        profile_description = _extract_profile_description_text(metadata)
        if profile_description:
            context["profile_description"] = profile_description

        color_space = _extract_color_space_text(metadata, profile_description=profile_description)
        if color_space:
            context["color_space"] = color_space

        creator_tool = _extract_creator_tool_text(metadata)
        if creator_tool:
            context["creator_tool"] = creator_tool

        file_size = _extract_file_size_text(photo_info, metadata)
        if file_size:
            context["file_size"] = file_size

        description = _extract_description_text(metadata)
        if description:
            context["description"] = description

        alpha_channel = _extract_alpha_channel_text(photo_info, metadata)
        if alpha_channel:
            context["alpha_channel"] = alpha_channel

        red_eye = _extract_red_eye_text(metadata)
        if red_eye:
            context["red_eye"] = red_eye

        capture_text = _extract_capture_text(photo_info, metadata)
        if capture_text:
            context["capture_text"] = capture_text

        capture_date = _extract_capture_date_text(photo_info, metadata)
        if capture_date:
            context["capture_date"] = capture_date

        author = _extract_author_text(metadata)
        if author:
            context["author"] = author

        context.update(_extract_normalized_file_entries(photo_info, metadata))
        return context

    def _read_text_value(self, photo_info: PhotoInfo, field: TemplateContextField | None) -> str:
        context = self.build_context_entries(photo_info)
        if field is not None:
            for candidate in (field.key, *field.aliases):
                normalized = _normalize_from_file_context_key(candidate)
                direct_value = _clean_text(context.get(normalized, ""))
                if direct_value:
                    return direct_value
                direct_value = _clean_text(context.get(candidate, ""))
                if direct_value:
                    return direct_value
        normalized_key = _normalize_from_file_context_key(self.source_key)
        if normalized_key:
            direct_value = _clean_text(context.get(normalized_key, ""))
            if direct_value:
                return direct_value
            direct_value = _clean_text(context.get(self.source_key, ""))
            if direct_value:
                return direct_value
        template_text = str(self.source_key or "").strip()
        if "{" in template_text and "}" in template_text:
            return _clean_text(format_text_with_context(template_text, build_template_context(photo_info)))
        return ""


class AutoProxyTemplateContextProvider(TemplateContextProvider):
    provider_id = TEMPLATE_SOURCE_AUTO
    display_name = ""
    _route_definitions_cache: dict[str, tuple[AutoProxyFieldRoute, ...]] | None = None

    @classmethod
    def delegate_provider_classes(cls) -> tuple[type[TemplateContextProvider], ...]:
        return (
            EditorTemplateContextProvider,
            ReportDBTemplateContextProvider,
            FromFileTemplateContextProvider,
            ExifTemplateContextProvider,
        )

    @classmethod
    def field_options(cls) -> list[tuple[str, str, str]]:
        result: list[tuple[str, str, str]] = []
        for provider_cls in cls.delegate_provider_classes():
            result.extend(provider_cls.field_options())
        return result

    @classmethod
    def normalize_field_key(cls, source_key: str) -> str:
        text = str(source_key or "").strip()
        if not text:
            return ""
        return _normalize_from_file_context_key(text)

    @classmethod
    def _build_field_definitions(cls) -> tuple[TemplateContextField, ...]:
        fields: list[TemplateContextField] = []
        seen: set[str] = set()
        for provider_cls in cls.delegate_provider_classes():
            for field in provider_cls.available_fields():
                normalized_candidates = [
                    provider_cls.normalize_field_key(field.key).lower(),
                    *(provider_cls.normalize_field_key(alias).lower() for alias in field.aliases),
                ]
                normalized_candidates = [item for item in normalized_candidates if item]
                if any(item in seen for item in normalized_candidates):
                    continue
                fields.append(field)
                seen.update(normalized_candidates)
        return tuple(fields)

    @classmethod
    def build_context_entries(cls, photo_info: PhotoInfo) -> TemplateContext:
        context: TemplateContext = {}
        for provider_cls in cls.delegate_provider_classes():
            context.update(provider_cls.build_context_entries(photo_info))
        return context

    @classmethod
    def route_definitions(cls) -> dict[str, tuple[AutoProxyFieldRoute, ...]]:
        cached = cls._route_definitions_cache
        if cached is not None:
            return cached
        cls._route_definitions_cache = _normalize_auto_proxy_route_config(
            _load_builtin_auto_proxy_route_config_raw()
        )
        return cls._route_definitions_cache

    @classmethod
    def _field_route_specs(
        cls,
        source_key: str,
        field: TemplateContextField | None,
    ) -> tuple[AutoProxyFieldRoute, ...]:
        for candidate in (
            field.key if field is not None else "",
            *(field.aliases if field is not None else ()),
            source_key,
        ):
            normalized = cls.normalize_field_key(candidate).lower()
            if not normalized:
                continue
            route_specs = cls.route_definitions().get(normalized)
            if route_specs:
                return route_specs
        return ()

    @classmethod
    def _candidate_keys_for_provider(
        cls,
        provider_cls: type[TemplateContextProvider],
        source_key: str,
        field: TemplateContextField | None,
    ) -> tuple[str, ...]:
        route_specs = cls._field_route_specs(source_key, field)
        if route_specs:
            for route in route_specs:
                if route.provider_id == provider_cls.provider_id:
                    return route.candidate_keys
            return ()

        candidates: list[str] = []
        seen: set[str] = set()
        for candidate in (
            field.key if field is not None else "",
            *(field.aliases if field is not None else ()),
            source_key,
        ):
            normalized = provider_cls.normalize_field_key(candidate).lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(candidate)
        return tuple(candidates)

    def inspect_candidates(self, photo_info: PhotoInfo) -> tuple[AutoProxyCandidateResult, ...]:
        info = ensure_photo_info(photo_info)
        field = self.resolve_field_definition(self.source_key)
        source_key = str(field.key if field is not None else self.source_key or "").strip()
        results: list[AutoProxyCandidateResult] = []
        for provider_cls in self.delegate_provider_classes():
            for candidate_key in self._candidate_keys_for_provider(provider_cls, source_key, field):
                provider = provider_cls(candidate_key)
                results.append(
                    AutoProxyCandidateResult(
                        provider_id=provider_cls.provider_id,
                        provider_name=provider_cls.display_name,
                        source_key=candidate_key,
                        display_caption=provider.get_display_caption(info),
                        text_content=_clean_text(provider.get_text_content(info)),
                    )
                )
        return tuple(results)

    def _read_text_value(self, photo_info: PhotoInfo, field: TemplateContextField | None) -> str:
        for candidate in self.inspect_candidates(photo_info):
            if candidate.text_content:
                return candidate.text_content
        return ""

    def get_display_caption(self, photo_info: PhotoInfo) -> str:
        if self.display_label:
            return f"{self.display_name}:{self.display_label}"
        field = self.resolve_field_definition(self.source_key)
        if field is not None and field.display_label:
            return f"{self.display_name}:{field.display_label}"
        for candidate in self.inspect_candidates(photo_info):
            caption = _clean_text(candidate.display_caption)
            if caption:
                return caption
        return super().get_display_caption(photo_info)


def iter_template_context_provider_classes() -> tuple[type[TemplateContextProvider], ...]:
    return (
        EditorTemplateContextProvider,
        ReportDBTemplateContextProvider,
        FromFileTemplateContextProvider,
        ExifTemplateContextProvider,
    )


def iter_template_context_selector_provider_classes() -> tuple[type[TemplateContextProvider], ...]:
    return (AutoProxyTemplateContextProvider,)


def get_template_context_field_options() -> list[tuple[str, str, str]]:
    """返回统一字段选项列表，模板编辑默认通过 AutoProxy 暴露字段。"""
    result: list[tuple[str, str, str]] = []
    for provider_cls in iter_template_context_selector_provider_classes():
        result.extend(provider_cls.field_options())
    return result


def build_template_context(
    photo: PhotoInfo | Path | str,
    raw_metadata: Dict[str, Any] | None = None,
) -> TemplateContext:
    """构建模板渲染与 UI 预览所需的上下文字典。"""
    photo_info = ensure_photo_info(photo, raw_metadata=raw_metadata)
    context: TemplateContext = dict(_BASE_TEMPLATE_CONTEXT)
    context["stem"] = photo_info.path.stem
    context["filename"] = photo_info.path.name
    for provider_cls in iter_template_context_provider_classes():
        context.update(provider_cls.build_context_entries(photo_info))
    return context


def build_template_context_provider(
    source_type: str,
    source_key: str,
    *,
    display_label: str = "",
) -> TemplateContextProvider:
    normalized = normalize_template_source_type(source_type)
    if normalized == TEMPLATE_SOURCE_AUTO:
        return AutoProxyTemplateContextProvider(source_key, display_label=display_label)
    provider_cls = _PROVIDER_CLASS_REGISTRY.get(normalized, AutoProxyTemplateContextProvider)
    return provider_cls(source_key, display_label=display_label)
