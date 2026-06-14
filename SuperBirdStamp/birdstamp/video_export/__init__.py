"""Video export package: one public class per module; import from here."""
from __future__ import annotations

from .constants import (
    DEFAULT_VIDEO_BACKGROUND_COLOR,
    DEFAULT_EXPORT_STAGE_ID,
    DEFAULT_PIPELINE_STAGE_ORDER,
    DEFAULT_VIDEO_RENDER_WORKERS,
    DEJITTER_REFERENCE_ENABLED_KEY,
    DEJITTER_REFERENCE_REGIONS_KEY,
    DEJITTER_REFERENCE_SOURCE_KEY,
    DEJITTER_STRATEGY_KEY,
    EXPORT_STAGE_GIF_ID,
    EXPORT_STAGE_ID_KEY,
    EXPORT_STAGE_PNG_ID,
    EXPORT_STAGE_VIDEO_ID,
    FFMPEG_ENV_VAR,
    PIPELINE_STAGE_ORDER_KEY,
    PIPELINE_STAGE_ENABLED_KEY,
    STAGE_FOCUS_OVERLAY_ENABLED_KEY,
    STAGE_FOCUS_OVERLAY_ID,
    STAGE_RESIZE_LIMIT_ENABLED_KEY,
    STAGE_RESIZE_LIMIT_ID,
    STAGE_TEMPLATE_CROP_ENABLED_KEY,
    STAGE_TEMPLATE_CROP_ID,
    STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
    STAGE_TEMPLATE_OVERLAY_ID,
)
from .focus_overlay_stage import FocusOverlayStage
from .gif_export_stage import GifExportStage
from .png_export_stage import PngExportStage
from .resize_limit_stage import ResizeLimitStage
from .template_crop_stage import TemplateCropStage
from .template_overlay_stage import TemplateOverlayStage
from .video_export_cancelled_error import VideoExportCancelledError
from .video_export_options import VideoExportOptions
from .video_export_progress import VideoExportProgress
from .video_frame_job import VideoFrameJob
from .video_proc_export_stage import VideoProcExportStage
from .pipeline import build_default_image_proc_pipeline, build_image_proc_export_stages
from .constants import _detect_primary_bird_box
from . import core as _core
from .core import (
    build_ffmpeg_command,
    crop_plan_precompute_required,
    dejitter_reference_active,
    export_video,
    ffmpeg_install_script_path,
    find_ffmpeg_executable,
    normalize_export_stage_id,
    normalize_frame_size,
    normalize_pipeline_stage_order,
    prepare_uniform_auto_crop_plans,
    preferred_ffmpeg_binary_path,
    preferred_ffmpeg_tool_dir,
    render_video_frame,
    resolve_target_frame_size,
    resolve_video_render_workers,
    source_frame_signature_for_job,
    validate_video_export_options,
)

# 测试与内部调用仍可通过 birdstamp.video_export._xxx 访问 core 私有符号。
_compute_auto_bird_crop_plan = _core._compute_auto_bird_crop_plan
_count_contiguous_rendered_frames = _core._count_contiguous_rendered_frames
_create_video_work_dir = _core._create_video_work_dir
_crop_plan_center_in_source_pixels = _core._crop_plan_center_in_source_pixels
_partial_video_output_path = _core._partial_video_output_path
_recommended_auto_render_workers = _core._recommended_auto_render_workers
_render_cache_key = _core._render_cache_key
_run_ffmpeg_command = _core._run_ffmpeg_command

__all__ = [
    "DEFAULT_VIDEO_BACKGROUND_COLOR",
    "DEFAULT_EXPORT_STAGE_ID",
    "DEFAULT_PIPELINE_STAGE_ORDER",
    "EXPORT_STAGE_GIF_ID",
    "EXPORT_STAGE_ID_KEY",
    "EXPORT_STAGE_PNG_ID",
    "EXPORT_STAGE_VIDEO_ID",
    "FFMPEG_ENV_VAR",
    "FocusOverlayStage",
    "GifExportStage",
    "PIPELINE_STAGE_ORDER_KEY",
    "PIPELINE_STAGE_ENABLED_KEY",
    "PngExportStage",
    "ResizeLimitStage",
    "STAGE_FOCUS_OVERLAY_ENABLED_KEY",
    "STAGE_FOCUS_OVERLAY_ID",
    "STAGE_RESIZE_LIMIT_ENABLED_KEY",
    "STAGE_RESIZE_LIMIT_ID",
    "STAGE_TEMPLATE_CROP_ENABLED_KEY",
    "STAGE_TEMPLATE_CROP_ID",
    "STAGE_TEMPLATE_OVERLAY_ENABLED_KEY",
    "STAGE_TEMPLATE_OVERLAY_ID",
    "TemplateCropStage",
    "TemplateOverlayStage",
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
