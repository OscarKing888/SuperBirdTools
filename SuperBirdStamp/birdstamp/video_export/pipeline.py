from __future__ import annotations

from typing import Any

from birdstamp.image_pipeline import ImageProcExportStage, ImageProcPipeline, ImageProcStage

from .constants import (
    DEFAULT_PIPELINE_STAGE_ORDER,
    EXPORT_STAGE_GIF_ID,
    EXPORT_STAGE_PNG_ID,
    EXPORT_STAGE_VIDEO_ID,
    STAGE_FOCUS_OVERLAY_ID,
    STAGE_RESIZE_LIMIT_ID,
    STAGE_TEMPLATE_CROP_ID,
    STAGE_TEMPLATE_OVERLAY_ID,
)
from .core import normalize_pipeline_stage_order
from .focus_overlay_stage import FocusOverlayStage
from .gif_export_stage import GifExportStage
from .png_export_stage import PngExportStage
from .resize_limit_stage import ResizeLimitStage
from .template_crop_stage import TemplateCropStage
from .template_overlay_stage import TemplateOverlayStage
from .video_proc_export_stage import VideoProcExportStage

_PROCESS_STAGE_CLASSES: dict[str, type[ImageProcStage]] = {
    STAGE_TEMPLATE_CROP_ID: TemplateCropStage,
    STAGE_RESIZE_LIMIT_ID: ResizeLimitStage,
    STAGE_TEMPLATE_OVERLAY_ID: TemplateOverlayStage,
    STAGE_FOCUS_OVERLAY_ID: FocusOverlayStage,
}


def build_default_image_proc_pipeline(stage_order: Any = None) -> ImageProcPipeline:
    return ImageProcPipeline(
        _PROCESS_STAGE_CLASSES[stage_id]()
        for stage_id in normalize_pipeline_stage_order(stage_order)
    )


def build_image_proc_export_stages() -> tuple[ImageProcExportStage, ...]:
    return (
        PngExportStage(),
        GifExportStage(),
        VideoProcExportStage(),
    )
