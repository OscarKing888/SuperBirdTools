from __future__ import annotations

from typing import Any, Mapping

from birdstamp.gui import template_context as _template_context
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

class TemplateOverlayStage(ImageProcStage):
    stage_id = STAGE_TEMPLATE_OVERLAY_ID
    label = "模板叠加"
    description = "绘制 Banner 背景和模板文字字段。"
    enabled_option_key = STAGE_TEMPLATE_OVERLAY_ENABLED_KEY
    enabled_by_default = True

    def parameter_options(self) -> tuple[ImageProcOptionSpec, ...]:
        return (
            ImageProcOptionSpec(key="draw_banner", label="Banner 底", value_type="bool", default=True),
            ImageProcOptionSpec(key="draw_text", label="文本", value_type="bool", default=True),
        )

    def is_enabled(self, settings: Mapping[str, Any]) -> bool:
        if not core._resolve_stage_enabled(
            settings,
            stage_id=self.stage_id,
            enabled_key=str(self.enabled_option_key or ""),
            default=self.enabled_by_default,
        ):
            return False
        return core._should_draw_template_overlay(dict(settings))

    def process(self, context: ImageProcContext) -> ImageProcContext:
        if not self.is_enabled(context.settings):
            return context
        settings = core._clone_render_settings(context.settings)
        template_payload = core._resolve_template_payload_for_render(settings, context.template_paths)
        raw_metadata = dict(context.raw_metadata or {})
        photo_source = context.photo_info or context.source_path or "."
        photo_info = _template_context.ensure_photo_info(photo_source, raw_metadata=raw_metadata)
        metadata_context = dict(context.metadata_context or {}) or core._build_metadata_context(photo_info, raw_metadata)
        context.image = core._render_template_overlay(
            context.image,
            raw_metadata=raw_metadata,
            metadata_context=metadata_context,
            photo_info=photo_info,
            template_payload=template_payload,
            draw_banner=core._parse_bool_value(settings.get("draw_banner"), True),
            draw_text=core._parse_bool_value(settings.get("draw_text"), True),
        )
        context.photo_info = photo_info
        context.metadata_context = metadata_context
        return context

