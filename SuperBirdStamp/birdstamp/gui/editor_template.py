# Template payload load/save/normalize and overlay rendering (PIL only in render path).
from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw

from birdstamp.config import get_config_path, resolve_bundled_path
from birdstamp.render.typography import load_font

from birdstamp.gui.editor_core import (
    CENTER_MODE_IMAGE,
    clean_text,
    crop_box_has_effect,
    normalize_center_mode,
    normalize_lookup,
    normalized_box_to_pixel_box,
)
from birdstamp.gui.editor_core import parse_bool_value as _parse_bool_value
from birdstamp.gui.editor_core import parse_ratio_value as _parse_ratio_value
from birdstamp.gui.editor_options import DEFAULT_FIELD_TAG, STYLE_OPTIONS
from birdstamp.gui.template_context import (
    PhotoInfo,
    TEMPLATE_SOURCE_AUTO,
    TEMPLATE_SOURCE_EXIF,
    TEMPLATE_SOURCE_FROM_FILE,
    TEMPLATE_SOURCE_REPORT_DB,
    build_template_context_provider,
    ensure_photo_info,
    normalize_template_source_type,
)
from birdstamp.gui.editor_utils import (
    ALIGN_OPTIONS_HORIZONTAL,
    ALIGN_OPTIONS_VERTICAL,
    TEMPLATE_BANNER_TOP_PADDING_PX,
    safe_color,
    template_banner_fill_color,
    template_font_path_from_type,
    normalize_template_banner_color,
)

_BANNER_BACKGROUND_STYLE_SOLID = "solid"
_BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM = "gradient_bottom"
_BANNER_BACKGROUND_STYLE_OPTIONS = (
    _BANNER_BACKGROUND_STYLE_SOLID,
    _BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM,
)
_BANNER_GRADIENT_HEIGHT_PCT_DEFAULT = 30.0
_BANNER_GRADIENT_HEIGHT_PCT_MIN = 10.0
_BANNER_GRADIENT_HEIGHT_PCT_MAX = 100.0
_BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT = 62.0
_BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MIN = 0.0
_BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MAX = 100.0
_BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT = 0.0
_BANNER_GRADIENT_TOP_COLOR_DEFAULT = "#000000"
_BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT = "#000000"
_DEFAULT_TEMPLATE_CROP_PADDING_PX = 0
_DEFAULT_TEMPLATE_CROP_PADDING_FILL = "#FFFFFF"
_DEFAULT_TEMPLATE_CENTER_MODE = CENTER_MODE_IMAGE
_DEFAULT_TEMPLATE_AUTO_CROP_BY_BIRD = True  # 固定为根据鸟体计算，保留键以兼容旧模板
_DEFAULT_TEMPLATE_MAX_LONG_EDGE = 0


def _clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _clamp_float(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _normalize_banner_background_style(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in _BANNER_BACKGROUND_STYLE_OPTIONS:
        return text
    return _BANNER_BACKGROUND_STYLE_SOLID


def _normalize_banner_gradient_height_pct(value: Any) -> float:
    return round(
        _clamp_float(
            value,
            _BANNER_GRADIENT_HEIGHT_PCT_MIN,
            _BANNER_GRADIENT_HEIGHT_PCT_MAX,
            _BANNER_GRADIENT_HEIGHT_PCT_DEFAULT,
        ),
        2,
    )


def _normalize_banner_gradient_bottom_opacity_pct(value: Any) -> float:
    return round(
        _clamp_float(
            value,
            _BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MIN,
            _BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MAX,
            _BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT,
        ),
        2,
    )


def _normalize_banner_gradient_top_opacity_pct(value: Any) -> float:
    return round(_clamp_float(value, 0.0, 100.0, _BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT), 2)


def _normalize_banner_gradient_color(value: Any, default: str) -> str:
    """Return a valid hex color string, falling back to default."""
    text = str(value or "").strip()
    if not text:
        return default
    try:
        from PIL import ImageColor as _IC
        rgb = _IC.getrgb(text)
        return "#{:02x}{:02x}{:02x}".format(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        return default


def template_directory() -> Path:
    # 模板运行时始终落到用户可写目录；打包内置模板只作为首启/补缺的种子源。
    return get_config_path().parent / "templates"


def _iter_seed_template_directories() -> list[Path]:
    """Return candidate directories that may contain bundled template JSON files."""
    candidates: list[Path] = []

    def _add(path: Path) -> None:
        normalized = path.resolve(strict=False)
        if normalized in candidates:
            return
        candidates.append(normalized)

    app_root = get_config_path().parents[1]
    _add(app_root / "config" / "templates")

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            _add(Path(meipass) / "config" / "templates")

        executable_dir = Path(sys.executable).resolve().parent
        _add(executable_dir / "_internal" / "config" / "templates")
        if sys.platform == "darwin":
            _add(executable_dir.parent / "Resources" / "config" / "templates")

    return [path for path in candidates if path.is_dir()]


def _copy_missing_seed_templates(template_dir: Path) -> int:
    """Copy bundled seed templates into the writable repository without overwriting user files."""
    copied = 0
    target_dir = template_dir.resolve(strict=False)
    for seed_dir in _iter_seed_template_directories():
        if seed_dir == target_dir:
            continue
        for source_path in sorted(seed_dir.glob("*.json")):
            if not source_path.is_file():
                continue
            target_path = template_dir / source_path.name
            if target_path.exists():
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            copied += 1
    return copied


@lru_cache(maxsize=1)
def _load_builtin_default_template_raw() -> dict[str, Any]:
    default_file = resolve_bundled_path("config", "templates", "default.json")
    try:
        text = default_file.read_text(encoding="utf-8")
        raw = json.loads(text)
    except Exception as exc:
        raise ValueError(f"默认模板读取失败: {default_file}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"默认模板格式错误: {default_file}")
    return raw


def _deep_copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _normalize_template_text_source(data: dict[str, Any]) -> dict[str, str]:
    source_raw = data.get("text_source")
    source_type = ""
    source_key = ""
    if isinstance(source_raw, dict):
        source_type = normalize_template_source_type(
            source_raw.get("type") or source_raw.get("provider_id") or source_raw.get("data_source")
        )
        source_key = str(
            source_raw.get("key") or source_raw.get("value") or source_raw.get("source_key") or ""
        ).strip()

    legacy_data_source = normalize_template_source_type(data.get("data_source"))
    legacy_report_field = str(data.get("report_field") or "").strip()
    legacy_fallback = str(data.get("fallback") or "").strip()
    legacy_tag = str(data.get("tag") or DEFAULT_FIELD_TAG).strip()

    if not source_key:
        if legacy_data_source == TEMPLATE_SOURCE_REPORT_DB and legacy_report_field:
            source_type = TEMPLATE_SOURCE_AUTO
            source_key = legacy_report_field
        elif legacy_data_source == TEMPLATE_SOURCE_EXIF and legacy_tag:
            source_type = TEMPLATE_SOURCE_AUTO
            source_key = legacy_tag
        elif legacy_fallback:
            source_type = TEMPLATE_SOURCE_AUTO
            source_key = legacy_fallback
        elif legacy_report_field:
            source_type = TEMPLATE_SOURCE_AUTO
            source_key = legacy_report_field
        elif legacy_tag:
            source_type = TEMPLATE_SOURCE_AUTO
            source_key = legacy_tag

    source_type = normalize_template_source_type(source_type)
    if source_key and source_type in {
        TEMPLATE_SOURCE_EXIF,
        TEMPLATE_SOURCE_FROM_FILE,
        TEMPLATE_SOURCE_REPORT_DB,
    }:
        source_type = TEMPLATE_SOURCE_AUTO
    if not source_key:
        source_type = TEMPLATE_SOURCE_AUTO
        source_key = "{bird}"
    return {
        "type": source_type,
        "key": source_key,
    }


def _normalize_template_field(data: dict[str, Any], index: int) -> dict[str, Any]:
    align_h = str(data.get("align_horizontal") or data.get("align") or "left").lower()
    if align_h not in ALIGN_OPTIONS_HORIZONTAL:
        align_h = "left"
    align_v = str(data.get("align_vertical") or "top").lower()
    if align_v not in ALIGN_OPTIONS_VERTICAL:
        align_v = "top"
    style = str(data.get("style") or "normal").lower()
    if style not in STYLE_OPTIONS:
        style = STYLE_OPTIONS[0]
    font_type = str(data.get("font_type") or "").strip()
    if not font_type or font_type.lower() in {"auto", "default", "system", "none"}:
        font_type = "auto"
    text_source = _normalize_template_text_source(data)
    data_source = text_source["type"]
    source_key = text_source["key"]
    report_field = ""
    fallback = ""
    tag = str(data.get("tag") or DEFAULT_FIELD_TAG)
    if data_source == TEMPLATE_SOURCE_REPORT_DB:
        report_field = source_key
    elif data_source == TEMPLATE_SOURCE_FROM_FILE:
        fallback = source_key
    elif data_source == TEMPLATE_SOURCE_EXIF:
        tag = source_key
    return {
        "name": str(data.get("name") or f"字段{index + 1}"),
        "tag": tag,
        "fallback": fallback,
        "data_source": data_source,
        "report_field": report_field,
        "text_source": text_source,
        "align_horizontal": align_h,
        "align_vertical": align_v,
        "x_offset_pct": round(_clamp_float(data.get("x_offset_pct"), -100.0, 100.0, 0.0), 2),
        "y_offset_pct": round(_clamp_float(data.get("y_offset_pct"), -100.0, 100.0, 5.0), 2),
        "color": safe_color(str(data.get("color") or "#FFFFFF"), "#FFFFFF"),
        "font_size": _clamp_int(data.get("font_size"), 8, 300, 24),
        "font_type": font_type,
        "style": style,
    }


def _resolve_template_field_text(provider, photo_info: PhotoInfo) -> str:
    """模板渲染文本优先取实际内容，空值时回退到 provider caption。"""
    text = clean_text(provider.get_text_content(photo_info))
    if text:
        return text
    return clean_text(provider.get_display_caption(photo_info))


def _default_template_field() -> dict[str, Any]:
    raw = _load_builtin_default_template_raw()
    fields = raw.get("fields")
    if isinstance(fields, list):
        for index, item in enumerate(fields):
            if isinstance(item, dict):
                return _normalize_template_field(item, index=index)
    return _normalize_template_field({}, index=0)


def _normalize_template_payload(payload: dict[str, Any], fallback_name: str) -> dict[str, Any]:
    fields_raw = payload.get("fields")
    fields: list[dict[str, Any]] = []
    if isinstance(fields_raw, list):
        for index, item in enumerate(fields_raw):
            if isinstance(item, dict):
                fields.append(_normalize_template_field(item, index=index))
    if not fields:
        fields.append(_default_template_field())
    ratio = _parse_ratio_value(payload.get("ratio"))
    banner_color = normalize_template_banner_color(payload.get("banner_color"))
    draw_banner_background = _parse_bool_value(payload.get("draw_banner_background"), True)
    banner_background_style = _normalize_banner_background_style(payload.get("banner_background_style"))
    banner_gradient_height_pct = _normalize_banner_gradient_height_pct(payload.get("banner_gradient_height_pct"))
    banner_gradient_top_opacity_pct = _normalize_banner_gradient_top_opacity_pct(
        payload.get("banner_gradient_top_opacity_pct")
    )
    banner_gradient_bottom_opacity_pct = _normalize_banner_gradient_bottom_opacity_pct(
        payload.get("banner_gradient_bottom_opacity_pct")
    )
    # Gradient stop colors: fall back to banner_color for backward compat with old templates
    _bc_fallback = banner_color if banner_color and banner_color != "none" else _BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT
    banner_gradient_top_color = _normalize_banner_gradient_color(
        payload.get("banner_gradient_top_color") or (None if "banner_gradient_top_color" in payload else _bc_fallback),
        _BANNER_GRADIENT_TOP_COLOR_DEFAULT,
    )
    banner_gradient_bottom_color = _normalize_banner_gradient_color(
        payload.get("banner_gradient_bottom_color") or (None if "banner_gradient_bottom_color" in payload else _bc_fallback),
        _BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT,
    )
    template_center_mode = normalize_center_mode(
        payload.get("center_mode") if "center_mode" in payload else _DEFAULT_TEMPLATE_CENTER_MODE
    )
    template_auto_crop = _parse_bool_value(
        payload.get("auto_crop_by_bird") if "auto_crop_by_bird" in payload else _DEFAULT_TEMPLATE_AUTO_CROP_BY_BIRD,
        _DEFAULT_TEMPLATE_AUTO_CROP_BY_BIRD,
    )
    try:
        template_max_long_edge = max(0, int(payload.get("max_long_edge") or _DEFAULT_TEMPLATE_MAX_LONG_EDGE))
    except Exception:
        template_max_long_edge = _DEFAULT_TEMPLATE_MAX_LONG_EDGE
    crop_padding_top = _clamp_int(payload.get("crop_padding_top"), -9999, 9999, _DEFAULT_TEMPLATE_CROP_PADDING_PX)
    crop_padding_bottom = _clamp_int(payload.get("crop_padding_bottom"), -9999, 9999, _DEFAULT_TEMPLATE_CROP_PADDING_PX)
    crop_padding_left = _clamp_int(payload.get("crop_padding_left"), -9999, 9999, _DEFAULT_TEMPLATE_CROP_PADDING_PX)
    crop_padding_right = _clamp_int(payload.get("crop_padding_right"), -9999, 9999, _DEFAULT_TEMPLATE_CROP_PADDING_PX)
    crop_padding_fill = _normalize_banner_gradient_color(
        payload.get("crop_padding_fill"), _DEFAULT_TEMPLATE_CROP_PADDING_FILL
    )
    return {
        "name": str(payload.get("name") or fallback_name),
        "ratio": ratio,
        "banner_color": banner_color,
        "draw_banner_background": draw_banner_background,
        "banner_background_style": banner_background_style,
        "banner_gradient_height_pct": banner_gradient_height_pct,
        "banner_gradient_top_color": banner_gradient_top_color,
        "banner_gradient_top_opacity_pct": banner_gradient_top_opacity_pct,
        "banner_gradient_bottom_color": banner_gradient_bottom_color,
        "banner_gradient_bottom_opacity_pct": banner_gradient_bottom_opacity_pct,
        "center_mode": template_center_mode,
        "auto_crop_by_bird": template_auto_crop,
        "max_long_edge": template_max_long_edge,
        "crop_padding_top": crop_padding_top,
        "crop_padding_bottom": crop_padding_bottom,
        "crop_padding_left": crop_padding_left,
        "crop_padding_right": crop_padding_right,
        "crop_padding_fill": crop_padding_fill,
        "fields": fields,
    }


def _default_template_payload(name: str = "default") -> dict[str, Any]:
    raw = _deep_copy_payload(_load_builtin_default_template_raw())
    raw["name"] = name or str(raw.get("name") or "default")
    return _normalize_template_payload(raw, fallback_name=str(raw["name"]))


def load_template_payload(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"模板格式错误: {path}")
    return _normalize_template_payload(raw, fallback_name=path.stem)


def save_template_payload(path: Path, payload: dict[str, Any]) -> None:
    normalized = _normalize_template_payload(payload, fallback_name=path.stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_template_repository(template_dir: Path) -> None:
    template_dir.mkdir(parents=True, exist_ok=True)
    _copy_missing_seed_templates(template_dir)
    has_json = any(path.suffix.lower() == ".json" for path in template_dir.iterdir() if path.is_file())
    if has_json:
        return
    default_path = template_dir / "default.json"
    save_template_payload(default_path, _default_template_payload(name="default"))


def list_template_names(template_dir: Path) -> list[str]:
    names: list[str] = []
    for path in sorted(template_dir.glob("*.json")):
        if path.is_file():
            names.append(path.stem)
    return names


def _format_with_context(text: str, context: dict[str, str]) -> str:
    if not text:
        return ""
    safe = defaultdict(str, context)
    try:
        return text.format_map(safe)
    except Exception:
        return text


def _lookup_tag_value(tag: str, lookup: dict[str, Any], context: dict[str, str]) -> str | None:
    token = (tag or "").strip()
    if not token:
        return None
    lowered = token.lower()
    if lowered in context:
        text = clean_text(context[lowered])
        if text:
            return text
    value = lookup.get(lowered)
    if value is None and ":" in lowered:
        value = lookup.get(lowered.split(":")[-1])
    if value is None:
        suffix = f":{lowered}"
        for key, candidate in lookup.items():
            if key.endswith(suffix):
                value = candidate
                break
    text = clean_text(value)
    if text:
        return text
    return None


def _latin1_safe_text(text: str) -> str:
    try:
        return str(text or "").encode("latin-1", errors="replace").decode("latin-1")
    except Exception:
        return "".join(ch if ord(ch) < 128 else "?" for ch in str(text or ""))


def _measure_text_with_fallback(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: Any,
) -> tuple[str, tuple[int, int, int, int]]:
    try:
        return text, draw.textbbox((0, 0), text, font=font)
    except UnicodeError:
        fallback_text = _latin1_safe_text(text)
        return fallback_text, draw.textbbox((0, 0), fallback_text, font=font)


def _draw_styled_text(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    x: int,
    y: int,
    color: str,
    font: Any,
    style: str,
) -> None:
    draw_text, text_box = _measure_text_with_fallback(draw, text, font=font)
    left, top, right, bottom = text_box
    width = max(1, right - left)
    height = max(1, bottom - top)
    layer = Image.new("RGBA", (width + 10, height + 10), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    text_pos = (5 - left, 5 - top)
    is_bold = style in {"bold", "bold_italic"}
    is_italic = style in {"italic", "bold_italic"}
    if is_bold:
        offsets = [(0, 0), (1, 0), (0, 1)]
        for dx, dy in offsets:
            layer_draw.text((text_pos[0] + dx, text_pos[1] + dy), draw_text, font=font, fill=color)
    else:
        layer_draw.text(text_pos, draw_text, font=font, fill=color)
    if is_italic:
        shear = -0.28
        new_width = int(round(layer.width + abs(shear) * layer.height))
        layer = layer.transform(
            (max(1, new_width), layer.height),
            Image.Transform.AFFINE,
            (1, shear, 0, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
    image.alpha_composite(layer, (x - 5, y - 5))


def _template_font_scale_for_canvas(width: int, height: int) -> float:
    if width <= 0 or height <= 0:
        return 1.0
    short_edge = float(min(width, height))
    long_edge = float(max(width, height))
    short_scale = short_edge / 900.0
    long_scale = long_edge / 1600.0
    scale = (short_scale * 0.68) + (long_scale * 0.32)
    return max(0.72, min(2.25, scale))


def _compute_template_text_position(
    *,
    canvas_width: int,
    canvas_height: int,
    text_width: int,
    text_height: int,
    align_h: str,
    align_v: str,
    x_offset_pct: float,
    y_offset_pct: float,
) -> tuple[int, int]:
    if align_h == "center":
        anchor_x = int(round((canvas_width * 0.5) + (canvas_width * x_offset_pct)))
        x = anchor_x - (text_width // 2)
    elif align_h == "right":
        anchor_x = int(round(canvas_width + (canvas_width * x_offset_pct)))
        x = anchor_x - text_width
    else:
        anchor_x = int(round(canvas_width * x_offset_pct))
        x = anchor_x
    if align_v == "center":
        anchor_y = int(round((canvas_height * 0.5) + (canvas_height * y_offset_pct)))
        y = anchor_y - (text_height // 2)
    elif align_v == "bottom":
        anchor_y = int(round(canvas_height + (canvas_height * y_offset_pct)))
        y = anchor_y - text_height
    else:
        anchor_y = int(round(canvas_height * y_offset_pct))
        y = anchor_y
    return (x, y)


def _text_boxes_overlap(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
    *,
    gap: int,
) -> bool:
    return not (
        a[2] + gap <= b[0]
        or b[2] + gap <= a[0]
        or a[3] + gap <= b[1]
        or b[3] + gap <= a[1]
    )


def _resolve_template_text_position_with_avoidance(
    *,
    base_x: int,
    base_y: int,
    text_width: int,
    text_height: int,
    canvas_width: int,
    canvas_height: int,
    align_h: str,
    align_v: str,
    occupied: list[tuple[int, int, int, int]],
    gap: int,
) -> tuple[int, int, tuple[int, int, int, int], bool]:
    max_x = max(0, canvas_width - text_width)
    max_y = max(0, canvas_height - text_height)
    origin_x = max(0, min(max_x, base_x))
    origin_y = max(0, min(max_y, base_y))
    step_y = max(4, int(round(text_height * 0.36)))
    step_x = max(6, int(round(text_width * 0.10)))
    y_steps = max(8, (canvas_height // step_y) + 3)
    y_offsets: list[int] = [0]
    if align_v == "bottom":
        y_offsets.extend([-step_y * i for i in range(1, y_steps + 1)])
        y_offsets.extend([step_y * i for i in range(1, max(3, y_steps // 2) + 1)])
    elif align_v == "top":
        y_offsets.extend([step_y * i for i in range(1, y_steps + 1)])
        y_offsets.extend([-step_y * i for i in range(1, max(3, y_steps // 2) + 1)])
    else:
        for i in range(1, y_steps + 1):
            y_offsets.extend([step_y * i, -step_y * i])
    x_offsets: list[int] = [0]
    x_span = max(2, min(8, canvas_width // max(1, step_x)))
    if align_h == "left":
        x_offsets.extend([step_x * i for i in range(1, x_span + 1)])
        x_offsets.extend([-step_x * i for i in range(1, max(2, x_span // 2) + 1)])
    elif align_h == "right":
        x_offsets.extend([-step_x * i for i in range(1, x_span + 1)])
        x_offsets.extend([step_x * i for i in range(1, max(2, x_span // 2) + 1)])
    else:
        for i in range(1, x_span + 1):
            x_offsets.extend([step_x * i, -step_x * i])
    best: tuple[int, int, tuple[int, int, int, int], int] | None = None
    for dy in y_offsets:
        for dx in x_offsets:
            x = max(0, min(max_x, origin_x + dx))
            y = max(0, min(max_y, origin_y + dy))
            rect = (x, y, x + text_width, y + text_height)
            overlaps = sum(1 for existing in occupied if _text_boxes_overlap(rect, existing, gap=gap))
            if overlaps == 0:
                return (x, y, rect, True)
            distance = abs(dx) + abs(dy)
            score = overlaps * 100000 + distance
            if best is None or score < best[3]:
                best = (x, y, rect, score)
    if best is not None:
        return (best[0], best[1], best[2], False)
    rect = (origin_x, origin_y, origin_x + text_width, origin_y + text_height)
    return (origin_x, origin_y, rect, False)


def _iter_font_sizes_for_layout(base_size: int, minimum: int = 8) -> list[int]:
    start = max(minimum, int(base_size))
    sizes = [start]
    if start <= minimum:
        return sizes
    step = max(1, int(round(start * 0.12)))
    current = start - step
    while current > minimum:
        sizes.append(current)
        current -= step
    if sizes[-1] != minimum:
        sizes.append(minimum)
    return sizes


def _compute_template_banner_rect(
    *,
    text_boxes: list[tuple[int, int, int, int]],
    canvas_width: int,
    canvas_height: int,
    top_padding: int = TEMPLATE_BANNER_TOP_PADDING_PX,
) -> tuple[int, int, int, int] | None:
    if not text_boxes or canvas_width <= 0 or canvas_height <= 0:
        return None
    top = min(box[1] for box in text_boxes) - max(0, int(top_padding))
    bottom = max(box[3] for box in text_boxes)
    left = 0
    top = max(0, min(canvas_height, top))
    right = canvas_width
    bottom = max(0, min(canvas_height, bottom))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _compute_template_bottom_gradient_rect(
    *,
    canvas_width: int,
    canvas_height: int,
    height_pct: float,
) -> tuple[int, int, int, int] | None:
    if canvas_width <= 0 or canvas_height <= 0:
        return None
    ratio = max(0.0, min(1.0, float(height_pct) / 100.0))
    scrim_height = int(round(canvas_height * ratio))
    scrim_height = max(1, min(canvas_height, scrim_height))
    top = max(0, canvas_height - scrim_height)
    return (0, top, canvas_width, canvas_height)


def _draw_vertical_gradient_scrim(
    image: Image.Image,
    *,
    rect: tuple[int, int, int, int],
    top_color: str,
    top_opacity_pct: float,
    bottom_color: str,
    bottom_opacity_pct: float,
) -> None:
    left, top, right, bottom = rect
    width = max(0, right - left)
    height = max(0, bottom - top)
    if width <= 0 or height <= 0:
        return
    try:
        top_rgb = ImageColor.getrgb(top_color)
    except Exception:
        return
    try:
        bot_rgb = ImageColor.getrgb(bottom_color)
    except Exception:
        bot_rgb = top_rgb
    tr, tg, tb = int(top_rgb[0]), int(top_rgb[1]), int(top_rgb[2])
    br, bg, bb = int(bot_rgb[0]), int(bot_rgb[1]), int(bot_rgb[2])
    top_alpha = int(round(max(0.0, min(100.0, top_opacity_pct)) * 2.55))
    bottom_alpha = int(round(max(0.0, min(100.0, bottom_opacity_pct)) * 2.55))
    if top_alpha <= 0 and bottom_alpha <= 0:
        return
    denominator = max(1, height - 1)
    pixels: list[tuple[int, int, int, int]] = []
    for row in range(height):
        t = row / float(denominator)
        r = int(round(tr + (br - tr) * t))
        g = int(round(tg + (bg - tg) * t))
        b = int(round(tb + (bb - tb) * t))
        a = int(round(top_alpha + (bottom_alpha - top_alpha) * t))
        pixels.append((r, g, b, a))
    gradient = Image.new("RGBA", (1, height))
    gradient.putdata(pixels)
    if width > 1:
        gradient = gradient.resize((width, height), resample=Image.Resampling.BILINEAR)
    overlay = Image.new("RGBA", image.size, color=(0, 0, 0, 0))
    overlay.paste(gradient, (left, top))
    image.alpha_composite(overlay)


BANNER_BACKGROUND_STYLE_SOLID = _BANNER_BACKGROUND_STYLE_SOLID
BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM = _BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM
BANNER_BACKGROUND_STYLE_OPTIONS = _BANNER_BACKGROUND_STYLE_OPTIONS

BANNER_GRADIENT_HEIGHT_PCT_DEFAULT = _BANNER_GRADIENT_HEIGHT_PCT_DEFAULT
BANNER_GRADIENT_HEIGHT_PCT_MIN = _BANNER_GRADIENT_HEIGHT_PCT_MIN
BANNER_GRADIENT_HEIGHT_PCT_MAX = _BANNER_GRADIENT_HEIGHT_PCT_MAX

BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT = _BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT
BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MIN = _BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MIN
BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MAX = _BANNER_GRADIENT_BOTTOM_OPACITY_PCT_MAX

BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT = _BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT
BANNER_GRADIENT_TOP_COLOR_DEFAULT = _BANNER_GRADIENT_TOP_COLOR_DEFAULT
BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT = _BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT
DEFAULT_TEMPLATE_CROP_PADDING_PX = _DEFAULT_TEMPLATE_CROP_PADDING_PX
DEFAULT_TEMPLATE_CROP_PADDING_FILL = _DEFAULT_TEMPLATE_CROP_PADDING_FILL
DEFAULT_TEMPLATE_CENTER_MODE = _DEFAULT_TEMPLATE_CENTER_MODE
DEFAULT_TEMPLATE_AUTO_CROP_BY_BIRD = _DEFAULT_TEMPLATE_AUTO_CROP_BY_BIRD
DEFAULT_TEMPLATE_MAX_LONG_EDGE = _DEFAULT_TEMPLATE_MAX_LONG_EDGE


def normalize_banner_background_style(value: Any) -> str:
    """Public wrapper for _normalize_banner_background_style."""
    return _normalize_banner_background_style(value)


def render_template_overlay(
    image: Image.Image,
    *,
    raw_metadata: dict[str, Any],
    metadata_context: dict[str, str],
    photo_info: PhotoInfo | None = None,
    template_payload: dict[str, Any],
    auto_scale_font: bool = True,
    draw_banner: bool = True,
    draw_text: bool = True,
) -> Image.Image:
    canvas = image.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    font_scale = _template_font_scale_for_canvas(canvas.width, canvas.height) if auto_scale_font else 1.0
    occupied_boxes: list[tuple[int, int, int, int]] = []
    text_gap = max(4, int(round(min(canvas.width, canvas.height) * 0.006)))
    draw_commands: list[tuple[str, int, int, str, Any, str, tuple[int, int, int, int]]] = []
    fields = template_payload.get("fields") or []
    if not isinstance(fields, list):
        fields = []
    source_file = raw_metadata.get("SourceFile") or raw_metadata.get("sourcefile") or "."
    render_photo_info = ensure_photo_info(photo_info or source_file, raw_metadata=raw_metadata)
    for field_index, raw_field in enumerate(fields):
        if not isinstance(raw_field, dict):
            continue
        field = _normalize_template_field(raw_field, field_index)
        text_source = field.get("text_source") or {}
        provider = build_template_context_provider(
            str(text_source.get("type") or TEMPLATE_SOURCE_FROM_FILE),
            str(text_source.get("key") or ""),
            display_label=str(field.get("name") or ""),
        )
        text = _resolve_template_field_text(provider, render_photo_info)
        if not text:
            continue
        font_size_base = max(8, int(field.get("font_size") or 24))
        color = safe_color(str(field.get("color") or "#FFFFFF"), "#FFFFFF")
        align_h = str(field.get("align_horizontal") or field.get("align") or "left").lower()
        align_v = str(field.get("align_vertical") or "top").lower()
        x_offset = float(field.get("x_offset_pct") or 0.0) / 100.0
        y_offset = float(field.get("y_offset_pct") or 0.0) / 100.0
        field_font_path = template_font_path_from_type(field.get("font_type"))
        scaled_size = max(8, min(320, int(round(font_size_base * font_scale))))
        chosen_font = load_font(field_font_path, scaled_size)
        chosen_x = 0
        chosen_y = 0
        chosen_rect = (0, 0, 1, 1)
        draw_text = text
        for candidate_size in _iter_font_sizes_for_layout(scaled_size, minimum=8):
            font = load_font(field_font_path, candidate_size)
            measured_text, text_box = _measure_text_with_fallback(draw, text, font=font)
            text_width = max(1, text_box[2] - text_box[0])
            text_height = max(1, text_box[3] - text_box[1])
            base_x, base_y = _compute_template_text_position(
                canvas_width=canvas.width,
                canvas_height=canvas.height,
                text_width=text_width,
                text_height=text_height,
                align_h=align_h,
                align_v=align_v,
                x_offset_pct=x_offset,
                y_offset_pct=y_offset,
            )
            x, y, rect, non_overlap = _resolve_template_text_position_with_avoidance(
                base_x=base_x,
                base_y=base_y,
                text_width=text_width,
                text_height=text_height,
                canvas_width=canvas.width,
                canvas_height=canvas.height,
                align_h=align_h,
                align_v=align_v,
                occupied=occupied_boxes,
                gap=text_gap,
            )
            chosen_font = font
            chosen_x = x
            chosen_y = y
            chosen_rect = rect
            draw_text = measured_text
            if non_overlap:
                break
        draw_commands.append(
            (
                draw_text,
                chosen_x,
                chosen_y,
                color,
                chosen_font,
                str(field.get("style") or "normal"),
                chosen_rect,
            )
        )
        occupied_boxes.append(chosen_rect)
    banner_fill = template_banner_fill_color(template_payload.get("banner_color"))
    draw_banner_background = _parse_bool_value(template_payload.get("draw_banner_background"), True)
    banner_background_style = _normalize_banner_background_style(template_payload.get("banner_background_style"))
    banner_gradient_height_pct = _normalize_banner_gradient_height_pct(template_payload.get("banner_gradient_height_pct"))
    banner_gradient_top_opacity_pct = _normalize_banner_gradient_top_opacity_pct(
        template_payload.get("banner_gradient_top_opacity_pct")
    )
    banner_gradient_bottom_opacity_pct = _normalize_banner_gradient_bottom_opacity_pct(
        template_payload.get("banner_gradient_bottom_opacity_pct")
    )
    _bc_fallback = banner_fill or _BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT
    banner_gradient_top_color = _normalize_banner_gradient_color(
        template_payload.get("banner_gradient_top_color"), _bc_fallback
    )
    banner_gradient_bottom_color = _normalize_banner_gradient_color(
        template_payload.get("banner_gradient_bottom_color"), _bc_fallback
    )
    if draw_banner and draw_banner_background and draw_commands:
        if banner_background_style == _BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM:
            scrim_rect = _compute_template_bottom_gradient_rect(
                canvas_width=canvas.width,
                canvas_height=canvas.height,
                height_pct=banner_gradient_height_pct,
            )
            if scrim_rect is not None:
                _draw_vertical_gradient_scrim(
                    canvas,
                    rect=scrim_rect,
                    top_color=banner_gradient_top_color,
                    top_opacity_pct=banner_gradient_top_opacity_pct,
                    bottom_color=banner_gradient_bottom_color,
                    bottom_opacity_pct=banner_gradient_bottom_opacity_pct,
                )
        elif banner_fill:
            banner_rect = _compute_template_banner_rect(
                text_boxes=[cmd[6] for cmd in draw_commands],
                canvas_width=canvas.width,
                canvas_height=canvas.height,
                top_padding=TEMPLATE_BANNER_TOP_PADDING_PX,
            )
            if banner_rect is not None:
                draw.rectangle(banner_rect, fill=banner_fill)
    if draw_text:
        for text, x, y, color, font, style, _rect in draw_commands:
            _draw_styled_text(
                canvas,
                draw,
                text,
                x=x,
                y=y,
                color=color,
                font=font,
                style=style,
            )
    return canvas.convert("RGB")


def default_template_payload(name: str = "default") -> dict[str, Any]:
    """Public wrapper for _default_template_payload."""
    return _default_template_payload(name=name)


def normalize_template_payload(payload: dict[str, Any], fallback_name: str) -> dict[str, Any]:
    """Public wrapper for _normalize_template_payload."""
    return _normalize_template_payload(payload, fallback_name)


def normalize_template_field(data: dict[str, Any], index: int) -> dict[str, Any]:
    """Public wrapper for _normalize_template_field."""
    return _normalize_template_field(data, index)


def deep_copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Public wrapper for _deep_copy_payload."""
    return _deep_copy_payload(payload)


def render_template_overlay_in_crop_region(
    image: Image.Image,
    *,
    raw_metadata: dict[str, Any],
    metadata_context: dict[str, str],
    photo_info: PhotoInfo | None = None,
    template_payload: dict[str, Any],
    crop_box: tuple[float, float, float, float] | None,
    draw_banner: bool = True,
    draw_text: bool = True,
) -> Image.Image:
    kw = dict(
        raw_metadata=raw_metadata,
        metadata_context=metadata_context,
        photo_info=photo_info,
        template_payload=template_payload,
        draw_banner=draw_banner,
        draw_text=draw_text,
    )
    if not crop_box_has_effect(crop_box):
        return render_template_overlay(image, **kw)
    crop_px = normalized_box_to_pixel_box(crop_box, image.width, image.height)
    if crop_px is None:
        return render_template_overlay(image, **kw)
    left, top, right, bottom = crop_px
    if right - left < 2 or bottom - top < 2:
        return render_template_overlay(image, **kw)
    crop_image = image.crop((left, top, right, bottom))
    rendered_crop = render_template_overlay(crop_image, **kw)
    merged = image.copy()
    merged.paste(rendered_crop, (left, top))
    return merged
