from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, Optional

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
TEMPLATE_SOURCE_AUTO = "auto"
TEMPLATE_SOURCE_METADATA_LEGACY = "metadata"

TemplateContext = Dict[str, str]

_REPORT_DB_ROW_RESOLVER: Optional[Callable[[Path], Optional[Dict[str, Any]]]] = None

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


def _extract_capture_datetime(photo_info: PhotoInfo, raw_metadata: Dict[str, Any]) -> datetime | None:
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
        return "FromFile"
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
        #return f"{self.display_name} - {label}"
        return f"{label}"


class ExifTemplateContextProvider(TemplateContextProvider):
    provider_id = TEMPLATE_SOURCE_EXIF
    display_name = "Exif"

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
    display_name = "ReportDB"

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
    display_name = "FromFile"

    _FIELD_DEFINITIONS: tuple[TemplateContextField, ...] = (
        TemplateContextField("{bird}", "鸟种名称", aliases=("bird",)),
        TemplateContextField("{bird_latin}", "鸟种拉丁文名称", aliases=("bird_latin",)),
        TemplateContextField("{bird_scientific}", "鸟种学名", aliases=("bird_scientific",)),
        TemplateContextField("{bird_common}", "鸟种通用名", aliases=("bird_common",)),
        TemplateContextField("{bird_family}", "鸟种科名", aliases=("bird_family",)),
        TemplateContextField("{bird_order}", "鸟种目名", aliases=("bird_order",)),
        TemplateContextField("{bird_class}", "鸟种纲名", aliases=("bird_class",)),
        TemplateContextField("{bird_phylum}", "鸟种门名", aliases=("bird_phylum",)),
        TemplateContextField("{bird_kingdom}", "鸟种界名", aliases=("bird_kingdom",)),
        TemplateContextField("{capture_date}", "拍摄日期", aliases=("capture_date", "date")),
        TemplateContextField(
            "{capture_text}",
            "拍摄日期时间",
            aliases=("capture_text", "capture_time", "capture_datetime", "date_time_original", "datetime_original"),
        ),
        TemplateContextField("{author}", "作者", aliases=("author",)),
        TemplateContextField("{location}", "拍摄地点", aliases=("location",)),
        TemplateContextField("{gps_text}", "GPS 坐标文字", aliases=("gps_text",)),
        TemplateContextField("{camera}", "相机型号", aliases=("camera",)),
        TemplateContextField("{lens}", "镜头型号", aliases=("lens",)),
        TemplateContextField("{settings_text}", "拍摄参数", aliases=("settings_text",)),
        TemplateContextField("{stem}", "文件名（不含扩展名）", aliases=("stem",)),
        TemplateContextField("{filename}", "完整文件名", aliases=("filename",)),
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
        capture_text = _extract_capture_text(photo_info, metadata)
        if capture_text:
            context["capture_text"] = capture_text

        capture_date = _extract_capture_date_text(photo_info, metadata)
        if capture_date:
            context["capture_date"] = capture_date

        author = _extract_author_text(metadata)
        if author:
            context["author"] = author
        return context

    def _read_text_value(self, photo_info: PhotoInfo, field: TemplateContextField | None) -> str:
        context = build_template_context(photo_info)
        if field is not None:
            for candidate in (field.key, *field.aliases):
                normalized = _normalize_from_file_context_key(candidate)
                direct_value = _clean_text(context.get(normalized, ""))
                if direct_value:
                    return direct_value
        normalized_key = _normalize_from_file_context_key(self.source_key)
        if normalized_key:
            direct_value = _clean_text(context.get(normalized_key, ""))
            if direct_value:
                return direct_value
        template_text = str(self.source_key or "").strip()
        if "{" in template_text and "}" in template_text:
            return _clean_text(format_text_with_context(template_text, context))
        return ""


class AutoProxyTemplateContextProvider(TemplateContextProvider):
    provider_id = TEMPLATE_SOURCE_AUTO
    display_name = "Auto"
    _route_definitions_cache: dict[str, tuple[AutoProxyFieldRoute, ...]] | None = None

    @classmethod
    def delegate_provider_classes(cls) -> tuple[type[TemplateContextProvider], ...]:
        return (
            FromFileTemplateContextProvider,
            ExifTemplateContextProvider,
            ReportDBTemplateContextProvider,
        )

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
            return self.display_label
        field = self.resolve_field_definition(self.source_key)
        if field is not None and field.display_label:
            return field.display_label
        for candidate in self.inspect_candidates(photo_info):
            caption = _clean_text(candidate.display_caption)
            if caption:
                return caption
        return super().get_display_caption(photo_info)


def iter_template_context_provider_classes() -> tuple[type[TemplateContextProvider], ...]:
    return (
        ExifTemplateContextProvider,
        FromFileTemplateContextProvider,
        ReportDBTemplateContextProvider,
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
    if normalized in {
        TEMPLATE_SOURCE_AUTO,
        TEMPLATE_SOURCE_EXIF,
        TEMPLATE_SOURCE_REPORT_DB,
        TEMPLATE_SOURCE_FROM_FILE,
    }:
        return AutoProxyTemplateContextProvider(source_key, display_label=display_label)
    provider_cls = _PROVIDER_CLASS_REGISTRY.get(normalized, AutoProxyTemplateContextProvider)
    return provider_cls(source_key, display_label=display_label)
