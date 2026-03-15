# Editor options loaded from config/editor_options.json (no Qt).
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from birdstamp.config import resolve_bundled_path

# Sentinel for "free aspect ratio" in crop (no ratio lock when dragging 9-grid).
RATIO_FREE = "free"

_FALLBACK_STYLE_OPTIONS = ("normal",)
_FALLBACK_RATIO_OPTIONS: list[tuple[str, float | None]] = [("原比例", None)]
_FALLBACK_MAX_LONG_EDGE_OPTIONS = [0]
_FALLBACK_OUTPUT_FORMAT_OPTIONS: list[tuple[str, str]] = [("png", "PNG"), ("jpg", "JPG")]
_FALLBACK_VIDEO_CONTAINER_OPTIONS: list[tuple[str, str]] = [("mp4", "MP4"), ("mov", "MOV")]
_FALLBACK_VIDEO_CODEC_OPTIONS: list[tuple[str, str]] = [("h264", "H.264 / libx264"), ("h265", "H.265 / libx265")]
_FALLBACK_VIDEO_PRESET_OPTIONS: list[tuple[str, str]] = [("fast", "fast"), ("medium", "medium"), ("slow", "slow")]
_FALLBACK_VIDEO_ORIENTATION_OPTIONS: list[tuple[str, str]] = [("横屏", "landscape"), ("竖屏", "portrait")]
_FALLBACK_VIDEO_FRAME_SIZE_OPTIONS: list[dict[str, Any]] = [
    {"label": "首帧尺寸", "mode": "auto", "width": 0, "height": 0},
    {"label": "4K UHD (3840 x 2160)", "mode": "preset", "width": 3840, "height": 2160},
    {"label": "自定义", "mode": "custom", "width": 0, "height": 0},
]
_FALLBACK_VIDEO_FPS_OPTIONS = [12.0, 24.0, 25.0, 30.0, 60.0]
_FALLBACK_DEFAULT_VIDEO_CONTAINER = "mp4"
_FALLBACK_DEFAULT_VIDEO_CODEC = "h264"
_FALLBACK_DEFAULT_VIDEO_PRESET = "slow"
_FALLBACK_DEFAULT_VIDEO_ORIENTATION = "landscape"
_FALLBACK_DEFAULT_VIDEO_FRAME_SIZE_MODE = "preset"
_FALLBACK_DEFAULT_VIDEO_FPS = 30.0
_FALLBACK_DEFAULT_VIDEO_CRF = 20
_FALLBACK_DEFAULT_VIDEO_WIDTH = 3840
_FALLBACK_DEFAULT_VIDEO_HEIGHT = 2160
_FALLBACK_COLOR_PRESETS: list[tuple[str, str]] = [("白色", "#FFFFFF"), ("黑色", "#111111")]
_FALLBACK_DEFAULT_FIELD_TAG = "EXIF:Model"
_FALLBACK_TAG_OPTIONS: list[tuple[str, str]] = [("机身型号 (EXIF)", "EXIF:Model")]
_FALLBACK_SAMPLE_RAW_METADATA: dict[str, Any] = {}


@lru_cache(maxsize=1)
def _load_builtin_editor_options_raw() -> dict[str, Any]:
    options_file = resolve_bundled_path("config", "editor_options.json")
    text = options_file.read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"编辑器选项格式错误: {options_file}")
    return raw


def _normalize_style_options(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return _FALLBACK_STYLE_OPTIONS
    items: list[str] = []
    for item in value:
        text = str(item).strip().lower()
        if text and text not in items:
            items.append(text)
    return tuple(items) if items else _FALLBACK_STYLE_OPTIONS


def _normalize_ratio_options(value: Any) -> list[tuple[str, float | None | str]]:
    if not isinstance(value, list):
        return list(_FALLBACK_RATIO_OPTIONS)
    items: list[tuple[str, float | None | str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        ratio_raw = item.get("value")
        ratio: float | None | str
        if ratio_raw is None:
            ratio = None
        elif isinstance(ratio_raw, str) and str(ratio_raw).strip().lower() == "free":
            ratio = RATIO_FREE
        else:
            try:
                ratio = float(ratio_raw)
            except Exception:
                continue
            if ratio <= 0:
                continue
        items.append((label, ratio))
    return items if items else list(_FALLBACK_RATIO_OPTIONS)


def _normalize_max_edges(value: Any) -> list[int]:
    if not isinstance(value, list):
        return list(_FALLBACK_MAX_LONG_EDGE_OPTIONS)
    items: list[int] = []
    for item in value:
        try:
            edge = int(float(item))
        except Exception:
            continue
        if edge < 0:
            continue
        if edge not in items:
            items.append(edge)
    return items if items else list(_FALLBACK_MAX_LONG_EDGE_OPTIONS)


def _normalize_output_formats(
    value: Any,
    fallback: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    fallback_items = list(fallback) if isinstance(fallback, list) else list(_FALLBACK_OUTPUT_FORMAT_OPTIONS)
    if not isinstance(value, list):
        return fallback_items
    items: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        suffix = str(item.get("suffix") or "").strip().lower().lstrip(".")
        label = str(item.get("label") or "").strip()
        if not suffix or not label:
            continue
        items.append((suffix, label))
    return items if items else fallback_items


def _normalize_numeric_list(value: Any, fallback: list[float]) -> list[float]:
    if not isinstance(value, list):
        return list(fallback)
    items: list[float] = []
    for item in value:
        try:
            parsed = float(item)
        except Exception:
            continue
        if parsed <= 0:
            continue
        if parsed not in items:
            items.append(parsed)
    return items if items else list(fallback)


def _normalize_labeled_values(value: Any, fallback: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return list(fallback)
    items: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        item_value = str(item.get("value") or "").strip()
        if not label or not item_value:
            continue
        items.append((label, item_value))
    return items if items else list(fallback)


def _normalize_video_frame_size_options(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return [dict(item) for item in _FALLBACK_VIDEO_FRAME_SIZE_OPTIONS]

    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        mode = str(item.get("mode") or "").strip().lower()
        if not label or mode not in {"auto", "preset", "custom"}:
            continue
        width = 0
        height = 0
        if mode == "preset":
            try:
                width = int(item.get("width") or 0)
                height = int(item.get("height") or 0)
            except Exception:
                continue
            if width <= 0 or height <= 0:
                continue
        items.append(
            {
                "label": label,
                "mode": mode,
                "width": width,
                "height": height,
            }
        )
    return items if items else [dict(item) for item in _FALLBACK_VIDEO_FRAME_SIZE_OPTIONS]


def _normalize_sample_raw_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(_FALLBACK_SAMPLE_RAW_METADATA)
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        text_key = str(key).strip()
        if text_key:
            cleaned[text_key] = item
    return cleaned if cleaned else dict(_FALLBACK_SAMPLE_RAW_METADATA)


def load_editor_options() -> dict[str, Any]:
    try:
        raw = _load_builtin_editor_options_raw()
    except Exception:
        raw = {}

    style_options = _normalize_style_options(raw.get("style_options"))
    ratio_options = _normalize_ratio_options(raw.get("ratio_options"))
    max_long_edge_options = _normalize_max_edges(raw.get("max_long_edge_options"))
    output_format_options = _normalize_output_formats(raw.get("output_format_options"), _FALLBACK_OUTPUT_FORMAT_OPTIONS)
    video_container_options = _normalize_output_formats(raw.get("video_container_options"), _FALLBACK_VIDEO_CONTAINER_OPTIONS)
    video_codec_options = _normalize_labeled_values(raw.get("video_codec_options"), _FALLBACK_VIDEO_CODEC_OPTIONS)
    video_preset_options = _normalize_labeled_values(raw.get("video_preset_options"), _FALLBACK_VIDEO_PRESET_OPTIONS)
    video_orientation_options = _normalize_labeled_values(
        raw.get("video_orientation_options"),
        _FALLBACK_VIDEO_ORIENTATION_OPTIONS,
    )
    video_frame_size_options = _normalize_video_frame_size_options(raw.get("video_frame_size_options"))
    video_fps_options = _normalize_numeric_list(raw.get("video_fps_options"), _FALLBACK_VIDEO_FPS_OPTIONS)
    color_presets = _normalize_labeled_values(raw.get("color_presets"), _FALLBACK_COLOR_PRESETS)
    tag_options = _normalize_labeled_values(raw.get("tag_options"), _FALLBACK_TAG_OPTIONS)
    sample_raw_metadata = _normalize_sample_raw_metadata(raw.get("sample_raw_metadata"))

    default_field_tag = str(raw.get("default_field_tag") or "").strip() or _FALLBACK_DEFAULT_FIELD_TAG
    tag_values = {value for _label, value in tag_options}
    if default_field_tag not in tag_values:
        default_field_tag = tag_options[0][1] if tag_options else _FALLBACK_DEFAULT_FIELD_TAG

    default_video_container = str(raw.get("default_video_container") or "").strip().lower() or _FALLBACK_DEFAULT_VIDEO_CONTAINER
    container_values = {value for value, _label in video_container_options}
    if default_video_container not in container_values:
        default_video_container = video_container_options[0][0] if video_container_options else _FALLBACK_DEFAULT_VIDEO_CONTAINER

    default_video_codec = str(raw.get("default_video_codec") or "").strip().lower() or _FALLBACK_DEFAULT_VIDEO_CODEC
    codec_values = {value for value, _label in video_codec_options}
    if default_video_codec not in codec_values:
        default_video_codec = video_codec_options[0][0] if video_codec_options else _FALLBACK_DEFAULT_VIDEO_CODEC

    default_video_preset = str(raw.get("default_video_preset") or "").strip().lower() or _FALLBACK_DEFAULT_VIDEO_PRESET
    preset_values = {value for value, _label in video_preset_options}
    if default_video_preset not in preset_values:
        default_video_preset = video_preset_options[0][0] if video_preset_options else _FALLBACK_DEFAULT_VIDEO_PRESET

    default_video_orientation = (
        str(raw.get("default_video_orientation") or "").strip().lower() or _FALLBACK_DEFAULT_VIDEO_ORIENTATION
    )
    orientation_values = {value for _label, value in video_orientation_options}
    if default_video_orientation not in orientation_values:
        default_video_orientation = (
            video_orientation_options[0][1] if video_orientation_options else _FALLBACK_DEFAULT_VIDEO_ORIENTATION
        )

    default_video_frame_size_mode = (
        str(raw.get("default_video_frame_size_mode") or "").strip().lower() or _FALLBACK_DEFAULT_VIDEO_FRAME_SIZE_MODE
    )
    frame_modes = {str(item.get("mode") or "").strip().lower() for item in video_frame_size_options}
    if default_video_frame_size_mode not in frame_modes:
        default_video_frame_size_mode = _FALLBACK_DEFAULT_VIDEO_FRAME_SIZE_MODE

    try:
        default_video_fps = float(raw.get("default_video_fps", _FALLBACK_DEFAULT_VIDEO_FPS))
    except Exception:
        default_video_fps = _FALLBACK_DEFAULT_VIDEO_FPS
    if default_video_fps <= 0:
        default_video_fps = _FALLBACK_DEFAULT_VIDEO_FPS

    try:
        default_video_crf = int(raw.get("default_video_crf", _FALLBACK_DEFAULT_VIDEO_CRF))
    except Exception:
        default_video_crf = _FALLBACK_DEFAULT_VIDEO_CRF

    try:
        default_video_width = int(raw.get("default_video_width", _FALLBACK_DEFAULT_VIDEO_WIDTH))
    except Exception:
        default_video_width = _FALLBACK_DEFAULT_VIDEO_WIDTH
    if default_video_width <= 0:
        default_video_width = _FALLBACK_DEFAULT_VIDEO_WIDTH

    try:
        default_video_height = int(raw.get("default_video_height", _FALLBACK_DEFAULT_VIDEO_HEIGHT))
    except Exception:
        default_video_height = _FALLBACK_DEFAULT_VIDEO_HEIGHT
    if default_video_height <= 0:
        default_video_height = _FALLBACK_DEFAULT_VIDEO_HEIGHT

    return {
        "style_options": style_options,
        "ratio_options": ratio_options,
        "max_long_edge_options": max_long_edge_options,
        "output_format_options": output_format_options,
        "video_container_options": video_container_options,
        "video_codec_options": video_codec_options,
        "video_preset_options": video_preset_options,
        "video_orientation_options": video_orientation_options,
        "video_frame_size_options": video_frame_size_options,
        "video_fps_options": video_fps_options,
        "default_video_container": default_video_container,
        "default_video_codec": default_video_codec,
        "default_video_preset": default_video_preset,
        "default_video_orientation": default_video_orientation,
        "default_video_frame_size_mode": default_video_frame_size_mode,
        "default_video_fps": default_video_fps,
        "default_video_crf": default_video_crf,
        "default_video_width": default_video_width,
        "default_video_height": default_video_height,
        "color_presets": color_presets,
        "default_field_tag": default_field_tag,
        "tag_options": tag_options,
        "sample_raw_metadata": sample_raw_metadata,
    }


_EDITOR_OPTIONS = load_editor_options()
STYLE_OPTIONS: tuple[str, ...] = _EDITOR_OPTIONS["style_options"]
RATIO_OPTIONS: list[tuple[str, float | None | str]] = _EDITOR_OPTIONS["ratio_options"]
MAX_LONG_EDGE_OPTIONS: list[int] = _EDITOR_OPTIONS["max_long_edge_options"]
OUTPUT_FORMAT_OPTIONS: list[tuple[str, str]] = _EDITOR_OPTIONS["output_format_options"]
VIDEO_CONTAINER_OPTIONS: list[tuple[str, str]] = _EDITOR_OPTIONS["video_container_options"]
VIDEO_CODEC_OPTIONS: list[tuple[str, str]] = _EDITOR_OPTIONS["video_codec_options"]
VIDEO_PRESET_OPTIONS: list[tuple[str, str]] = _EDITOR_OPTIONS["video_preset_options"]
VIDEO_ORIENTATION_OPTIONS: list[tuple[str, str]] = _EDITOR_OPTIONS["video_orientation_options"]
VIDEO_FRAME_SIZE_OPTIONS: list[dict[str, Any]] = _EDITOR_OPTIONS["video_frame_size_options"]
VIDEO_FPS_OPTIONS: list[float] = _EDITOR_OPTIONS["video_fps_options"]
DEFAULT_VIDEO_CONTAINER: str = _EDITOR_OPTIONS["default_video_container"]
DEFAULT_VIDEO_CODEC: str = _EDITOR_OPTIONS["default_video_codec"]
DEFAULT_VIDEO_PRESET: str = _EDITOR_OPTIONS["default_video_preset"]
DEFAULT_VIDEO_ORIENTATION: str = _EDITOR_OPTIONS["default_video_orientation"]
DEFAULT_VIDEO_FRAME_SIZE_MODE: str = _EDITOR_OPTIONS["default_video_frame_size_mode"]
DEFAULT_VIDEO_FPS: float = _EDITOR_OPTIONS["default_video_fps"]
DEFAULT_VIDEO_CRF: int = _EDITOR_OPTIONS["default_video_crf"]
DEFAULT_VIDEO_WIDTH: int = _EDITOR_OPTIONS["default_video_width"]
DEFAULT_VIDEO_HEIGHT: int = _EDITOR_OPTIONS["default_video_height"]
COLOR_PRESETS: list[tuple[str, str]] = _EDITOR_OPTIONS["color_presets"]
DEFAULT_FIELD_TAG: str = _EDITOR_OPTIONS["default_field_tag"]
TAG_OPTIONS: list[tuple[str, str]] = _EDITOR_OPTIONS["tag_options"]
SAMPLE_RAW_METADATA: dict[str, Any] = _EDITOR_OPTIONS["sample_raw_metadata"]
