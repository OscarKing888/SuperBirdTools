# -*- coding: utf-8 -*-
"""
EXIF 读取、解析、标签显示、优先级与报告元数据。供 SuperViewer 主界面与 focus_preview_loader 使用。
可依赖 paths_settings、app_common，不依赖 SuperViewer 类模块。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import piexif
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS as PIL_TAGS

from app_common.exif_io import (
    _get_exiftool_tag_target,
    get_exiftool_executable_path,
    read_xmp_sidecar,
    run_exiftool_json,
)
from app_common.log import get_logger
from app_common.preview_canvas import (
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
)
from app_common.report_db import PHOTO_COLUMNS

from .paths_settings import _load_settings, _sanitize_display_string, _save_settings

try:
    import exifread
except ImportError:
    exifread = None

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None

# 支持的图片扩展名（含各家相机 RAW 与 HEIC/HEIF）
IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif",
    ".heic", ".heif", ".hif",
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".rw2", ".raw", ".orf", ".ori", ".raf", ".dng", ".pef", ".ptx",
    ".x3f", ".rwl", ".3fr", ".dcr", ".kdc", ".mef", ".mrw", ".rwz",
)
IMAGE_EXTENSIONS = tuple(dict.fromkeys(e.lower() for e in IMAGE_EXTENSIONS))
RAW_EXTENSIONS = frozenset(
    e for e in IMAGE_EXTENSIONS
    if e not in (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".heic", ".heif", ".hif")
)
HEIF_EXTENSIONS = frozenset({".heic", ".heif", ".hif"})
PIEXIF_WRITABLE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".jpe", ".webp", ".tif", ".tiff"})

EXIFTOOL_IFD_GROUP_MAP = {
    "0th": "IFD0",
    "Exif": "EXIF",
    "GPS": "GPS",
    "1st": "IFD1",
    "Interop": "InteropIFD",
}

META_IFD_NAME = "Meta"
META_TITLE_TAG_ID = "Title"
META_TITLE_PRIORITY_KEY = f"{META_IFD_NAME}:{META_TITLE_TAG_ID}"
META_DESCRIPTION_TAG_ID = "Description"
META_DESCRIPTION_PRIORITY_KEY = f"{META_IFD_NAME}:{META_DESCRIPTION_TAG_ID}"
CALC_IFD_NAME = "Calc"
HYPERFOCAL_TAG_ID = "HyperfocalDistance"
HYPERFOCAL_PRIORITY_KEY = f"{CALC_IFD_NAME}:{HYPERFOCAL_TAG_ID}"

EXIFTOOL_KEYS_DUPLICATE_OF_TITLE = frozenset({"XMP-dc:Title", "IFD0:XPTitle", "IFD0:DocumentName"})
EXIFTOOL_KEYS_DUPLICATE_OF_DESCRIPTION = frozenset({
    "XMP-dc:Description",
    "IFD0:XPComment",
    "IFD0:ImageDescription",
    "EXIF:UserComment",
    "ExifIFD:UserComment",
})

IFD_DISPLAY_NAMES = {
    META_IFD_NAME: "文件信息",
    "0th": "图像 (0th IFD)",
    "Exif": "Exif IFD",
    "GPS": "GPS",
    "1st": "缩略图 (1st IFD)",
    "Interop": "Interop IFD",
    CALC_IFD_NAME: "计算信息",
    "thumbnail": "缩略图数据",
}

_EXIF_ENCODINGS = ("utf-8", "utf-16", "utf-16-be", "latin-1", "cp1252", "gbk", "gb2312", "big5")
ORIENTATION_TAG = 274

_log = get_logger("exif_helpers")


def _build_exiftool_key_to_piexif_key() -> dict:
    """exiftool Group:Tag -> piexif ifd:tag_id，用于排序与隐藏过滤。"""
    out = {}
    for ifd_name in ("0th", "Exif", "GPS", "1st", "Interop"):
        for tag_id in piexif.TAGS.get(ifd_name, {}):
            t = _get_exiftool_tag_target(ifd_name, tag_id)
            if t:
                out[t] = f"{ifd_name}:{tag_id}"
    return out


EXIFTOOL_KEY_TO_PIEXIF_KEY = _build_exiftool_key_to_piexif_key()

EXIFTOOL_GROUP_ALIASES = {
    "IFD0": "IFD0",
    "IFD1": "IFD1",
    "GPS": "GPS",
    "EXIF": "EXIF",
    "ExifIFD": "EXIF",
    "SubIFD": "EXIF",
    "Interop": "InteropIFD",
    "InteropIFD": "InteropIFD",
}

EXIFTOOL_ALIAS_KEY_TO_PIEXIF_KEY = {
    "IFD0:ModifyDate": "0th:306",
    "EXIF:ISO": "Exif:34855",
    "ExifIFD:ISO": "Exif:34855",
    "EXIF:ExifImageWidth": "Exif:40962",
    "EXIF:ExifImageHeight": "Exif:40963",
    "ExifIFD:ExifImageWidth": "Exif:40962",
    "ExifIFD:ExifImageHeight": "Exif:40963",
    "EXIF:ExposureCompensation": "Exif:37378",
    "ExifIFD:ExposureCompensation": "Exif:37378",
}

DEFAULT_EXIF_TAG_PRIORITY = [
    META_TITLE_PRIORITY_KEY,
    META_DESCRIPTION_PRIORITY_KEY,
    HYPERFOCAL_PRIORITY_KEY,
    "0th:271", "0th:272", "0th:306",
    "Exif:33434", "Exif:33437", "Exif:37386", "Exif:37382", "Exif:41996", "Exif:34855",
    "Exif:36867", "Exif:42036", "Exif:41987", "Exif:37378", "Exif:40962", "Exif:40963",
]


def _safe_decode_bytes(data: bytes) -> str:
    """将 EXIF 字节按多种编码尝试解码，避免乱码。"""
    if not data:
        return ""
    data = data.rstrip(b"\x00")
    if not data:
        return ""
    for enc in _EXIF_ENCODINGS:
        try:
            s = data.decode(enc)
            if "\ufffd" in s and enc != "latin-1":
                continue
            return _sanitize_display_string(s)
        except (UnicodeDecodeError, LookupError):
            continue
    return _sanitize_display_string(data.decode("latin-1"))


def _decoded_looks_text(s: str) -> bool:
    """解码后的字符串是否像可读文本。"""
    if not s or len(s) < 2:
        return True
    n = len(s)
    if s.count("\ufffd") > n * 0.15:
        return False
    ok = 0
    for c in s:
        code = ord(c)
        if code == 0 or (code < 32 and c not in "\t\n\r"):
            continue
        ok += 1
    return ok >= n * 0.5


def _tuple_as_bytes(value: tuple) -> bytes | None:
    """若 tuple 全为 0–255 的整数则视为字节序列，返回 bytes；否则返回 None。"""
    if not value:
        return None
    try:
        if all(isinstance(x, int) and 0 <= x <= 255 for x in value):
            return bytes(value)
    except (TypeError, ValueError):
        pass
    return None


def _format_hex_bytes(data: bytes) -> str:
    """将二进制数据格式化为十六进制字符串（过长时截断）。"""
    if len(data) <= 64:
        return data.hex()
    return data[:64].hex() + "..."


def get_tag_type(ifd_name: str, tag_id: int) -> int | None:
    """读取 piexif 标签定义类型，失败返回 None。"""
    info = piexif.TAGS.get(ifd_name, {}).get(tag_id)
    if isinstance(info, dict):
        t = info.get("type")
        if isinstance(t, int):
            return t
    return None


def format_exif_value(value, expected_type: int | None = None):
    """将 piexif 原始值格式化为可读字符串。"""
    text_type = getattr(piexif.TYPES, "Ascii", 2)
    rational_types = {getattr(piexif.TYPES, "Rational", 5), getattr(piexif.TYPES, "SRational", 10)}

    if value is None:
        return ""
    if isinstance(value, bytes):
        if expected_type == text_type:
            s = _safe_decode_bytes(value)
            return s[:2048] + "\n... (已截断)" if len(s) > 2048 else s
        return _format_hex_bytes(value)
    if isinstance(value, tuple):
        if (
            expected_type in rational_types
            and len(value) == 2
            and isinstance(value[0], int)
            and isinstance(value[1], int)
        ):
            if value[1] != 0:
                return f"{value[0]}/{value[1]} ({value[0] / value[1]:.4f})"
            return f"{value[0]}/{value[1]}"
        data = _tuple_as_bytes(value)
        if data is not None:
            if expected_type == text_type:
                s = _safe_decode_bytes(data)
                return s[:2048] + "\n... (已截断)" if len(s) > 2048 else s
            return _format_hex_bytes(data)
        return ", ".join(str(format_exif_value(v)) for v in value)
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _to_float_exif_number(v) -> float | None:
    """将 EXIF 数值（含有理数）转为浮点，失败返回 None。"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], int):
        if v[1] == 0:
            return None
        return float(v[0]) / float(v[1])
    return None


def _to_float_text_number(v) -> float | None:
    """Parse exiftool values like '1/800', '400 mm', 5.6 into float."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list):
        for x in v:
            f = _to_float_text_number(x)
            if f is not None:
                return f
        return None
    s = _sanitize_display_string(str(v or "")).strip()
    if not s:
        return None
    if "/" in s:
        a, _, b = s.partition("/")
        try:
            num = float(a.strip())
            den = float(b.strip().split()[0]) if b.strip() else 0.0
            if den != 0:
                return num / den
        except (TypeError, ValueError):
            pass
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _calc_hyperfocal_distance_from_exiftool_obj(obj: dict, default_coc_mm: float = 0.03) -> float | None:
    """Calculate hyperfocal distance from exiftool json object."""
    if not isinstance(obj, dict):
        return None

    def _pick(*keys):
        for k in keys:
            if k in obj:
                f = _to_float_text_number(obj.get(k))
                if f is not None:
                    return f
        return None

    f_mm = _pick("ExifIFD:FocalLength", "EXIF:FocalLength", "Composite:FocalLength")
    n = _pick("ExifIFD:FNumber", "EXIF:FNumber", "Composite:Aperture")
    if f_mm is None or n is None or f_mm <= 0 or n <= 0:
        return None

    coc_mm = default_coc_mm if default_coc_mm > 0 else 0.03
    focal_35 = _pick("ExifIFD:FocalLengthIn35mmFormat", "EXIF:FocalLengthIn35mmFormat")
    if focal_35 is not None and focal_35 > 0:
        crop = focal_35 / f_mm
        if crop > 0:
            coc_mm = 0.03 / crop
    if coc_mm <= 0:
        coc_mm = 0.03

    h_mm = (f_mm * f_mm) / (n * coc_mm) + f_mm
    if h_mm <= 0:
        return None
    return h_mm / 1000.0


def _calc_hyperfocal_distance_m(exif_data: dict, default_coc_mm: float = 0.03) -> float | None:
    """计算超焦距（米）。"""
    exif_ifd = exif_data.get("Exif") if isinstance(exif_data, dict) else None
    if not isinstance(exif_ifd, dict):
        return None
    f_mm = _to_float_exif_number(exif_ifd.get(37386))
    n = _to_float_exif_number(exif_ifd.get(33437))
    if f_mm is None or n is None or f_mm <= 0 or n <= 0:
        return None

    coc_mm = default_coc_mm if default_coc_mm > 0 else 0.03
    focal_35 = _to_float_exif_number(exif_ifd.get(41989))
    if focal_35 is not None and focal_35 > 0:
        crop = focal_35 / f_mm
        if crop > 0:
            coc_mm = 0.03 / crop
    if coc_mm <= 0:
        coc_mm = 0.03

    h_mm = (f_mm * f_mm) / (n * coc_mm) + f_mm
    if h_mm <= 0:
        return None
    return h_mm / 1000.0


def _format_hyperfocal_distance(value_m: float | None) -> str:
    """格式化超焦距显示文本。"""
    if value_m is None:
        return "无法计算"
    return f"{value_m:.2f} m"


def _extract_exiftool_text_value(value) -> str:
    """Normalize exiftool json value to display text."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for k in ("x-default", "zh-CN", "zh-cn", "en-US", "en-us"):
            if k in value:
                s = _extract_exiftool_text_value(value.get(k))
                if s:
                    return s
        for v in value.values():
            s = _extract_exiftool_text_value(v)
            if s:
                return s
        return ""
    if isinstance(value, list):
        parts = [_extract_exiftool_text_value(v) for v in value]
        parts = [p for p in parts if p]
        return " ".join(parts)
    return _sanitize_display_string(str(value))


def _is_likely_mojibake_meta_text(s: str) -> bool:
    """Detect common mojibake pattern."""
    txt = _sanitize_display_string(str(s or "")).strip()
    if not txt:
        return False
    if any(0x4E00 <= ord(ch) <= 0x9FFF for ch in txt):
        return False
    has_placeholder = ("?" in txt) or ("\ufffd" in txt)
    has_suspicious_script = any(
        (0x0370 <= ord(ch) <= 0x052F) or (0x0180 <= ord(ch) <= 0x024F)
        for ch in txt
    )
    return has_placeholder and has_suspicious_script


def _pick_preferred_meta_text(*candidates) -> str:
    """Pick first non-empty candidate; if earlier looks mojibake, fallback to next."""
    cleaned = []
    for c in candidates:
        s = _extract_exiftool_text_value(c)
        if s:
            cleaned.append(s)
    if not cleaned:
        return ""
    for s in cleaned:
        if not _is_likely_mojibake_meta_text(s):
            return s
    return cleaned[0]


def _normalize_meta_edit_text(text: str | None) -> str:
    """统一元数据编辑值，避免"（未设置）"被当成真实内容。"""
    s = _sanitize_display_string(str(text or ""))
    if s in ("（未设置）", "(未设置)", "<未设置>"):
        return ""
    return s


def _load_macos_mdls_text(path: str, attr_name: str) -> str | None:
    """读取 macOS Spotlight 元数据字段。"""
    if sys.platform != "darwin":
        return None
    try:
        cp = subprocess.run(
            ["mdls", "-name", attr_name, "-raw", path],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if cp.returncode != 0:
        return None
    s = _sanitize_display_string(cp.stdout.strip())
    if not s or s in ("(null)", "null"):
        return None
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = _sanitize_display_string(s[1:-1])
    return s or None


def _decode_xp_text_value(value) -> str | None:
    """解码 XP* 文本字段（常见为 UTF-16LE）。"""
    data = None
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, tuple):
        data = _tuple_as_bytes(value)
    if not data:
        return None
    while len(data) >= 2 and data[-2:] == b"\x00\x00":
        data = data[:-2]
    if len(data) % 2 == 1:
        data = data[:-1]
    if not data:
        return None
    try:
        s = data.decode("utf-16-le", errors="ignore")
    except Exception:
        s = _safe_decode_bytes(data)
    s = _sanitize_display_string(s)
    return s or None


def _decode_xp_title_value(value) -> str | None:
    return _decode_xp_text_value(value)


def _decode_xp_comment_value(value) -> str | None:
    return _decode_xp_text_value(value)


def _extract_ifd_text_value(ifd_data: dict, tag_id: int) -> str | None:
    """从指定 IFD 标签中提取文本。"""
    if not isinstance(ifd_data, dict):
        return None
    v = ifd_data.get(tag_id)
    if isinstance(v, bytes):
        s = _safe_decode_bytes(v)
    elif isinstance(v, tuple):
        b = _tuple_as_bytes(v)
        s = _safe_decode_bytes(b) if b is not None else None
    else:
        s = _sanitize_display_string(str(v)) if v is not None else None
    return s or None


def _decode_user_comment_value(value) -> str | None:
    """解码 UserComment（Exif:37510）。"""
    data = None
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, tuple):
        data = _tuple_as_bytes(value)
    if not data:
        return None
    if len(data) >= 8:
        prefix = data[:8]
        payload = data[8:]
        if prefix.startswith(b"ASCII"):
            s = _safe_decode_bytes(payload)
            return s or None
        if prefix.startswith(b"UNICODE"):
            for enc in ("utf-16-be", "utf-16-le", "utf-8"):
                try:
                    s = _sanitize_display_string(payload.decode(enc, errors="ignore"))
                    if s:
                        return s
                except Exception:
                    continue
        if prefix.startswith(b"JIS"):
            for enc in ("shift_jis", "cp932", "utf-8"):
                try:
                    s = _sanitize_display_string(payload.decode(enc, errors="ignore"))
                    if s:
                        return s
                except Exception:
                    continue
    s = _safe_decode_bytes(data)
    return s or None


def _extract_title_from_exif_data(exif_data: dict | None) -> str | None:
    """从 EXIF 数据中提取标题候选。"""
    if not isinstance(exif_data, dict):
        return None
    ifd0 = exif_data.get("0th")
    if not isinstance(ifd0, dict):
        return None
    xp_title = _decode_xp_title_value(ifd0.get(40091))
    if xp_title:
        return xp_title
    s = _extract_ifd_text_value(ifd0, 269)
    if s and len(s) <= 120 and "\n" not in s:
        return s
    return None


def _extract_description_from_exif_data(exif_data: dict | None) -> str | None:
    """从 EXIF 数据中提取描述候选。"""
    if not isinstance(exif_data, dict):
        return None
    ifd0 = exif_data.get("0th")
    if isinstance(ifd0, dict):
        xp_comment = _decode_xp_comment_value(ifd0.get(40092))
        if xp_comment:
            return xp_comment
        s = _extract_ifd_text_value(ifd0, 270)
        if s:
            return s
    exif_ifd = exif_data.get("Exif")
    if isinstance(exif_ifd, dict):
        s = _decode_user_comment_value(exif_ifd.get(37510))
        if s:
            return s
    return None


def load_display_title(path: str, exif_data: dict | None = None) -> str:
    """读取用于展示的标题。"""
    title = _extract_title_from_exif_data(exif_data)
    if title:
        return title
    title = _load_macos_mdls_text(path, "kMDItemTitle")
    if title:
        return title
    return "（未设置）"


def load_display_description(path: str, exif_data: dict | None = None) -> str:
    """读取用于展示的描述。"""
    desc = _extract_description_from_exif_data(exif_data)
    if desc:
        return desc
    desc = _load_macos_mdls_text(path, "kMDItemDescription")
    if desc:
        return desc
    return "（未设置）"


def _split_tag_name_tokens(name: str) -> list[str]:
    """将 EXIF 原始标签名切分为可读 token。"""
    if not name:
        return []
    s = str(name).strip()
    protected_tokens = ("YCbCr", "GPS", "Exif", "EXIF", "JPEG", "TIFF", "CFA", "XP", "XMP", "ISO", "DNG", "OECF")
    placeholders = {}
    for idx, token in enumerate(protected_tokens):
        ph = f"zzph{chr(97 + idx)}zz"
        if token in s:
            s = s.replace(token, f" {ph} ")
            placeholders[ph] = token
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", s)
    s = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return []
    tokens = [x for x in s.split(" ") if x]
    return [placeholders.get(x, x) for x in tokens]


def _format_english_tag_name(name: str) -> str:
    """将原始标签名格式化为更可读的英文。"""
    tokens = _split_tag_name_tokens(name)
    if not tokens:
        return _sanitize_display_string(str(name or ""))
    return " ".join(tokens)


def load_tag_name_token_map_zh_from_settings(data: dict | None = None) -> dict:
    """从 super_viewer.cfg 读取标签分词中文映射。"""
    default_map = {}
    if data is None:
        data = _load_settings()
    val = data.get("exif_tag_name_token_map_zh")
    if not isinstance(val, dict):
        return default_map
    merged = dict(default_map)
    for k, v in val.items():
        if isinstance(k, str) and isinstance(v, str):
            kk = _sanitize_display_string(k)
            vv = _sanitize_display_string(v)
            if kk and vv:
                merged[kk] = vv
    return merged


def _translate_tag_name_to_chinese(name: str, token_map: dict | None = None) -> str:
    """将英文 EXIF 标签名尽量转换为中文可读名称。"""
    if not name:
        return ""
    if token_map is None:
        token_map = load_tag_name_token_map_zh_from_settings()
    fast = token_map.get(name)
    if fast:
        return fast
    parts = []
    for tok in _split_tag_name_tokens(name):
        zh = token_map.get(tok) or token_map.get(tok.lower())
        parts.append(zh if zh else tok)
    if not parts:
        return _sanitize_display_string(str(name))
    text = " ".join(parts)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return _sanitize_display_string(text)


def _build_default_exif_tag_names_zh(token_map: dict | None = None) -> dict:
    """基于 piexif 全量标签生成默认中文名映射。"""
    if token_map is None:
        token_map = load_tag_name_token_map_zh_from_settings()
    result = {}
    for ifd_name in ("0th", "Exif", "GPS", "1st", "Interop"):
        ifd_data = piexif.TAGS.get(ifd_name, {})
        for tag_id, info in ifd_data.items():
            key = f"{ifd_name}:{tag_id}"
            raw_name = str(info.get("name", f"Tag {tag_id}")) if isinstance(info, dict) else str(info)
            result[key] = _translate_tag_name_to_chinese(raw_name, token_map=token_map)
    result[META_TITLE_PRIORITY_KEY] = "标题"
    result[META_DESCRIPTION_PRIORITY_KEY] = "描述"
    result[HYPERFOCAL_PRIORITY_KEY] = "超焦距"
    return result


def load_exif_tag_names_zh_from_settings() -> dict:
    """从 super_viewer.cfg 读取 EXIF 标签中文名映射并补全缺失项。"""
    data = _load_settings()
    token_map = load_tag_name_token_map_zh_from_settings(data)
    merged = _build_default_exif_tag_names_zh(token_map=token_map)
    val = data.get("exif_tag_names_zh")
    if not isinstance(val, dict):
        return merged
    for k, v in val.items():
        if isinstance(k, str) and isinstance(v, str):
            vv = _sanitize_display_string(v)
            if vv:
                merged[k] = vv
    return merged


def get_tag_name(ifd_name: str, tag_id: int, use_chinese: bool = False, names_zh: dict | None = None) -> str:
    """获取 tag 的可读名称。"""
    if ifd_name == META_IFD_NAME and str(tag_id) == META_TITLE_TAG_ID:
        if use_chinese:
            if names_zh is None:
                names_zh = load_exif_tag_names_zh_from_settings()
            zh_name = names_zh.get(META_TITLE_PRIORITY_KEY) if isinstance(names_zh, dict) else None
            return _sanitize_display_string(zh_name) if isinstance(zh_name, str) and zh_name.strip() else "标题"
        return "Title"
    if ifd_name == META_IFD_NAME and str(tag_id) == META_DESCRIPTION_TAG_ID:
        if use_chinese:
            if names_zh is None:
                names_zh = load_exif_tag_names_zh_from_settings()
            zh_name = names_zh.get(META_DESCRIPTION_PRIORITY_KEY) if isinstance(names_zh, dict) else None
            return _sanitize_display_string(zh_name) if isinstance(zh_name, str) and zh_name.strip() else "描述"
        return "Description"
    if ifd_name == "thumbnail":
        return "（二进制数据）"
    if ifd_name == CALC_IFD_NAME and str(tag_id) == HYPERFOCAL_TAG_ID:
        if use_chinese:
            if names_zh is None:
                names_zh = load_exif_tag_names_zh_from_settings()
            zh_name = names_zh.get(HYPERFOCAL_PRIORITY_KEY) if isinstance(names_zh, dict) else None
            return _sanitize_display_string(zh_name) if isinstance(zh_name, str) and zh_name.strip() else "超焦距"
        return "Hyperfocal Distance"
    key = f"{ifd_name}:{tag_id}"
    t = piexif.TAGS.get(ifd_name, {})
    info = t.get(tag_id)
    raw_name = str(info.get("name", f"Tag {tag_id}")) if isinstance(info, dict) else (f"Tag {tag_id}" if info is None else str(info))
    raw_name = _sanitize_display_string(raw_name)
    if use_chinese:
        if names_zh is None:
            names_zh = load_exif_tag_names_zh_from_settings()
        zh_name = names_zh.get(key) if isinstance(names_zh, dict) else None
        if isinstance(zh_name, str) and zh_name.strip():
            return _sanitize_display_string(zh_name)
        auto_zh = _translate_tag_name_to_chinese(raw_name)
        return auto_zh if auto_zh else f"标签 {tag_id}"
    if info is None:
        return f"Tag {tag_id}"
    return _format_english_tag_name(raw_name) or f"Tag {tag_id}"


def load_exif_piexif(path: str) -> dict | None:
    """使用 piexif 加载 EXIF（JPEG/WebP/TIFF）。"""
    try:
        return piexif.load(path)
    except Exception:
        return None


def load_exif_heic(path: str) -> dict | None:
    """使用 pillow-heif 加载 HEIC/HEIF/HIF 的 EXIF。"""
    if pillow_heif is None:
        return None
    ext = Path(path).suffix.lower()
    if ext not in HEIF_EXTENSIONS:
        return None
    try:
        heif_file = pillow_heif.open_heif(path)
        if not heif_file or len(heif_file) == 0:
            return None
        img = heif_file[0]
        exif_bytes = getattr(img, "info", None)
        if isinstance(exif_bytes, dict):
            exif_bytes = exif_bytes.get("exif")
        if not exif_bytes:
            exif_bytes = getattr(img, "exif", None)
        if not exif_bytes or not isinstance(exif_bytes, bytes):
            return None
        return piexif.load(exif_bytes)
    except Exception:
        return None


def load_exif_exifread(path: str) -> list[tuple[str, str, str]]:
    """使用 ExifRead 加载 EXIF（用于 RAW 等）。返回 [(group, name, value), ...]。"""
    if exifread is None:
        return []
    rows = []
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=True, extract_thumbnail=False)
        for key, tag in tags.items():
            if key in ("JPEGThumbnail", "TIFFThumbnail", "Filename"):
                continue
            if " " in key:
                group, name = key.split(None, 1)
            else:
                group, name = "ExifRead", key
            try:
                value_str = str(tag.printable) if hasattr(tag, "printable") else str(tag)
            except Exception:
                value_str = str(tag)
            value_str = _sanitize_display_string(value_str)
            rows.append((group, name, value_str))
    except Exception:
        pass
    return rows


def load_exif_pillow(path: str) -> list[tuple[str, str, str]]:
    """使用 Pillow 加载 EXIF。返回 [(ifd, name, value), ...]。"""
    rows = []
    try:
        img = Image.open(path)
        exif = img.getexif()
        if not exif:
            return rows
        for tag_id, value in exif.items():
            name = PIL_TAGS.get(tag_id, f"Tag {tag_id}")
            if isinstance(value, bytes):
                if tag_id == 37510 and len(value) > 8:
                    value = value[8:]
                    if not value.strip(b"\x00"):
                        rows.append(("Pillow Exif", str(name), "（无内容）"))
                        continue
                s = _safe_decode_bytes(value)
                value = s[:2048] + ("\n... (已截断)" if len(s) > 2048 else "") if _decoded_looks_text(s) else (value.hex() if len(value) <= 64 else value.hex() + "...")
            else:
                value = _sanitize_display_string(str(value))
            rows.append(("Pillow Exif", str(name), value))
        img.close()
    except Exception:
        pass
    return rows


def _parse_value_back(s: str, raw_value) -> tuple | bytes | int:
    """将用户输入的字符串按原始类型转回 EXIF 可写格式。"""
    if raw_value is None:
        return s.encode("utf-8")
    if isinstance(raw_value, bytes):
        return s.encode("utf-8")
    if isinstance(raw_value, int):
        try:
            return int(s.strip())
        except ValueError:
            return raw_value
    if isinstance(raw_value, tuple):
        if len(raw_value) == 2 and isinstance(raw_value[0], int) and isinstance(raw_value[1], int):
            s = s.strip()
            if "/" in s:
                a, _, b = s.partition("/")
                try:
                    return (int(a.strip()), int(b.strip()) if b.strip() else 1)
                except ValueError:
                    pass
            try:
                from fractions import Fraction
                f = float(s)
                fr = Fraction(f).limit_denominator(10000)
                return (fr.numerator, fr.denominator)
            except ValueError:
                pass
            return raw_value
        if len(raw_value) > 2 and all(isinstance(x, int) and 0 <= x <= 255 for x in raw_value):
            return s.encode("utf-8")
        if all(isinstance(x, int) for x in raw_value):
            try:
                return tuple(int(x) for x in s.replace(",", " ").split())
            except ValueError:
                return raw_value
    return s.encode("utf-8")


def _format_exception_message(e: Exception) -> str:
    """将异常格式化为可读文本。"""
    msg = str(e).strip()
    if msg:
        return msg
    rep = repr(e).strip()
    if rep and rep != "Exception()":
        return rep
    return f"{type(e).__name__}（无详细错误信息）"


def map_exiftool_key_to_piexif_key(exiftool_key: str | None) -> str | None:
    """Normalize exiftool Group:Tag key to piexif style key (ifd:tag_id)."""
    if not isinstance(exiftool_key, str):
        return None
    key = exiftool_key.strip()
    if not key or ":" not in key:
        return None
    mapped = EXIFTOOL_KEY_TO_PIEXIF_KEY.get(key)
    if mapped:
        return mapped
    mapped = EXIFTOOL_ALIAS_KEY_TO_PIEXIF_KEY.get(key)
    if mapped:
        return mapped
    group, tag_name = key.split(":", 1)
    group_norm = EXIFTOOL_GROUP_ALIASES.get(group)
    if not group_norm:
        return None
    mapped = EXIFTOOL_KEY_TO_PIEXIF_KEY.get(f"{group_norm}:{tag_name}")
    if mapped:
        return mapped
    mapped = EXIFTOOL_ALIAS_KEY_TO_PIEXIF_KEY.get(f"{group_norm}:{tag_name}")
    if mapped:
        return mapped
    return None


def get_tag_name_for_exiftool_key(
    exiftool_key: str, tag_name: str, use_chinese: bool, names_zh: dict | None = None
) -> str:
    """根据 exiftool 的 Group:Tag 键和原始标签名，返回显示用标签名。"""
    if not use_chinese:
        return _format_english_tag_name(tag_name) or _sanitize_display_string(tag_name)
    if names_zh is None:
        names_zh = load_exif_tag_names_zh_from_settings()
    zh = names_zh.get(exiftool_key) if isinstance(names_zh, dict) else None
    if isinstance(zh, str) and zh.strip():
        return _sanitize_display_string(zh)
    piexif_key = map_exiftool_key_to_piexif_key(exiftool_key)
    if piexif_key:
        parts = piexif_key.split(":", 1)
        if len(parts) == 2:
            try:
                ifd_name, tag_id = parts[0], int(parts[1])
                return get_tag_name(ifd_name, tag_id, use_chinese=True, names_zh=names_zh)
            except ValueError:
                pass
    auto_zh = _translate_tag_name_to_chinese(tag_name)
    return auto_zh if auto_zh else _sanitize_display_string(tag_name)


def get_all_exif_tag_keys(use_chinese: bool = False) -> list[tuple]:
    """从 piexif.TAGS 收集所有可配置的 (key, 显示文本)。"""
    result = []
    names_zh = load_exif_tag_names_zh_from_settings() if use_chinese else None
    title_name = get_tag_name(META_IFD_NAME, META_TITLE_TAG_ID, use_chinese=use_chinese, names_zh=names_zh)
    result.append((META_TITLE_PRIORITY_KEY, f"{IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME)} - {title_name}"))
    desc_name = get_tag_name(META_IFD_NAME, META_DESCRIPTION_TAG_ID, use_chinese=use_chinese, names_zh=names_zh)
    result.append((META_DESCRIPTION_PRIORITY_KEY, f"{IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME)} - {desc_name}"))
    calc_name = get_tag_name(CALC_IFD_NAME, HYPERFOCAL_TAG_ID, use_chinese=use_chinese, names_zh=names_zh)
    result.append((HYPERFOCAL_PRIORITY_KEY, f"{IFD_DISPLAY_NAMES.get(CALC_IFD_NAME, CALC_IFD_NAME)} - {calc_name}"))
    for ifd_name in ("0th", "Exif", "GPS", "1st", "Interop"):
        ifd_data = piexif.TAGS.get(ifd_name, {})
        if not ifd_data:
            continue
        group = IFD_DISPLAY_NAMES.get(ifd_name, ifd_name)
        for tag_id, info in ifd_data.items():
            name = get_tag_name(ifd_name, tag_id, use_chinese=use_chinese, names_zh=names_zh)
            key = f"{ifd_name}:{tag_id}"
            result.append((key, f"{group} - {name}"))
    return result


def load_tag_priority_from_settings() -> list:
    """从 super_viewer.cfg 读取优先显示的 tag key 列表。"""
    data = _load_settings()
    val = data.get("exif_tag_priority", [])
    lst = list(val) if isinstance(val, list) else []
    base = lst if lst else DEFAULT_EXIF_TAG_PRIORITY.copy()
    normalized = []
    seen = set()
    for key in (META_TITLE_PRIORITY_KEY, META_DESCRIPTION_PRIORITY_KEY, HYPERFOCAL_PRIORITY_KEY, *base):
        if not isinstance(key, str) or not key or key in seen:
            continue
        normalized.append(key)
        seen.add(key)
    return normalized


def save_tag_priority_to_settings(priority_keys: list) -> None:
    """将优先显示的 tag key 列表写入 EXIF.cfg。"""
    data = _load_settings()
    normalized = []
    seen = set()
    for key in (META_TITLE_PRIORITY_KEY, META_DESCRIPTION_PRIORITY_KEY, HYPERFOCAL_PRIORITY_KEY, *(list(priority_keys) if isinstance(priority_keys, list) else [])):
        if not isinstance(key, str) or not key or key in seen:
            continue
        normalized.append(key)
        seen.add(key)
    data["exif_tag_priority"] = normalized
    _save_settings(data)


def load_exif_tag_hidden_from_settings() -> set:
    """从 super_viewer.cfg 读取禁止显示的 tag key 集合。"""
    data = _load_settings()
    val = data.get("exif_tag_hidden", [])
    lst = val if isinstance(val, list) else []
    return {str(k).strip() for k in lst if isinstance(k, str) and k.strip()}


def save_exif_tag_hidden_to_settings(hidden_keys: list) -> None:
    """将禁止显示的 tag key 列表写入 EXIF.cfg。"""
    data = _load_settings()
    normalized = []
    seen = set()
    for k in (list(hidden_keys) if isinstance(hidden_keys, list) else []):
        s = str(k).strip() if k is not None else ""
        if not s or s in seen:
            continue
        normalized.append(s)
        seen.add(s)
    data["exif_tag_hidden"] = normalized
    _save_settings(data)


def load_tag_label_chinese_from_settings() -> bool:
    """是否使用中文显示 EXIF 标签名。"""
    data = _load_settings()
    return bool(data.get("exif_tag_label_chinese", False))


def save_tag_label_chinese_to_settings(use_chinese: bool) -> None:
    """保存 EXIF 标签名显示语言。"""
    data = _load_settings()
    data["exif_tag_label_chinese"] = use_chinese
    _save_settings(data)


def load_preview_grid_mode_from_settings() -> str:
    """读取预览区构图辅助线模式。"""
    data = _load_settings()
    return normalize_preview_composition_grid_mode(data.get("preview_grid_mode", "none"))


def save_preview_grid_mode_to_settings(mode: str | None) -> None:
    """保存预览区构图辅助线模式。"""
    normalized = normalize_preview_composition_grid_mode(mode)
    data = _load_settings()
    data["preview_grid_mode"] = normalized
    _save_settings(data)


def load_preview_grid_line_width_from_settings() -> int:
    """读取预览区构图辅助线线宽。"""
    data = _load_settings()
    return normalize_preview_composition_grid_line_width(data.get("preview_grid_line_width", 1))


def save_preview_grid_line_width_to_settings(width: int | str | None) -> None:
    """保存预览区构图辅助线线宽。"""
    normalized = normalize_preview_composition_grid_line_width(width)
    data = _load_settings()
    data["preview_grid_line_width"] = normalized
    _save_settings(data)


def load_hyperfocal_coc_mm_from_settings() -> float:
    """读取超焦距计算的默认弥散圆（mm），缺省 0.03。"""
    data = _load_settings()
    val = data.get("hyperfocal_coc_mm", 0.03)
    try:
        f = float(val)
        if f > 0:
            return f
    except (TypeError, ValueError):
        pass
    return 0.03


def apply_tag_priority(rows: list[tuple], priority_keys: list[str]) -> list[tuple]:
    """按配置的 tag 顺序重排。"""

    def row_key(row):
        if len(row) > 6 and row[6]:
            mapped = map_exiftool_key_to_piexif_key(row[6])
            return mapped if mapped else row[6]
        if len(row) < 2:
            return None
        ifd_name, tag_id = row[0], row[1]
        if ifd_name is None or tag_id is None:
            return None
        return f"{ifd_name}:{tag_id}"

    def row_signature(row):
        if len(row) < 5:
            return None
        name = _sanitize_display_string(str(row[3] or "")).strip().lower()
        value = _sanitize_display_string(str(row[4] or "")).strip()
        if not name and not value:
            return None
        return f"{name}\x1f{value}"

    normalized_priority = [k for k in priority_keys if isinstance(k, str) and k]
    if not normalized_priority:
        return list(rows)

    exif_info_list = list(rows)
    display_list = []
    displayed_keys = set()

    for key in normalized_priority:
        matched_row = None
        remaining_rows = []
        for row in exif_info_list:
            if row_key(row) == key:
                if matched_row is None:
                    matched_row = row
                continue
            remaining_rows.append(row)
        exif_info_list = remaining_rows
        if matched_row is not None:
            display_list.append(matched_row)
            displayed_keys.add(key)
            if key == META_TITLE_PRIORITY_KEY:
                exif_info_list = [r for r in exif_info_list if not (len(r) > 6 and r[6] in EXIFTOOL_KEYS_DUPLICATE_OF_TITLE)]
            elif key == META_DESCRIPTION_PRIORITY_KEY:
                exif_info_list = [r for r in exif_info_list if not (len(r) > 6 and r[6] in EXIFTOOL_KEYS_DUPLICATE_OF_DESCRIPTION)]

    exif_info_list = [r for r in exif_info_list if row_key(r) not in displayed_keys]

    seen_signatures = set()
    for row in display_list:
        sig = row_signature(row)
        if sig:
            seen_signatures.add(sig)
    for row in exif_info_list:
        sig = row_signature(row)
        if sig and sig in seen_signatures:
            continue
        if sig:
            seen_signatures.add(sig)
        display_list.append(row)
    return display_list


def load_all_exif_exiftool(path: str, tag_label_chinese: bool = False) -> list[tuple]:
    """用 exiftool -j -G1 加载 EXIF，返回 7 元组列表。"""
    lst = run_exiftool_json(path)
    if not lst or not isinstance(lst[0], dict):
        return []
    obj = lst[0]
    names_zh = load_exif_tag_names_zh_from_settings() if tag_label_chinese else None
    hidden_keys = load_exif_tag_hidden_from_settings()
    hidden_exiftool = set()
    for k in hidden_keys:
        parts = k.split(":", 1)
        if len(parts) == 2:
            try:
                ifd_name, tag_id = parts[0], int(parts[1])
                t = _get_exiftool_tag_target(ifd_name, tag_id)
                if t:
                    hidden_exiftool.add(t)
            except ValueError:
                pass

    def _fmt(v):
        return _extract_exiftool_text_value(v)

    title_value = _pick_preferred_meta_text(
        obj.get("XMP-dc:Title"),
        obj.get("IFD0:XPTitle"),
        obj.get("IFD0:DocumentName"),
    )
    desc_value = _pick_preferred_meta_text(
        obj.get("XMP-dc:Description"),
        obj.get("IFD0:XPComment"),
        obj.get("IFD0:ImageDescription"),
        obj.get("EXIF:UserComment"),
        obj.get("ExifIFD:UserComment"),
    )
    desc_raw_value = _normalize_meta_edit_text(desc_value)
    rows = []
    rows.append((
        META_IFD_NAME, META_TITLE_TAG_ID,
        IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME),
        get_tag_name(META_IFD_NAME, META_TITLE_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
        title_value, _normalize_meta_edit_text(title_value), None,
    ))
    rows.append((
        META_IFD_NAME, META_DESCRIPTION_TAG_ID,
        IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME),
        get_tag_name(META_IFD_NAME, META_DESCRIPTION_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
        desc_value, desc_raw_value, None,
    ))
    rows.append((
        CALC_IFD_NAME, HYPERFOCAL_TAG_ID,
        IFD_DISPLAY_NAMES.get(CALC_IFD_NAME, CALC_IFD_NAME),
        get_tag_name(CALC_IFD_NAME, HYPERFOCAL_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
        _format_hyperfocal_distance(_calc_hyperfocal_distance_from_exiftool_obj(obj, default_coc_mm=load_hyperfocal_coc_mm_from_settings())),
        None, None,
    ))
    skip_keys = {"SourceFile", "File:FileName", "File:Directory", "File:FileSize", "File:FileModifyDate", "File:FileAccessDate", "File:FileCreateDate", "File:FilePermissions", "File:FileType", "File:FileTypeExtension", "File:MIMEType"}
    skip_keys |= EXIFTOOL_KEYS_DUPLICATE_OF_TITLE | EXIFTOOL_KEYS_DUPLICATE_OF_DESCRIPTION
    for key, value in obj.items():
        if not isinstance(key, str) or ":" not in key or key in skip_keys:
            continue
        group, tag_name = key.split(":", 1)
        if group in {"System", "ExifTool", "File"}:
            continue
        mapped_key = map_exiftool_key_to_piexif_key(key)
        if key in hidden_exiftool or (mapped_key in hidden_keys if mapped_key else False):
            continue
        value_str = _fmt(value)
        display_name = get_tag_name_for_exiftool_key(key, tag_name, tag_label_chinese, names_zh)
        rows.append((None, None, group, display_name, value_str, value, key))
    return rows


def load_all_exif(path: str, tag_label_chinese: bool = False) -> list[tuple]:
    """加载全部 EXIF，返回 [(ifd_name, tag_id, 分组, 标签名, 值字符串, raw_value, exiftool_key?), ...]。"""
    _log.info("[load_all_exif] EXIF 查询 path=%r", path)
    if get_exiftool_executable_path():
        exif_rows = load_all_exif_exiftool(path, tag_label_chinese=tag_label_chinese)
        if len(exif_rows) > 2:
            _log.info("[load_all_exif] 完成 来源=exiftool path=%r 条数=%s", path, len(exif_rows))
            return exif_rows
    rows = []
    names_zh = load_exif_tag_names_zh_from_settings() if tag_label_chinese else None
    data = load_exif_piexif(path) or (load_exif_heic(path) if Path(path).suffix.lower() in HEIF_EXTENSIONS else None)
    if data:
        _log.info("[load_all_exif] 来源=文件内(%s) path=%r", "heic" if Path(path).suffix.lower() in HEIF_EXTENSIONS else "piexif", path)
    title_value = load_display_title(path, exif_data=data)
    desc_value = load_display_description(path, exif_data=data)
    desc_raw_value = _normalize_meta_edit_text(desc_value)
    has_front_desc = bool(desc_raw_value)

    def _is_image_description_name(tag_name: str | None) -> bool:
        if not tag_name:
            return False
        s = _sanitize_display_string(str(tag_name)).strip()
        if not s:
            return False
        if s in ("图像描述", "ImageDescription", "Image Description"):
            return True
        key = re.sub(r"[\s_-]+", "", s).lower()
        return key == "imagedescription"

    exiftool_key_for = _get_exiftool_tag_target
    rows.append((
        META_IFD_NAME, META_TITLE_TAG_ID,
        IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME),
        get_tag_name(META_IFD_NAME, META_TITLE_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
        title_value, _normalize_meta_edit_text(title_value), None,
    ))
    rows.append((
        META_IFD_NAME, META_DESCRIPTION_TAG_ID,
        IFD_DISPLAY_NAMES.get(META_IFD_NAME, META_IFD_NAME),
        get_tag_name(META_IFD_NAME, META_DESCRIPTION_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
        desc_value, desc_raw_value, None,
    ))
    hidden_keys = load_exif_tag_hidden_from_settings()
    if data:
        hyperfocal_m = _calc_hyperfocal_distance_m(data, default_coc_mm=load_hyperfocal_coc_mm_from_settings())
        rows.append((
            CALC_IFD_NAME, HYPERFOCAL_TAG_ID,
            IFD_DISPLAY_NAMES.get(CALC_IFD_NAME, CALC_IFD_NAME),
            get_tag_name(CALC_IFD_NAME, HYPERFOCAL_TAG_ID, use_chinese=tag_label_chinese, names_zh=names_zh),
            _format_hyperfocal_distance(hyperfocal_m), None, None,
        ))
        for ifd_name in ("0th", "Exif", "GPS", "1st", "Interop"):
            ifd_data = data.get(ifd_name)
            if not ifd_data or not isinstance(ifd_data, dict):
                continue
            group = IFD_DISPLAY_NAMES.get(ifd_name, ifd_name)
            for tag_id, value in ifd_data.items():
                if f"{ifd_name}:{tag_id}" in hidden_keys:
                    continue
                if has_front_desc and ifd_name == "0th" and tag_id == 270:
                    continue
                name = get_tag_name(ifd_name, tag_id, use_chinese=tag_label_chinese, names_zh=names_zh)
                raw = value
                tag_type = get_tag_type(ifd_name, tag_id)
                ek = exiftool_key_for(ifd_name, tag_id)
                rows.append((ifd_name, tag_id, group, name, format_exif_value(value, expected_type=tag_type), raw, ek))
        if data.get("thumbnail"):
            rows.append((None, None, IFD_DISPLAY_NAMES["thumbnail"], "（存在）", "是", None, None))
    n_before = len(rows)
    if len(rows) <= 2 and Path(path).suffix.lower() in RAW_EXTENSIONS and exifread:
        for group, name, value in load_exif_exifread(path):
            if has_front_desc and _is_image_description_name(name):
                continue
            rows.append((None, None, group, name, value, None, None))
        if len(rows) > n_before:
            _log.info("[load_all_exif] 补充 来源=exifread path=%r 新增条数=%s", path, len(rows) - n_before)
    n_before = len(rows)
    if len(rows) <= 2:
        for group, name, value in load_exif_pillow(path):
            if has_front_desc and _is_image_description_name(name):
                continue
            rows.append((None, None, group, name, value, None, None))
        if len(rows) > n_before:
            _log.info("[load_all_exif] 补充 来源=pillow path=%r 新增条数=%s", path, len(rows) - n_before)
    n_before = len(rows)
    if not get_exiftool_executable_path():
        try:
            for group, name, value in read_xmp_sidecar(path):
                rows.append((None, None, group, name, value, None, None))
            if len(rows) > n_before:
                _log.info("[load_all_exif] 补充 来源=XMP_sidecar path=%r 新增条数=%s", path, len(rows) - n_before)
        except Exception:
            pass
    _log.info("[load_all_exif] 完成 path=%r 总条数=%s", path, len(rows))
    return rows


def _format_report_metadata_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _is_title_like_row(row: tuple) -> bool:
    if len(row) > 6 and row[6] in EXIFTOOL_KEYS_DUPLICATE_OF_TITLE:
        return True
    if len(row) >= 2 and row[0] == META_IFD_NAME and str(row[1]) == META_TITLE_TAG_ID:
        return True
    return False


def build_report_metadata_rows(report_row: dict | None) -> list[tuple]:
    if not isinstance(report_row, dict):
        return []
    rows = []
    ordered_names = ["bird_species_cn", "bird_species_en"] + [
        name for name, _sql, _default in PHOTO_COLUMNS
        if name not in {"bird_species_cn", "bird_species_en"}
    ]
    name_map = {
        "bird_species_cn": "标题",
        "bird_species_en": "标题Eng",
        "title": "标题Raw",
    }
    for col_name in ordered_names:
        display_name = name_map.get(col_name, col_name)
        value_str = _format_report_metadata_value(report_row.get(col_name))
        rows.append((None, None, "ReportDB", display_name, value_str, report_row.get(col_name), None))
    return rows


def merge_report_metadata_rows(rows: list[tuple], report_row: dict | None) -> list[tuple]:
    if not isinstance(report_row, dict):
        return list(rows)
    merged_rows = list(rows)
    species_title = str(report_row.get("bird_species_cn") or "").strip()
    if species_title:
        merged_rows = [row for row in merged_rows if not _is_title_like_row(row)]
    return build_report_metadata_rows(report_row) + merged_rows
