from __future__ import annotations

import sys

from birdstamp.gui import editor_core, editor_template, editor_utils


DEFAULT_VIDEO_BACKGROUND_COLOR = "#000000"
DEFAULT_VIDEO_RENDER_WORKERS = 0
FFMPEG_ENV_VAR = "BIRDSTAMP_FFMPEG"
_PLATFORM_TOOL_SUBDIR = {
    "darwin": "macos",
    "win32": "windows",
}
_BIRD_DETECT_WARNING_EMITTED = False
_VIDEO_RENDER_CACHE_VERSION = 2
_VIDEO_RENDER_CACHE_ROOT_NAME = "birdstamp_export_cache"

_build_metadata_context = editor_utils.build_metadata_context
_safe_color = editor_utils.safe_color
_path_key = editor_utils.path_key
_parse_ratio_value = editor_core.parse_ratio_value
_is_ratio_free = editor_core.is_ratio_free
_is_ratio_no_crop = editor_core.is_ratio_no_crop
_crop_box_has_effect = editor_core.crop_box_has_effect
_crop_plan_from_override = editor_core._crop_plan_from_override
_parse_bool_value = editor_core.parse_bool_value
_parse_padding_value = editor_core.parse_padding_value
_normalize_center_mode = editor_core.normalize_center_mode
_resize_fit = editor_core.resize_fit
_pad_image = editor_core.pad_image
_crop_image_by_normalized_box = editor_core.crop_image_by_normalized_box
_compute_crop_output_size = editor_core.compute_crop_output_size
_compute_ratio_crop_box = editor_core.compute_ratio_crop_box
_draw_focus_box_overlay = editor_core.draw_focus_box_overlay
_expand_unit_box_to_unclamped_pixels = editor_core.expand_unit_box_to_unclamped_pixels
_normalize_unit_box = editor_core.normalize_unit_box
_normalize_extended_unit_box = editor_core.normalize_extended_unit_box
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

STAGE_TEMPLATE_CROP_ENABLED_KEY = "stage_template_crop_enabled"
STAGE_RESIZE_LIMIT_ENABLED_KEY = "stage_resize_limit_enabled"
STAGE_TEMPLATE_OVERLAY_ENABLED_KEY = "stage_template_overlay_enabled"
STAGE_FOCUS_OVERLAY_ENABLED_KEY = "stage_focus_overlay_enabled"
PIPELINE_STAGE_ORDER_KEY = "pipeline_stage_order"
PIPELINE_STAGE_ENABLED_KEY = "pipeline_stage_enabled"
EXPORT_STAGE_ID_KEY = "selected_export_stage_id"
STAGE_TEMPLATE_CROP_ID = "template_crop"
STAGE_RESIZE_LIMIT_ID = "resize_limit"
STAGE_TEMPLATE_OVERLAY_ID = "template_overlay"
STAGE_FOCUS_OVERLAY_ID = "focus_overlay"
DEFAULT_PIPELINE_STAGE_ORDER = (
    STAGE_TEMPLATE_CROP_ID,
    STAGE_RESIZE_LIMIT_ID,
    STAGE_TEMPLATE_OVERLAY_ID,
    STAGE_FOCUS_OVERLAY_ID,
)
EXPORT_STAGE_PNG_ID = "export_png"
EXPORT_STAGE_GIF_ID = "export_gif"
EXPORT_STAGE_VIDEO_ID = "export_video"
DEFAULT_EXPORT_STAGE_ID = EXPORT_STAGE_PNG_ID

DEJITTER_STRATEGY_KEY = "dejitter_strategy"
DEJITTER_REFERENCE_ENABLED_KEY = "dejitter_reference_enabled"
DEJITTER_REFERENCE_REGIONS_KEY = "dejitter_reference_regions"
DEJITTER_REFERENCE_SOURCE_KEY = "dejitter_reference_source"

__all__ = [
    "DEFAULT_EXPORT_STAGE_ID",
    "DEFAULT_PIPELINE_STAGE_ORDER",
    "DEFAULT_VIDEO_BACKGROUND_COLOR",
    "DEFAULT_VIDEO_RENDER_WORKERS",
    "DEJITTER_REFERENCE_ENABLED_KEY",
    "DEJITTER_REFERENCE_REGIONS_KEY",
    "DEJITTER_REFERENCE_SOURCE_KEY",
    "DEJITTER_STRATEGY_KEY",
    "EXPORT_STAGE_GIF_ID",
    "EXPORT_STAGE_ID_KEY",
    "EXPORT_STAGE_PNG_ID",
    "EXPORT_STAGE_VIDEO_ID",
    "FFMPEG_ENV_VAR",
    "PIPELINE_STAGE_ENABLED_KEY",
    "PIPELINE_STAGE_ORDER_KEY",
    "STAGE_FOCUS_OVERLAY_ENABLED_KEY",
    "STAGE_FOCUS_OVERLAY_ID",
    "STAGE_RESIZE_LIMIT_ENABLED_KEY",
    "STAGE_RESIZE_LIMIT_ID",
    "STAGE_TEMPLATE_CROP_ENABLED_KEY",
    "STAGE_TEMPLATE_CROP_ID",
    "STAGE_TEMPLATE_OVERLAY_ENABLED_KEY",
    "STAGE_TEMPLATE_OVERLAY_ID",
    "_BIRD_DETECT_WARNING_EMITTED",
    "_CENTER_MODE_BIRD",
    "_CENTER_MODE_CUSTOM",
    "_CENTER_MODE_FOCUS",
    "_CENTER_MODE_IMAGE",
    "_DEFAULT_CROP_PADDING_PX",
    "_DEFAULT_TEMPLATE_CENTER_MODE",
    "_DEFAULT_TEMPLATE_MAX_LONG_EDGE",
    "_PLATFORM_TOOL_SUBDIR",
    "_VIDEO_RENDER_CACHE_ROOT_NAME",
    "_VIDEO_RENDER_CACHE_VERSION",
    "_box_center",
    "_build_metadata_context",
    "_compute_crop_output_size",
    "_compute_ratio_crop_box",
    "_crop_box_has_effect",
    "_crop_image_by_normalized_box",
    "_crop_plan_from_override",
    "_deep_copy_payload",
    "_detect_primary_bird_box",
    "_default_template_payload",
    "_draw_focus_box_overlay",
    "_expand_unit_box_to_unclamped_pixels",
    "_get_bird_detector_error_message",
    "_get_focus_point_for_display",
    "_is_ratio_free",
    "_is_ratio_no_crop",
    "_load_template_payload",
    "_normalize_center_mode",
    "_normalize_extended_unit_box",
    "_normalize_template_payload",
    "_normalize_unit_box",
    "_pad_image",
    "_parse_bool_value",
    "_parse_padding_value",
    "_parse_ratio_value",
    "_path_key",
    "_render_template_overlay",
    "_resize_fit",
    "_resolve_focus_box_after_processing",
    "_resolve_focus_camera_type_from_metadata",
    "_safe_color",
]
