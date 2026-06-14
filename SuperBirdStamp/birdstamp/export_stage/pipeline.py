from __future__ import annotations

from typing import Any

from birdstamp.image_pipeline import ImageProcExportStage, ImageProcPipeline, ImageProcStage
from birdstamp.image_pipeline.image_proc_stage import (
    ImageProcFocusOverlayStage,
    ImageProcResizeLimitStage,
    ImageProcTemplateCropStage,
    ImageProcTemplateOverlayStage,
)

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
from .gif_export_stage import GifExportStage
from .png_export_stage import PngExportStage
from .video_proc_export_stage import VideoProcExportStage

_PROCESS_STAGE_CLASSES: dict[str, type[ImageProcStage]] = {
    STAGE_TEMPLATE_CROP_ID: ImageProcTemplateCropStage,
    STAGE_RESIZE_LIMIT_ID: ImageProcResizeLimitStage,
    STAGE_TEMPLATE_OVERLAY_ID: ImageProcTemplateOverlayStage,
    STAGE_FOCUS_OVERLAY_ID: ImageProcFocusOverlayStage,
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
