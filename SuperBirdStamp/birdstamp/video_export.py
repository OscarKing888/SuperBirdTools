from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import math
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageColor

from app_common.log import get_logger
from birdstamp.config import get_app_dir, get_app_resource_dir, get_user_data_dir
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.gui import editor_core, editor_template, editor_utils, template_context as _template_context
from birdstamp.subprocess_utils import decode_subprocess_output

_log = get_logger("video_export")

DEFAULT_VIDEO_BACKGROUND_COLOR = "#000000"
DEFAULT_VIDEO_RENDER_WORKERS = 0
FFMPEG_ENV_VAR = "BIRDSTAMP_FFMPEG"
_PLATFORM_TOOL_SUBDIR = {
    "darwin": "macos",
    "win32": "windows",
}
_BIRD_DETECT_WARNING_EMITTED = False
_VIDEO_RENDER_CACHE_VERSION = 2
_VIDEO_RENDER_CACHE_ROOT_NAME = "birdstamp_video_cache"

_build_metadata_context = editor_utils.build_metadata_context
_safe_color = editor_utils.safe_color
_path_key = editor_utils.path_key
_parse_ratio_value = editor_core.parse_ratio_value
_is_ratio_free = editor_core.is_ratio_free
_crop_box_has_effect = editor_core.crop_box_has_effect
_crop_plan_from_override = editor_core._crop_plan_from_override
_parse_bool_value = editor_core.parse_bool_value
_parse_padding_value = editor_core.parse_padding_value
_normalize_center_mode = editor_core.normalize_center_mode
_resize_fit = editor_core.resize_fit
_pad_image = editor_core.pad_image
_crop_image_by_normalized_box = editor_core.crop_image_by_normalized_box
_compute_ratio_crop_box = editor_core.compute_ratio_crop_box
_draw_focus_box_overlay = editor_core.draw_focus_box_overlay
_expand_unit_box_to_unclamped_pixels = editor_core.expand_unit_box_to_unclamped_pixels
_normalize_unit_box = editor_core.normalize_unit_box
_box_center = editor_core.box_center
_get_focus_point_for_display = editor_core.get_focus_point_for_display
_resolve_focus_box_after_processing = editor_core.resolve_focus_box_after_processing
_resolve_focus_camera_type_from_metadata = editor_core.resolve_focus_camera_type_from_metadata
_detect_primary_bird_box = editor_core.detect_primary_bird_box
_get_bird_detector_error_message = editor_core.get_bird_detector_error_message
_CENTER_MODE_IMAGE = editor_core.CENTER_MODE_IMAGE
_CENTER_MODE_FOCUS = editor_core.CENTER_MODE_FOCUS
_CENTER_MODE_BIRD = editor_core.CENTER_MODE_BIRD
_CENTER_MODE_CUSTOM = editor_core.CENTER_MODE_CUSTOM
_DEFAULT_CROP_PADDING_PX = editor_core.DEFAULT_CROP_PADDING_PX
_DEFAULT_TEMPLATE_CENTER_MODE = editor_template.DEFAULT_TEMPLATE_CENTER_MODE
_DEFAULT_TEMPLATE_MAX_LONG_EDGE = editor_template.DEFAULT_TEMPLATE_MAX_LONG_EDGE
_default_template_payload = editor_template.default_template_payload
_normalize_template_payload = editor_template.normalize_template_payload
_deep_copy_payload = editor_template.deep_copy_payload
_load_template_payload = editor_template.load_template_payload
_render_template_overlay = editor_template.render_template_overlay


@dataclass(slots=True)
class VideoFrameJob:
    """单帧渲染所需的最小快照。"""

    path: Path
    settings: dict[str, Any]
    raw_metadata: dict[str, Any]
    metadata_context: dict[str, str]
    photo_info: _template_context.PhotoInfo | None = None
    source_image: Image.Image | None = None


@dataclass(slots=True)
class VideoExportOptions:
    """视频编码参数。"""

    output_path: Path
    container: str = "mp4"
    codec: str = "h264"
    fps: float = 25.0
    preset: str = "medium"
    crf: int = 20
    frame_size_mode: str = "auto"
    frame_width: int = 0
    frame_height: int = 0
    background_color: str = DEFAULT_VIDEO_BACKGROUND_COLOR
    render_workers: int = DEFAULT_VIDEO_RENDER_WORKERS
    overwrite: bool = True
    preserve_temp_files: bool = True

    def normalized_output_path(self) -> Path:
        container = str(self.container or "mp4").strip().lower().lstrip(".")
        output = self.output_path.resolve(strict=False)
        if output.suffix.lower() != f".{container}":
            output = output.with_suffix(f".{container}")
        return output


@dataclass(slots=True)
class VideoExportProgress:
    """导出进度通知。"""

    phase: str
    current: int
    total: int
    message: str


VideoExportProgressCallback = Callable[[VideoExportProgress], None]


class VideoExportCancelledError(RuntimeError):
    """视频导出已被用户中断。"""

    def __init__(
        self,
        message: str,
        *,
        preserved_frames_dir: Path | None = None,
        partial_output_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.preserved_frames_dir = preserved_frames_dir
        self.partial_output_path = partial_output_path


def _platform_tool_subdir() -> str:
    return _PLATFORM_TOOL_SUBDIR.get(sys.platform, sys.platform)


def preferred_ffmpeg_tool_dir() -> Path:
    base_dir = get_user_data_dir() if getattr(sys, "frozen", False) else get_app_dir()
    return base_dir / "tools" / "ffmpeg" / _platform_tool_subdir()


def preferred_ffmpeg_binary_path() -> Path:
    return preferred_ffmpeg_tool_dir() / _ffmpeg_executable_name()


def ffmpeg_install_script_path() -> Path | None:
    candidates = [
        get_app_resource_dir() / "scripts_dev" / "install_ffmpeg_tool.py",
        get_app_dir() / "scripts_dev" / "install_ffmpeg_tool.py",
        Path(__file__).resolve().parent.parent / "scripts_dev" / "install_ffmpeg_tool.py",
        Path.cwd() / "scripts_dev" / "install_ffmpeg_tool.py",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            resolved = candidate
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        try:
            if resolved.is_file():
                return resolved
        except Exception:
            continue
    return None


def _ffmpeg_executable_name() -> str:
    return "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"


def _iter_ffmpeg_candidates() -> list[Path]:
    exe_name = _ffmpeg_executable_name()
    roots = [
        get_app_resource_dir(),
        get_app_dir(),
        get_user_data_dir(),
    ]
    rel_paths = [
        Path("tools") / "ffmpeg" / _platform_tool_subdir() / exe_name,
        Path("tools") / "ffmpeg" / exe_name,
        Path("tools") / exe_name,
    ]

    candidates: list[Path] = []
    seen: set[str] = set()

    env_value = str(os.environ.get(FFMPEG_ENV_VAR) or "").strip()
    if env_value:
        env_path = Path(env_value).expanduser()
        try:
            env_resolved = env_path.resolve(strict=False)
        except Exception:
            env_resolved = env_path
        key = str(env_resolved)
        if key not in seen:
            seen.add(key)
            candidates.append(env_resolved)

    for root in roots:
        for rel_path in rel_paths:
            candidate = root / rel_path
            try:
                resolved = candidate.resolve(strict=False)
            except Exception:
                resolved = candidate
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(resolved)

    which_path = shutil.which("ffmpeg")
    if which_path:
        candidate = Path(which_path)
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            resolved = candidate
        key = str(resolved)
        if key not in seen:
            candidates.append(resolved)
    return candidates


def find_ffmpeg_executable() -> Path | None:
    for candidate in _iter_ffmpeg_candidates():
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None


def _crf_range_for_codec(codec: str) -> tuple[int, int]:
    if codec == "h265":
        return (0, 51)
    return (0, 51)


def validate_video_export_options(options: VideoExportOptions) -> VideoExportOptions:
    container = str(options.container or "mp4").strip().lower().lstrip(".")
    if container not in {"mp4", "mov"}:
        raise ValueError(f"不支持的视频容器: {container}")

    codec = str(options.codec or "h264").strip().lower()
    if codec not in {"h264", "h265"}:
        raise ValueError(f"不支持的视频编码器: {codec}")

    try:
        fps = float(options.fps)
    except Exception as exc:
        raise ValueError("FPS 必须为数字。") from exc
    if fps <= 0:
        raise ValueError("FPS 必须大于 0。")

    preset = str(options.preset or "medium").strip().lower() or "medium"
    if not preset:
        raise ValueError("编码 preset 不能为空。")

    try:
        crf = int(options.crf)
    except Exception as exc:
        raise ValueError("CRF 必须为整数。") from exc
    crf_min, crf_max = _crf_range_for_codec(codec)
    if crf < crf_min or crf > crf_max:
        raise ValueError(f"CRF 超出范围: {crf}（允许 {crf_min}-{crf_max}）")

    mode = str(options.frame_size_mode or "auto").strip().lower() or "auto"
    width = 0
    height = 0
    if mode != "auto":
        try:
            width = int(options.frame_width)
            height = int(options.frame_height)
        except Exception as exc:
            raise ValueError("视频尺寸必须为整数。") from exc
        if width <= 0 or height <= 0:
            raise ValueError("视频尺寸必须大于 0。")

    try:
        render_workers = int(options.render_workers)
    except Exception as exc:
        raise ValueError("渲染线程数必须为整数。") from exc
    if render_workers < 0:
        raise ValueError("渲染线程数不能小于 0。")

    return VideoExportOptions(
        output_path=options.output_path,
        container=container,
        codec=codec,
        fps=fps,
        preset=preset,
        crf=crf,
        frame_size_mode=mode,
        frame_width=width,
        frame_height=height,
        background_color=_safe_color(str(options.background_color or DEFAULT_VIDEO_BACKGROUND_COLOR), DEFAULT_VIDEO_BACKGROUND_COLOR),
        render_workers=render_workers,
        overwrite=bool(options.overwrite),
        preserve_temp_files=bool(options.preserve_temp_files),
    )


def _emit_progress(
    callback: VideoExportProgressCallback | None,
    *,
    phase: str,
    current: int,
    total: int,
    message: str,
) -> None:
    if callback is None:
        return
    callback(VideoExportProgress(phase=phase, current=current, total=total, message=message))


def _is_cancel_requested(cancel_event: threading.Event | None) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def _raise_if_cancel_requested(
    cancel_event: threading.Event | None,
    *,
    message: str = "视频导出已中断。",
) -> None:
    if _is_cancel_requested(cancel_event):
        raise VideoExportCancelledError(message)


def _source_signature(path: Path) -> str:
    try:
        stat = path.stat()
        return f"{_path_key(path)}:{stat.st_size}:{stat.st_mtime_ns}"
    except Exception:
        return _path_key(path)


def _json_dumps_stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _render_cache_key(jobs: list[VideoFrameJob], options: VideoExportOptions) -> str:
    """仅包含影响帧图像内容的参数，编码/FPS 变化不会切换缓存。"""
    validated = validate_video_export_options(options)
    payload = {
        "version": _VIDEO_RENDER_CACHE_VERSION,
        "frame_size_mode": validated.frame_size_mode,
        "frame_width": int(validated.frame_width),
        "frame_height": int(validated.frame_height),
        "background_color": str(validated.background_color or DEFAULT_VIDEO_BACKGROUND_COLOR),
        "jobs": [
            {
                "path": str(job.path.resolve(strict=False)),
                "source_signature": _source_signature(job.path),
                "settings": _clone_render_settings(job.settings),
            }
            for job in jobs
        ],
    }
    return hashlib.sha1(_json_dumps_stable(payload).encode("utf-8")).hexdigest()


def _sanitize_video_work_name(text: str, *, max_length: int = 64) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(text or "").strip())
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip("_-")
    return sanitized or "video"


def _clone_render_settings(settings: dict[str, Any]) -> dict[str, Any]:
    template_name = str(settings.get("template_name") or "default").strip() or "default"
    template_payload_raw = settings.get("template_payload")
    if isinstance(template_payload_raw, dict):
        template_payload = _normalize_template_payload(template_payload_raw, fallback_name=template_name)
    else:
        template_payload = _default_template_payload(name=template_name)

    ratio = _parse_ratio_value(settings.get("ratio"))
    try:
        max_long_edge = int(settings.get("max_long_edge", _DEFAULT_TEMPLATE_MAX_LONG_EDGE) or 0)
    except Exception:
        max_long_edge = _DEFAULT_TEMPLATE_MAX_LONG_EDGE
    max_long_edge = max(0, max_long_edge)

    custom_center_x = settings.get("custom_center_x")
    custom_center_y = settings.get("custom_center_y")
    crop_box: list[float] | None = None
    crop_box_raw = settings.get("crop_box")
    if isinstance(crop_box_raw, (list, tuple)) and len(crop_box_raw) == 4:
        try:
            normalized_crop_box = _normalize_unit_box(
                (
                    float(crop_box_raw[0]),
                    float(crop_box_raw[1]),
                    float(crop_box_raw[2]),
                    float(crop_box_raw[3]),
                )
            )
            if _crop_box_has_effect(normalized_crop_box):
                crop_box = [float(value) for value in normalized_crop_box]
        except Exception:
            crop_box = None
    return {
        "template_name": template_name,
        "template_payload": _deep_copy_payload(template_payload),
        "draw_banner": _parse_bool_value(settings.get("draw_banner"), True),
        "draw_text": _parse_bool_value(settings.get("draw_text"), True),
        "draw_focus": _parse_bool_value(settings.get("draw_focus"), False),
        "ratio": ratio,
        "center_mode": _normalize_center_mode(settings.get("center_mode") or _DEFAULT_TEMPLATE_CENTER_MODE),
        "max_long_edge": max_long_edge,
        "crop_padding_top": _parse_padding_value(settings.get("crop_padding_top"), _DEFAULT_CROP_PADDING_PX),
        "crop_padding_bottom": _parse_padding_value(settings.get("crop_padding_bottom"), _DEFAULT_CROP_PADDING_PX),
        "crop_padding_left": _parse_padding_value(settings.get("crop_padding_left"), _DEFAULT_CROP_PADDING_PX),
        "crop_padding_right": _parse_padding_value(settings.get("crop_padding_right"), _DEFAULT_CROP_PADDING_PX),
        "crop_padding_fill": _safe_color(
            str(settings.get("crop_padding_fill") or "#FFFFFF"),
            "#FFFFFF",
        ),
        "crop_box": crop_box,
        "custom_center_x": float(custom_center_x) if custom_center_x is not None else None,
        "custom_center_y": float(custom_center_y) if custom_center_y is not None else None,
    }


def _should_draw_template_overlay(settings: dict[str, Any]) -> bool:
    return _parse_bool_value(settings.get("draw_banner"), True) or _parse_bool_value(settings.get("draw_text"), True)


def _resolve_template_payload_for_render(
    settings: dict[str, Any],
    template_paths: dict[str, Path] | None,
) -> dict[str, Any]:
    template_name = str(settings.get("template_name") or "default").strip() or "default"
    payload_raw = settings.get("template_payload")
    if isinstance(payload_raw, dict):
        payload = _normalize_template_payload(payload_raw, fallback_name=template_name)
    else:
        payload = _default_template_payload(name=template_name)

    if not isinstance(template_paths, dict):
        return payload
    template_path = template_paths.get(template_name)
    if template_path and template_path.is_file():
        try:
            return _load_template_payload(template_path)
        except Exception as exc:
            _log.warning("template reload failed: name=%s path=%s err=%s", template_name, template_path, exc)
    return payload


def _resolve_bird_box_for_image(
    path: Path | None,
    image: Image.Image,
    bird_box_cache: dict[str, tuple[float, float, float, float] | None],
    bird_box_lock: threading.Lock | None = None,
) -> tuple[float, float, float, float] | None:
    global _BIRD_DETECT_WARNING_EMITTED

    if path is None:
        return None

    signature = _source_signature(path)
    if bird_box_lock is None:
        if signature in bird_box_cache:
            return bird_box_cache[signature]
        bird_box = _detect_primary_bird_box(image)
        bird_box_cache[signature] = bird_box
        if bird_box is None and not _BIRD_DETECT_WARNING_EMITTED:
            message = _get_bird_detector_error_message()
            if message:
                _log.warning("bird detect unavailable during video export: %s", message)
                _BIRD_DETECT_WARNING_EMITTED = True
        return bird_box

    with bird_box_lock:
        if signature in bird_box_cache:
            return bird_box_cache[signature]
        bird_box = _detect_primary_bird_box(image)
        bird_box_cache[signature] = bird_box
        if bird_box is None and not _BIRD_DETECT_WARNING_EMITTED:
            message = _get_bird_detector_error_message()
            if message:
                _log.warning("bird detect unavailable during video export: %s", message)
                _BIRD_DETECT_WARNING_EMITTED = True
    return bird_box


def _resolve_crop_anchor_and_keep_box(
    *,
    path: Path | None,
    image: Image.Image,
    raw_metadata: dict[str, Any],
    center_mode: str,
    bird_box_cache: dict[str, tuple[float, float, float, float] | None],
    bird_box_lock: threading.Lock | None = None,
    custom_center: tuple[float, float] | None = None,
) -> tuple[tuple[float, float], tuple[float, float, float, float] | None]:
    focus_camera_type = _resolve_focus_camera_type_from_metadata(raw_metadata)
    focus_point = _get_focus_point_for_display(
        raw_metadata,
        image.width,
        image.height,
        camera_type=focus_camera_type,
    )
    mode = _normalize_center_mode(center_mode)
    if mode == _CENTER_MODE_CUSTOM and custom_center is not None:
        return (custom_center, None)
    bird_box: tuple[float, float, float, float] | None = None
    if mode in {_CENTER_MODE_BIRD, _CENTER_MODE_FOCUS}:
        bird_box = _resolve_bird_box_for_image(path, image, bird_box_cache, bird_box_lock)

    if mode == _CENTER_MODE_BIRD:
        if bird_box is not None:
            return (_box_center(bird_box), bird_box)
        if focus_point is not None:
            return (focus_point, None)
        return ((0.5, 0.5), None)

    if mode == _CENTER_MODE_FOCUS:
        if focus_point is not None:
            return (focus_point, None)
        if bird_box is not None:
            return (_box_center(bird_box), None)
        return ((0.5, 0.5), None)

    return ((0.5, 0.5), None)


def _compute_auto_bird_crop_plan(
    *,
    image: Image.Image,
    bird_box: tuple[float, float, float, float],
    ratio: float,
    inner_top: int,
    inner_bottom: int,
    inner_left: int,
    inner_right: int,
) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]]:
    width, height = image.size
    if width <= 0 or height <= 0 or ratio <= 0:
        return (None, (0, 0, 0, 0))

    expanded_px = _expand_unit_box_to_unclamped_pixels(
        bird_box,
        width=width,
        height=height,
        top=inner_top,
        bottom=inner_bottom,
        left=inner_left,
        right=inner_right,
    )
    if expanded_px is None:
        return (None, (0, 0, 0, 0))

    keep_left, keep_top, keep_right, keep_bottom = expanded_px
    keep_width = max(1.0, keep_right - keep_left)
    keep_height = max(1.0, keep_bottom - keep_top)
    center_x = (keep_left + keep_right) * 0.5
    center_y = (keep_top + keep_bottom) * 0.5

    crop_width = keep_width
    crop_height = crop_width / ratio
    if crop_height < keep_height:
        crop_height = keep_height
        crop_width = crop_height * ratio

    crop_left = center_x - (crop_width * 0.5)
    crop_top = center_y - (crop_height * 0.5)
    crop_right = crop_left + crop_width
    crop_bottom = crop_top + crop_height

    outer_left = max(0, int(math.ceil(max(0.0, -crop_left))))
    outer_top = max(0, int(math.ceil(max(0.0, -crop_top))))
    outer_right = max(0, int(math.ceil(max(0.0, crop_right - width))))
    outer_bottom = max(0, int(math.ceil(max(0.0, crop_bottom - height))))

    padded_width = width + outer_left + outer_right
    padded_height = height + outer_top + outer_bottom
    if padded_width <= 0 or padded_height <= 0:
        return (None, (0, 0, 0, 0))

    crop_box = _normalize_unit_box(
        (
            (crop_left + outer_left) / float(padded_width),
            (crop_top + outer_top) / float(padded_height),
            (crop_right + outer_left) / float(padded_width),
            (crop_bottom + outer_top) / float(padded_height),
        )
    )
    return (crop_box, (outer_top, outer_bottom, outer_left, outer_right))


def _compute_crop_plan_for_image(
    *,
    path: Path | None,
    image: Image.Image,
    raw_metadata: dict[str, Any],
    settings: dict[str, Any],
    bird_box_cache: dict[str, tuple[float, float, float, float] | None],
    bird_box_lock: threading.Lock | None = None,
) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]]:
    ratio = _parse_ratio_value(settings.get("ratio"))
    crop_box_raw = settings.get("crop_box")
    if crop_box_raw is not None and isinstance(crop_box_raw, (list, tuple)) and len(crop_box_raw) == 4:
        try:
            cb = (float(crop_box_raw[0]), float(crop_box_raw[1]), float(crop_box_raw[2]), float(crop_box_raw[3]))
            if _crop_box_has_effect(cb):
                return _crop_plan_from_override(image.width, image.height, cb)
        except (TypeError, ValueError):
            pass
    if ratio is None or _is_ratio_free(ratio):
        return (None, (0, 0, 0, 0))

    custom_center: tuple[float, float] | None = None
    try:
        cx = float(settings.get("custom_center_x")) if "custom_center_x" in settings else None
        cy = float(settings.get("custom_center_y")) if "custom_center_y" in settings else None
        if cx is not None and cy is not None:
            custom_center = (cx, cy)
    except Exception:
        custom_center = None

    anchor, keep_box = _resolve_crop_anchor_and_keep_box(
        path=path,
        image=image,
        raw_metadata=raw_metadata,
        center_mode=str(settings.get("center_mode") or _CENTER_MODE_IMAGE),
        bird_box_cache=bird_box_cache,
        bird_box_lock=bird_box_lock,
        custom_center=custom_center,
    )
    if keep_box is not None:
        crop_box, outer_pad = _compute_auto_bird_crop_plan(
            image=image,
            bird_box=keep_box,
            ratio=ratio,
            inner_top=_parse_padding_value(settings.get("crop_padding_top"), 0),
            inner_bottom=_parse_padding_value(settings.get("crop_padding_bottom"), 0),
            inner_left=_parse_padding_value(settings.get("crop_padding_left"), 0),
            inner_right=_parse_padding_value(settings.get("crop_padding_right"), 0),
        )
        if crop_box is not None:
            return (crop_box, outer_pad)

    crop_box = _compute_ratio_crop_box(
        width=image.width,
        height=image.height,
        ratio=ratio,
        anchor=anchor,
        keep_box=None,
    )
    return (crop_box, (0, 0, 0, 0))


def _build_processed_image(
    image: Image.Image,
    raw_metadata: dict[str, Any],
    *,
    settings: dict[str, Any],
    source_path: Path | None,
    bird_box_cache: dict[str, tuple[float, float, float, float] | None],
    bird_box_lock: threading.Lock | None = None,
    crop_plan: tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]] | None = None,
) -> Image.Image:
    if crop_plan is None:
        crop_box, outer_pad = _compute_crop_plan_for_image(
            path=source_path,
            image=image,
            raw_metadata=raw_metadata,
            settings=settings,
            bird_box_cache=bird_box_cache,
            bird_box_lock=bird_box_lock,
        )
    else:
        crop_box, outer_pad = crop_plan
    top, bottom, left, right = outer_pad
    if top or bottom or left or right:
        fill = str(settings.get("crop_padding_fill") or "#FFFFFF").strip() or "#FFFFFF"
        image = _pad_image(image, top=top, bottom=bottom, left=left, right=right, fill=fill)

    image = _crop_image_by_normalized_box(image, crop_box)
    image = _resize_fit(image, max(0, int(settings.get("max_long_edge") or 0)))
    return image


def render_video_frame(
    job: VideoFrameJob,
    *,
    template_paths: dict[str, Path] | None = None,
    bird_box_cache: dict[str, tuple[float, float, float, float] | None] | None = None,
    bird_box_lock: threading.Lock | None = None,
) -> Image.Image:
    cache = bird_box_cache if isinstance(bird_box_cache, dict) else {}
    settings = _clone_render_settings(job.settings)
    raw_metadata = dict(job.raw_metadata or {})

    if job.source_image is not None:
        image = job.source_image.copy()
    else:
        image = decode_image(job.path, decoder="auto")

    crop_box, outer_pad = _compute_crop_plan_for_image(
        path=job.path,
        image=image,
        raw_metadata=raw_metadata,
        settings=settings,
        bird_box_cache=cache,
        bird_box_lock=bird_box_lock,
    )
    processed = _build_processed_image(
        image,
        raw_metadata,
        settings=settings,
        source_path=job.path,
        bird_box_cache=cache,
        bird_box_lock=bird_box_lock,
        crop_plan=(crop_box, outer_pad),
    )
    rendered: Image.Image
    if _should_draw_template_overlay(settings):
        template_payload = _resolve_template_payload_for_render(settings, template_paths)
        photo_info = _template_context.ensure_photo_info(job.photo_info or job.path, raw_metadata=raw_metadata)
        metadata_context = dict(job.metadata_context or {}) or _build_metadata_context(photo_info, raw_metadata)
        rendered = _render_template_overlay(
            processed,
            raw_metadata=raw_metadata,
            metadata_context=metadata_context,
            photo_info=photo_info,
            template_payload=template_payload,
            draw_banner=_parse_bool_value(settings.get("draw_banner"), True),
            draw_text=_parse_bool_value(settings.get("draw_text"), True),
        )
    else:
        rendered = processed.convert("RGB")

    if _parse_bool_value(settings.get("draw_focus"), False):
        focus_box = _resolve_focus_box_after_processing(
            raw_metadata,
            source_width=image.width,
            source_height=image.height,
            crop_box=crop_box,
            outer_pad=outer_pad,
            apply_ratio_crop=True,
            camera_type=_resolve_focus_camera_type_from_metadata(raw_metadata),
        )
        if focus_box is not None:
            rendered = _draw_focus_box_overlay(rendered, focus_box)
    return rendered.convert("RGB")


def _ensure_even_size(width: int, height: int) -> tuple[int, int]:
    even_width = max(2, width if width % 2 == 0 else width + 1)
    even_height = max(2, height if height % 2 == 0 else height + 1)
    return (even_width, even_height)


def resolve_target_frame_size(options: VideoExportOptions, first_frame_size: tuple[int, int]) -> tuple[int, int]:
    validated = validate_video_export_options(options)
    if validated.frame_size_mode == "auto":
        width, height = first_frame_size
    else:
        width, height = validated.frame_width, validated.frame_height
    return _ensure_even_size(int(width), int(height))


def normalize_frame_size(
    image: Image.Image,
    target_size: tuple[int, int],
    *,
    background_color: str = DEFAULT_VIDEO_BACKGROUND_COLOR,
) -> Image.Image:
    target_width, target_height = _ensure_even_size(int(target_size[0]), int(target_size[1]))
    frame = image.convert("RGB")
    if frame.width == target_width and frame.height == target_height:
        return frame

    scale = min(target_width / float(frame.width), target_height / float(frame.height))
    resized_width = max(1, min(target_width, int(round(frame.width * scale))))
    resized_height = max(1, min(target_height, int(round(frame.height * scale))))
    if (resized_width, resized_height) != frame.size:
        frame = frame.resize((resized_width, resized_height), Image.Resampling.LANCZOS)

    background = Image.new(
        "RGB",
        (target_width, target_height),
        ImageColor.getrgb(_safe_color(background_color, DEFAULT_VIDEO_BACKGROUND_COLOR)),
    )
    offset_x = max(0, (target_width - frame.width) // 2)
    offset_y = max(0, (target_height - frame.height) // 2)
    background.paste(frame, (offset_x, offset_y))
    return background


def _ffmpeg_fps_text(fps: float) -> str:
    text = f"{fps:.6f}".rstrip("0").rstrip(".")
    return text or "25"


def _recommended_auto_render_workers(
    *,
    physical_cpu_count: int | None,
    logical_cpu_count: int | None,
) -> int:
    """自动渲染线程数。

    优先按物理核心估算为 `核心数 * 2 - 4`，给系统/前台交互预留余量；
    如果拿不到物理核心数，则退回到逻辑核心数减 4。
    """
    if physical_cpu_count is not None:
        try:
            physical = max(1, int(physical_cpu_count))
        except Exception:
            physical = 1
        return max(1, physical * 2 - 4)

    try:
        logical = max(1, int(logical_cpu_count or 1))
    except Exception:
        logical = 1
    return max(1, logical - 4)


@lru_cache(maxsize=1)
def _detect_physical_cpu_count() -> int | None:
    try:
        import psutil  # optional dependency

        detected = psutil.cpu_count(logical=False)
        if detected is not None and int(detected) > 0:
            return int(detected)
    except Exception:
        pass

    if sys.platform == "darwin":
        try:
            output = subprocess.check_output(
                ["sysctl", "-n", "hw.physicalcpu"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            detected = int(output)
            if detected > 0:
                return detected
        except Exception:
            return None

    return None


def resolve_video_render_workers(render_workers: int, pending_jobs: int) -> int:
    if pending_jobs <= 0:
        return 1
    requested = max(0, int(render_workers))
    if requested > 0:
        return max(1, min(requested, pending_jobs))

    auto_workers = _recommended_auto_render_workers(
        physical_cpu_count=_detect_physical_cpu_count(),
        logical_cpu_count=os.cpu_count(),
    )
    return max(1, min(auto_workers, pending_jobs))


def _save_normalized_temp_frame(
    image: Image.Image,
    frame_path: Path,
    target_size: tuple[int, int],
    *,
    background_color: str,
) -> None:
    normalized = normalize_frame_size(
        image,
        target_size,
        background_color=background_color,
    )
    try:
        # 临时中间帧优先追求速度，不做 optimize 压缩。
        normalized.save(frame_path, format="PNG", compress_level=1)
    finally:
        try:
            normalized.close()
        except Exception:
            pass


def _render_and_save_video_frame(
    *,
    job: VideoFrameJob,
    index: int,
    frames_dir: Path,
    target_size: tuple[int, int],
    background_color: str,
    template_paths: dict[str, Path] | None,
    bird_box_cache: dict[str, tuple[float, float, float, float] | None],
    bird_box_lock: threading.Lock | None,
    cancel_event: threading.Event | None,
) -> tuple[int, str]:
    _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在保留已完成帧。")
    rendered = render_video_frame(
        job,
        template_paths=template_paths,
        bird_box_cache=bird_box_cache,
        bird_box_lock=bird_box_lock,
    )
    try:
        _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在保留已完成帧。")
        frame_path = frames_dir / f"frame_{index:06d}.png"
        _save_normalized_temp_frame(
            rendered,
            frame_path,
            target_size,
            background_color=background_color,
        )
    finally:
        try:
            rendered.close()
        except Exception:
            pass
    return (index, job.path.name)


def build_ffmpeg_command(
    ffmpeg_path: Path,
    frames_dir: Path,
    options: VideoExportOptions,
    *,
    output_path: Path | None = None,
) -> list[str]:
    validated = validate_video_export_options(options)
    fps_text = _ffmpeg_fps_text(validated.fps)
    input_pattern = str(frames_dir / "frame_%06d.png")
    resolved_output_path = str((output_path or validated.normalized_output_path()).resolve(strict=False))

    cmd = [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if validated.overwrite else "-n",
        "-framerate",
        fps_text,
        "-i",
        input_pattern,
        *_codec_args_for_options(validated),
    ]
    if validated.container == "mp4":
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(resolved_output_path)
    return cmd


def _codec_args_for_options(options: VideoExportOptions) -> list[str]:
    validated = validate_video_export_options(options)
    if validated.codec == "h265":
        return [
            "-c:v",
            "libx265",
            "-preset",
            validated.preset,
            "-crf",
            str(validated.crf),
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "hvc1",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        validated.preset,
        "-crf",
        str(validated.crf),
        "-pix_fmt",
        "yuv420p",
    ]


def _subprocess_popen_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if sys.platform.startswith("win"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def _persistent_video_cache_root(output_path: Path) -> Path:
    parent_dir = output_path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_video_work_name(output_path.stem.strip() or "video", max_length=48)
    return parent_dir / _VIDEO_RENDER_CACHE_ROOT_NAME / safe_name


def _persistent_video_work_dir(output_path: Path, *, cache_key: str) -> Path:
    cache_root = _persistent_video_cache_root(output_path)
    cache_tag = str(cache_key or "").strip().lower() or "default"
    return cache_root / f"render_{cache_tag}"


def _create_video_work_dir(output_path: Path, *, preserve_temp_files: bool, cache_key: str = "") -> Path:
    parent_dir = output_path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    if preserve_temp_files:
        return _persistent_video_work_dir(output_path, cache_key=cache_key)
    stem = output_path.stem.strip() or "video"
    safe_stem = _sanitize_video_work_name(stem)
    work_dir_text = tempfile.mkdtemp(prefix=f"{safe_stem}__birdstamp_video_work_", dir=str(parent_dir))
    return Path(work_dir_text)


def _render_manifest_path(work_dir: Path) -> Path:
    return work_dir / "render_manifest.json"


def _write_render_manifest(work_dir: Path, *, cache_key: str, target_size: tuple[int, int], total: int) -> None:
    payload = {
        "version": _VIDEO_RENDER_CACHE_VERSION,
        "cache_key": str(cache_key or "").strip(),
        "target_size": [int(target_size[0]), int(target_size[1])],
        "total": max(0, int(total)),
    }
    _render_manifest_path(work_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_render_manifest(work_dir: Path) -> dict[str, Any] | None:
    manifest_path = _render_manifest_path(work_dir)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _manifest_target_size(manifest: dict[str, Any] | None) -> tuple[int, int] | None:
    if not isinstance(manifest, dict):
        return None
    raw_size = manifest.get("target_size")
    if not isinstance(raw_size, (list, tuple)) or len(raw_size) != 2:
        return None
    try:
        width = int(raw_size[0])
        height = int(raw_size[1])
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return _ensure_even_size(width, height)


def _render_cache_is_reusable(
    manifest: dict[str, Any] | None,
    *,
    cache_key: str,
    total: int,
) -> bool:
    if not isinstance(manifest, dict):
        return False
    if int(manifest.get("version") or 0) != _VIDEO_RENDER_CACHE_VERSION:
        return False
    if str(manifest.get("cache_key") or "").strip() != str(cache_key or "").strip():
        return False
    if int(manifest.get("total") or -1) != int(total):
        return False
    return _manifest_target_size(manifest) is not None


def _existing_rendered_frame_indices(frames_dir: Path, expected_total: int) -> set[int]:
    indices: set[int] = set()
    if expected_total <= 0 or not frames_dir.is_dir():
        return indices
    for frame_path in frames_dir.glob("frame_*.png"):
        if not frame_path.is_file():
            continue
        stem = frame_path.stem
        _, _, suffix = stem.partition("_")
        try:
            index = int(suffix)
        except Exception:
            continue
        if 1 <= index <= int(expected_total):
            indices.add(index)
    return indices


def _list_rendered_frame_paths(frames_dir: Path) -> list[Path]:
    frame_paths = [path for path in frames_dir.glob("frame_*.png") if path.is_file()]
    return sorted(frame_paths, key=lambda path: path.name)


def _count_contiguous_rendered_frames(frames_dir: Path, expected_total: int) -> int:
    count = 0
    for index in range(1, max(0, int(expected_total)) + 1):
        frame_path = frames_dir / f"frame_{index:06d}.png"
        if not frame_path.is_file():
            break
        count += 1
    return count


def _partial_video_output_path(output_path: Path, frame_count: int) -> Path:
    suffix = output_path.suffix or ".mp4"
    stem = output_path.stem.strip() or "video"
    return output_path.with_name(f"{stem}__partial_{max(0, int(frame_count)):06d}{suffix}")


def _cleanup_incomplete_output(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except Exception:
        _log.debug("cleanup incomplete video output failed: %s", path, exc_info=True)


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2.0)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=2.0)
        except Exception:
            _log.debug("terminate ffmpeg process failed", exc_info=True)


def _run_ffmpeg_command(
    cmd: list[str],
    *,
    cancel_event: threading.Event | None = None,
    cancel_message: str = "视频编码已中断，正在保留已完成帧。",
) -> None:
    process = subprocess.Popen(cmd, **_subprocess_popen_kwargs())
    stdout_data = b""
    stderr_data = b""
    try:
        while True:
            if _is_cancel_requested(cancel_event):
                _terminate_process(process)
                stdout_data, stderr_data = process.communicate()
                raise VideoExportCancelledError(cancel_message)
            return_code = process.poll()
            if return_code is not None:
                stdout_data, stderr_data = process.communicate()
                if return_code != 0:
                    stderr_text = decode_subprocess_output(stderr_data).strip()
                    stdout_text = decode_subprocess_output(stdout_data).strip()
                    detail = stderr_text or stdout_text or f"ffmpeg exit code={return_code}"
                    raise RuntimeError(f"视频编码失败: {detail}")
                return
            time.sleep(0.2)
    except Exception:
        if process.poll() is None:
            _terminate_process(process)
            stdout_data, stderr_data = process.communicate()
        raise


def _build_partial_video_from_frames(
    ffmpeg_path: Path,
    frames_dir: Path,
    options: VideoExportOptions,
    *,
    frame_paths: list[Path],
) -> Path | None:
    if not frame_paths:
        return None

    validated = validate_video_export_options(options)
    partial_output_path = _partial_video_output_path(validated.normalized_output_path(), len(frame_paths))
    concat_path = frames_dir / "rendered_frames.ffconcat"
    frame_duration = 1.0 / max(0.001, float(validated.fps))
    duration_text = f"{frame_duration:.12f}".rstrip("0").rstrip(".") or "0.04"

    lines = ["ffconcat version 1.0"]
    for frame_path in frame_paths:
        lines.append(f"file '{frame_path.name}'")
        lines.append(f"duration {duration_text}")
    lines.append(f"file '{frame_paths[-1].name}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-safe",
        "0",
        "-f",
        "concat",
        "-i",
        str(concat_path),
        *_codec_args_for_options(validated),
    ]
    if validated.container == "mp4":
        cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(partial_output_path))
    _run_ffmpeg_command(cmd, cancel_event=None)
    return partial_output_path if partial_output_path.is_file() else None


def _ffmpeg_not_found_message() -> str:
    script_path = ffmpeg_install_script_path()
    expected_binary = preferred_ffmpeg_binary_path()
    if script_path is not None:
        return f"未找到 ffmpeg，可先运行安装脚本: {script_path}\n目标位置: {expected_binary}"
    return f"未找到 ffmpeg，请将 ffmpeg 放到: {expected_binary}\n或加入系统 PATH。"


def export_video(
    jobs: list[VideoFrameJob],
    options: VideoExportOptions,
    *,
    template_paths: dict[str, Path] | None = None,
    progress_callback: VideoExportProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    validated = validate_video_export_options(options)
    if not jobs:
        raise ValueError("没有可用于生成视频的图片。")

    ffmpeg_path = find_ffmpeg_executable()
    if ffmpeg_path is None:
        raise FileNotFoundError(_ffmpeg_not_found_message())

    output_path = validated.normalized_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not validated.overwrite:
        raise FileExistsError(f"输出文件已存在: {output_path}")

    bird_box_cache: dict[str, tuple[float, float, float, float] | None] = {}
    bird_box_lock = threading.Lock()
    total = len(jobs)
    cache_key = _render_cache_key(jobs, validated)
    work_dir = _create_video_work_dir(
        output_path,
        preserve_temp_files=validated.preserve_temp_files,
        cache_key=cache_key,
    )
    reusable_manifest: dict[str, Any] | None = None
    if validated.preserve_temp_files and work_dir.exists():
        reusable_manifest = _load_render_manifest(work_dir)
        if not _render_cache_is_reusable(reusable_manifest, cache_key=cache_key, total=total):
            _cleanup_incomplete_output(work_dir / output_path.name)
            shutil.rmtree(work_dir, ignore_errors=True)
            reusable_manifest = None
    work_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = work_dir / "frames"
    temp_output_path = work_dir / output_path.name
    frames_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_incomplete_output(temp_output_path)

    try:
        _raise_if_cancel_requested(cancel_event, message="视频导出已中断，尚未开始渲染。")

        first_job = jobs[0]
        target_size = _manifest_target_size(reusable_manifest)
        existing_indices = (
            _existing_rendered_frame_indices(frames_dir, total)
            if reusable_manifest is not None and target_size is not None
            else set()
        )
        completed_count = len(existing_indices)
        if completed_count > 0:
            _emit_progress(
                progress_callback,
                phase="render",
                current=completed_count,
                total=total,
                message=f"复用已渲染帧 {completed_count}/{total} 帧。",
            )

        if 1 not in existing_indices:
            _emit_progress(
                progress_callback,
                phase="render",
                current=completed_count,
                total=total,
                message=f"正在渲染首帧 1/{total}: {first_job.path.name}",
            )
            _raise_if_cancel_requested(cancel_event, message="视频导出已中断，尚未开始渲染。")
            first_frame = render_video_frame(
                first_job,
                template_paths=template_paths,
                bird_box_cache=bird_box_cache,
                bird_box_lock=bird_box_lock,
            )
            try:
                _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在保留已完成帧。")
                if target_size is None:
                    target_size = resolve_target_frame_size(validated, first_frame.size)
                _save_normalized_temp_frame(
                    first_frame,
                    frames_dir / "frame_000001.png",
                    target_size,
                    background_color=validated.background_color,
                )
                _write_render_manifest(work_dir, cache_key=cache_key, target_size=target_size, total=total)
            finally:
                try:
                    first_frame.close()
                except Exception:
                    pass
            existing_indices.add(1)
            completed_count += 1
            _emit_progress(
                progress_callback,
                phase="render",
                current=completed_count,
                total=total,
                message=f"已渲染 {completed_count}/{total} 帧: {first_job.path.name}",
            )
        elif target_size is not None:
            _write_render_manifest(work_dir, cache_key=cache_key, target_size=target_size, total=total)

        if target_size is None:
            raise RuntimeError("无法确定视频输出帧尺寸。")

        missing_jobs = [
            (index, job)
            for index, job in enumerate(jobs, start=1)
            if index not in existing_indices
        ]
        if missing_jobs:
            render_workers = resolve_video_render_workers(validated.render_workers, len(missing_jobs))
            _log.info(
                "video export parallel render workers=%s missing_frames=%s reused=%s target_size=%sx%s",
                render_workers,
                len(missing_jobs),
                len(existing_indices),
                target_size[0],
                target_size[1],
            )
            _emit_progress(
                progress_callback,
                phase="render",
                current=completed_count,
                total=total,
                message=f"正在并行渲染剩余 {len(missing_jobs)} 帧，线程数 {render_workers}",
            )
            executor = ThreadPoolExecutor(max_workers=render_workers, thread_name_prefix="birdstamp-video-render")
            futures: dict[Any, tuple[int, str]] = {}
            try:
                for index, job in missing_jobs:
                    if index == 1:
                        continue
                    _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在停止剩余帧渲染。")
                    future = executor.submit(
                        _render_and_save_video_frame,
                        job=job,
                        index=index,
                        frames_dir=frames_dir,
                        target_size=target_size,
                        background_color=validated.background_color,
                        template_paths=template_paths,
                        bird_box_cache=bird_box_cache,
                        bird_box_lock=bird_box_lock,
                        cancel_event=cancel_event,
                    )
                    futures[future] = (index, job.path.name)

                for future in as_completed(futures):
                    try:
                        _index, frame_name = future.result()
                    except VideoExportCancelledError:
                        if cancel_event is not None:
                            cancel_event.set()
                        for pending in futures:
                            pending.cancel()
                        raise
                    completed_count += 1
                    existing_indices.add(_index)
                    _emit_progress(
                        progress_callback,
                        phase="render",
                        current=completed_count,
                        total=total,
                        message=f"已渲染 {completed_count}/{total} 帧: {frame_name}",
                    )
                    _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在停止剩余帧渲染。")
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
        else:
            _emit_progress(
                progress_callback,
                phase="render",
                current=total,
                total=total,
                message=f"已准备 {total}/{total} 帧，开始编码视频。",
            )

        _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在停止视频编码。")
        _emit_progress(
            progress_callback,
            phase="encode",
            current=total,
            total=total,
            message=f"正在编码视频: {output_path.name}",
        )
        cmd = build_ffmpeg_command(ffmpeg_path, frames_dir, validated, output_path=temp_output_path)
        _log.info("video export ffmpeg command: %s", cmd)
        _run_ffmpeg_command(cmd, cancel_event=cancel_event)

        if not temp_output_path.is_file():
            raise RuntimeError(f"视频编码完成但输出文件不存在: {temp_output_path}")

        os.replace(temp_output_path, output_path)

        _emit_progress(
            progress_callback,
            phase="done",
            current=total,
            total=total,
            message=f"视频导出完成: {output_path}",
        )
        if not validated.preserve_temp_files:
            shutil.rmtree(work_dir, ignore_errors=True)
        return output_path
    except VideoExportCancelledError:
        _cleanup_incomplete_output(temp_output_path)
        rendered_frame_paths = _list_rendered_frame_paths(frames_dir)
        contiguous_count = _count_contiguous_rendered_frames(frames_dir, total)
        partial_output_path: Path | None = None
        if rendered_frame_paths:
            _emit_progress(
                progress_callback,
                phase="cancel",
                current=len(rendered_frame_paths),
                total=total,
                message=f"正在保留已渲染帧，共 {len(rendered_frame_paths)}/{total} 帧。",
            )
            try:
                partial_output_path = _build_partial_video_from_frames(
                    ffmpeg_path,
                    frames_dir,
                    validated,
                    frame_paths=rendered_frame_paths,
                )
            except Exception as exc:
                _log.warning("build partial video after cancel failed: %s", exc, exc_info=True)

        detail_lines = [
            "视频导出已中断。",
            f"已保留工作目录: {work_dir}",
            f"已保留视频帧: {len(rendered_frame_paths)}/{total}",
        ]
        if contiguous_count != len(rendered_frame_paths):
            detail_lines.append(f"其中连续前缀帧: {contiguous_count}")
        if partial_output_path is not None:
            detail_lines.append(f"已生成部分视频: {partial_output_path}")
        else:
            detail_lines.append("未生成部分视频，可使用保留帧稍后继续合成。")
        raise VideoExportCancelledError(
            "\n".join(detail_lines),
            preserved_frames_dir=frames_dir,
            partial_output_path=partial_output_path,
        ) from None
    except Exception:
        _cleanup_incomplete_output(temp_output_path)
        if not validated.preserve_temp_files:
            shutil.rmtree(work_dir, ignore_errors=True)
        raise


__all__ = [
    "DEFAULT_VIDEO_BACKGROUND_COLOR",
    "FFMPEG_ENV_VAR",
    "VideoExportCancelledError",
    "VideoExportOptions",
    "VideoExportProgress",
    "VideoFrameJob",
    "build_ffmpeg_command",
    "export_video",
    "ffmpeg_install_script_path",
    "find_ffmpeg_executable",
    "normalize_frame_size",
    "preferred_ffmpeg_binary_path",
    "preferred_ffmpeg_tool_dir",
    "render_video_frame",
    "resolve_target_frame_size",
    "resolve_video_render_workers",
    "validate_video_export_options",
]
