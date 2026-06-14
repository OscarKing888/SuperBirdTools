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

class ResizeLimitStage(ImageProcStage):
    stage_id = STAGE_RESIZE_LIMIT_ID
    label = "尺寸限制"
    description = "在裁切后按最长边限制缩放图像。"
    enabled_option_key = STAGE_RESIZE_LIMIT_ENABLED_KEY
    enabled_by_default = True

    def parameter_options(self) -> tuple[ImageProcOptionSpec, ...]:
        return (
            ImageProcOptionSpec(
                key="max_long_edge",
                label="最长边",
                value_type="int",
                default=_DEFAULT_TEMPLATE_MAX_LONG_EDGE,
                minimum=0,
            ),
        )

    def process(self, context: ImageProcContext) -> ImageProcContext:
        settings = core._clone_render_settings(context.settings)
        context.image = core._resize_fit(context.image, max(0, int(settings.get("max_long_edge") or 0)))
        return context

    def is_enabled(self, settings: Mapping[str, Any]) -> bool:
        return core._resolve_stage_enabled(
            settings,
            stage_id=self.stage_id,
            enabled_key=str(self.enabled_option_key or ""),
            default=self.enabled_by_default,
        )

