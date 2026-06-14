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
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
from PIL import Image, ImageColor

from app_common.log import get_logger
from birdstamp import image_dejitter as _dejitter
from birdstamp.config import get_app_dir, get_app_resource_dir, get_user_data_dir
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.export_frame_cache import (
    SOURCE_FRAME_BUCKET_KIND,
    VIDEO_FRAME_BUCKET_KIND,
    build_source_frame_bucket_key,
    build_source_frame_signature,
    build_video_frame_bucket_key,
    build_video_frame_signature,
    create_frame_cache_plan,
    frame_output_path as _cache_frame_output_path,
    global_export_settings_from_settings,
    load_frame_manifest,
    path_signature,
    reusable_frame_path,
    stable_json_dumps as _json_dumps_stable,
    update_frame_manifest_record,
    write_frame_manifest,
)
from birdstamp.gui import editor_core, editor_template, editor_utils, template_context as _template_context
from birdstamp.image_pipeline import (
    ImageProcContext,
    ImageProcExportStage,
    ImageProcOptionChoice,
    ImageProcOptionSpec,
    ImageProcPipeline,
    ImageProcStage,
)
from birdstamp.subprocess_utils import decode_subprocess_output

from .constants import *
from .video_export_cancelled_error import VideoExportCancelledError
from .video_export_options import VideoExportOptions
from .video_export_progress import VideoExportProgress, VideoExportProgressCallback
from .video_frame_job import VideoFrameJob

_log = get_logger("export_stage")


def _export_stage_callable(name: str) -> Callable[..., Any]:
    """经包命名空间解析可调用对象，便于测试 patch ``birdstamp.export_stage`` 上的符号。"""
    from birdstamp import export_stage as _export_stage

    return getattr(_export_stage, name)











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
        Path(__file__).resolve().parent.parent.parent / "scripts_dev" / "install_ffmpeg_tool.py",
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
    return path_signature(path)


def _global_export_settings_from_jobs(jobs: list[VideoFrameJob]) -> dict[str, Any]:
    if not jobs:
        return global_export_settings_from_settings({})
    return global_export_settings_from_settings(_clone_render_settings(jobs[0].settings))


def _render_cache_key(jobs: list[VideoFrameJob], options: VideoExportOptions) -> str:
    """返回源渲染帧缓存桶 key。"""
    _ = options
    return build_source_frame_bucket_key(global_export_settings=_global_export_settings_from_jobs(jobs))


def _sanitize_video_work_name(text: str, *, max_length: int = 64) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(text or "").strip())
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip("_-")
    return sanitized or "video"


def _normalize_precomputed_crop_plan(
    crop_plan: Any,
) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]] | None:
    if not isinstance(crop_plan, (list, tuple)) or len(crop_plan) != 2:
        return None
    raw_box, raw_pad = crop_plan
    crop_box: tuple[float, float, float, float] | None = None
    if raw_box is not None:
        if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
            return None
        try:
            normalized_box = _normalize_unit_box(
                (
                    float(raw_box[0]),
                    float(raw_box[1]),
                    float(raw_box[2]),
                    float(raw_box[3]),
                )
            )
        except Exception:
            return None
        if normalized_box is None:
            return None
        crop_box = normalized_box
    if not isinstance(raw_pad, (list, tuple)) or len(raw_pad) != 4:
        return None
    try:
        outer_pad = tuple(max(0, int(value)) for value in raw_pad)
    except Exception:
        return None
    return (crop_box, (outer_pad[0], outer_pad[1], outer_pad[2], outer_pad[3]))


def _serialize_crop_plan(crop_plan: Any) -> dict[str, Any] | None:
    normalized = _normalize_precomputed_crop_plan(crop_plan)
    if normalized is None:
        return None
    crop_box, outer_pad = normalized
    return {
        "crop_box": [round(float(value), 10) for value in crop_box] if crop_box is not None else None,
        "outer_pad": [int(value) for value in outer_pad],
    }


def source_frame_signature_for_job(job: VideoFrameJob) -> str:
    render_settings = _clone_render_settings(job.settings)
    crop_payload = _serialize_crop_plan(job.crop_plan)
    if crop_payload is not None:
        render_settings["_precomputed_crop_plan"] = crop_payload
    return build_source_frame_signature(render_settings=render_settings)


def _source_frame_signature_for_job(job: VideoFrameJob) -> str:
    return source_frame_signature_for_job(job)


def _video_frame_signature_for_source(
    source_frame_path: Path,
    *,
    target_size: tuple[int, int],
    background_color: str,
) -> str:
    return build_video_frame_signature(
        source_frame_signature=path_signature(source_frame_path),
        target_size=target_size,
        background_color=_safe_color(background_color, DEFAULT_VIDEO_BACKGROUND_COLOR),
    )


def _parse_percent_setting(value: Any, default: int = 0) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = int(default)
    return max(0, min(100, parsed))


def normalize_pipeline_stage_order(value: Any) -> tuple[str, ...]:
    known = set(DEFAULT_PIPELINE_STAGE_ORDER)
    ordered: list[str] = []
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = []
    for item in raw_items:
        stage_id = str(item or "").strip()
        if stage_id in known and stage_id not in ordered:
            ordered.append(stage_id)
    if STAGE_TEMPLATE_CROP_ID in ordered:
        ordered.remove(STAGE_TEMPLATE_CROP_ID)
    ordered.insert(0, STAGE_TEMPLATE_CROP_ID)
    for stage_id in DEFAULT_PIPELINE_STAGE_ORDER:
        if stage_id not in ordered:
            ordered.append(stage_id)
    return tuple(ordered)


def normalize_export_stage_id(value: Any) -> str:
    stage_id = str(value or "").strip()
    if stage_id in {EXPORT_STAGE_PNG_ID, EXPORT_STAGE_GIF_ID, EXPORT_STAGE_VIDEO_ID}:
        return stage_id
    return DEFAULT_EXPORT_STAGE_ID


def _normalize_reference_regions(value: Any) -> list[list[float]]:
    """把去抖动参考区列表归一化为有效的 0..1 box 列表（顺序保留）。"""
    regions: list[list[float]] = []
    if not isinstance(value, (list, tuple)):
        return regions
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            continue
        try:
            box = (float(item[0]), float(item[1]), float(item[2]), float(item[3]))
        except (TypeError, ValueError):
            continue
        normalized = _normalize_unit_box(box)
        if normalized is None:
            continue
        if (normalized[2] - normalized[0]) <= 1e-4 or (normalized[3] - normalized[1]) <= 1e-4:
            continue
        regions.append([float(normalized[0]), float(normalized[1]), float(normalized[2]), float(normalized[3])])
    return regions


def _stabilization_eligible(settings: dict[str, Any]) -> bool:
    """是否可对该帧做裁切中心稳定化（固定比例、无手动裁切框覆盖）。"""
    ratio = _parse_ratio_value(settings.get("ratio"))
    if ratio is None or _is_ratio_free(ratio) or _is_ratio_no_crop(ratio):
        return False
    if _crop_box_has_effect(settings.get("crop_box")):
        return False
    return True


def dejitter_reference_active(settings: dict[str, Any] | None) -> bool:
    """是否启用了"参考区特征对齐"去抖动（策略为参考区、开关开启且存在有效参考区）。"""
    cloned = _clone_render_settings(settings if isinstance(settings, dict) else {})
    strategy = _dejitter.resolve_dejitter_strategy(cloned.get(DEJITTER_STRATEGY_KEY))
    return bool(
        strategy.requires_reference_regions
        and _parse_bool_value(cloned.get(DEJITTER_REFERENCE_ENABLED_KEY), False)
        and (cloned.get(DEJITTER_REFERENCE_REGIONS_KEY) or [])
    )


def crop_plan_precompute_required(settings: dict[str, Any] | None) -> bool:
    """统一裁切或参考区去抖动任一启用时，都需要批量预计算裁切计划。"""
    raw = settings if isinstance(settings, dict) else {}
    return _parse_bool_value(raw.get("uniform_auto_crop"), False) or dejitter_reference_active(raw)


def _extract_region_patch(
    image: Image.Image,
    box: tuple[float, float, float, float],
    patch_size: int,
) -> "np.ndarray | None":
    """从源图按归一化 box 裁取灰度 patch，缩放为 patch_size 方阵用于特征对齐。"""
    width = int(image.width)
    height = int(image.height)
    if width < 2 or height < 2:
        return None
    left = max(0, min(width - 1, int(round(float(box[0]) * width))))
    top = max(0, min(height - 1, int(round(float(box[1]) * height))))
    right = max(left + 1, min(width, int(round(float(box[2]) * width))))
    bottom = max(top + 1, min(height, int(round(float(box[3]) * height))))
    try:
        patch = image.crop((left, top, right, bottom)).convert("L")
        if patch.width < 2 or patch.height < 2:
            return None
        size = max(2, int(patch_size))
        patch = patch.resize((size, size))
        return np.asarray(patch, dtype=np.float64)
    except Exception:
        return None


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
            normalized_crop_box = _normalize_extended_unit_box(
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
    uniform_auto_crop = _parse_bool_value(settings.get("uniform_auto_crop"), False)
    dejitter_strategy = _dejitter.normalize_strategy_id(settings.get(DEJITTER_STRATEGY_KEY))
    dejitter_reference_enabled = _parse_bool_value(settings.get(DEJITTER_REFERENCE_ENABLED_KEY), False)
    reference_regions = _normalize_reference_regions(settings.get(DEJITTER_REFERENCE_REGIONS_KEY))
    reference_active = dejitter_reference_enabled and bool(reference_regions)
    reference_source_raw = settings.get(DEJITTER_REFERENCE_SOURCE_KEY)
    reference_source = (
        str(reference_source_raw).strip()
        if reference_active and reference_source_raw
        else None
    )
    return {
        "template_name": template_name,
        "template_payload": _deep_copy_payload(template_payload),
        "draw_banner": _parse_bool_value(settings.get("draw_banner"), True),
        "draw_text": _parse_bool_value(settings.get("draw_text"), True),
        "draw_focus": _parse_bool_value(settings.get("draw_focus"), False),
        STAGE_TEMPLATE_CROP_ENABLED_KEY: _resolve_stage_enabled(
            settings,
            stage_id=STAGE_TEMPLATE_CROP_ID,
            enabled_key=STAGE_TEMPLATE_CROP_ENABLED_KEY,
        ),
        STAGE_RESIZE_LIMIT_ENABLED_KEY: _resolve_stage_enabled(
            settings,
            stage_id=STAGE_RESIZE_LIMIT_ID,
            enabled_key=STAGE_RESIZE_LIMIT_ENABLED_KEY,
        ),
        STAGE_TEMPLATE_OVERLAY_ENABLED_KEY: _resolve_stage_enabled(
            settings,
            stage_id=STAGE_TEMPLATE_OVERLAY_ID,
            enabled_key=STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
        ),
        STAGE_FOCUS_OVERLAY_ENABLED_KEY: _resolve_stage_enabled(
            settings,
            stage_id=STAGE_FOCUS_OVERLAY_ID,
            enabled_key=STAGE_FOCUS_OVERLAY_ENABLED_KEY,
        ),
        PIPELINE_STAGE_ORDER_KEY: list(normalize_pipeline_stage_order(settings.get(PIPELINE_STAGE_ORDER_KEY))),
        EXPORT_STAGE_ID_KEY: normalize_export_stage_id(settings.get(EXPORT_STAGE_ID_KEY)),
        "uniform_auto_crop": uniform_auto_crop,
        "auto_crop_stabilization": _parse_percent_setting(settings.get("auto_crop_stabilization"), 0)
        if uniform_auto_crop else 0,
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
        DEJITTER_STRATEGY_KEY: dejitter_strategy,
        DEJITTER_REFERENCE_ENABLED_KEY: reference_active,
        DEJITTER_REFERENCE_REGIONS_KEY: reference_regions if reference_active else [],
        DEJITTER_REFERENCE_SOURCE_KEY: reference_source,
    }


def _resolve_stage_enabled(
    settings: Mapping[str, Any],
    *,
    stage_id: str,
    enabled_key: str,
    default: bool = True,
) -> bool:
    """解析 stage 开关：优先扁平 key，其次 pipeline_stage_enabled[stage_id]。"""
    if enabled_key in settings:
        return _parse_bool_value(settings.get(enabled_key), default)
    nested = settings.get(PIPELINE_STAGE_ENABLED_KEY)
    if isinstance(nested, dict) and stage_id in nested:
        return _parse_bool_value(nested.get(stage_id), default)
    return default


def _should_draw_template_overlay(settings: dict[str, Any]) -> bool:
    if not _resolve_stage_enabled(
        settings,
        stage_id=STAGE_TEMPLATE_OVERLAY_ID,
        enabled_key=STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
    ):
        return False
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










def _context_bird_box_cache(
    context: ImageProcContext,
) -> dict[str, tuple[float, float, float, float] | None]:
    if isinstance(context.bird_box_cache, dict):
        return context.bird_box_cache
    return {}


def _detect_primary_bird_box_for_export(
    image: Image.Image,
) -> tuple[float, float, float, float] | None:
    return _export_stage_callable("_detect_primary_bird_box")(image)


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
        bird_box = _detect_primary_bird_box_for_export(image)
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
        bird_box = _detect_primary_bird_box_for_export(image)
        bird_box_cache[signature] = bird_box
        if bird_box is None and not _BIRD_DETECT_WARNING_EMITTED:
            message = _get_bird_detector_error_message()
            if message:
                _log.warning("bird detect unavailable during video export: %s", message)
                _BIRD_DETECT_WARNING_EMITTED = True
    return bird_box


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
    from birdstamp.gui import editor_core

    return editor_core.compute_auto_bird_crop_plan(
        image=image,
        bird_box=bird_box,
        ratio=ratio,
        inner_top=inner_top,
        inner_bottom=inner_bottom,
        inner_left=inner_left,
        inner_right=inner_right,
    )


def _crop_plan_center_in_source_pixels(
    *,
    source_width: int,
    source_height: int,
    crop_plan: tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]],
) -> tuple[float, float] | None:
    if source_width <= 0 or source_height <= 0:
        return None
    crop_box, outer_pad = crop_plan
    top, bottom, left, right = outer_pad
    padded_width = source_width + max(0, int(left)) + max(0, int(right))
    padded_height = source_height + max(0, int(top)) + max(0, int(bottom))
    if padded_width <= 0 or padded_height <= 0:
        return None
    box = _normalize_unit_box(crop_box) or (0.0, 0.0, 1.0, 1.0)
    crop_left = box[0] * padded_width
    crop_top = box[1] * padded_height
    crop_right = box[2] * padded_width
    crop_bottom = box[3] * padded_height
    return (
        ((crop_left + crop_right) * 0.5) - max(0, int(left)),
        ((crop_top + crop_bottom) * 0.5) - max(0, int(top)),
    )


def _compute_fixed_size_crop_plan(
    *,
    source_width: int,
    source_height: int,
    center: tuple[float, float],
    crop_width: int,
    crop_height: int,
) -> tuple[tuple[float, float, float, float], tuple[int, int, int, int]]:
    target_width = max(1, int(crop_width))
    target_height = max(1, int(crop_height))
    center_x = float(center[0])
    center_y = float(center[1])
    crop_left = int(round(center_x - target_width * 0.5))
    crop_top = int(round(center_y - target_height * 0.5))
    crop_right = crop_left + target_width
    crop_bottom = crop_top + target_height

    outer_left = max(0, -crop_left)
    outer_top = max(0, -crop_top)
    outer_right = max(0, crop_right - source_width)
    outer_bottom = max(0, crop_bottom - source_height)

    padded_width = source_width + outer_left + outer_right
    padded_height = source_height + outer_top + outer_bottom
    if padded_width <= 0 or padded_height <= 0:
        return ((0.0, 0.0, 1.0, 1.0), (0, 0, 0, 0))

    normalized = (
        (crop_left + outer_left) / float(padded_width),
        (crop_top + outer_top) / float(padded_height),
        (crop_right + outer_left) / float(padded_width),
        (crop_bottom + outer_top) / float(padded_height),
    )
    return (
        _normalize_unit_box(normalized) or (0.0, 0.0, 1.0, 1.0),
        (outer_top, outer_bottom, outer_left, outer_right),
    )


def _uniform_crop_group_key(settings: dict[str, Any]) -> tuple[str, int] | None:
    if not _parse_bool_value(settings.get("uniform_auto_crop"), False):
        return None
    ratio = _parse_ratio_value(settings.get("ratio"))
    if ratio is None or _is_ratio_free(ratio) or _is_ratio_no_crop(ratio):
        return None
    if _crop_box_has_effect(settings.get("crop_box")):
        return None
    try:
        max_long_edge = max(0, int(settings.get("max_long_edge") or 0))
    except Exception:
        max_long_edge = 0
    return (f"{float(ratio):.8f}", max_long_edge)


def _resolve_uniform_group_target_size(
    *,
    ratio_text: str,
    sizes: list[tuple[int, int]],
) -> tuple[int, int] | None:
    if not sizes:
        return None
    try:
        ratio = float(ratio_text)
    except Exception:
        return None
    if ratio <= 0:
        return None
    width = max(1, max(int(size[0]) for size in sizes))
    height = max(1, max(int(size[1]) for size in sizes))
    if width / float(height) < ratio:
        width = max(width, int(math.ceil(height * ratio)))
    else:
        height = max(height, int(math.ceil(width / ratio)))
    return (width, height)


def _open_job_image_for_crop_plan(job: VideoFrameJob) -> tuple[Image.Image, bool]:
    if job.source_image is not None:
        return (job.source_image, False)
    return (decode_image(job.path, decoder="auto"), True)


def prepare_uniform_auto_crop_plans(
    jobs: list[VideoFrameJob],
    *,
    bird_box_cache: dict[str, tuple[float, float, float, float] | None] | None = None,
    bird_box_lock: threading.Lock | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    """Precompute crop plans, optionally apply de-jitter and unify auto-crop sizes.

    去抖动稳定化通过 :mod:`birdstamp.image_dejitter` 的策略接口完成：
    * 默认"中位中心混合"策略保留原有 ``uniform_auto_crop`` + 防抖滑块行为；
    * 当选择"参考区特征对齐"策略且存在有效参考区时，按帧间平移补偿裁切中心，
      此路径即使未开启 ``uniform_auto_crop`` 也会生效（逐帧使用各自裁切尺寸）。
    """
    total = len(jobs)
    if total <= 0:
        return 0

    global_settings = _clone_render_settings(jobs[0].settings)
    strategy = _dejitter.resolve_dejitter_strategy(global_settings.get(DEJITTER_STRATEGY_KEY))
    reference_regions = tuple(
        tuple(float(value) for value in box)
        for box in (global_settings.get(DEJITTER_REFERENCE_REGIONS_KEY) or [])
        if isinstance(box, (list, tuple)) and len(box) == 4
    )
    use_reference = bool(
        strategy.requires_reference_regions
        and _parse_bool_value(global_settings.get(DEJITTER_REFERENCE_ENABLED_KEY), False)
        and reference_regions
    )
    reference_source_text = global_settings.get(DEJITTER_REFERENCE_SOURCE_KEY) if use_reference else None
    reference_source = Path(reference_source_text) if reference_source_text else None

    any_uniform = any(_parse_bool_value(job.settings.get("uniform_auto_crop"), False) for job in jobs)
    if not any_uniform and not use_reference:
        return 0

    cache = bird_box_cache if isinstance(bird_box_cache, dict) else {}
    candidates: list[dict[str, Any]] = []
    grouped_candidates: dict[tuple[str, int], list[dict[str, Any]]] = {}
    grouped_sizes: dict[tuple[str, int], list[tuple[int, int]]] = {}
    frames: list[_dejitter.DeJitterFrame] = []
    reference_patches: tuple["np.ndarray | None", ...] = ()
    reference_raw_center: tuple[float, float] | None = None
    patch_size = _dejitter.DEFAULT_PATCH_SIZE
    prepared = 0

    for index, job in enumerate(jobs, start=1):
        _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在停止统一裁切预计算。")
        settings = _clone_render_settings(job.settings)
        job_uniform = _parse_bool_value(settings.get("uniform_auto_crop"), False)
        if not job_uniform and not use_reference:
            if callable(progress_callback):
                progress_callback(index, total)
            continue

        image, close_image = _open_job_image_for_crop_plan(job)
        try:
            crop_plan = _compute_crop_plan_for_image(
                path=job.path,
                image=image,
                raw_metadata=dict(job.raw_metadata or {}),
                settings=settings,
                bird_box_cache=cache,
                bird_box_lock=bird_box_lock,
            )
            job.crop_plan = crop_plan
            prepared += 1
            group_key = _uniform_crop_group_key(settings)
            crop_size = _compute_crop_output_size(
                image.width,
                image.height,
                crop_plan[0],
                crop_plan[1],
            )
            center = _crop_plan_center_in_source_pixels(
                source_width=image.width,
                source_height=image.height,
                crop_plan=crop_plan,
            )

            is_reference_frame = bool(
                use_reference
                and reference_source is not None
                and _path_key(job.path) == _path_key(reference_source)
            )
            region_patches: tuple["np.ndarray | None", ...] = ()
            if use_reference and _stabilization_eligible(settings):
                region_patches = tuple(
                    _extract_region_patch(image, box, patch_size) for box in reference_regions
                )

            # 参考区模式需要固定比例/无覆盖才稳定化；统一裁切沿用 group_key 资格。
            do_uniform = group_key is not None
            do_stabilize = crop_size is not None and center is not None and (
                do_uniform or (use_reference and _stabilization_eligible(settings))
            )
            if do_stabilize:
                frame = _dejitter.DeJitterFrame(
                    source_width=int(image.width),
                    source_height=int(image.height),
                    center=center,
                    center_norm=(
                        center[0] / float(max(1, image.width)),
                        center[1] / float(max(1, image.height)),
                    ),
                    strength=_parse_percent_setting(settings.get("auto_crop_stabilization"), 0),
                    source_path=job.path,
                    region_patches=region_patches if use_reference else (),
                    is_reference=is_reference_frame,
                )
                candidate = {
                    "job": job,
                    "group_key": group_key,
                    "frame": frame,
                    "crop_size": crop_size,
                    "source_width": int(image.width),
                    "source_height": int(image.height),
                }
                candidates.append(candidate)
                frames.append(frame)
                if do_uniform:
                    grouped_candidates.setdefault(group_key, []).append(candidate)
                    grouped_sizes.setdefault(group_key, []).append(crop_size)

            if is_reference_frame and center is not None:
                reference_patches = region_patches
                reference_raw_center = center
        finally:
            if close_image:
                try:
                    image.close()
                except Exception:
                    pass
        if callable(progress_callback):
            progress_callback(index, total)

    _run_dejitter_stabilization(
        strategy=strategy,
        frames=frames,
        grouped_candidates=grouped_candidates,
        use_reference=use_reference,
        reference_regions=reference_regions,
        reference_patches=reference_patches,
        reference_raw_center=reference_raw_center,
        reference_source=reference_source,
    )

    target_sizes = {
        group_key: _resolve_uniform_group_target_size(
            ratio_text=group_key[0],
            sizes=sizes,
        )
        for group_key, sizes in grouped_sizes.items()
    }
    for candidate in candidates:
        frame = candidate["frame"]
        job = candidate["job"]
        source_w = int(candidate["source_width"])
        source_h = int(candidate["source_height"])
        center = frame.stable_center if frame.stable_center is not None else frame.center
        group_key = candidate["group_key"]
        if group_key is not None:
            target_size = target_sizes.get(group_key)
            if target_size is None:
                continue
            job.crop_plan = _compute_fixed_size_crop_plan(
                source_width=source_w,
                source_height=source_h,
                center=center,
                crop_width=target_size[0],
                crop_height=target_size[1],
            )
        elif use_reference and frame.stable_center is not None:
            crop_size = candidate["crop_size"]
            job.crop_plan = _compute_fixed_size_crop_plan(
                source_width=source_w,
                source_height=source_h,
                center=center,
                crop_width=crop_size[0],
                crop_height=crop_size[1],
            )
    return prepared


def _run_dejitter_stabilization(
    *,
    strategy: "_dejitter.DeJitterStrategy",
    frames: list["_dejitter.DeJitterFrame"],
    grouped_candidates: dict[tuple[str, int], list[dict[str, Any]]],
    use_reference: bool,
    reference_regions: tuple[tuple[float, float, float, float], ...],
    reference_patches: tuple["np.ndarray | None", ...],
    reference_raw_center: tuple[float, float] | None,
    reference_source: Path | None,
) -> None:
    """根据策略执行去抖动稳定化，把稳定中心写回各 frame。"""
    if not frames:
        return
    if use_reference:
        strength = max((int(frame.strength) for frame in frames), default=0)
        context = _dejitter.DeJitterContext(
            frames=frames,
            strength=strength,
            reference_regions=reference_regions,
            reference_patches=reference_patches,
            reference_raw_center=reference_raw_center,
            reference_source=reference_source,
            aligner=_dejitter.NumpyPhaseCorrelationAligner(),
        )
        strategy.stabilize(context)
        return

    # 非参考区路径：保留原有"按比例组中位中心混合"语义。
    median_strategy = (
        strategy
        if isinstance(strategy, _dejitter.MedianCenterStabilizationStrategy)
        else _dejitter.MedianCenterStabilizationStrategy()
    )
    for group_candidates in grouped_candidates.values():
        group_frames = [candidate["frame"] for candidate in group_candidates]
        strength = max((int(frame.strength) for frame in group_frames), default=0)
        context = _dejitter.DeJitterContext(frames=group_frames, strength=strength)
        median_strategy.stabilize(context)


def _compute_crop_plan_for_image(
    *,
    path: Path | None,
    image: Image.Image,
    raw_metadata: dict[str, Any],
    settings: dict[str, Any],
    bird_box_cache: dict[str, tuple[float, float, float, float] | None],
    bird_box_lock: threading.Lock | None = None,
) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]]:
    from birdstamp.gui import editor_core

    center_mode = str(settings.get("center_mode") or _CENTER_MODE_IMAGE)
    mode = _normalize_center_mode(center_mode)
    bird_box: tuple[float, float, float, float] | None = None
    focus_camera_type = _resolve_focus_camera_type_from_metadata(raw_metadata)
    focus_point = _get_focus_point_for_display(
        raw_metadata,
        image.width,
        image.height,
        camera_type=focus_camera_type,
    )
    needs_bird_box = mode == _CENTER_MODE_BIRD or (
        mode == _CENTER_MODE_FOCUS and focus_point is None
    )
    if needs_bird_box:
        bird_box = _resolve_bird_box_for_image(path, image, bird_box_cache, bird_box_lock)

    return editor_core.compute_crop_plan_for_image(
        image=image,
        raw_metadata=raw_metadata,
        settings=settings,
        bird_box=bird_box,
        camera_type=focus_camera_type,
    )


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

    precomputed_crop_plan = _normalize_precomputed_crop_plan(job.crop_plan)
    context = ImageProcContext(
        image=image,
        settings=settings,
        source_path=job.path,
        source_paths=tuple(job.source_paths or (job.path,)),
        raw_metadata=raw_metadata,
        metadata_context=dict(job.metadata_context or {}),
        photo_info=job.photo_info,
        template_paths=dict(template_paths or {}),
        precomputed={"crop_plan": precomputed_crop_plan} if precomputed_crop_plan is not None else {},
        crop_plan=precomputed_crop_plan,
        bird_box_cache=cache,
        bird_box_lock=bird_box_lock,
    )
    from .pipeline import build_default_image_proc_pipeline

    rendered_context = build_default_image_proc_pipeline(settings.get(PIPELINE_STAGE_ORDER_KEY)).process(context)
    return rendered_context.image.convert("RGB")


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


def _save_rendered_source_frame(image: Image.Image, frame_path: Path) -> None:
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    source_frame = image.convert("RGB")
    try:
        # 渲染源帧作为缓存中间产物，优先保留复用速度。
        source_frame.save(frame_path, format="PNG", compress_level=1)
    finally:
        try:
            source_frame.close()
        except Exception:
            pass


def _source_frame_cache_metadata(*, total: int) -> dict[str, Any]:
    return {
        "total": max(0, int(total)),
    }


def _video_frame_cache_metadata(
    *,
    total: int,
    target_size: tuple[int, int],
    background_color: str,
    source_bucket_key: str,
) -> dict[str, Any]:
    return {
        "total": max(0, int(total)),
        "target_size": [int(target_size[0]), int(target_size[1])],
        "background_color": str(background_color or "").strip(),
        "source_bucket_key": str(source_bucket_key or "").strip(),
    }


def _manifest_metadata(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {}
    metadata = manifest.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _source_frame_total_matches(manifest: dict[str, Any] | None, *, total: int) -> bool:
    metadata = _manifest_metadata(manifest)
    return int(metadata.get("total") or -1) == int(total)


def _video_frame_manifest_reusable(
    manifest: dict[str, Any] | None,
    *,
    total: int,
    target_size: tuple[int, int],
    background_color: str,
    source_bucket_key: str,
) -> bool:
    metadata = _manifest_metadata(manifest)
    raw_target_size = metadata.get("target_size")
    if int(metadata.get("total") or -1) != int(total):
        return False
    if str(metadata.get("background_color") or "").strip() != str(background_color or "").strip():
        return False
    if str(metadata.get("source_bucket_key") or "").strip() != str(source_bucket_key or "").strip():
        return False
    if not isinstance(raw_target_size, (list, tuple)) or len(raw_target_size) != 2:
        return False
    try:
        width = int(raw_target_size[0])
        height = int(raw_target_size[1])
    except Exception:
        return False
    return (width, height) == (int(target_size[0]), int(target_size[1]))


def _resolve_target_size_from_source_frame(source_frame_path: Path, options: VideoExportOptions) -> tuple[int, int]:
    with Image.open(source_frame_path) as first_frame:
        return resolve_target_frame_size(options, first_frame.size)


def _render_and_cache_source_frame(
    *,
    job: VideoFrameJob,
    index: int,
    source_plan,
    template_paths: dict[str, Path] | None,
    bird_box_cache: dict[str, tuple[float, float, float, float] | None],
    bird_box_lock: threading.Lock | None,
    cancel_event: threading.Event | None,
) -> tuple[int, str, Path, str, str]:
    _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在保留已完成源帧。")
    rendered = _export_stage_callable("render_video_frame")(
        job,
        template_paths=template_paths,
        bird_box_cache=bird_box_cache,
        bird_box_lock=bird_box_lock,
    )
    frame_path = _cache_frame_output_path(source_plan, index, suffix="png")
    source_signature = _source_signature(job.path)
    frame_signature = _source_frame_signature_for_job(job)
    try:
        _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在保留已完成源帧。")
        _save_rendered_source_frame(rendered, frame_path)
    finally:
        try:
            rendered.close()
        except Exception:
            pass
    return (index, job.path.name, frame_path, source_signature, frame_signature)


def _normalize_and_cache_video_frame(
    *,
    index: int,
    source_frame_path: Path,
    label: str,
    video_plan,
    target_size: tuple[int, int],
    background_color: str,
    cancel_event: threading.Event | None,
) -> tuple[int, str, Path, str, str]:
    _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在保留已完成视频帧。")
    frame_path = _cache_frame_output_path(video_plan, index, suffix="png")
    source_signature = path_signature(source_frame_path)
    frame_signature = _video_frame_signature_for_source(
        source_frame_path,
        target_size=target_size,
        background_color=background_color,
    )
    with Image.open(source_frame_path) as source_image:
        _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在保留已完成视频帧。")
        _save_normalized_temp_frame(
            source_image,
            frame_path,
            target_size,
            background_color=background_color,
        )
    return (index, label, frame_path, source_signature, frame_signature)


def _prune_cache_frames(frames_dir: Path, manifest: dict[str, Any], *, total: int) -> None:
    frames = manifest.get("frames") if isinstance(manifest, dict) else None
    if isinstance(frames, dict):
        stale_keys: list[str] = []
        for key in list(frames.keys()):
            try:
                index = int(key)
            except Exception:
                stale_keys.append(key)
                continue
            if index > int(total) or index <= 0:
                stale_keys.append(key)
        for key in stale_keys:
            frames.pop(key, None)
    if not frames_dir.is_dir():
        return
    for frame_path in frames_dir.glob("frame_*.png"):
        stem = frame_path.stem
        _, _, suffix = stem.partition("_")
        try:
            index = int(suffix)
        except Exception:
            continue
        if index <= 0 or index > int(total):
            try:
                frame_path.unlink()
            except Exception:
                _log.debug("prune stale cache frame failed: %s", frame_path, exc_info=True)


def _ensure_source_frame_cache(
    jobs: list[VideoFrameJob],
    *,
    output_path: Path,
    options: VideoExportOptions,
    template_paths: dict[str, Path] | None,
    progress_callback: VideoExportProgressCallback | None,
    cancel_event: threading.Event | None,
    bird_box_cache: dict[str, tuple[float, float, float, float] | None],
    bird_box_lock: threading.Lock,
    dirty_path_keys: set[str],
) -> tuple[str, Any, list[Path]]:
    total = len(jobs)
    if any(
        crop_plan_precompute_required(job.settings)
        and _normalize_precomputed_crop_plan(job.crop_plan) is None
        for job in jobs
    ):
        _emit_progress(
            progress_callback,
            phase="prepare",
            current=0,
            total=total,
            message=f"正在预计算统一自动裁切尺寸，共 {total} 张。",
        )

        def _on_prepare_progress(current: int, total_count: int) -> None:
            _emit_progress(
                progress_callback,
                phase="prepare",
                current=current,
                total=total_count,
                message=f"正在预计算统一自动裁切尺寸 {current}/{total_count}",
            )

        prepare_uniform_auto_crop_plans(
            jobs,
            bird_box_cache=bird_box_cache,
            bird_box_lock=bird_box_lock,
            progress_callback=_on_prepare_progress,
            cancel_event=cancel_event,
        )
    source_bucket_key = _render_cache_key(jobs, options)
    source_plan = create_frame_cache_plan(
        output_path,
        bucket_kind=SOURCE_FRAME_BUCKET_KIND,
        bucket_key=source_bucket_key,
        persistent=options.preserve_temp_files,
    )
    manifest = load_frame_manifest(source_plan)
    source_plan.frames_dir.mkdir(parents=True, exist_ok=True)
    _prune_cache_frames(source_plan.frames_dir, manifest, total=total)

    if dirty_path_keys:
        _log.info(
            "video export dirty photos=%s source_bucket=%s",
            len(dirty_path_keys),
            source_bucket_key,
        )

    source_frame_paths = [_cache_frame_output_path(source_plan, index, suffix="png") for index in range(1, total + 1)]
    pending_jobs: list[tuple[int, VideoFrameJob, str, str]] = []
    reused_count = 0
    for index, job in enumerate(jobs, start=1):
        source_signature = _source_signature(job.path)
        frame_signature = _source_frame_signature_for_job(job)
        reusable_path = reusable_frame_path(
            source_plan,
            manifest,
            index=index,
            source_path=job.path,
            source_signature=source_signature,
            frame_signature=frame_signature,
        )
        if reusable_path is not None:
            source_frame_paths[index - 1] = reusable_path
            reused_count += 1
            continue
        pending_jobs.append((index, job, source_signature, frame_signature))

    if reused_count > 0:
        _emit_progress(
            progress_callback,
            phase="render",
            current=reused_count,
            total=total,
            message=f"复用已缓存源帧 {reused_count}/{total} 帧。",
        )

    if not pending_jobs:
        write_frame_manifest(source_plan, manifest, metadata=_source_frame_cache_metadata(total=total))
        return (source_bucket_key, source_plan, source_frame_paths)

    render_workers = resolve_video_render_workers(options.render_workers, len(pending_jobs))
    _emit_progress(
        progress_callback,
        phase="render",
        current=reused_count,
        total=total,
        message=f"正在渲染源帧，剩余 {len(pending_jobs)} 张，线程数 {render_workers}",
    )
    completed = reused_count
    if len(pending_jobs) == 1:
        index, job, source_signature, frame_signature = pending_jobs[0]
        rendered_index, frame_name, frame_path, _, _ = _render_and_cache_source_frame(
            job=job,
            index=index,
            source_plan=source_plan,
            template_paths=template_paths,
            bird_box_cache=bird_box_cache,
            bird_box_lock=bird_box_lock,
            cancel_event=cancel_event,
        )
        source_frame_paths[rendered_index - 1] = frame_path
        update_frame_manifest_record(
            source_plan,
            manifest,
            index=rendered_index,
            source_path=job.path,
            source_signature=source_signature,
            frame_signature=frame_signature,
            frame_path=frame_path,
        )
        completed += 1
        write_frame_manifest(source_plan, manifest, metadata=_source_frame_cache_metadata(total=total))
        _emit_progress(
            progress_callback,
            phase="render",
            current=completed,
            total=total,
            message=f"已渲染源帧 {completed}/{total}: {frame_name}",
        )
        return (source_bucket_key, source_plan, source_frame_paths)

    executor = ThreadPoolExecutor(max_workers=render_workers, thread_name_prefix="birdstamp-video-source-render")
    futures: dict[Any, tuple[int, VideoFrameJob, str, str]] = {}
    try:
        for index, job, source_signature, frame_signature in pending_jobs:
            _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在停止剩余源帧渲染。")
            future = executor.submit(
                _render_and_cache_source_frame,
                job=job,
                index=index,
                source_plan=source_plan,
                template_paths=template_paths,
                bird_box_cache=bird_box_cache,
                bird_box_lock=bird_box_lock,
                cancel_event=cancel_event,
            )
            futures[future] = (index, job, source_signature, frame_signature)

        for future in as_completed(futures):
            index, job, source_signature, frame_signature = futures[future]
            try:
                rendered_index, frame_name, frame_path, _, _ = future.result()
            except VideoExportCancelledError:
                if cancel_event is not None:
                    cancel_event.set()
                for pending in futures:
                    pending.cancel()
                raise
            source_frame_paths[rendered_index - 1] = frame_path
            update_frame_manifest_record(
                source_plan,
                manifest,
                index=index,
                source_path=job.path,
                source_signature=source_signature,
                frame_signature=frame_signature,
                frame_path=frame_path,
            )
            completed += 1
            write_frame_manifest(source_plan, manifest, metadata=_source_frame_cache_metadata(total=total))
            _emit_progress(
                progress_callback,
                phase="render",
                current=completed,
                total=total,
                message=f"已渲染源帧 {completed}/{total}: {frame_name}",
            )
            _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在停止剩余源帧渲染。")
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    write_frame_manifest(source_plan, manifest, metadata=_source_frame_cache_metadata(total=total))
    return (source_bucket_key, source_plan, source_frame_paths)


def _ensure_video_frame_cache(
    source_frame_paths: list[Path],
    jobs: list[VideoFrameJob],
    *,
    output_path: Path,
    options: VideoExportOptions,
    source_bucket_key: str,
    progress_callback: VideoExportProgressCallback | None,
    cancel_event: threading.Event | None,
) -> tuple[Any, tuple[int, int], Path]:
    total = len(jobs)
    target_size = _resolve_target_size_from_source_frame(source_frame_paths[0], options)
    video_bucket_key = build_video_frame_bucket_key(
        source_bucket_key=source_bucket_key,
        target_size=target_size,
        background_color=options.background_color,
    )
    video_plan = create_frame_cache_plan(
        output_path,
        bucket_kind=VIDEO_FRAME_BUCKET_KIND,
        bucket_key=video_bucket_key,
        persistent=options.preserve_temp_files,
    )
    manifest = load_frame_manifest(video_plan)
    video_plan.frames_dir.mkdir(parents=True, exist_ok=True)
    _prune_cache_frames(video_plan.frames_dir, manifest, total=total)
    temp_output_path = video_plan.cache_dir / output_path.name
    _cleanup_incomplete_output(temp_output_path)

    pending_frames: list[tuple[int, Path, str, str]] = []
    reused_count = 0
    for index, job in enumerate(jobs, start=1):
        source_frame_path = source_frame_paths[index - 1]
        source_signature = path_signature(source_frame_path)
        frame_signature = _video_frame_signature_for_source(
            source_frame_path,
            target_size=target_size,
            background_color=options.background_color,
        )
        reusable_path = reusable_frame_path(
            video_plan,
            manifest,
            index=index,
            source_path=source_frame_path,
            source_signature=source_signature,
            frame_signature=frame_signature,
        )
        if reusable_path is not None:
            reused_count += 1
            continue
        pending_frames.append((index, source_frame_path, source_signature, job.path.name))

    if reused_count > 0:
        _emit_progress(
            progress_callback,
            phase="render",
            current=reused_count,
            total=total,
            message=f"复用已缓存视频帧 {reused_count}/{total} 帧。",
        )

    if pending_frames:
        render_workers = resolve_video_render_workers(options.render_workers, len(pending_frames))
        _emit_progress(
            progress_callback,
            phase="render",
            current=reused_count,
            total=total,
            message=f"正在准备视频帧，剩余 {len(pending_frames)} 张，线程数 {render_workers}",
        )
        completed = reused_count
        if len(pending_frames) == 1:
            index, source_frame_path, source_signature, frame_name = pending_frames[0]
            rendered_index, _, frame_path, _, frame_signature = _normalize_and_cache_video_frame(
                index=index,
                source_frame_path=source_frame_path,
                label=frame_name,
                video_plan=video_plan,
                target_size=target_size,
                background_color=options.background_color,
                cancel_event=cancel_event,
            )
            update_frame_manifest_record(
                video_plan,
                manifest,
                index=rendered_index,
                source_path=source_frame_path,
                source_signature=source_signature,
                frame_signature=frame_signature,
                frame_path=frame_path,
            )
            completed += 1
            write_frame_manifest(
                video_plan,
                manifest,
                metadata=_video_frame_cache_metadata(
                    total=total,
                    target_size=target_size,
                    background_color=options.background_color,
                    source_bucket_key=source_bucket_key,
                ),
            )
            _emit_progress(
                progress_callback,
                phase="render",
                current=completed,
                total=total,
                message=f"已准备视频帧 {completed}/{total}: {frame_name}",
            )
        else:
            executor = ThreadPoolExecutor(max_workers=render_workers, thread_name_prefix="birdstamp-video-frame-cache")
            futures: dict[Any, tuple[int, Path, str, str]] = {}
            try:
                for index, source_frame_path, source_signature, frame_name in pending_frames:
                    _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在停止剩余视频帧准备。")
                    future = executor.submit(
                        _normalize_and_cache_video_frame,
                        index=index,
                        source_frame_path=source_frame_path,
                        label=frame_name,
                        video_plan=video_plan,
                        target_size=target_size,
                        background_color=options.background_color,
                        cancel_event=cancel_event,
                    )
                    futures[future] = (index, source_frame_path, source_signature, frame_name)

                completed = reused_count
                for future in as_completed(futures):
                    index, source_frame_path, source_signature, frame_name = futures[future]
                    try:
                        rendered_index, _, frame_path, _, frame_signature = future.result()
                    except VideoExportCancelledError:
                        if cancel_event is not None:
                            cancel_event.set()
                        for pending in futures:
                            pending.cancel()
                        raise
                    update_frame_manifest_record(
                        video_plan,
                        manifest,
                        index=rendered_index,
                        source_path=source_frame_path,
                        source_signature=source_signature,
                        frame_signature=frame_signature,
                        frame_path=frame_path,
                    )
                    completed += 1
                    write_frame_manifest(
                        video_plan,
                        manifest,
                        metadata=_video_frame_cache_metadata(
                            total=total,
                            target_size=target_size,
                            background_color=options.background_color,
                            source_bucket_key=source_bucket_key,
                        ),
                    )
                    _emit_progress(
                        progress_callback,
                        phase="render",
                        current=completed,
                        total=total,
                        message=f"已准备视频帧 {completed}/{total}: {frame_name}",
                    )
                    _raise_if_cancel_requested(cancel_event, message="视频导出已中断，正在停止剩余视频帧准备。")
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

    write_frame_manifest(
        video_plan,
        manifest,
        metadata=_video_frame_cache_metadata(
            total=total,
            target_size=target_size,
            background_color=options.background_color,
            source_bucket_key=source_bucket_key,
        ),
    )
    return (video_plan, target_size, temp_output_path)


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
    rendered = _export_stage_callable("render_video_frame")(
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
    plan = create_frame_cache_plan(
        output_path,
        bucket_kind=VIDEO_FRAME_BUCKET_KIND,
        bucket_key=str(cache_key or "").strip().lower() or "default",
        persistent=preserve_temp_files,
    )
    return plan.cache_dir


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
    _export_stage_callable("_run_ffmpeg_command")(cmd, cancel_event=None)
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
    dirty_path_keys: set[str] | None = None,
    progress_callback: VideoExportProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    validated = validate_video_export_options(options)
    if not jobs:
        raise ValueError("没有可用于生成视频的图片。")

    ffmpeg_path = _export_stage_callable("find_ffmpeg_executable")()
    if ffmpeg_path is None:
        raise FileNotFoundError(_ffmpeg_not_found_message())

    output_path = validated.normalized_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not validated.overwrite:
        raise FileExistsError(f"输出文件已存在: {output_path}")

    bird_box_cache: dict[str, tuple[float, float, float, float] | None] = {}
    bird_box_lock = threading.Lock()
    total = len(jobs)
    dirty_keys = set(dirty_path_keys or set())
    source_plan = None
    video_plan = None
    work_dir: Path | None = None
    frames_dir: Path | None = None
    temp_output_path: Path | None = None

    try:
        _raise_if_cancel_requested(cancel_event, message="视频导出已中断，尚未开始渲染。")
        source_bucket_key, source_plan, source_frame_paths = _ensure_source_frame_cache(
            jobs,
            output_path=output_path,
            options=validated,
            template_paths=template_paths,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            bird_box_cache=bird_box_cache,
            bird_box_lock=bird_box_lock,
            dirty_path_keys=dirty_keys,
        )
        video_plan, _target_size, temp_output_path = _ensure_video_frame_cache(
            source_frame_paths,
            jobs,
            output_path=output_path,
            options=validated,
            source_bucket_key=source_bucket_key,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        work_dir = video_plan.cache_dir
        frames_dir = video_plan.frames_dir
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
        _export_stage_callable("_run_ffmpeg_command")(cmd, cancel_event=cancel_event)

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
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)
            if source_plan is not None and source_plan.cache_dir != work_dir:
                shutil.rmtree(source_plan.cache_dir, ignore_errors=True)
        return output_path
    except VideoExportCancelledError:
        if temp_output_path is not None:
            _cleanup_incomplete_output(temp_output_path)
        rendered_frame_paths = _list_rendered_frame_paths(frames_dir) if frames_dir is not None else []
        contiguous_count = _count_contiguous_rendered_frames(frames_dir, total) if frames_dir is not None else 0
        partial_output_path: Path | None = None
        if frames_dir is not None and rendered_frame_paths:
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
        ]
        if work_dir is not None:
            detail_lines.append(f"已保留工作目录: {work_dir}")
        elif source_plan is not None:
            detail_lines.append(f"已保留源帧缓存目录: {source_plan.cache_dir}")
        if frames_dir is not None:
            detail_lines.append(f"已保留视频帧: {len(rendered_frame_paths)}/{total}")
        elif source_plan is not None:
            detail_lines.append(f"已保留源帧: {len(_list_rendered_frame_paths(source_plan.frames_dir))}/{total}")
        else:
            detail_lines.append("尚未生成可用于编码的视频帧。")
        if frames_dir is not None and contiguous_count != len(rendered_frame_paths):
            detail_lines.append(f"其中连续前缀帧: {contiguous_count}")
        if partial_output_path is not None:
            detail_lines.append(f"已生成部分视频: {partial_output_path}")
        else:
            detail_lines.append("未生成部分视频，可使用保留帧稍后继续合成。")
        raise VideoExportCancelledError(
            "\n".join(detail_lines),
            preserved_frames_dir=frames_dir if frames_dir is not None else (source_plan.frames_dir if source_plan is not None else None),
            partial_output_path=partial_output_path,
        ) from None
    except Exception:
        if temp_output_path is not None:
            _cleanup_incomplete_output(temp_output_path)
        if not validated.preserve_temp_files:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)
            if source_plan is not None and source_plan.cache_dir != work_dir:
                shutil.rmtree(source_plan.cache_dir, ignore_errors=True)
        raise


__all__ = [
    "DEFAULT_VIDEO_BACKGROUND_COLOR",
    "DEFAULT_EXPORT_STAGE_ID",
    "DEFAULT_PIPELINE_STAGE_ORDER",
    "EXPORT_STAGE_GIF_ID",
    "EXPORT_STAGE_ID_KEY",
    "EXPORT_STAGE_PNG_ID",
    "EXPORT_STAGE_VIDEO_ID",
    "FFMPEG_ENV_VAR",
    "GifExportStage",
    "PIPELINE_STAGE_ORDER_KEY",
    "PIPELINE_STAGE_ENABLED_KEY",
    "PngExportStage",
    "STAGE_FOCUS_OVERLAY_ENABLED_KEY",
    "STAGE_FOCUS_OVERLAY_ID",
    "STAGE_RESIZE_LIMIT_ENABLED_KEY",
    "STAGE_RESIZE_LIMIT_ID",
    "STAGE_TEMPLATE_CROP_ENABLED_KEY",
    "STAGE_TEMPLATE_CROP_ID",
    "STAGE_TEMPLATE_OVERLAY_ENABLED_KEY",
    "STAGE_TEMPLATE_OVERLAY_ID",
    "VideoExportCancelledError",
    "VideoExportOptions",
    "VideoExportProgress",
    "VideoFrameJob",
    "VideoProcExportStage",
    "build_default_image_proc_pipeline",
    "build_image_proc_export_stages",
    "build_ffmpeg_command",
    "export_video",
    "ffmpeg_install_script_path",
    "find_ffmpeg_executable",
    "crop_plan_precompute_required",
    "dejitter_reference_active",
    "normalize_frame_size",
    "normalize_export_stage_id",
    "normalize_pipeline_stage_order",
    "prepare_uniform_auto_crop_plans",
    "preferred_ffmpeg_binary_path",
    "preferred_ffmpeg_tool_dir",
    "render_video_frame",
    "resolve_target_frame_size",
    "resolve_video_render_workers",
    "source_frame_signature_for_job",
    "validate_video_export_options",
]
