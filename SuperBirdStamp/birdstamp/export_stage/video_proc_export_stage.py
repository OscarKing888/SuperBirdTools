from __future__ import annotations

from typing import Any, Mapping

from birdstamp.image_pipeline import ImageProcContext, ImageProcExportStage, ImageProcOptionChoice, ImageProcOptionSpec, ImageProcStage

from . import core
from .constants import (
    EXPORT_STAGE_GIF_ID,
    EXPORT_STAGE_PNG_ID,
    EXPORT_STAGE_VIDEO_ID,
    STAGE_FOCUS_OVERLAY_ENABLED_KEY,
    STAGE_FOCUS_OVERLAY_ID,
    STAGE_RESIZE_LIMIT_ENABLED_KEY,
    STAGE_RESIZE_LIMIT_ID,
    STAGE_TEMPLATE_CROP_ENABLED_KEY,
    STAGE_TEMPLATE_CROP_ID,
    STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
    STAGE_TEMPLATE_OVERLAY_ID,
    _CENTER_MODE_BIRD,
    _CENTER_MODE_CUSTOM,
    _CENTER_MODE_FOCUS,
    _CENTER_MODE_IMAGE,
    _DEFAULT_TEMPLATE_CENTER_MODE,
    _DEFAULT_TEMPLATE_MAX_LONG_EDGE,
)

class VideoProcExportStage(ImageProcExportStage):
    stage_id = EXPORT_STAGE_VIDEO_ID
    export_kind = "video"
    label = "视频导出"
    description = "将处理后的图片序列编码为 MP4 或 MOV 视频。"

    def parameter_options(self) -> tuple[ImageProcOptionSpec, ...]:
        return (
            ImageProcOptionSpec(key="video_container", label="格式", value_type="choice", default="mp4"),
            ImageProcOptionSpec(key="video_fps", label="FPS", value_type="float", default=25.0, minimum=0.1),
        )

